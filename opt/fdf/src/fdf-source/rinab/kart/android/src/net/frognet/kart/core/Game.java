// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart.core;

import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * A kart player: kartdemo.py's lobby and race, without a screen. [KART_LOBBY_V1] [KART_ANDROID_V1]
 * The game writes its own truths in the kart memory -- (kart, lobby, NAME) {name, machine, bots, ready, start, laps} and its
 * chat lines -- and reads the service's: (kart, agreed, lobby), (kart, server, *), records, results. When a server tuple
 * names this player, it maps that race memory, takes its grid slots, races its karts there (written on change), and is
 * back in the lobby when the server tuple is gone. It never hosts a race (a phone has no rina-host).
 * tick() is called from one thread (the game loop); the getters are safe from the drawing thread.
 */
public final class Game {
    public static final String BUILD = "kart-android-2026-10-23-r2";
    static final Map<String, int[]> COLORS = new HashMap<String, int[]>();
    static final int[][] BOT_COLORS = {{240,140,40},{150,90,220},{120,200,40},{40,170,150},{200,60,110},{110,110,240},{220,220,120},{170,120,80}};
    static {
        COLORS.put("red", new int[]{222,67,67}); COLORS.put("blue", new int[]{66,122,222}); COLORS.put("green", new int[]{72,190,96});
        COLORS.put("yellow", new int[]{230,198,60}); COLORS.put("pink", new int[]{224,96,180}); COLORS.put("cyan", new int[]{70,196,208});
    }
    public static int[] color(String n) { int[] c = COLORS.get(n); return c != null ? c : new int[]{200,200,200}; }

    public final String name, machine, kartHost;
    public final int kartPort;
    public final Kart me;
    public final List<Kart> bots = new ArrayList<Kart>();
    private final Map<String, Kart> mine = new LinkedHashMap<String, Kart>();
    private final Region main;
    private final Region comm;                                 // [KART_CALLS_IN_THE_GAME_V1] call tiles: (comm, tile, NAME), as kartdemo
    private volatile String callStatus = "", callCalling = "", callId = "";
    private Map<String, Object> tileWritten = null;
    private int tileGen = -1;
    private volatile Region race = null;
    private final Region.Log log;
    private final ArrayDeque<String> lines = new ArrayDeque<String>();

    public volatile boolean autodrive;
    private volatile boolean ready = false, start = false;
    private volatile int laps;
    private Map<String, Object> lobbyWritten = null;
    private int lobbyGen = -1, chatN = 0;

    public volatile String state = "lobby";
    private volatile String myRace = null;
    private volatile Map<String, Object> myServer = null;
    private volatile boolean placed = false;
    private volatile double startAt = 0;
    private volatile int lapsTotal = 0;
    private volatile Map<String, Object> lastResult = null;

    public Game(String name, String machine, String kartHost, int kartPort, int botCount, boolean autodrive, int laps, Region.Log sink) {
        this.name = name; this.machine = machine; this.kartHost = kartHost; this.kartPort = kartPort;
        this.autodrive = autodrive; this.laps = Math.max(1, laps);
        final Region.Log outer = sink;
        this.log = new Region.Log() { public void say(String l) { keep(l); if (outer != null) outer.say(l); } };
        Random rnd = new Random();
        int seed = 0; for (char c : name.toCharArray()) seed += c;
        me = new Kart(name, color(name), false, 1.0, rnd);
        mine.put(name, me);
        for (int i = 0; i < Math.max(0, botCount); i++) {
            Kart b = new Kart(name + "-bot" + (i + 1), BOT_COLORS[(seed + i) % BOT_COLORS.length], true, 0.72 + rnd.nextDouble() * 0.23, rnd);
            bots.add(b); mine.put(b.name, b);
        }
        log.say(BUILD + ": " + name + " on " + machine + ": kart memory " + kartHost + ":" + kartPort + "; " + bots.size() + " robot(s); this device does not serve games");
        main = new Region("kart memory", kartHost, kartPort, "/ram.php", "kart", log, null, null);
        comm = new Region("comm", kartHost, kartPort, "/ram.php", "comm", log, null, null);
    }

    /** the call, as kartdemo's tile says it: status "calling X" / "in a call" / "" (then racing or in the lobby); calling X
     *  with a call id is what makes a desktop kart game open its Communicator for X's call */
    public void setCall(String status, String calling, String id) { callStatus = status; callCalling = calling; callId = id; }
    /** everyone's call tiles, this game's too */
    public Map<String, Map<String, Object>> tiles() { return comm.family("tile"); }

