// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.lang.ref.WeakReference;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * [THE_CALL_IS_UNDER_THE_FRAME_V1] The Communicator's channel, on the phone: src/comms/channel.hpp, the same rules.
 *
 * A call's planes are for one Role (by default "mediahost"). The Role's setting is a tuple in the Communicator's
 * memory, and each end's report is another:
 *
 *     (channel, setting, [Role])      {driver, number, protection, step, ram_host, ram_port, check}
 *     (channel, at, [Role]:[end])     {role, end, driver, number, protection, step, sealed, framed, ram, ignored, build}
 *
 * The app holds one Channel. It holds the read on the setting (its own session to the memory), proves each setting it
 * reads with the Role's secret on this device (a setting whose check does not verify is ignored and the one held is
 * kept), and tells every plane's end of the frame, which switches in its own stream. [THE_MEMORY_MOVES_V1] ram_host
 * and ram_port say where the memory is: when they name another address, RamAt is told and everything follows.
 *
 * The secret is a file in the app's private storage: [secrets]/[Role], or [secrets]/default. A device with no secret
 * takes any setting and cannot open or send the Role's sealed frames.
 *
 * The owner's work (show, change) is here too: the Security tab calls it, as comms-channel calls the C++ library.
 */
public final class Channel {
    public static final String SERVICE = "channel", VAR = "setting", AT = "at", ROLE = "mediahost";
    public static final double REPORT_S = 10.0;
    public static final int REPORT_FRESH_S = 25;

    public interface Logger { void log(String line); }

    public final String role, sender, secrets, end;
    public final boolean framed;
    private final Logger log;
    private final Object mu = new Object();                 // this app's copy of the setting and its list of planes' ends
    private ChannelFrame.Setting held;
    private Map<String, String> fields = new TreeMap<String, String>();
    private String ignored = "";
    private volatile long changes = 0;
    // [THE_OVERLAY_SHOWS_THE_CHANNEL_V1] what the call's overlay shows of the channel: the last change and when, 8
    // characters of a hash of the key this app sends under (never the key), and what its planes have done
    private String changedWhat = "", changedAt = "", keyTag = "";
    private long changedNs = 0;
    private final ChannelFrame.Counts gone = new ChannelFrame.Counts();        // of the planes that have closed
    private Map<WeakReference<ChannelFrame.Codec>, ChannelFrame.Counts> seen = new java.util.IdentityHashMap<WeakReference<ChannelFrame.Codec>, ChannelFrame.Counts>();
    private final List<WeakReference<ChannelFrame.Codec>> codecs = new ArrayList<WeakReference<ChannelFrame.Codec>>();
    private static volatile Channel process, watching;

    // the read this app holds on the Role's setting
    private volatile RamSession c;
    private volatile boolean stop, moved, readOnce;
    private int movedId;
    private Thread watch;

    public Channel(String role, String sender, String secrets, boolean framed, Logger log) {
        this.role = role; this.sender = sender; this.secrets = secrets; this.framed = framed; this.log = log;
        StringBuilder b = new StringBuilder();
        SecureRandom r = new SecureRandom();
        for (int k = 0; k < 5; k++) b.append((char) ('0' + r.nextInt(10)));
        this.end = sender + "." + b;
        if (framed) say(ChannelFrame.BUILD + ": this app's planes are under the frame, for the Role '" + role + "', sending as " + sender);
        else say("this app's planes are not under the frame; it holds the read on '" + role + "'s setting to follow the memory when it moves");
    }

    private void say(String text) { log.log("[channel] " + text); }

    /** The app's channel when its planes are under the frame; null when they are as they were. */
    public static Channel ofProcess() { return process; }
    /** The app's channel, framed or not (null before the Communicator made it). */
    public static Channel watching() { return watching; }

    /** This device's ID as a sender: it is in every key it sends under. A phone is not a FrogNet node: "comms-[name]". */
    public static String senderId(String me) { return "comms-" + me; }

    // ------------------------------------------------------------------------------------------------ the setting
    private static String text(ChannelFrame.Setting s) {
        if (s == null) return "no setting: tcp, not sealed";
        return s.driver + ", " + (s.number != 0 ? "sealed with " + s.protection : "not sealed") + (s.step > 0 ? ", the Role's own secret at step " + s.step : "");
    }

