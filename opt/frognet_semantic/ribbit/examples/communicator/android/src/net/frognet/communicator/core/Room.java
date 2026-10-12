// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The room on comms-ram, the same rows the C++ room (src/comms/room.cpp) and the Python room (frogcomms/room.py) write
 * and read, so the phone shares one lobby and one call with them: presence, chat, CallsAnnounce / CallsJoined /
 * CallsInvitations, MediaSpeed, MediaHold. Service "communicator", endpoint /comms-api.
 *
 * [HOLD_THE_READ_V1] everything that READS is answered from this participant's copy of the region, kept by ONE held
 * read on its own session ("wake me when anything newer than write-order id N exists"); a full read after 15 s quiet.
 * [READ_YOUR_OWN_WRITES_V1] a write lands in the copy at once.
 */
public final class Room {
    public static final String SERVICE = "communicator", API = "/comms-api";
    static final int HEARTBEAT_S = 5, PRESENCE_FRESH_S = 15;
    static final double HOLD_S = 15.0;

    public static final class Member { public String meId, name, status, caps, addr; public double ageS; }
    public static final class Line { public String fromId, fromName, text; public long tsMs; }
    public static final class Offer {
        public String session, mediaHost, offeredBy; public int mediaPort; public double ageS;
        Offer(String s, String h, String by, int p) { session = s; mediaHost = h; offeredBy = by; mediaPort = p; }
    }
    public static final class Hold {
        public boolean present, unanchored, keyHeld, passthrough, audioOnly;
        public long aimed, delivered, intersShed, keysHeld, w, h;
    }
    private static final class Row { String var, scope, addr; Map<String, Object> value; long id; double ageS; long gotMs; }

    public interface Logger { void log(String line); }

    public final String name, caps, meKey, meId;
    private final RamSession c, w;
    private final String ip;
    private final Logger log;
    private final Object mu = new Object();
    private final Map<String, Row> rows = new HashMap<String, Row>();
    private long lastId = -1;
    private volatile long version = 0;
    private volatile String status = "online";
    private final Map<String, String[]> offered = new LinkedHashMap<String, String[]>(), joined = new LinkedHashMap<String, String[]>();
    private final Set<String> everOffered = new HashSet<String>();
    private final List<String[]> owned = new ArrayList<String[]>();
    private volatile boolean stop = false;
    private volatile String fault = null;
    private final Thread watcher, beat;

    public Room(String host, int port, String name, String caps, Logger log) throws RamSession.Unreachable, RamSession.Refused {
        this.name = name; this.caps = caps; this.log = log;
        this.meKey = name.startsWith("*") ? name.substring(1) : name;   // comms_control: the name without the '*'
        this.meId = meKey;
        c = new RamSession(host, port, 8000);
        RamSession ww;
        try { ww = new RamSession(host, port, 8000); } catch (RamSession.Unreachable e) { c.close(); throw e; }
        w = ww;
        ip = c.localAddress();
        announce("online");
        absorb(get(w, 0, 0, 0, "", -1), true);
        watcher = new Thread(new Runnable() { public void run() { watch(); } }, "room-watch");
        watcher.setDaemon(true); watcher.start();
        beat = new Thread(new Runnable() { public void run() { heartbeat(); } }, "room-beat");
        beat.setDaemon(true); beat.start();
        log.log("[room] joined " + host + ":" + port + " as " + name + " (my address toward it: " + ip + ")");
    }

    public String myIp() { return ip; }
    public long version() { return version; }
    public String fault() { return fault; }

    public void close() {
        stop = true;
        w.close();                                            // [A_HELD_READ_ENDS_AT_ONCE_V1]
        synchronized (mu) { mu.notifyAll(); }
        beat.interrupt();
        try { beat.join(2000); watcher.join(2000); } catch (InterruptedException ignored) { }
        List<String[]> mine;
        synchronized (mu) { mine = new ArrayList<String[]>(owned); }
        for (String[] o : mine) {                              // own=true rows go with the writer (TuplesClient)
            try { removeTuple(o[0], o[1]); } catch (Exception e) { log.log("[room] could not remove " + o[0] + " " + o[1] + ": " + e.getMessage()); }
        }
        c.close();
    }

