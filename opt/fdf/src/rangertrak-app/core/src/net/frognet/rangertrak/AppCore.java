// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * [RT_APP_CORE_V1] The RangerTrak app's core, the same on the desktop and the phone: its door is the node it is on
 * (the node's ram.php: which RAMs are served where, the login), its RAMs are read through followers of the RAM
 * library (frognet-ram-java), and its own truth is written on those sessions with own=true, so it goes when the app
 * does ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]). The screens are the app's own page (ui/), which talks to this core by
 * JSON lines: handle(line) in, Ui.deliver(line) out.
 *
 * In (from the page):
 *   {"op":"connect","door":"10.170.170.1","dev":"...","name":"...","role":"eoc|team|public","team":"Alpha",
 *    "user":"","password":""}
 *   {"op":"say","to":"all"|"team:Alpha"|"dev:<id>","text":"..."}
 *   {"op":"fix","lat":..,"lng":..,"acc":..}                 this device's own position (GPS), when it has one
 * Out (to the page):
 *   {"ev":"status","text":"..."}        {"ev":"connected","role":..,"team":..,"user":..,"mission":..,"rams":{..}}
 *   {"ev":"world","who":{id:bag},"pos":{id:bag}}            everyone present now, and where
 *   {"ev":"msg","from":..,"name":..,"to":..,"text":..,"at":..}  a message, once each
 *   {"ev":"error","text":"..."}
 * Cells: (rt-who, <mission>, <dev>) and (rt-pos, <mission>, <dev>), latest, own; (rt-msg, <mission>, <dev>.<ms>) to
 * everyone; (rt-team, <mission>, <dev>.<ms>) in the team's own RAM; (rt-dm, <mission>.<to>, <from>.<ms>) in the
 * addressee's own variable, which only the addressee reads.
 */
public final class AppCore {
    public interface Ui { void deliver(String json); }

    public static final String APP = "rangertrak";
    public static final String API = "/FawcettInnovations.RINA.ram_interface.php";
    public static final String BUILD = "rt-app-2026-11-01-r3";

    private final Ui ui;
    private String door = "", dev = "", name = "", role = "", team = "", user = "", mission = "";
    private Map<String, String> rams = new HashMap<String, String>();
    private volatile Follower general, mis, dm, positions, msgs;
    private final Map<String, Follower> teams = new HashMap<String, Follower>();
    private final Map<String, Boolean> seen = new HashMap<String, Boolean>();   // message ids shown
    private Map<String, Object> fix = null;
    private volatile boolean resolving = false;

    public AppCore(Ui ui) { this.ui = ui; }

    private void out(Object... kv) { ui.deliver(Json.write(Json.o(kv))); }
    private void say(String t) { out("ev", "status", "text", t); }

    public synchronized void handle(String line) {
        Map<String, Object> m;
        try { m = Json.obj(Json.parse(line)); } catch (IllegalArgumentException e) { out("ev", "error", "text", "bad request: " + line); return; }
        String op = Json.str(m.get("op"));
        try {
            if (op.equals("connect")) connect(m);
            else if (op.equals("say")) send(Json.str(m.get("to")), Json.str(m.get("text")));
            else if (op.equals("fix")) position(m);
            else if (op.equals("hosts")) hosts();
            else out("ev", "error", "text", "unknown op " + op);
        } catch (Exception e) { out("ev", "error", "text", op + ": " + e.getMessage()); }
    }