    /** A tuple's JSON, each value as text the way the check's text has it (Python's and the C++ library's). */
    public static Map<String, String> fieldsOf(Map<String, Object> value) {
        Map<String, String> f = new TreeMap<String, String>();
        for (Map.Entry<String, Object> e : value.entrySet()) {
            Object v = e.getValue();
            if (v instanceof String) f.put(e.getKey(), (String) v);
            else if (v instanceof Boolean) f.put(e.getKey(), ((Boolean) v) ? "true" : "false");
            else if (v instanceof Number) {
                double d = ((Number) v).doubleValue();
                f.put(e.getKey(), d == Math.floor(d) && Math.abs(d) < 9e15 ? String.valueOf((long) d) : String.valueOf(d));
            }
        }
        return f;
    }

    /** And back, for the one who writes it: "step" is a number, everything else is text ("number" is 62 bits). */
    static Map<String, Object> valueOf(Map<String, String> fields) {
        Map<String, Object> v = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, String> e : fields.entrySet())
            v.put(e.getKey(), e.getKey().equals("step") ? (Object) Long.valueOf(Long.parseLong(e.getValue())) : (Object) e.getValue());
        return v;
    }

    /** A new plane's end of the frame, holding the setting this app holds now. */
    public ChannelFrame.Codec codec(String far) {
        ChannelFrame.Codec cd = new ChannelFrame.Codec(true, far, sender, secrets, new ChannelFrame.Say() { public void say(String t) { Channel.this.say(t); } });
        synchronized (mu) {
            cd.setting(role, held);
            for (int i = codecs.size() - 1; i >= 0; i--) if (codecs.get(i).get() == null) codecs.remove(i);
            codecs.add(new WeakReference<ChannelFrame.Codec>(cd));
        }
        return cd;
    }

    /** The setting's tuple as just read (null: there is none). Proved, kept, and every plane told. */
    void read(Map<String, String> f) {
        String toHost = ""; int toPort = 0;
        synchronized (mu) {
            if (f == null) {
                if (held != null) say("the setting of '" + role + "' is gone from the memory; this app keeps the one it holds (" + text(held) + ")");
                return;
            }
            ChannelFrame.Taken t;
            try { t = ChannelFrame.takeSetting(role, f, secrets); }
            catch (ChannelFrame.Gone e) { ignored = e.getMessage(); say("a setting of '" + role + "' is IGNORED: " + ignored); return; }
            if (!t.taken) { ignored = t.why; say("a setting of '" + role + "' is IGNORED: " + t.why + ". This app keeps " + text(held)); return; }
            ignored = "";
            String hNow = f.get("ram_host"), pNow = f.get("ram_port");
            List<String> what = new ArrayList<String>();
            if (held == null) what.add("first setting: " + t.setting.driver + ", " + cipherOf(t.setting));
            else {
                if (!held.driver.equals(t.setting.driver)) what.add("protocol " + held.driver + " -> " + t.setting.driver);
                if (!cipherOf(held).equals(cipherOf(t.setting))) what.add("cipher " + cipherOf(held) + " -> " + cipherOf(t.setting));
                else if (held.number != t.setting.number) what.add("new key");
                if (hNow != null && !hNow.isEmpty() && !(hNow.equals(fields.get("ram_host")) && String.valueOf(pNow).equals(String.valueOf(fields.get("ram_port")))))
                    what.add("memory -> " + hNow + ":" + pNow);
            }
            StringBuilder wb = new StringBuilder();
            for (String x : what) wb.append(wb.length() > 0 ? ", " : "").append(x);
            changedWhat = wb.length() > 0 ? wb.toString() : "setting written again";
            changedNs = System.nanoTime();
            changedAt = new java.text.SimpleDateFormat("HH:mm:ss.SSS", java.util.Locale.ROOT).format(new java.util.Date());
            keyTag = "";
            if (t.setting.number != 0) {
                try { keyTag = ChannelFrame.secretFingerprint(ChannelFrame.keyFor(ChannelFrame.secretAt(ChannelFrame.lower(role), t.setting.step, secrets).value, sender, ChannelFrame.lower(role), t.setting.number)); }
                catch (ChannelFrame.Gone e) { keyTag = "no secret"; }
            }
            held = t.setting;
            fields = new TreeMap<String, String>(f); fields.remove("ts");
            changes++;
            say("the setting of '" + role + "' is now " + text(held) + (t.fingerprint.isEmpty() ? " (this device holds no secret for it)" : " (secret " + t.fingerprint + ")")
                    + (t.moved ? "; this device's secret file moved forward to that step and the earlier secret was erased" : ""));
            for (WeakReference<ChannelFrame.Codec> w : codecs) { ChannelFrame.Codec cd = w.get(); if (cd != null) cd.setting(role, held); }
            String h = fields.get("ram_host"), p = fields.get("ram_port");
            if (h != null && p != null && !h.isEmpty()) { toHost = h; try { toPort = Integer.parseInt(p); } catch (NumberFormatException e) { toPort = 0; } }
        }
        // [THE_MEMORY_MOVES_V1] told outside the guard: each holder of a session ends its own and attaches there
        if (!toHost.isEmpty() && toPort > 0 && RamAt.move(toHost, toPort))
            say("the memory of '" + role + "' is at " + toHost + ":" + toPort + " now: this app follows it");
    }

    public String held() { synchronized (mu) { return text(held); } }
    public boolean sealed() { synchronized (mu) { return held != null && held.number != 0; } }
    public long changes() { return changes; }
    public String ignored() { synchronized (mu) { return ignored; } }
    public Map<String, String> fields() { synchronized (mu) { return new TreeMap<String, String>(fields); } }
    public boolean readOnce() { return readOnce; }

    private static String cipherOf(ChannelFrame.Setting h) { return h.number != 0 ? h.protection : "not sealed"; }

    /** 8 characters of a hash of the key this app sends under now ("" when not sealed, "no secret" without one). */
    public String keyTag() { synchronized (mu) { return keyTag; } }

    /** {what changed, the wall clock then, seconds since}; what is "" before the first setting. */
    public String[] lastChange() {
        synchronized (mu) { return new String[]{changedWhat, changedAt, changedWhat.isEmpty() ? "-1" : String.valueOf((System.nanoTime() - changedNs) / 1000000000L)}; }
    }

    private static void add(ChannelFrame.Counts t, ChannelFrame.Counts c) {
        t.sent += c.sent; t.received += c.received; t.notOpened += c.notOpened; t.sentAgain += c.sentAgain;
        for (int i = 0; i < 2; i++) {
            t.protocol[i] += c.protocol[i]; t.encryption[i] += c.encryption[i]; t.notOpenedCalls[i] += c.notOpenedCalls[i];
            t.again[i] += c.again[i]; t.receivedCalls[i] += c.receivedCalls[i];
        }
    }

    /** What this app's planes have done under the frame, summed over every plane it has had (a plane that has closed
     *  keeps what it had done when this was last asked; the screen asks twice a second). */
    public ChannelFrame.Counts totals() {
        synchronized (mu) {
            Map<WeakReference<ChannelFrame.Codec>, ChannelFrame.Counts> live = new java.util.IdentityHashMap<WeakReference<ChannelFrame.Codec>, ChannelFrame.Counts>();
            for (WeakReference<ChannelFrame.Codec> w : codecs) { ChannelFrame.Codec cd = w.get(); if (cd != null) live.put(w, cd.counts()); }
            for (Map.Entry<WeakReference<ChannelFrame.Codec>, ChannelFrame.Counts> e : seen.entrySet()) if (!live.containsKey(e.getKey())) add(gone, e.getValue());
            seen = live;
            ChannelFrame.Counts t = new ChannelFrame.Counts();
            add(t, gone);
            for (ChannelFrame.Counts c : live.values()) add(t, c);
            return t;
        }
    }

    /** This app's report: the setting it is running, or the one it ignored and why. No key, no secret. */
    Map<String, Object> report() {
        synchronized (mu) {
            ChannelFrame.Setting s = held != null ? held : new ChannelFrame.Setting();
            return Json.o("role", role, "end", end, "driver", s.driver, "number", String.valueOf(s.number), "protection", s.number != 0 ? s.protection : "",
                    "step", Math.max(0L, s.step), "sealed", s.number != 0, "framed", framed, "ram", RamAt.text(), "ignored", ignored, "build", ChannelFrame.BUILD);
        }
    }

    // ------------------------------------------------------------------------------------------- the held read
    /**
     * Makes the app's Channel and holds the read on the Role's setting at RamAt. framed: the app's planes are under
     * the frame; then this returns once the setting as it stands has been read, so no plane is opened before the app
     * knows whether the Role is sealed (RamSession.Unreachable if the memory does not answer in 10 s).
     */
    public static Channel forProgram(String role, boolean framed, String me, String secrets, Logger log) throws RamSession.Unreachable {
        Channel old = watching;
        if (old != null) old.close();
        Channel ch = new Channel(role == null || role.isEmpty() ? ROLE : role, senderId(me), secrets, framed, log);
        watching = ch;
        process = framed ? ch : null;
        ch.start();
        if (!framed) return ch;
        long until = System.currentTimeMillis() + 10000;
        while (!ch.readOnce && System.currentTimeMillis() < until) { try { Thread.sleep(10); } catch (InterruptedException e) { break; } }
        if (!ch.readOnce) {
            ch.close();
            throw new RamSession.Unreachable("the setting of '" + ch.role + "' could not be read from the memory at " + RamAt.text()
                    + " in 10 s: no plane is opened before this app knows whether the Role is sealed");
        }
        return ch;
    }

    private void start() {
        // [THE_MEMORY_MOVES_V1] this read is what moves the app, and it moves too: its session ends and the loop
        // attaches where the memory is now, reads the setting as it stands there and writes this end's report there.
        movedId = RamAt.whenMoved(new Runnable() { public void run() { moved = true; RamSession s = c; if (s != null) s.close(); } });
        watch = new Thread(new Runnable() { public void run() { watch(); } }, "channel-watch");
        watch.setDaemon(true);
        watch.start();
    }

    private void watch() {
        long after = -1;
        while (!stop) {
            try {
                if (moved) { moved = false; RamSession s = c; c = null; if (s != null) s.close(); }
                if (c == null || !c.alive()) { c = new RamSession(RamAt.host(), RamAt.port(), 8000); after = -1; }
                List<Tuples.Row> ts = after < 0 ? Tuples.get(c, SERVICE, VAR, 0, 0, 0, -1) : Tuples.get(c, SERVICE, VAR, 0, REPORT_S, 1, after);
                boolean mine = false;
                for (Tuples.Row t : ts) {
                    after = Math.max(after, t.id);
                    if (t.scope.equals(role)) { read(fieldsOf(t.value)); mine = true; }
                }
                if (!readOnce && !mine) read(null);
                // this end's report, written again at least every REPORT_S. A setting that moved the memory is
                // reported at the new address, by the next pass.
                if (!moved) Tuples.put(c, SERVICE, AT, role + ":" + end, report());
                readOnce = true;
                if (after < 0) after = 0;
            } catch (Exception e) {
                if (stop) break;
                if (moved) continue;                           // the move ended the read
                say("the read of the setting failed: " + e.getMessage() + " (the setting held is kept)");
                RamSession s = c; c = null; if (s != null) s.close();
                try { Thread.sleep(1000); } catch (InterruptedException ie) { if (stop) break; }
            }
        }
        RamSession s = c; c = null; if (s != null) s.close();
    }

    /** Ends the read and takes this end's report with it. */
    public void close() {
        if (stop) return;
        stop = true;
        RamAt.forget(movedId);
        try {
            RamSession s = new RamSession(RamAt.host(), RamAt.port(), 3000);
            try { Tuples.remove(s, SERVICE, AT, role + ":" + end); } finally { s.close(); }
        } catch (Exception ignored) { }
        RamSession s = c; if (s != null) s.close();
        if (watch != null) watch.interrupt();
        if (process == this) process = null;
        if (watching == this) watching = null;
    }

    // ------------------------------------------------------------------------------------------ the owner's work
    public static final class Now {                         // what the Security tab shows
        public Map<String, String> setting = new TreeMap<String, String>();        // without its check; empty: none
        public Map<String, Map<String, String>> ends = new TreeMap<String, Map<String, String>>();
    }

    public static final class Change {                      // exactly one of: protocol; an encryption; moveHost
        public String protocol = "", algorithm = "", moveHost = "";
        public boolean seal, unseal, newKey;
        public int movePort;
    }

    public static final class Done {
        public Map<String, String> setting;
        public boolean checked = true, moved;
        public List<String> lines = new ArrayList<String>();
        public int ends, running, ignoring, behind, chatCarried;
        public double ms;
        public String summary() {
            return ends + " end(s) there: " + running + (moved ? " attached there" : " running it") + (ignoring > 0 ? ", " + ignoring + " ignored it" : "")
                    + (behind > 0 ? ", " + behind + " not yet" : "") + " (" + Math.round(ms) + " ms after the write)";
        }
    }

    private static Map<String, String> settingAt(RamSession s, String role) throws RamSession.Unreachable, RamSession.Refused {
        for (Tuples.Row t : Tuples.get(s, SERVICE, VAR, 0, 0, 0, -1)) if (t.scope.equals(role)) { Map<String, String> f = fieldsOf(t.value); f.remove("ts"); return f; }
        return new TreeMap<String, String>();
    }

    private static Map<String, Map<String, String>> endsAt(RamSession s, String role) throws RamSession.Unreachable, RamSession.Refused {
        Map<String, Map<String, String>> out = new TreeMap<String, Map<String, String>>();
        for (Tuples.Row t : Tuples.get(s, SERVICE, AT, REPORT_FRESH_S, 0, 0, -1))
            if (t.scope.startsWith(role + ":")) { Map<String, String> f = fieldsOf(t.value); f.remove("ts"); out.put(t.scope.substring(role.length() + 1), f); }
        return out;
    }

    public static Now show(String ramHost, int ramPort, String role) throws RamSession.Unreachable, RamSession.Refused {
        RamSession s = new RamSession(ramHost, ramPort, 8000);
        try {
            Now n = new Now();
            n.setting = settingAt(s, role); n.setting.remove("check");
            n.ends = endsAt(s, role);
            return n;
        } finally { s.close(); }
    }

    /** This device's secret for the Role, as a screen may show it; throws if it holds none. */
    public static String secretLine(String role, String secrets) throws ChannelFrame.Gone {
        ChannelFrame.Secret s = ChannelFrame.readSecret(ChannelFrame.lower(role), secrets);
        return (s.own ? "the Role's own" : "the network's default") + ", hash " + ChannelFrame.secretFingerprint(s.value) + (s.own ? ", at step " + s.step : "");
    }

    private static String get(Map<String, String> f, String k) { String v = f.get(k); return v == null ? "" : v; }

    /**
     * One change by the owner. Writes the Role's setting with the change made and returns when every end's report
     * shows it, or says it ignored it, or waitS has passed. A move writes the setting at the new address first (and
     * carries the chat there, which has no owner to assert it), then at the old one, and waits for every end that
     * was at the old address to report from the new. IllegalArgumentException says why a change cannot be made.
     */
    public static Done change(String ramHost, int ramPort, String role, Change ch, double waitS, String secrets)
            throws RamSession.Unreachable, RamSession.Refused, ChannelFrame.Gone {
        boolean encryption = ch.seal || ch.unseal || ch.newKey || !ch.algorithm.isEmpty(), move = !ch.moveHost.isEmpty();
        if ((ch.protocol.isEmpty() ? 0 : 1) + (encryption ? 1 : 0) + (move ? 1 : 0) > 1)
            throw new IllegalArgumentException("a protocol, an encryption and the memory's address are not changed in one write: give one, then the next");
        if (ch.protocol.isEmpty() && !encryption && !move) throw new IllegalArgumentException("nothing to set");
        String r = ChannelFrame.lower(role);
        RamSession c = new RamSession(ramHost, ramPort, 8000), n = null;
        try {
            Map<String, String> now = settingAt(c, role);
            Map<String, String> next = new TreeMap<String, String>(now); next.remove("check");
            if (!ch.protocol.isEmpty()) {
                if (!ChannelFrame.driverKnown(ch.protocol)) throw new IllegalArgumentException("no protocol '" + ch.protocol + "' is here (there are: tcp, chorus, openqasm)");
                next.put("driver", ch.protocol);
            } else if (ch.unseal) {
                next.remove("number"); next.remove("protection");
            } else if (encryption) {
                if (!ch.algorithm.isEmpty() && !ChannelFrame.protectionKnown(ch.algorithm))
                    throw new IllegalArgumentException("no algorithm '" + ch.algorithm + "' is here (there are: aes-256-gcm, chacha20-poly1305)");
                String was = get(now, "number");
                if (ch.newKey && (was.isEmpty() || was.equals("0"))) throw new IllegalArgumentException(role + " is not sealed: Seal turns its encryption on");
                ChannelFrame.readSecret(r, secrets);           // this device must hold a secret: it makes the setting's check
                String number;
                SecureRandom rnd = new SecureRandom();
                do { number = String.valueOf((rnd.nextLong() >>> 2) + 1); } while (number.equals(was));   // 62 bits, never 0
                next.put("number", number);                    // the owner's random number: the key is computed from it
                next.put("protection", !ch.algorithm.isEmpty() ? ch.algorithm : !get(now, "protection").isEmpty() ? get(now, "protection") : ChannelFrame.DEFAULT_PROTECTION);
            } else {
                if (ch.movePort <= 0) throw new IllegalArgumentException("the memory's new address needs a port");
                if (ch.moveHost.equals(ramHost) && ch.movePort == ramPort) throw new IllegalArgumentException("the memory is at " + ramHost + ":" + ramPort + " already");
                next.put("ram_host", ch.moveHost); next.put("ram_port", String.valueOf(ch.movePort));
            }
            if (!next.containsKey("driver")) next.put("driver", "tcp");
            next.remove("step");                               // [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1]
            Done done = new Done();
            done.moved = move;
            ChannelFrame.Secret file = null;
            try { file = ChannelFrame.readSecret(r, secrets); } catch (ChannelFrame.DoesNotOpen e) { done.checked = false; }
            long step = 0;
            if (done.checked && file.own) {
                long had = 0; try { had = Long.parseLong(get(now, "step")); } catch (NumberFormatException e) { had = 0; }
                step = Math.max(had, file.step);
                if (!get(next, "number").isEmpty() && !get(next, "number").equals(get(now, "number"))) step += 1;
                if (step > 0) next.put("step", String.valueOf(step));
            }
            if (done.checked) next.put("check", ChannelFrame.checkOf(ChannelFrame.secretAt(r, step, secrets).value, r, next));
            done.setting = next;

            Map<String, Map<String, String>> were = new TreeMap<String, Map<String, String>>();
            if (move) {                                        // [THE_MEMORY_MOVES_V1]
                were = endsAt(c, role);
                try { n = new RamSession(ch.moveHost, ch.movePort, 8000); }
                catch (RamSession.Unreachable e) { throw new IllegalArgumentException("nothing answers as a memory at " + ch.moveHost + ":" + ch.movePort + " (" + e.getMessage() + "): the memory is not moved"); }
                for (Tuples.Row t : Tuples.get(c, Room.SERVICE, "chat", 0, 0, 0, -1)) { Tuples.put(n, Room.SERVICE, "chat", t.scope, t.value); done.chatCarried++; }
                Tuples.put(n, SERVICE, VAR, role, valueOf(next));   // first where everyone is going
            }
            RamSession w = move ? n : c;
            long after = 0;
            for (Tuples.Row t : Tuples.get(w, SERVICE, AT, 0, 0, 0, -1)) after = Math.max(after, t.id);
            long wrote = Tuples.put(c, SERVICE, VAR, role, valueOf(next));
            if (!move) after = wrote;
            String here = ch.moveHost + ":" + ch.movePort;
            long t0 = System.nanoTime();
            Map<String, Map<String, String>> ends = new TreeMap<String, Map<String, String>>();
            for (boolean first = true; ; first = false) {
                double left = waitS - (System.nanoTime() - t0) / 1e9;
                if (!first && left <= 0) break;
                // an end that follows reports at the new address; one that ignores the move says so at the old one
                if (!first) for (Tuples.Row t : Tuples.get(w, SERVICE, AT, 0, move ? Math.min(left, 0.5) : left, 1, after)) after = Math.max(after, t.id);
                ends.clear();
                for (String e : were.keySet()) ends.put(e, new TreeMap<String, String>());
                if (move) for (Map.Entry<String, Map<String, String>> e : endsAt(c, role).entrySet()) if (!get(e.getValue(), "ignored").isEmpty()) ends.put(e.getKey(), e.getValue());
                ends.putAll(endsAt(w, role));
                boolean all = true;
                for (Map<String, String> rep : ends.values()) if (!matches(rep, next, move, here) && get(rep, "ignored").isEmpty()) all = false;
                if (all) break;
            }
            done.ms = (System.nanoTime() - t0) / 1e6;
            for (Map.Entry<String, Map<String, String>> e : ends.entrySet()) {
                done.ends++;
                if (matches(e.getValue(), next, move, here)) { done.running++; continue; }
                if (!get(e.getValue(), "ignored").isEmpty()) { done.ignoring++; done.lines.add(e.getKey() + " IGNORED it: " + get(e.getValue(), "ignored")); }
                else { done.behind++; done.lines.add(e.getKey() + (move ? " has not arrived at " + here + " in " : " has not reported it in ") + Math.round(waitS) + " s"); }
            }
            return done;
        } finally {
            c.close();
            if (n != null) n.close();
        }
    }

    private static boolean matches(Map<String, String> rep, Map<String, String> next, boolean move, String here) {
        if (move) return get(rep, "ram").equals(here);
        String want = get(next, "number").isEmpty() ? "0" : get(next, "number");
        return get(rep, "driver").equals(get(next, "driver")) && get(rep, "number").equals(want) && (want.equals("0") || get(rep, "protection").equals(get(next, "protection")))
                && (get(next, "step").isEmpty() || get(rep, "step").equals(get(next, "step")));
    }
}