    // ---- the tuples API (defaults/api/tuples.cpp) ------------------------------------------------------------
    private long put(String var, String scope, Map<String, Object> value, boolean own) throws RamSession.Unreachable, RamSession.Refused {
        if (!value.containsKey("ts")) value.put("ts", System.currentTimeMillis() / 1000L);
        Map<String, Object> bag = Json.o("value", value, "addr", ip);
        String body = Json.write(Json.o("service", SERVICE, "variable", var, "instance", scope, "bag", bag));
        Map<String, Object> r = c.call("POST", API + "?op=write", body, 15.0);
        if (own) synchronized (mu) {
            boolean have = false;
            for (String[] o : owned) if (o[0].equals(var) && o[1].equals(scope)) have = true;
            if (!have) owned.add(new String[]{var, scope});
        }
        return Json.num(r.get("id"));
    }

    private static List<Row> get(RamSession s, int freshS, double waitS, int minRows, String var, long after)
            throws RamSession.Unreachable, RamSession.Refused {
        StringBuilder q = new StringBuilder(API + "?op=read&service=" + RamSession.urlenc(SERVICE));
        if (!var.isEmpty()) q.append("&variable=").append(RamSession.urlenc(var));
        if (freshS > 0) q.append("&fresh_s=").append(freshS);
        if (after >= 0) q.append("&after=").append(after);
        if (waitS > 0) q.append(String.format(Locale.ROOT, "&wait_s=%.3f&min_rows=%d", waitS, minRows > 0 ? minRows : 1));
        Map<String, Object> r = s.call("GET", q.toString(), "", 15.0 + waitS);
        List<Row> out = new ArrayList<Row>();
        double now = System.currentTimeMillis() / 1000.0;
        for (Object o : Json.arr(r.get("rows"))) {
            Map<String, Object> row = Json.obj(o);
            Map<String, Object> bag = Json.obj(row.get("bag"));
            Row t = new Row();
            t.var = Json.str(row.get("variable")); t.scope = Json.str(row.get("instance"));
            t.addr = Json.str(bag.get("addr")); t.value = Json.obj(bag.get("value"));
            t.id = Json.num(row.get("id")); t.ageS = now - Json.dbl(row.get("updated_epoch"));
            out.add(t);
        }
        return out;
    }

    private void removeTuple(String var, String scope) throws RamSession.Unreachable, RamSession.Refused {
        String q = API + "?op=read&service=" + RamSession.urlenc(SERVICE) + "&variable=" + RamSession.urlenc(var)
                + "&instance=" + RamSession.urlenc(scope);
        for (Object o : Json.arr(c.call("GET", q, "", 15.0).get("rows")))
            c.call("DELETE", API + "?op=remove&id=" + Json.num(Json.obj(o).get("id")), "", 15.0);
    }

    // ---- the copy of the region ------------------------------------------------------------------------------
    private void watch() {
        while (!stop) {
            try {
                long after;
                synchronized (mu) { after = lastId; }
                List<Row> ts = get(w, 0, HOLD_S, 1, "", after);
                if (stop) break;
                if (ts.isEmpty()) absorb(get(w, 0, 0, 0, "", -1), true);   // quiet: resync removals and ages
                else absorb(ts, false);
                fault = null;
            } catch (Exception e) {
                if (stop) break;
                fault = e.getMessage();
                log.log("[room] watch failed: " + e.getMessage() + " -- the session is gone; the room must be rejoined");
                break;                                             // gone is gone: the owner sees fault() and rejoins
            }
        }
    }

    private void absorb(List<Row> ts, boolean full) {
        long now = System.currentTimeMillis();
        synchronized (mu) {
            if (full) rows.clear();
            for (Row t : ts) {
                lastId = Math.max(lastId, t.id);
                t.gotMs = now;
                rows.put(t.var + "\u0000" + t.scope, t);
            }
            version++;
            mu.notifyAll();
        }
    }

    private void write(String var, String scope, Map<String, Object> value, boolean own) throws RamSession.Unreachable, RamSession.Refused {
        long id = put(var, scope, value, own);
        Row t = new Row();
        t.var = var; t.scope = scope; t.value = value; t.addr = ""; t.ageS = 0; t.id = id; t.gotMs = System.currentTimeMillis();
        synchronized (mu) { rows.put(var + "\u0000" + scope, t); version++; mu.notifyAll(); }
    }