    // ------------------------------------------------------------------ the door: the node this device is on
    private static String http(String method, String url, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000); c.setReadTimeout(10000); c.setRequestMethod(method);
        if (body != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
            OutputStream o = c.getOutputStream(); o.write(body.getBytes(StandardCharsets.UTF_8)); o.close();
        }
        InputStream in = c.getResponseCode() >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        while (in != null && (n = in.read(k)) > 0) b.write(k, 0, n);
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }

    private Map<String, Object> doorCall(String q, String body) throws Exception {
        String r = http(body == null ? "GET" : "POST", "http://" + door + "/dc/ram.php?" + q, body);
        Map<String, Object> o;
        try { o = Json.obj(Json.parse(r)); }
        catch (IllegalArgumentException e) {      // not the door's JSON: say what it means, not the page
            if (r.contains("404")) throw new Exception("the node at " + door + " does not serve RangerTrak (no /dc/ram.php there): use a node that has the app, e.g. Seattle7");
            throw new Exception("the node at " + door + " did not answer as RangerTrak's door");
        }
        if (!Json.bool(o.get("ok"))) throw new Exception(Json.str(o.get("error")));
        return o;
    }

    private Map<String, String> servedRams() throws Exception {
        Map<String, Object> o = doorCall("op=rams&app=" + APP, null);
        Map<String, String> out = new HashMap<String, String>();
        for (Map.Entry<String, Object> e : Json.obj(o.get("rams")).entrySet()) out.put(e.getKey(), Json.str(e.getValue()));
        return out;
    }

    private void connect(Map<String, Object> m) throws Exception {
        door = Json.str(m.get("door")).trim(); dev = Json.str(m.get("dev")); name = Json.str(m.get("name"));
        String want = Json.str(m.get("role"));
        say("connecting to the node at " + door + " (" + BUILD + ")");
        if (want.equals("eoc")) {          // the EOC's role is activated at its user's login, read at the door
            Map<String, Object> o = doorCall("op=login&app=" + APP, Json.write(Json.o("user", Json.str(m.get("user")), "password", Json.str(m.get("password")))));
            role = Json.str(o.get("role")); user = Json.str(m.get("user"));
            if (!role.equals("eoc")) throw new Exception("user " + user + " has role '" + role + "', not the EOC's");
            team = "";
        } else if (want.equals("team")) { role = "team"; team = Json.str(m.get("team")); user = ""; }
        else { role = "public"; team = ""; user = ""; }
        rams = servedRams();
        if (!rams.containsKey("general")) throw new Exception("RangerTrak's general RAM is not served on this network yet");
        start();
        out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
    }

    /** a team as people say it: its RAM is "alpha", the team is "Alpha" */
    static String cap(String t) { return t.isEmpty() ? t : Character.toUpperCase(t.charAt(0)) + t.substring(1).toLowerCase(); }

    private static String[] hp(String a) { int c = a.lastIndexOf(':'); return new String[]{a.substring(0, c), a.substring(c + 1)}; }

    private void stopAll() {
        for (Follower f : new Follower[]{general, mis, dm, positions, msgs}) if (f != null) f.stop();
        for (Follower f : teams.values()) f.stop();
        teams.clear(); general = mis = dm = positions = msgs = null;
    }

    /** the followers, on the RAMs where they are served now; ours go with them */
    private void start() {
        stopAll();
        String[] g = hp(rams.get("general"));
        Follower.Log lg = new Follower.Log() { public void say(String l) { onSessionLine(l); } };
        mis = new Follower("mission", g[0], Integer.parseInt(g[1]), API, "rt-mission", "settings", lg, null, new Runnable() { public void run() { onMission(); } });
        if (role.equals("public")) return;   // the public reads the world only (no chat)
        for (Map.Entry<String, String> e : rams.entrySet()) {
            String k = e.getKey();
            if (k.equals("general")) continue;      // the door says the app's RAMs by their names: general, and one per team
            String t = k;
            if (role.equals("eoc") || t.equalsIgnoreCase(team)) {
                String[] a = hp(e.getValue());
                final String tn = t;
                teams.put(t, new Follower("team " + t, a[0], Integer.parseInt(a[1]), API, "rt-team", lg, null,
                        new Runnable() { public void run() { onTeam(tn); } }));
            }
        }
    }

    private void onMission() {
        String best = ""; Map<String, Map<String, Object>> f = mis.family("settings");
        for (String k : f.keySet()) if (best.isEmpty() || k.compareTo(best) > 0) best = k;   // (one exercise at a time)
        if (best.isEmpty() || best.equals(mission)) return;
        mission = best;
        String[] g = hp(rams.get("general"));
        Follower.Log lg = new Follower.Log() { public void say(String l) { onSessionLine(l); } };
        for (Follower x : new Follower[]{general, positions, msgs, dm}) if (x != null) x.stop();
        general = new Follower("presence", g[0], Integer.parseInt(g[1]), API, "rt-who", lg, null, new Runnable() { public void run() { world(general, null); } });
        // rt-who and rt-pos and rt-msg: one follower each would be three sessions; the library follows one service, so
        // the world is two followers on the general RAM (presence + position) and the messages a third
        positions = new Follower("positions", g[0], Integer.parseInt(g[1]), API, "rt-pos", lg, null,
                new Runnable() { public void run() { world(general, null); } });
        if (!role.equals("public")) {
            msgs = new Follower("messages", g[0], Integer.parseInt(g[1]), API, "rt-msg", mission, lg, null, new Runnable() { public void run() { onMsgs(); } });
            dm = new Follower("direct", g[0], Integer.parseInt(g[1]), API, "rt-dm", mission + "." + dev, lg, null, new Runnable() { public void run() { onDm(); } });
        }
        out("ev", "mission", "id", mission, "name", Json.str(f.get(mission).get("name")));
        writeOwn();
    }


    /** this device's own truth, written again after every new session (a RAM change, a reconnect) */
    private void writeOwn() {
        if (general == null || role.equals("public")) return;
        new Thread(new Runnable() { public void run() {
            for (int i = 0; i < 100 && (general == null || !general.up() || positions == null || !positions.up()); i++) sleepMs(50);
            if (general == null) return;
            general.write(mission, dev, Json.o("name", name, "kind", role.equals("eoc") ? "eoc" : "team", "team", team, "host", "", "station", dev, "via", door,
                    "app", BUILD), true);
            if (fix != null && positions != null) positions.write(mission, dev, fix, true);
        } }, "write-own").start();
    }

    private void position(Map<String, Object> m) {
        if (role.equals("public") || positions == null) return;
        fix = Json.o("name", name, "kind", role.equals("eoc") ? "eoc" : "team", "team", team, "lat", Json.dbl(m.get("lat")),
                "lng", Json.dbl(m.get("lng")), "acc", Json.dbl(m.get("acc")), "at", System.currentTimeMillis() / 1000, "via", door);
        positions.write(mission, dev, fix, true);
    }

    private void world(Follower who, Object unused) {
        if (who == null || positions == null) return;
        out("ev", "world", "who", new HashMap<String, Object>(who.family(mission)), "pos", new HashMap<String, Object>(positions.family(mission)));
    }

    private void emitOnce(String id, Map<String, Object> b, String to) {
        synchronized (seen) { if (seen.containsKey(id)) return; seen.put(id, Boolean.TRUE); }
        out("ev", "msg", "id", id, "from", Json.str(b.get("from")), "name", Json.str(b.get("name")), "to", to,
                "text", Json.str(b.get("text")), "at", Json.num(b.get("at")));
    }

    private void onMsgs() { for (Map.Entry<String, Map<String, Object>> e : msgs.family(mission).entrySet()) emitOnce("m:" + e.getKey(), e.getValue(), "all"); }
    private void onDm() { for (Map.Entry<String, Map<String, Object>> e : dm.family(mission + "." + dev).entrySet()) emitOnce("d:" + e.getKey(), e.getValue(), "dev:" + dev); }
    private void onTeam(String t) {
        Follower f = teams.get(t); if (f == null) return;
        for (Map.Entry<String, Map<String, Object>> e : f.family(mission).entrySet()) emitOnce("t:" + t + ":" + e.getKey(), e.getValue(), "team:" + cap(t));
    }

    private void send(String to, String text) throws Exception {
        if (role.equals("public")) throw new Exception("the public view sends nothing");
        long ms = System.currentTimeMillis();
        Map<String, Object> b = Json.o("from", dev, "name", name, "text", text, "at", ms / 1000);
        String inst = dev + "." + ms;
        if (to.equals("all")) { if (msgs == null || !msgs.write(mission, inst, b, false)) throw new Exception("not connected"); }
        else if (to.startsWith("team:")) {
            String t = to.substring(5); Follower f = null;
            for (Map.Entry<String, Follower> e : teams.entrySet()) if (e.getKey().equalsIgnoreCase(t)) f = e.getValue();
            if (f == null) throw new Exception("team " + t + "'s RAM is not served on this network");
            b.put("team", t);
            if (!f.write(mission, inst, b, false)) throw new Exception("not connected to team " + t);
        } else if (to.startsWith("dev:")) {
            // in the addressee's own variable: only the addressee reads it; the sender keeps its own copy (the page shows it)
            if (dm == null || !dm.write(mission + "." + to.substring(4), inst, b, false)) throw new Exception("not connected");
            // dm follows OUR variable; the write goes to theirs on the same RAM (the follower's service is rt-dm)
            emitOnce("s:" + inst, b, to);
        }
    }

    /** [RT_HOSTS_V1] the nodes, where they are: a node with a GPS records it as its own sensor (gpsd_reporter.bash:
     *  metric_upsert.sh GPS Position, the gpsd TPV), in the network's sensor_data; read here through this node's
     *  proxy, once when asked (the Hosts layer). A node without a GPS has no position: it is not drawn. */
    private void hosts() {
        new Thread(new Runnable() { public void run() {
            List<Object> list = new ArrayList<Object>();
            List<String> nofix = new ArrayList<String>();
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", "http://databasehost.frognet/api.php?entity=sensors&action=list&SensorType=GPS", null)));
                for (Object r0 : Json.arr(o.get("rows"))) {
                    Map<String, Object> r = Json.obj(r0);
                    String nm = Json.str(r.get("SensorName")), id = Json.str(r.get("SensorID"));
                    if (!nm.endsWith(".GPS.Position") || id.isEmpty()) continue;
                    Map<String, Object> d = Json.obj(Json.parse(http("GET", "http://databasehost.frognet/api.php?entity=sensor_data&action=get&SensorID=" + id, null)));
                    Object jd = Json.obj(d.get("row")).get("jsonData");
                    String host = nm.substring(0, nm.length() - ".GPS.Position".length());
                    Map<String, Object> tpv = fixOf(jd);
                    if (tpv == null) { nofix.add(host); continue; }             // no fix: no position
                    list.add(Json.o("name", host, "address", Json.str(r.get("SensorAddress")),
                            "lat", Json.dbl(tpv.get("lat")), "lng", Json.dbl(tpv.get("lon")), "fixAt", Json.str(tpv.get("time"))));
                }
                out("ev", "hosts", "list", list);
                StringBuilder names = new StringBuilder();
                for (Object h : list) names.append(names.length() == 0 ? "" : ", ").append(Json.str(Json.obj(h).get("name")));
                say("Hosts: " + (list.size() + nofix.size()) + " node(s) record a GPS sensor; " + list.size() + " with a fix ("
                        + names + ")" + (nofix.isEmpty() ? "" : "; no fix: " + String.join(", ", nofix)));
            } catch (Exception e) { out("ev", "error", "text", "the nodes' GPS sensors (databasehost.frognet): " + e.getMessage()); }
        } }, "hosts").start();
    }

    /** [RT_HOSTS_V1] the fix in a GPS sensor's jsonData, as gpsd_reporter.bash stores it: gpsd's TPV object; or, when
     *  get_gps_data printed more than one TPV line (gpspipe -n 5), the reporter's {"error":"invalid gps json","raw":...}
     *  with those lines in raw -- the last one with a position is the fix. null: no position (no fix). */
    static Map<String, Object> fixOf(Object jd) {
        try {
            Map<String, Object> o = jd instanceof String ? Json.obj(Json.parse((String) jd)) : Json.obj(jd);
            if (o.get("lat") instanceof Number && o.get("lon") instanceof Number) return o;
            String raw = Json.str(o.get("raw"));
            Map<String, Object> last = null;
            for (int i = raw.indexOf('{'); i >= 0; i = raw.indexOf('{', i + 1)) {
                int depth = 0, j = i;
                for (; j < raw.length(); j++) { char c = raw.charAt(j); if (c == '{') depth++; else if (c == '}' && --depth == 0) break; }
                if (j >= raw.length()) break;
                try {
                    Map<String, Object> t = Json.obj(Json.parse(raw.substring(i, j + 1)));
                    if (t.get("lat") instanceof Number && t.get("lon") instanceof Number) last = t;
                } catch (IllegalArgumentException ignored) { }
                i = j;
            }
            return last;
        } catch (IllegalArgumentException e) { return null; }
    }

    // ------------------------------------------------------------------ sessions: a lost one means look again
    private void onSessionLine(String l) {
        say(l);
        if (!l.contains("FAILING") || resolving) return;
        resolving = true;
        new Thread(new Runnable() { public void run() {
            try {
                for (int i = 0; ; i++) {
                    try {
                        Map<String, String> now = servedRams();
                        if (!now.equals(rams)) {
                            say("the app's RAMs moved: " + now); rams = now; String m0 = mission; mission = "";
                            synchronized (AppCore.this) { start(); }
                            out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
                        }
                        return;          // the same RAMs: the followers' own reconnects carry on
                    } catch (Exception e) { say("the door at " + door + ": " + e.getMessage() + "; again in 2 s"); sleepMs(2000); }
                }
            } finally { resolving = false; }
        } }, "re-resolve").start();
    }

    private static void sleepMs(long ms) { try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }

    public synchronized void close() { stopAll(); }
}