    private void publishTile() {
        int[] c = me.color;
        List<Object> col = new ArrayList<Object>(); for (int v : c) col.add((long) v);
        String st = !callStatus.isEmpty() ? callStatus : (state.equals("racing") ? "racing" : "in the lobby");
        Map<String, Object> bag = Json.o("name", name, "color", col, "status", st, "calling", callCalling, "call_id", callId, "machine", machine);
        if (bag.equals(tileWritten) && tileGen == comm.gen()) return;
        int g = comm.gen();
        if (comm.write("tile", name, bag, true)) { tileWritten = bag; tileGen = g; }
    }

    /** a line for the game's Log screen only (the caller has logged it already) */
    public void note(String l) { keep(l); }
    private void keep(String l) { synchronized (lines) { lines.addLast(l); while (lines.size() > 200) lines.removeFirst(); } }
    public List<String> log() { synchronized (lines) { return new ArrayList<String>(lines); } }

    // ---- the player's hands
    public void setReady(boolean r) { ready = r; if (!r) start = false; }
    public void pressStart() { ready = true; start = true; }                     // [KART_START_IS_ONE_CLICK_V1]
    public void lapsUp() { laps = Math.min(20, laps + 1); }
    public void lapsDown() { laps = Math.max(1, laps - 1); }
    public boolean ready() { return ready; }
    public boolean started() { return start; }
    public int laps() { return laps; }
    public void chat(String text) {
        text = text.trim(); if (text.isEmpty()) return;
        chatN++;
        main.write("chat", name + "#" + (chatN % 40), Json.o("who", name, "text", text, "at", System.currentTimeMillis() / 1000.0), false);
    }

    // ---- what the drawing reads
    public Map<String, Map<String, Object>> lobby() { return main.family("lobby"); }
    public Map<String, Object> agreed() { return main.family("agreed").get("lobby"); }
    public Map<String, Map<String, Object>> servers() { return main.family("server"); }
    public Map<String, Map<String, Object>> records() { return main.family("record"); }
    public Map<String, Object> lastResult() { return lastResult; }
    public String memoryState() { return main.up() ? null : (main.failing() == null ? "connecting to the kart memory " + kartHost + ":" + kartPort : main.failing()); }
    public String raceState() { Region r = race; return r == null ? null : (r.up() ? null : (r.failing() == null ? "connecting to the race memory" : r.failing())); }
    public String myRace() { return myRace; }
    public Map<String, Object> myServer() { return myServer; }
    public boolean placed() { return placed; }
    public double startAt() { return startAt; }
    public int lapsTotal() { return lapsTotal; }
    public List<Map<String, Object>> chats() {
        List<Map<String, Object>> c = new ArrayList<Map<String, Object>>(main.family("chat").values());
        Collections.sort(c, new Comparator<Map<String, Object>>() {
            public int compare(Map<String, Object> a, Map<String, Object> b) { return Double.compare(Json.dbl(a.get("at")), Json.dbl(b.get("at"))); }
        });
        return c;
    }
    public List<Kart> mine() { return new ArrayList<Kart>(mine.values()); }
    /** the other games' karts, where to draw them now: name -> {x, y, h, r, g, b} */
    public Map<String, double[]> others(double now) {
        Map<String, double[]> out = new HashMap<String, double[]>();
        Region r = race; if (r == null) return out;
        Map<String, Map<String, Object>> ks = r.family("kart");
        Map<String, Double> ts = r.familyT("kart");
        for (Map.Entry<String, Map<String, Object>> e : ks.entrySet()) {
            if (mine.containsKey(e.getKey())) continue;
            Double t = ts.get(e.getKey());
            double[] p = Kart.carried(e.getValue(), now - (t == null ? now : t));
            List<Object> c = Json.arr(e.getValue().get("color"));
            out.put(e.getKey(), new double[]{p[0], p[1], p[2], c.size() == 3 ? Json.num(c.get(0)) : 200, c.size() == 3 ? Json.num(c.get(1)) : 200, c.size() == 3 ? Json.num(c.get(2)) : 200});
        }
        return out;
    }
    public Map<String, Object> myRecord() { Region r = race; return r == null ? null : r.family("record").get(name); }
    public long writes() { Region r = race; return main.writes() + (r == null ? 0 : r.writes()); }