    /** Blocks until the copy changes from `seen` or the timeout passes. */
    public boolean waitChange(long seen, long timeoutMs) {
        long end = System.currentTimeMillis() + timeoutMs;
        synchronized (mu) {
            while (version == seen && !stop) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) return false;
                try { mu.wait(left); } catch (InterruptedException e) { return false; }
            }
        }
        return true;
    }

    private List<Row> cached(String var, double freshS) {
        List<Row> out = new ArrayList<Row>();
        long now = System.currentTimeMillis();
        synchronized (mu) {
            for (Row t : rows.values()) {
                if (!t.var.equals(var)) continue;
                double age = t.ageS + (now - t.gotMs) / 1000.0;
                if (freshS > 0 && age > freshS) continue;
                Row r = new Row();
                r.var = t.var; r.scope = t.scope; r.addr = t.addr; r.value = t.value; r.id = t.id; r.ageS = age; r.gotMs = t.gotMs;
                out.add(r);
            }
        }
        return out;
    }

    private void heartbeat() {
        while (!stop) {
            try { Thread.sleep(HEARTBEAT_S * 1000L); } catch (InterruptedException e) { if (stop) return; }
            if (stop) return;
            try { announce(status); refreshCalls(); }
            catch (Exception e) { log.log("[room] heartbeat failed: " + e.getMessage()); fault = e.getMessage(); }
        }
    }

    // ---- presence and the lobby ------------------------------------------------------------------------------
    public void announce(String st) throws RamSession.Unreachable, RamSession.Refused {
        status = st;
        write("presence", "host:" + ip + ":presence:" + meId,
              Json.o("name", name, "status", st, "caps", caps, "ts", System.currentTimeMillis() / 1000L), true);
    }

    public List<Member> roster() {
        List<Member> out = new ArrayList<Member>();
        for (Row t : cached("presence", PRESENCE_FRESH_S)) {
            int i = t.scope.indexOf(":presence:");
            Member m = new Member();
            m.meId = i < 0 ? t.scope : t.scope.substring(i + 10);
            m.name = Json.str(t.value.get("name")); m.status = Json.str(t.value.get("status"));
            m.caps = Json.str(t.value.get("caps")); m.addr = t.addr; m.ageS = t.ageS;
            out.add(m);
        }
        Collections.sort(out, new Comparator<Member>() { public int compare(Member a, Member b) { return a.name.compareToIgnoreCase(b.name); } });
        return out;
    }

    // ---- chat --------------------------------------------------------------------------------------------------
    public void sendChat(String session, String text) throws RamSession.Unreachable, RamSession.Refused {
        write("chat", "session:chat:" + session + ":" + hex(10),
              Json.o("from_id", meId, "from_name", name, "text", text, "ts", System.currentTimeMillis()), false);  // lossless
    }

    public List<Line> readChat(String session) {
        String pre = "session:chat:" + session + ":";
        List<Line> out = new ArrayList<Line>();
        for (Row t : cached("chat", 3600)) {
            if (!t.scope.startsWith(pre)) continue;
            Line l = new Line();
            l.fromId = Json.str(t.value.get("from_id")); l.fromName = Json.str(t.value.get("from_name"));
            l.text = Json.str(t.value.get("text")); l.tsMs = Json.num(t.value.get("ts"));
            out.add(l);
        }
        Collections.sort(out, new Comparator<Line>() { public int compare(Line a, Line b) { return Long.compare(a.tsMs, b.tsMs); } });
        return out;
    }

    // ---- the viewer's link (MediaSpeed) and what the media server does for it (MediaHold) -----------------------
    public void setMediaSpeed(String session, long bps) throws RamSession.Unreachable, RamSession.Refused {
        write("MediaSpeed", "session:" + session + ":viewer:" + meId,
              Json.o("session", session, "viewer", meId, "bps", bps, "addr", ip, "ts", System.currentTimeMillis() / 1000L), false);
    }

    public Hold myHold(String session) {
        Hold h = new Hold();
        for (Row t : cached("MediaHold", 10)) {
            Map<String, Object> v = t.value;
            if (!session.equals(Json.str(v.get("session"))) || !meId.equals(Json.str(v.get("viewer")))) continue;
            h.present = true; h.unanchored = Json.bool(v.get("unanchored")); h.keyHeld = Json.bool(v.get("key_held"));
            h.passthrough = Json.bool(v.get("passthrough")); h.audioOnly = Json.bool(v.get("audio_only"));
            h.aimed = Json.num(v.get("aimed")); h.delivered = Json.num(v.get("delivered"));
            h.intersShed = Json.num(v.get("inters_shed")); h.keysHeld = Json.num(v.get("keys_held"));
            h.w = Json.num(v.get("w")); h.h = Json.num(v.get("h"));
        }
        return h;
    }

    // ---- calls (keyed to the participant, never an address: [ONE_ANNOUNCEMENT_PER_PARTICIPANT_V1]) -------------
    private static Map<String, Object> calls(Map<String, String[]> m, String sender) {
        Map<String, Object> o = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, String[]> e : m.entrySet()) {
            Map<String, Object> c = Json.o("host", e.getValue()[0], "port", Long.parseLong(e.getValue()[1]));
            if (sender != null && !sender.isEmpty()) c.put("from", sender);
            o.put(e.getKey(), c);
        }
        return o;
    }

    private void writeAnnounce() throws RamSession.Unreachable, RamSession.Refused {
        String scope = "user:" + meKey;
        Map<String, String[]> current = new LinkedHashMap<String, String[]>();
        for (Row t : cached("CallsAnnounce", 30)) {
            if (!t.scope.equals(scope)) continue;
            for (Map.Entry<String, Object> e : Json.obj(t.value.get("calls")).entrySet()) {
                Map<String, Object> cc = Json.obj(e.getValue());
                current.put(e.getKey(), new String[]{Json.str(cc.get("host")), String.valueOf(Json.num(cc.get("port")))});
            }
            break;
        }
        synchronized (mu) {
            for (String sid : everOffered) if (!offered.containsKey(sid)) current.remove(sid);
            current.putAll(offered);
        }
        write("CallsAnnounce", scope, Json.o("user", meKey, "host", ip, "calls", calls(current, null)), false);
    }

    private void writeJoined() throws RamSession.Unreachable, RamSession.Refused {
        Map<String, String[]> j;
        synchronized (mu) { j = new LinkedHashMap<String, String[]>(joined); }
        write("CallsJoined", "user:" + meKey, Json.o("user", meKey, "calls", calls(j, null)), false);
    }

    public void offerCall(String session, String host, int port) throws RamSession.Unreachable, RamSession.Refused {
        synchronized (mu) { offered.put(session, new String[]{host, String.valueOf(port)}); everOffered.add(session); }
        writeAnnounce();
    }

    public void joinCall(String session, String host, int port) throws RamSession.Unreachable, RamSession.Refused {
        synchronized (mu) { joined.put(session, new String[]{host, String.valueOf(port)}); }
        writeJoined();
    }

    /** Hanging up: this participant no longer offers or is in the session. */
    public void leaveCall(String session) throws RamSession.Unreachable, RamSession.Refused {
        boolean o, j;
        synchronized (mu) { o = offered.remove(session) != null; j = joined.remove(session) != null; }
        if (o) writeAnnounce();
        if (j) writeJoined();
    }

    public void invite(String who, String session, String host, int port) throws RamSession.Unreachable, RamSession.Refused {
        Map<String, String[]> m = new LinkedHashMap<String, String[]>();
        m.put(session, new String[]{host, String.valueOf(port)});
        write("CallsInvitations", "user:" + who, Json.o("user", who, "calls", calls(m, name)), false);
    }

    public void refreshCalls() throws RamSession.Unreachable, RamSession.Refused {
        boolean anyOffer, anyJoin;
        synchronized (mu) { anyOffer = !offered.isEmpty(); anyJoin = !joined.isEmpty(); }
        if (anyOffer) writeAnnounce();
        if (anyJoin) writeJoined();
    }

    public List<Offer> offers() {
        List<Offer> out = new ArrayList<Offer>();
        for (Row t : cached("CallsAnnounce", 30))
            for (Map.Entry<String, Object> e : Json.obj(t.value.get("calls")).entrySet()) {
                Map<String, Object> cc = Json.obj(e.getValue());
                out.add(new Offer(e.getKey(), Json.str(cc.get("host")), Json.str(t.value.get("user")), (int) Json.num(cc.get("port"))));
            }
        return out;
    }

    public List<String> callsOf(String user) {
        List<String> out = new ArrayList<String>();
        for (Row t : cached("CallsJoined", 30))
            if (t.scope.equals("user:" + user)) out.addAll(Json.obj(t.value.get("calls")).keySet());
        return out;
    }

    public List<Offer> invitations() {
        List<Offer> out = new ArrayList<Offer>();
        for (Row t : cached("CallsInvitations", 120)) {
            if (!t.scope.equals("user:" + meKey)) continue;
            for (Map.Entry<String, Object> e : Json.obj(t.value.get("calls")).entrySet()) {
                Map<String, Object> cc = Json.obj(e.getValue());
                Offer o = new Offer(e.getKey(), Json.str(cc.get("host")), Json.str(cc.get("from")), (int) Json.num(cc.get("port")));
                o.ageS = t.ageS;
                out.add(o);
            }
        }
        return out;
    }

    static String hex(int n) {
        SecureRandom r = new SecureRandom();
        StringBuilder b = new StringBuilder();
        for (int k = 0; k < n; k++) b.append("0123456789abcdef".charAt(r.nextInt(16)));
        return b.toString();
    }

    public static String newSession() { return hex(12); }
}
