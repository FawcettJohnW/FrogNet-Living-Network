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
 *   [TEAM_HOST_V1] connect's "teamRam":"host:port" -- a member on its team host's hotspot: the team's RAM is there
 *   {"op":"teamHost","port":21800,"addr":"<where the EOC reaches this phone>","card":{"door":..,"join":..}}
 *                                                           this phone hosts its team's RAM (the lead)
 *   {"op":"door","door":"10.81.81.1"}                       [TEAM_SPLIT_V1] this device is on another network now: its node
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
    public static final String BUILD = "rt-app-2026-11-02-r19";

    private final Ui ui;
    private String door = "", dev = "", name = "", role = "", team = "", user = "", mission = "";
    private Map<String, String> rams = new HashMap<String, String>();
    private volatile Follower general, mis, dm, positions, msgs, helpF, reports, fieldF, facF, trF, callF, logF;
    private String status = "available";
    private final Map<String, Follower> teams = new HashMap<String, Follower>();
    private final Map<String, Boolean> seen = new HashMap<String, Boolean>();   // message ids shown
    private Map<String, Object> fix = null;
    private volatile boolean resolving = false;
    // [TEAM_HOST_V1] (John 2026-11-02: EOC > Team > Team Member; the team lead's phone hosts the team's RAM, the members
    // reach it on the lead's hotspot, the EOC wherever the lead's phone is on the network). The door still says where
    // every RAM fdf-app serves (doorRams); a team whose lead hosts says where its RAM is in (rt-team-host, host, <team>)
    // on the general RAM, the lead's own cell (own: gone with the lead's session), and that is where its RAM is.
    private Map<String, String> doorRams = new HashMap<String, String>();
    private String teamRam = "";                        // given at connect: a member on the host's hotspot
    private String hosting = "", hostAddr = "";         // the lead: its RAM here (127.0.0.1:port), where others reach it
    private Map<String, Object> card = null;            // the lead's card for its members: team, door, join line
    private volatile Follower hostF;
    private volatile String leadVia = "";

    public AppCore(Ui ui) { this.ui = ui; }

    private void out(Object... kv) { ui.deliver(Json.write(Json.o(kv))); }
    private void say(String t) { out("ev", "status", "text", t); }

    public synchronized void handle(String line) {
        Map<String, Object> m;
        try { m = Json.obj(Json.parse(line)); } catch (IllegalArgumentException e) { out("ev", "error", "text", "bad request: " + line); return; }
        String op = Json.str(m.get("op"));
        try {
            if (op.equals("connect")) connect(m);
            else if (op.equals("roles")) roles(m);
            else if (op.equals("missionSet")) missionSet(m);
            else if (op.equals("say")) send(Json.str(m.get("to")), Json.str(m.get("text")));
            else if (op.equals("fix")) position(m);
            else if (op.equals("hosts")) hosts();
            else if (op.equals("help")) help(m);
            else if (op.equals("helpAction")) helpAction(m);
            else if (op.equals("report")) report(m);
            else if (op.equals("sensorHosts")) sensorHosts();
            else if (op.equals("sensors")) sensors(Json.str(m.get("host")));
            else if (op.equals("sensor")) sensor(Json.str(m.get("id")), Json.str(m.get("name")));
            else if (op.equals("field")) field(m);
            else if (op.equals("fieldAction")) fieldAction(m);
            else if (op.equals("status")) myStatus(Json.str(m.get("status")));
            else if (op.equals("compliance")) compliance();
            else if (op.equals("facility")) facility(m);
            else if (op.equals("transport")) transport(m);
            else if (op.equals("transportAction")) transportAction(m);
            else if (op.equals("call")) call(m);
            else if (op.equals("teamHost")) teamHost(m);
            else if (op.equals("door")) redoor(Json.str(m.get("door")).trim());
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
        LAST_STATUS.set(c.getResponseCode());
        StringBuilder hs = new StringBuilder();                       // [DOOR_ANSWER_SAID_V1] who answered: the headers that say
        for (String h : new String[]{"Server", "X-FDF-Door", "X-FrogNet-Cache"}) { String v = c.getHeaderField(h); if (v != null) hs.append(hs.length() > 0 ? ", " : "").append(h).append(": ").append(v); }
        LAST_HEADERS.set(hs.toString());
        InputStream in = c.getResponseCode() >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        while (in != null && (n = in.read(k)) > 0) b.write(k, 0, n);
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }

    // [DOOR_ANSWER_SAID_V1] the calling thread's last answer: http() is static and runs on many threads at once
    private static final ThreadLocal<Integer> LAST_STATUS = new ThreadLocal<Integer>();
    private static final ThreadLocal<String> LAST_HEADERS = new ThreadLocal<String>();
    /** [DOOR_ANSWER_SAID_V1] what answered a call that was not the door's JSON: status, who, and the start of it */
    private String answered(String q, String r) {
        String t = r.replaceAll("<[^>]*>", " ").replaceAll("\\s+", " ").trim();
        String hs = LAST_HEADERS.get() == null ? "" : LAST_HEADERS.get();
        return "HTTP " + LAST_STATUS.get() + " for ?" + q.replaceAll("&.*", "") + (hs.isEmpty() ? "" : " (" + hs + ")") + ": " + (t.length() > 200 ? t.substring(0, 200) + "…" : t);
    }
    private Map<String, Object> doorCall(String q, String body) throws Exception {
        String r;
        try { r = http(body == null ? "GET" : "POST", "http://" + door + "/dc/ram.php?" + q, body); }
        catch (java.io.IOException e) {          // [DOOR_UNREACHABLE_SAYS_WHERE_FROM_V1] which addresses this device has
            throw new Exception("this device cannot reach the node at " + door + " (" + e.getMessage() + "); this device's addresses: " + myAddresses());
        }
        Map<String, Object> o;
        try { o = Json.obj(Json.parse(r)); }
        catch (IllegalArgumentException e) {      // not the door's JSON: say what it means, not the page
            throw new Exception("the node at " + door + " did not answer as RangerTrak's door: " + answered(q, r));
        }
        if (!Json.bool(o.get("ok"))) {            // the door says why ("why"); a stand-in may say "error"
            String why = Json.str(o.get("why")); if (why.isEmpty()) why = Json.str(o.get("error"));
            throw new Exception(why.isEmpty() ? "the door refused with no reason: " + answered(q, r) : why);
        }
        return o;
    }

    /** [RT_MISSION_V1] set the mission: an update of the current one (its settings, written again whole), or a new one
     *  (id: from its name and the time; "at": now, so it is the mission now on every device). (rt-mission, settings, <id>) */
    private void missionSet(Map<String, Object> m) throws Exception {
        if (mis == null) throw new Exception("not connected");
        if (role.equals("public")) throw new Exception("the public view sees the mission; it does not set it");
        Map<String, Object> b = Json.obj(m.get("bag"));
        String id = Json.str(m.get("id"));
        long now = System.currentTimeMillis() / 1000;
        if (id.isEmpty()) {
            String slug = Json.str(b.get("name")).toLowerCase().replaceAll("[^a-z0-9]+", "-").replaceAll("^-|-$", "");
            if (slug.isEmpty()) throw new Exception("a new mission needs a name");
            id = slug + "-" + new java.text.SimpleDateFormat("yyyyMMdd-HHmm").format(new java.util.Date());
            b.put("at", now);
        } else {
            Map<String, Object> was = mis.family("settings").get(id);
            if (was == null) throw new Exception("no mission " + id);
            if (was.get("at") != null) b.put("at", was.get("at"));
        }
        b.put("updated", now); b.put("by", name);
        if (!mis.write("settings", id, b, false)) throw new Exception("not connected");
        say((Json.str(m.get("id")).isEmpty() ? "mission started: " : "mission updated: ") + Json.str(b.get("name")));
    }

    /** [ADMIN_APP_V1] the teams and roles the admin defined, for the connect screen: (door op=roles) */
    private void roles(Map<String, Object> m) throws Exception {
        door = Json.str(m.get("door")).trim();
        if (door.isEmpty()) return;
        Map<String, Object> o = doorCall("op=roles&app=" + APP, null);
        out("ev", "roles", "roles", o.get("roles"), "login", o.get("login"));
    }

    /** this device's IPv4 addresses, each with its interface: where it is on the network, said when the node is out of reach */
    public static String myAddresses() {
        StringBuilder b = new StringBuilder();
        try {
            for (java.net.NetworkInterface n : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp() || n.isLoopback()) continue;
                for (java.net.InetAddress a : java.util.Collections.list(n.getInetAddresses()))
                    if (a instanceof java.net.Inet4Address) b.append(b.length() > 0 ? ", " : "").append(a.getHostAddress()).append(" (").append(n.getName()).append(")");
            }
        } catch (Exception e) { return "unknown (" + e.getMessage() + ")"; }
        return b.length() == 0 ? "none (no network)" : b.toString();
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
        else if (want.equals("hospital") || want.equals("ambulance")) { role = want; team = Json.str(m.get("unit")); user = ""; }
        else { role = "public"; team = ""; user = ""; }
        teamRam = Json.str(m.get("teamRam")).trim();
        doorRams = servedRams(); rams = withTeamRams(doorRams);
        if (!rams.containsKey("general")) throw new Exception("RangerTrak's general RAM is not served on this network yet");
        start();
        out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
        if (!role.equals("public")) probeSensors();
    }

    /** [RT_SENSORS_V1] said once at connect: whether this device reaches the network's sensor store, and how many nodes
     *  report there -- so a Sensors screen that would be empty says why before anyone presses it */
    private void probeSensors() {
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorType=System", null)));
                java.util.Set<String> h = new java.util.TreeSet<String>();
                for (Object r0 : Json.arr(o.get("rows"))) { String nm = Json.str(Json.obj(r0).get("SensorName")); int i = nm.indexOf(".System."); if (i > 0) h.add(nm.substring(0, i)); }
                say("Sensors: " + h.size() + " node(s) reporting (" + String.join(", ", h) + ") -- the Sensors button shows them");
            } catch (Exception e) {
                out("ev", "error", "text", "Sensors: this device does not reach the network's sensor store (" + SENSORS + "): " + e);
            }
        } }, "probe-sensors").start();
    }

    /** a team as people say it: its RAM is "alpha", the team is "Alpha" */
    static String cap(String t) { return t.isEmpty() ? t : Character.toUpperCase(t.charAt(0)) + t.substring(1).toLowerCase(); }

    private static String[] hp(String a) { int c = a.lastIndexOf(':'); return new String[]{a.substring(0, c), a.substring(c + 1)}; }

    private void stopAll() {
        for (Follower f : new Follower[]{general, mis, dm, positions, msgs, helpF, reports, fieldF, facF, trF, callF, logF, hostF}) if (f != null) f.stop();
        for (Follower f : teams.values()) f.stop();
        teams.clear(); general = mis = dm = positions = msgs = helpF = reports = fieldF = facF = trF = callF = logF = hostF = null;
    }

    /** the followers, on the RAMs where they are served now; ours go with them */
    private void start() {
        stopAll();
        String[] g = hp(rams.get("general"));
        Follower.Log lg = new Follower.Log() { public void say(String l) { onSessionLine(l); } };
        mis = new Follower("mission", g[0], Integer.parseInt(g[1]), API, "rt-mission", "settings", lg, null, new Runnable() { public void run() { onMission(); } });
        if (role.equals("public")) return;   // the public reads the world only (no chat)
        hostF = new Follower("team hosts", g[0], Integer.parseInt(g[1]), API, "rt-team-host", lg, null, new Runnable() { public void run() { onHosts(); } });
        startTeams();
    }

    /** the team followers: each on where its RAM is now */
    private void startTeams() {
        Follower.Log lg = new Follower.Log() { public void say(String l) { onSessionLine(l); } };
        for (Follower f : teams.values()) f.stop();
        teams.clear();
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

    // ------------------------------------------------------------------ [TEAM_HOST_V1] a team's RAM on its lead's phone
    /** where each RAM is for this device: the door's answer, then each hosting lead's own cell, then this device's own
     *  team as it was given (a member on the hotspot) or as it serves it (the lead) */
    private Map<String, String> withTeamRams(Map<String, String> door) {
        Map<String, String> out = new HashMap<String, String>(door);
        Follower h = hostF;
        if (h != null) for (Map.Entry<String, Map<String, Object>> e : h.family("host").entrySet()) {
            String r = Json.str(e.getValue().get("ram"));
            if (!r.isEmpty()) out.put(e.getKey().toLowerCase(), r);
        }
        String mine = team.toLowerCase();
        if (!mine.isEmpty()) { if (!hosting.isEmpty()) out.put(mine, hosting); else if (!teamRam.isEmpty()) out.put(mine, teamRam); }
        return out;
    }

    /** a lead's cell came or went: the teams follow their RAMs where they are now; a lead writes its own again when it is
     *  not there (its session began again, or it moved) */
    private void onHosts() {
        Follower h = hostF; if (h == null) return;
        String mine = team.toLowerCase();
        if (!hosting.isEmpty()) {
            Map<String, Object> c = h.family("host").get(mine);
            if (c == null || !hostAddr.equals(Json.str(c.get("ram"))) || !dev.equals(Json.str(c.get("dev"))))
                if (h.write("host", mine, Json.o("team", cap(team), "ram", hostAddr, "dev", dev, "name", name, "at", System.currentTimeMillis() / 1000), true))
                    say("[TEAM_HOST_V1] team " + cap(team) + "'s RAM is on this phone: the EOC reaches it at " + hostAddr);
        }
        synchronized (this) {
            Map<String, String> now = withTeamRams(doorRams);
            if (now.equals(rams)) return;
            say("[TEAM_HOST_V1] the teams' RAMs now: " + now);
            rams = now; startTeams();
            out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
        }
    }

    /** the lead's phone serves its team's RAM now (the app started it): its members are told (the card in that RAM, read
     *  by a member's app on the hotspot before it connects), the EOC by the lead's cell */
    private void teamHost(Map<String, Object> m) throws Exception {
        if (!role.equals("team") || team.isEmpty()) throw new Exception("only a team's phone hosts its team");
        int port = (int) Json.num(m.get("port")); String addr = Json.str(m.get("addr"));
        if (port <= 0) throw new Exception("no port for the team's RAM");
        hosting = "127.0.0.1:" + port; hostAddr = addr.isEmpty() ? "" : addr + ":" + port;
        Map<String, Object> c = Json.obj(m.get("card"));
        card = Json.o("team", cap(team), "door", Json.str(c.get("door")).isEmpty() ? door : Json.str(c.get("door")), "join", Json.str(c.get("join")),
                      "via", Json.str(c.get("via")), "lead", name, "at", System.currentTimeMillis() / 1000);
        say("[TEAM_HOST_V1] hosting team " + cap(team) + "'s RAM on this phone (" + hosting + ")" + (hostAddr.isEmpty() ? "; the EOC is not told where (no address on the network yet)" : "; the EOC is told " + hostAddr));
        onHosts();
        Follower f = teams.get(team.toLowerCase()); if (f != null) onTeam(team.toLowerCase());
    }

    /** [TEAM_SPLIT_V1] the node this device asks is another one now (the lead's phone joined another FrogNet; a member
     *  follows its lead's card): the RAMs are where that node says, the session starts again there. Nothing is carried
     *  over: each owner writes its own truth again where the RAMs are now. */
    private synchronized void redoor(String d) throws Exception {
        if (d.isEmpty() || d.equals(door)) return;
        say("[TEAM_SPLIT_V1] the node is " + d + " now (was " + door + ")");
        door = d;
        Map<String, Object> cd = card; if (cd != null) cd.put("door", d);
        doorRams = servedRams(); rams = withTeamRams(doorRams); mission = "";
        start();
        out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
    }

    /** [RT_MISSION_V1] the mission now: the one started last ("at", its start; a mission with none is older than any
     *  with one, then by id). Every mission and the current one's settings go to the page on every change. */
    private static double startedAt(Map<String, Object> b) { return b == null ? -1 : Json.num(b.get("at")); }
    private void onMission() {
        String best = ""; Map<String, Map<String, Object>> f = mis.family("settings");
        for (String k : f.keySet())
            if (best.isEmpty() || startedAt(f.get(k)) > startedAt(f.get(best)) || (startedAt(f.get(k)) == startedAt(f.get(best)) && k.compareTo(best) > 0)) best = k;
        if (best.isEmpty()) return;
        if (best.equals(mission)) { onLog(); out("ev", "mission", "id", mission, "name", Json.str(f.get(mission).get("name")), "bag", f.get(mission), "all", new HashMap<String, Object>(f)); return; }
        mission = best;
        String[] g = hp(rams.get("general"));
        Follower.Log lg = new Follower.Log() { public void say(String l) { onSessionLine(l); } };
        for (Follower x : new Follower[]{general, positions, msgs, dm, helpF, reports, fieldF, facF, trF, callF, logF}) if (x != null) x.stop();
        general = new Follower("presence", g[0], Integer.parseInt(g[1]), API, "rt-who", lg, null, new Runnable() { public void run() { world(general, null); } });
        // rt-who and rt-pos and rt-msg: one follower each would be three sessions; the library follows one service, so
        // the world is two followers on the general RAM (presence + position) and the messages a third
        positions = new Follower("positions", g[0], Integer.parseInt(g[1]), API, "rt-pos", lg, null,
                new Runnable() { public void run() { world(general, null); } });
        // [RT_HELP_V1] help requests, as DisasterComm's help page keeps them: (help, request, <rid>) the registrant's own
        // record (status open / safe / resolved: "safe" is how a person says they are safe); (help, action, <rid>~<dev>)
        // a responder's or the EOC's action on it. Not own: a request stays when the phone that made it is gone.
        helpF = new Follower("help", g[0], Integer.parseInt(g[1]), API, "help", lg, null, new Runnable() { public void run() { onHelp(); } });
        // [RT_FIELD_V1] what teams do in the field: (rt-field, <mission>, <dev>.<ms>) a deployed sensor platform, a
        // request for services, a hazard, a searched area -- its writer's record, not own (it stays when the phone goes);
        // (rt-field, <mission>.act, <item>~<dev>) the EOC's or a team's action on one (acknowledged, dispatched, done)
        // [RT_FACILITY_V1] each hospital's own status (beds by type, diversion, power, water, oxygen, staff, needs):
        // (rt-facility, <mission>, <facility>), written by whoever runs that facility's desk, not own (it stays);
        // [RT_TRANSPORT_V1] each patient transport, (rt-transport, <mission>, <dev>.<ms>), the unit's record, and the
        // hospital's acknowledgement (rt-transport, <mission>.act, <id>~<dev>)
        facF = new Follower("facilities", g[0], Integer.parseInt(g[1]), API, "rt-facility", mission, lg, null, new Runnable() { public void run() { onFacilities(); } });
        trF = new Follower("transports", g[0], Integer.parseInt(g[1]), API, "rt-transport", lg, null, new Runnable() { public void run() { onTransports(); } });
        // [RT_CALL_V1] a call to this device: (rt-call, <mission>.<dev>, <from>.<ms>), in the callee's own variable
        if (!role.equals("public"))
            callF = new Follower("calls", g[0], Integer.parseInt(g[1]), API, "rt-call", mission + "." + dev, lg, null, new Runnable() { public void run() { onCalls(); } });
        // [RT_RADIO_LOG_IN_THE_APP_V1] the radio log's entries (RangerTrak's own app, /rt/log.html: scribes and field phones),
        // (rt-log, <mission>.<opPeriod>, <dev>.<seq>), in the same general RAM: the EOC and the teams see them as they are
        // typed. Read only here: an entry's message number is its post's (ICS-309), made by the radio log.
        if (!role.equals("public"))
            logF = new Follower("radio log", g[0], Integer.parseInt(g[1]), API, "rt-log", lg, null, new Runnable() { public void run() { onLog(); } });
        fieldF = new Follower("field", g[0], Integer.parseInt(g[1]), API, "rt-field", lg, null, new Runnable() { public void run() { onField(); } });
        if (!role.equals("public")) {
            reports = new Follower("reports", g[0], Integer.parseInt(g[1]), API, "rt-report", mission, lg, null, new Runnable() { public void run() { onReports(); } });
        }
        if (!role.equals("public")) {
            msgs = new Follower("messages", g[0], Integer.parseInt(g[1]), API, "rt-msg", mission, lg, null, new Runnable() { public void run() { onMsgs(); } });
            dm = new Follower("direct", g[0], Integer.parseInt(g[1]), API, "rt-dm", mission + "." + dev, lg, null, new Runnable() { public void run() { onDm(); } });
        }
        out("ev", "mission", "id", mission, "name", Json.str(f.get(mission).get("name")), "bag", f.get(mission), "all", new HashMap<String, Object>(f));
        writeOwn();
    }


    /** this device's own truth, written again after every new session (a RAM change, a reconnect) */
    private void writeOwn() {
        if (general == null || role.equals("public")) return;
        new Thread(new Runnable() { public void run() {
            for (int i = 0; i < 100 && (general == null || !general.up() || positions == null || !positions.up()); i++) sleepMs(50);
            if (general == null) return;
            general.write(mission, dev, Json.o("name", name, "kind", kindOf(), "team", role.equals("team") ? team : "", "unit", team, "host", "", "station", dev, "via", door,
                    "status", status, "app", BUILD), true);
            if (fix != null && positions != null) positions.write(mission, dev, fix, true);
        } }, "write-own").start();
    }

    private void position(Map<String, Object> m) {
        out("ev", "myfix", "lat", Json.dbl(m.get("lat")), "lng", Json.dbl(m.get("lng")), "src", Json.str(m.get("src")));   // the page's "use my location"
        if (role.equals("public") || positions == null) return;          // the public's position is not published
        fix = Json.o("name", name, "kind", kindOf(), "team", role.equals("team") ? team : "", "unit", team, "lat", Json.dbl(m.get("lat")),
                "lng", Json.dbl(m.get("lng")), "acc", Json.dbl(m.get("acc")), "at", System.currentTimeMillis() / 1000, "via", door, "status", status,
                "src", Json.str(m.get("src")).isEmpty() ? "gps" : Json.str(m.get("src")));   // [RT_NO_GPS_V1] "address": typed, not measured
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
        // [TEAM_HOST_V1] the lead's card in its own team's RAM: what a member's app reads on the hotspot to connect
        Map<String, Object> cd = card;
        if (cd != null && !hosting.isEmpty() && t.equalsIgnoreCase(team)) {
            Map<String, Object> was = f.family("card").get(t); boolean same = was != null;
            if (same) for (String k : new String[]{"team", "door", "join", "via", "lead"}) same &= Json.str(cd.get(k)).equals(Json.str(was.get(k)));
            if (!same) f.write("card", t, cd, false);
        }
        // [TEAM_SPLIT_V1] a member on the lead's hotspot asks the node its lead asks: when the lead's card names another
        if (!teamRam.isEmpty() && hosting.isEmpty() && t.equalsIgnoreCase(team)) {
            Map<String, Object> lc = f.family("card").get(t); final String ld = lc == null ? "" : Json.str(lc.get("door"));
            // how the lead reaches its node ("lan": its members are on that LAN through it; "tunnel": each member needs
            // its own way there, its own membership) -- said to the phone's own side, which starts or ends it
            String via = lc == null ? "" : Json.str(lc.get("via"));
            if (!via.equals(leadVia)) { leadVia = via; out("ev", "teamVia", "via", via, "join", lc == null ? "" : Json.str(lc.get("join"))); }
            if (!ld.isEmpty() && !ld.equals(door))
                new Thread(new Runnable() { public void run() { try { redoor(ld); } catch (Exception e) { out("ev", "error", "text", "the lead's node " + ld + ": " + e.getMessage()); } } }, "redoor").start();
        }
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

    // ------------------------------------------------------------------ help, safe, reports (the public's own records)
    private void onHelp() {
        if (helpF == null) return;
        out("ev", "help", "requests", new HashMap<String, Object>(helpF.family("request")), "actions", new HashMap<String, Object>(helpF.family("action")));
    }

    private String kindOf() { return role.equals("eoc") ? "eoc" : role.equals("ambulance") ? "ambulance" : role.equals("hospital") ? "staff" : "team"; }

    private void onLog() {
        Follower f = logF, mf = mis; if (f == null || mf == null) return;
        Map<String, Object> mb = mf.family("settings").get(mission);
        Object o = mb == null ? null : mb.get("opPeriod");      // as the radio log's page says it (String(1) is "1")
        String op = o instanceof Number && ((Number) o).doubleValue() == ((Number) o).longValue() ? String.valueOf(((Number) o).longValue()) : Json.str(o);
        String v = mission + "." + (op.isEmpty() ? "1" : op);
        out("ev", "radiolog", "op", op.isEmpty() ? "1" : op, "entries", new HashMap<String, Object>(f.family(v)));
    }
    private void onFacilities() { if (facF != null) out("ev", "facilities", "list", new HashMap<String, Object>(facF.family(mission))); }
    private void onTransports() {
        if (trF == null) return;
        out("ev", "transports", "items", new HashMap<String, Object>(trF.family(mission)), "acts", new HashMap<String, Object>(trF.family(mission + ".act")));
    }
    private void facility(Map<String, Object> m) throws Exception {
        if (facF == null) throw new Exception("not connected");
        Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", name); b.put("at", System.currentTimeMillis() / 1000);
        if (!facF.write(mission, Json.str(m.get("id")), b, false)) throw new Exception("not connected");
        say("published the status of " + Json.str(b.get("name")));
    }
    private void transport(Map<String, Object> m) throws Exception {
        if (trF == null) throw new Exception("not connected");
        long ms = System.currentTimeMillis();
        Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", dev); b.put("unit", team.isEmpty() ? name : team); b.put("name", name); b.put("at", ms / 1000);
        if (!trF.write(mission, dev + "." + ms, b, false)) throw new Exception("not connected");
        say("transport to " + Json.str(b.get("to")) + " sent: the hospital sees it now");
    }
    private void transportAction(Map<String, Object> m) throws Exception {
        if (trF == null) throw new Exception("not connected");
        Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", team.isEmpty() ? name : team, "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
        if (!trF.write(mission + ".act", Json.str(m.get("id")) + "~" + dev, b, false)) throw new Exception("not connected");
    }

    // ------------------------------------------------------------------ [RT_CALL_V1] Call: the Communicator, both ends
    private final Map<String, Boolean> callsSeen = new HashMap<String, Boolean>();
    private void call(Map<String, Object> m) throws Exception {
        String to = Json.str(m.get("to"));
        if (callF == null) throw new Exception("not connected");
        long ms = System.currentTimeMillis();
        if (!callF.write(mission + "." + to, dev + "." + ms, Json.o("from", dev, "name", name, "door", door, "at", ms / 1000), false))
            throw new Exception("not connected");
        out("ev", "comms", "name", name, "peer", Json.str(m.get("peerName")), "door", door);
    }
    private void onCalls() {
        if (callF == null) return;
        long now = System.currentTimeMillis() / 1000;
        for (Map.Entry<String, Map<String, Object>> e : callF.family(mission + "." + dev).entrySet()) {
            synchronized (callsSeen) { if (callsSeen.containsKey(e.getKey())) continue; callsSeen.put(e.getKey(), Boolean.TRUE); }
            Map<String, Object> b = e.getValue();
            if (now - Json.num(b.get("at")) > 90) continue;           // an old ring is not a call now
            // the callee joins the caller's Communicator servers (the caller's node), so both ends meet in one place
            String d = Json.str(b.get("door"));
            out("ev", "incoming", "from", Json.str(b.get("from")), "name", Json.str(b.get("name")), "door", d.isEmpty() ? door : d, "me", name);
        }
    }

    private void onField() {
        if (fieldF == null) return;
        out("ev", "field", "items", new HashMap<String, Object>(fieldF.family(mission)), "acts", new HashMap<String, Object>(fieldF.family(mission + ".act")));
    }

    private void field(Map<String, Object> m) throws Exception {
        if (role.equals("public") || fieldF == null) throw new Exception("field actions are the teams' and the EOC's");
        long ms = System.currentTimeMillis();
        Map<String, Object> b = Json.obj(m.get("bag"));
        b.put("by", dev); b.put("name", name); b.put("team", team); b.put("at", ms / 1000);
        if (!fieldF.write(mission, dev + "." + ms, b, false)) throw new Exception("not connected");
        say("sent: " + Json.str(b.get("what")) + " " + Json.str(b.get("type")));
    }

    private void fieldAction(Map<String, Object> m) throws Exception {
        if (role.equals("public") || fieldF == null) throw new Exception("not connected");
        Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", name, "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
        if (!fieldF.write(mission + ".act", Json.str(m.get("id")) + "~" + dev, b, false)) throw new Exception("not connected");
    }

    /** this member's status (available, en route, on scene, needs assistance, out of service): part of its own presence */
    private void myStatus(String st) {
        status = st;
        writeOwn();
        if (fix != null && positions != null) { fix.put("status", st); positions.write(mission, dev, fix, true); }
    }

    private void onReports() {
        if (reports == null) return;
        out("ev", "reports", "list", new HashMap<String, Object>(reports.family(mission)));
    }

    private void help(Map<String, Object> m) throws Exception {
        String rid = Json.str(m.get("rid"));
        if (rid.isEmpty() || helpF == null) throw new Exception("not connected");
        Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", dev); b.put("at", System.currentTimeMillis() / 1000);
        if (!helpF.write("request", rid, b, false)) throw new Exception("not connected");
        say("published: " + Json.str(b.get("status")).replace("open", "a request for help") + " for " + Json.str(b.get("name")));
    }

    private void helpAction(Map<String, Object> m) throws Exception {
        if (role.equals("public") || helpF == null) throw new Exception("only responders and the EOC act on requests");
        String rid = Json.str(m.get("rid"));
        Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", name, "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
        if (!helpF.write("action", rid + "~" + dev, b, false)) throw new Exception("not connected");
    }

    private void report(Map<String, Object> m) throws Exception {
        long ms = System.currentTimeMillis();
        Map<String, Object> b = Json.o("from", dev, "name", name, "category", Json.str(m.get("category")), "text", Json.str(m.get("text")),
                "at", ms / 1000);
        if (m.get("lat") instanceof Number) { b.put("lat", Json.dbl(m.get("lat"))); b.put("lng", Json.dbl(m.get("lng"))); }
        // the public has no reports follower (it reads none): the write goes on the help session, to rt-report
        String[] g = hp(rams.get("general"));
        net.frognet.ram.RamSession s = new net.frognet.ram.RamSession(g[0], Integer.parseInt(g[1]), 5000);
        try { s.call("POST", API + "?op=write", Json.write(Json.o("service", "rt-report", "variable", mission, "instance", dev + "." + ms, "bag", b)), 10); }
        finally { s.close(); }
        say("your report is with the EOC");
    }

    // ------------------------------------------------------------------ sensors: every node's, as metric_upsert.sh files them
    // the network's sensor store, through this device's node (its DNS names databasehost.frognet); rt.sensors: a test's stand-in
    private static final String SENSORS = System.getProperty("rt.sensors", "http://databasehost.frognet/api.php");
    private static String enc(String s) { return net.frognet.ram.RamSession.urlenc(s); }

    /** [RT_SENSORS_V1] the nodes that report: every node has at least its System sensor (sysperf_reporter.bash ->
     *  metric_upsert.sh System Perf), named <node>.System.<metric>. Read once when asked; Refresh asks again. */
    private void sensorHosts() {
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorType=System", null)));
                Map<String, Object> hosts = new HashMap<String, Object>();
                for (Object r0 : Json.arr(o.get("rows"))) {
                    Map<String, Object> r = Json.obj(r0); String nm = Json.str(r.get("SensorName"));
                    int i = nm.indexOf(".System."); if (i <= 0) continue;
                    hosts.put(nm.substring(0, i), Json.str(r.get("SensorAddress")));
                }
                out("ev", "sensorHosts", "hosts", hosts);
            } catch (Exception e) { out("ev", "error", "text", "the nodes' sensors (databasehost.frognet): " + e.getMessage()); }
        } }, "sensor-hosts").start();
    }

    private void sensors(final String host) {
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorName__like=" + enc(host + ".%"), null)));
                List<Object> list = new ArrayList<Object>();
                for (Object r0 : Json.arr(o.get("rows"))) {
                    Map<String, Object> r = Json.obj(r0);
                    list.add(Json.o("id", Json.str(r.get("SensorID")), "name", Json.str(r.get("SensorName")), "type", Json.str(r.get("SensorType")),
                            "address", Json.str(r.get("SensorAddress"))));
                }
                out("ev", "sensors", "host", host, "list", list);
            } catch (Exception e) { out("ev", "error", "text", "sensors of " + host + ": " + e.getMessage()); }
        } }, "sensors").start();
    }

    private void sensor(final String id, final String nm) {
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> d = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensor_data&action=get&SensorID=" + enc(id), null)));
                Map<String, Object> row = Json.obj(d.get("row"));
                Object jd = row.get("jsonData");
                if (jd instanceof String) try { jd = Json.parse((String) jd); } catch (IllegalArgumentException ignored) { }
                Map<String, Object> fx = nm.endsWith(".GPS.Position") ? fixOf(row.get("jsonData")) : null;
                out("ev", "sensor", "id", id, "name", nm, "data", jd, "updated", Json.str(row.get("updated_at")), "fix", fx);
            } catch (Exception e) { out("ev", "error", "text", "sensor " + nm + ": " + e.getMessage()); }
        } }, "sensor").start();
    }

    /** [RT_COMPLIANCE_V1] (John, 2026-11-01) "Any host with working environmental sensors should identify itself as being
     *  out of compliance." A node's environmental sensor is one whose reading carries environmental measurements
     *  (temperature, humidity, pressure, air quality, gas, radiation, water...: sensehat_reporter.bash and the like);
     *  working = its reading has such a value. Every node is asked once (its System sensor names it), and each such
     *  node is reported with its readings. The rule is here, in one place, to change when the criteria do. */
    static final String[] ENV = {"temp", "humid", "pressure", "pm2", "pm10", "aqi", "co2", "voc", "gas", "radiation", "water", "smoke", "lux"};
    static Map<String, Object> environmental(Object jd) {
        Map<String, Object> o;
        try { o = jd instanceof String ? Json.obj(Json.parse((String) jd)) : Json.obj(jd); } catch (IllegalArgumentException e) { return null; }
        Map<String, Object> got = new java.util.TreeMap<String, Object>();
        for (Map.Entry<String, Object> e : o.entrySet()) {
            String k = e.getKey().toLowerCase();
            if (!(e.getValue() instanceof Number)) continue;
            for (String w : ENV) if (k.contains(w)) { got.put(e.getKey(), e.getValue()); break; }
        }
        return got.isEmpty() ? null : got;
    }

    private void compliance() {
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorType=System", null)));
                Map<String, String> hosts = new java.util.TreeMap<String, String>();
                for (Object r0 : Json.arr(o.get("rows"))) {
                    String nm = Json.str(Json.obj(r0).get("SensorName")); int i = nm.indexOf(".System.");
                    if (i > 0) hosts.put(nm.substring(0, i), Json.str(Json.obj(r0).get("SensorAddress")));
                }
                Map<String, Object> out = new HashMap<String, Object>();
                for (Map.Entry<String, String> h : hosts.entrySet()) {
                    Map<String, Object> l = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorName__like=" + enc(h.getKey() + ".%"), null)));
                    List<Object> found = new ArrayList<Object>();
                    for (Object r0 : Json.arr(l.get("rows"))) {
                        Map<String, Object> r = Json.obj(r0); String t = Json.str(r.get("SensorType")), nm = Json.str(r.get("SensorName"));
                        if (t.equals("System") || t.equals("GPS") || t.equals("LinkStats") || t.equals("SemanticProxy")) continue;
                        Map<String, Object> d = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensor_data&action=get&SensorID=" + enc(Json.str(r.get("SensorID"))), null)));
                        Map<String, Object> env = environmental(Json.obj(d.get("row")).get("jsonData"));
                        if (env != null) found.add(Json.o("sensor", nm, "readings", env, "updated", Json.str(Json.obj(d.get("row")).get("updated_at"))));
                    }
                    if (!found.isEmpty()) out.put(h.getKey(), Json.o("address", h.getValue(), "sensors", found));
                }
                out("ev", "compliance", "hosts", out, "asked", hosts.size());
            } catch (Exception e) { out("ev", "error", "text", "compliance (the nodes' sensors): " + e.getMessage()); }
        } }, "compliance").start();
    }

    /** [RT_HOSTS_V1] the nodes, where they are: a node with a GPS records it as its own sensor (gpsd_reporter.bash:
     *  metric_upsert.sh GPS Position, the gpsd TPV), in the network's sensor_data; read here through this node's
     *  proxy, once when asked (the Hosts layer). A node without a GPS has no position: it is not drawn. */
    private void hosts() {
        new Thread(new Runnable() { public void run() {
            List<Object> list = new ArrayList<Object>();
            List<String> nofix = new ArrayList<String>();
            try {
                Map<String, Object> o = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensors&action=list&SensorType=GPS", null)));
                for (Object r0 : Json.arr(o.get("rows"))) {
                    Map<String, Object> r = Json.obj(r0);
                    String nm = Json.str(r.get("SensorName")), id = Json.str(r.get("SensorID"));
                    if (!nm.endsWith(".GPS.Position") || id.isEmpty()) continue;
                    Map<String, Object> d = Json.obj(Json.parse(http("GET", SENSORS + "?entity=sensor_data&action=get&SensorID=" + id, null)));
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
                        Map<String, String> door0 = servedRams(); Map<String, String> now = withTeamRams(door0);
                        if (!now.equals(rams)) {
                            say("the app's RAMs moved: " + now); doorRams = door0; rams = now; mission = "";
                            synchronized (AppCore.this) { start(); }
                            out("ev", "connected", "role", role, "team", team, "user", user, "rams", new HashMap<String, Object>(rams), "build", BUILD);
                            return;
                        }
                        // [FAILOVER_ASKS_UNTIL_ANSWERED_V1] the same answer: the node may not know yet where the RAMs went.
                        // Ask again until the answer changes or the old sessions come back (the RAM was only blipped).
                        Follower g = general != null ? general : mis;
                        if (g != null && g.up() && g.failing() == null) return;
                        sleepMs(500);
                    } catch (Exception e) { say("the door at " + door + ": " + e.getMessage() + "; again in 2 s"); sleepMs(2000); }
                }
            } finally { resolving = false; }
        } }, "re-resolve").start();
    }

    private static void sleepMs(long ms) { try { Thread.sleep(ms); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } }

    public synchronized void close() { stopAll(); }
}