    private void publishLobby() {
        Map<String, Object> bag = Json.o("name", name, "machine", machine, "bots", (long) bots.size(), "ready", ready, "start", start, "laps", (long) laps);
        if (bag.equals(lobbyWritten) && lobbyGen == main.gen()) return;
        int g = main.gen();
        if (main.write("lobby", name, bag, true)) { lobbyWritten = bag; lobbyGen = g; }
    }

    /** One frame: steer/gas are the player's hands (ignored on the autopilot). now in seconds. */
    public void tick(double now, double dt, double steer, double gas) {
        publishTile();
        Map<String, Map<String, Object>> servers = main.family("server");
        if (state.equals("lobby")) {
            String found = null;
            for (Map.Entry<String, Map<String, Object>> e : servers.entrySet())
                if (Json.arr(e.getValue().get("players")).contains(name) && (found == null || e.getKey().compareTo(found) < 0)) found = e.getKey();
            if (found != null) {                                      // the service launched my race and its server is up
                Map<String, Object> b = servers.get(found);
                myRace = found; myServer = b;
                String api = b.containsKey("api") ? Json.str(b.get("api")) : "/ram.php";
                race = new Region("race memory", Json.str(b.get("addr")), (int) Json.num(b.get("port")), api, "race", log,
                        new Region.Ends() { public boolean ended(Map<String, Map<String, Object>> v) { return !v.containsKey("meta\u0000info"); } }, null);
                state = "racing"; placed = false; startAt = b.containsKey("start_at") ? Json.dbl(b.get("start_at")) : now;
                ready = false; start = false;                         // ready means ready for the NEXT race: spent on joining this one
                log.say("race " + found + " is up on " + Json.str(b.get("host")) + " at " + Json.str(b.get("addr")) + ":" + Json.num(b.get("port")) + ": racing");
            }
            publishLobby();
            if (state.equals("lobby")) {
                Map<String, Map<String, Object>> res = main.family("result");
                if (!res.isEmpty()) {
                    String newest = Collections.max(res.keySet());
                    if (lastResult == null || !newest.equals(Json.str(lastResult.get("race")))) lastResult = res.get(newest);
                }
                return;
            }
        }
        // racing
        Map<String, Object> b = servers.get(myRace);
        Region r = race;
        if (b == null) {                                              // the race server wrote its tuple {} or is gone
            if (r != null) r.stop();
            race = null; state = "lobby";
            Map<String, Map<String, Object>> res = main.family("result");
            Map<String, Object> mineRes = res.get(myRace);
            if (mineRes != null) lastResult = mineRes;
            log.say("race " + myRace + " is over (" + (mineRes != null ? Json.str(mineRes.get("why")) : "its server is gone, no result") + "): back in the lobby");
            myRace = null; myServer = null; publishLobby();
            return;
        }
        Map<String, Map<String, Object>> grid = r.family("grid");
        Map<String, Object> meta = r.family("meta").get("info");
        if (!placed && meta != null && grid.keySet().containsAll(mine.keySet())) {
            StringBuilder s = new StringBuilder();
            for (Kart k : mine.values()) { k.place(grid.get(k.name)); s.append(s.length() == 0 ? "" : ", ").append(k.name).append(" slot ").append(Json.num(grid.get(k.name).get("slot"))); }
            placed = true; lapsTotal = (int) Json.num(meta.get("laps"));
            log.say("placed on the grid from the race memory: " + s + "; " + lapsTotal + " lap(s)");
        }
        if (placed && now >= startAt) {
            Map<String, double[]> drawn = others(now);
            for (Kart k : mine.values()) {
                if (k.lap >= lapsTotal) { k.v *= 0.9; k.w = 0; continue; }           // finished: roll to a stop
                List<double[]> near = new ArrayList<double[]>(drawn.values());
                for (Kart q : mine.values()) if (q != k) near.add(new double[]{q.x, q.y});
                double[] sg = (k == me && !autodrive) ? new double[]{steer, gas} : k.autopilot(dt, near);
                k.drive(sg[0], sg[1], dt);
            }
        }
        if (placed) for (Kart k : mine.values()) k.publish(r, machine, name, now);
    }

    /** Ends both sessions: the memory writes this game's lobby tuple and karts {} for everyone. */
    public void stop() {
        Region r = race; if (r != null) r.stop();
        comm.stop();
        main.stop();
    }
}
