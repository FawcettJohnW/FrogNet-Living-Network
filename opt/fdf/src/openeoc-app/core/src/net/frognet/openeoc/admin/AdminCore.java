// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.admin;

import net.frognet.ram.Json;

import javax.crypto.Mac;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * [ADMIN_APP_V1] the admin app's core (opt/fdf/docs/APP-SETUP-AND-OPERATION.md, "An app is an admin app and a client
 * app"): what `fdf-app config` does on a member's command line, as a screen on a desktop or a phone, through any member's
 * door (/dc/ram.php). Same plumbing as the client: JSON lines in (handle), JSON lines out (Ui.deliver).
 *
 * It reads every member's configuration tuple (op=admin): the app's RAMs, partition behaviour, login, roles, users
 * with their verifiers, and the admin's salt and check value. The admin's password is checked here: PBKDF2-SHA256 over
 * the salt gives the admin key, and HMAC(key, "fdf-admin-check") must be the check value. The key never leaves this
 * device. A change is the whole configuration as text, a seq (now, in ms: newer than what the members have), and
 * HMAC(key, text + "\n" + seq); the door relays it as (fdf-app, admin, <app>) and every member that has the app checks
 * the mac with its own copy of the key and adopts it, or says why not, in (fdf-app, applied, <app>.<guid>). A new
 * user's password becomes its verifier here; the password goes nowhere.
 *
 * Ops: connect {door, app, user, password}; apply {config: {rams, partition, login, roles, users}, passwords: {user: pw}}.
 * Events: status, error, connected, state {configs, applied, served, current}, applied {seq, members}.
 *
 * [ENROLL_V1] a user may lead its role's team ("lead": true): that user's device may ask the region for invitations for
 * its own members (the door's op=identity). The admin page draws each user's enrollment QR (frogqr.js).
 * [ASSIGNMENTS_SAVE_V1] the assignments (teams, users, who leads) live in the region's RAM, written by its members; when
 * the node holding them goes, they are gone until the admin puts them back. That is intentional and by hand: op save
 * writes the current configuration (verifiers, never passwords) to a file in the saves directory; op saves lists them;
 * op restore {file} loads one into the page as an edit, which the admin reviews and applies like any change (users it
 * brings back keep the verifiers it carries). Nothing here saves or restores on its own.
 */
public final class AdminCore {
    public interface Ui { void deliver(String json); }
    public static final String BUILD = "openeoc-admin-2026-11-09-r20";

    private final Ui ui;
    private volatile String door = "", app = "", user = "";
    private volatile byte[] key;
    private volatile long last = -1, sent = 0;
    private volatile Thread watcher;
    private volatile Map<String, Object> current = Json.o();
    private volatile Map<String, Object> restored = Json.o();   // [ASSIGNMENTS_SAVE_V1] the users of the last restore
    private final File saves;

    public AdminCore(Ui ui) { this(ui, new File(System.getProperty("user.home", "."), "FrogNet-assignments")); }
    public AdminCore(Ui ui, File saves) { this.ui = ui; this.saves = saves; }
    private void out(Object... kv) { ui.deliver(Json.write(Json.o(kv))); }
    private void say(String t) { out("ev", "status", "text", t); }

    public synchronized void handle(String line) {
        Map<String, Object> m;
        try { m = Json.obj(Json.parse(line)); } catch (IllegalArgumentException e) { out("ev", "error", "text", "bad request"); return; }
        String op = Json.str(m.get("op"));
        try {
            if (op.equals("connect")) connect(m);
            else if (op.equals("apply")) apply(m);
            else if (op.equals("refresh")) read(false);
            else if (op.equals("save")) save();
            else if (op.equals("saves")) saves();
            else if (op.equals("restore")) restore(m);
            else throw new Exception("unknown op " + op);
        } catch (Exception e) {
            out("ev", "error", "text", op + ": " + (e.getMessage() == null ? e.toString() : e.getMessage()));
        }
    }

    // ------------------------------------------------------------------ the door
    /** the last answer's HTTP status, for a refusal that gives no reason ([DOOR_REFUSAL_SAID_V1]) */
    private static final ThreadLocal<Integer> lastStatus = new ThreadLocal<Integer>();
    static String http(String method, String url, String body, int readMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000); c.setReadTimeout(readMs); c.setRequestMethod(method);
        if (body != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
            OutputStream o = c.getOutputStream(); o.write(body.getBytes(StandardCharsets.UTF_8)); o.close();
        }
        lastStatus.set(c.getResponseCode());
        InputStream in = c.getResponseCode() >= 400 ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        while (in != null && (n = in.read(k)) > 0) b.write(k, 0, n);
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }
    private Map<String, Object> door(String q, String body, int readMs) throws Exception {
        String r = http(body == null ? "GET" : "POST", "http://" + door + "/dc/ram.php?" + q, body, readMs);
        Map<String, Object> o;
        try { o = Json.obj(Json.parse(r)); }
        catch (IllegalArgumentException e) {
            if (r.contains("404")) throw new Exception("the node at " + door + " has no app door (/dc/ram.php): use a node with the app's add-in");
            throw new Exception("the node at " + door + " did not answer as an app door");
        }
        if (!Json.bool(o.get("ok"))) {
            String why = Json.str(o.get("why")); if (why.isEmpty()) why = Json.str(o.get("error"));
            // [DOOR_REFUSAL_SAID_V1] no reason given: say what did answer, so the screen names the step that failed
            throw new Exception(why.isEmpty() ? "the door at " + door + " refused " + q.replaceAll("&.*", "") + " with no reason (HTTP " + lastStatus.get() + "): "
                    + (r.length() > 300 ? r.substring(0, 300) + "…" : r) : why);
        }
        return o;
    }

    // ------------------------------------------------------------------ the admin key
    static byte[] pbkdf2(String pw, byte[] salt, int n) throws Exception {
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(new PBEKeySpec(pw.toCharArray(), salt, n, 256)).getEncoded();
    }
    static String hmac(byte[] key, String text) throws Exception {
        Mac h = Mac.getInstance("HmacSHA256"); h.init(new SecretKeySpec(key, "HmacSHA256"));
        return hex(h.doFinal(text.getBytes(StandardCharsets.UTF_8)));
    }
    static String hex(byte[] b) { StringBuilder s = new StringBuilder(); for (byte x : b) s.append(String.format("%02x", x & 255)); return s.toString(); }
    static byte[] unhex(String h) { byte[] b = new byte[h.length() / 2]; for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(h.substring(2 * i, 2 * i + 2), 16); return b; }
    /** a user's password as the configuration keeps it (the same as fdf-app's verifier(): PBKDF2-SHA256, 200000, its own salt) */
    static Map<String, Object> verifier(String pw) throws Exception {
        byte[] salt = new byte[16]; new SecureRandom().nextBytes(salt);
        return Json.o("kdf", "pbkdf2-sha256", "n", 200000, "salt", hex(salt), "hash", hex(pbkdf2(pw, salt, 200000)));
    }

    // ------------------------------------------------------------------ connect, read, apply
    private void connect(Map<String, Object> m) throws Exception {
        stopWatch(); stopOnNow();
        door = Json.str(m.get("door")).trim(); app = Json.str(m.get("app")).trim(); user = Json.str(m.get("user")).trim();
        key = null;
        if (door.isEmpty() || app.isEmpty()) throw new Exception("the node and the app are needed");
        say("reading " + app + "'s configuration through the node at " + door + " (" + BUILD + ")");
        Map<String, Object> st = read(false);
        Map<String, Object> adm = Json.o(); double at = -1;     // the admin as the newest member with one says it
        for (Object c0 : Json.arr(st.get("configs"))) {
            Map<String, Object> c = Json.obj(c0), a = Json.obj(c.get("admin"));
            if (!Json.str(a.get("check")).isEmpty() && Json.num(c.get("seq")) > at) { adm = a; at = Json.num(c.get("seq")); }
        }
        if (Json.str(adm.get("check")).isEmpty())
            throw new Exception(app + " has no admin on this network: on its members, fdf-app config " + app + " ... --admin USER:PASSWORD");
        if (!Json.str(adm.get("user")).equals(user)) throw new Exception("that user and password are not " + app + "'s admin");
        byte[] k = pbkdf2(Json.str(m.get("password")), unhex(Json.str(adm.get("salt"))), (int) Json.num(adm.get("n")));
        if (!MessageDigest.isEqual(hmac(k, "fdf-admin-check").getBytes(StandardCharsets.UTF_8), Json.str(adm.get("check")).getBytes(StandardCharsets.UTF_8)))
            throw new Exception("that user and password are not " + app + "'s admin");
        key = k;
        out("ev", "connected", "app", app, "user", user, "door", door, "build", BUILD);
        deliverState(st);
        startWatch();
        startOnNow();
    }

    // ------------------------------------------------------------ [ADMIN_ON_NOW_V1] who is on now
    // The admin's groups said where each one lives and is served, not who is in it: a team brought up on Windows did not
    // show (John 2026-11-05). The admin follows the app's general RAM as the apps do -- the mission (openeoc-mission, settings:
    // the newest by its at), then its presence (openeoc-who, <mission>: one tuple per device on now, its session's: gone when
    // the device is) -- and says it: ev onNow {mission, who: {dev: {name, kind, team, unit, via, status, app}}}.
    private volatile net.frognet.ram.Follower nowMis, nowWho;
    private volatile String nowMission = "";
    private void startOnNow() {
        stopOnNow();
        new Thread(new Runnable() { public void run() {
            try {
                Map<String, Object> o = door("op=rams&app=" + URLEncoder.encode(app, "UTF-8"), null, 10000);
                String g = Json.str(Json.obj(o.get("rams")).get("general"));
                if (g.isEmpty()) { out("ev", "error", "text", "who is on now: the door says no general RAM for " + app); return; }
                final String h = g.substring(0, g.lastIndexOf(':')); final int pt = Integer.parseInt(g.substring(g.lastIndexOf(':') + 1));
                final net.frognet.ram.Follower.Log lg = new net.frognet.ram.Follower.Log() { public void say(String l) { if (l.contains("FAIL")) out("ev", "error", "text", "who is on now: " + l); } };
                nowMis = new net.frognet.ram.Follower("admin-mission", h, pt, API, "openeoc-mission", "settings", lg, null, new Runnable() { public void run() {
                    net.frognet.ram.Follower f = nowMis; if (f == null) return;
                    String best = ""; Map<String, Map<String, Object>> ms = f.family("settings");
                    for (String k : ms.keySet())
                        if (best.isEmpty() || Json.num(ms.get(k).get("at")) > Json.num(ms.get(best).get("at")) || (Json.num(ms.get(k).get("at")) == Json.num(ms.get(best).get("at")) && k.compareTo(best) > 0)) best = k;
                    if (best.isEmpty()) { out("ev", "onNow", "mission", "", "who", Json.o()); return; }
                    if (best.equals(nowMission) && nowWho != null) return;
                    nowMission = best; final String mis = best;
                    if (nowWho != null) nowWho.stop();
                    nowWho = new net.frognet.ram.Follower("admin-who", h, pt, API, "openeoc-who", mis, lg, null, new Runnable() { public void run() {
                        net.frognet.ram.Follower w = nowWho; if (w == null || !mis.equals(nowMission)) return;
                        out("ev", "onNow", "mission", mis, "at", g, "who", new java.util.HashMap<String, Object>(w.family(mis)));
                    } });
                } });
            } catch (Exception e) { out("ev", "error", "text", "who is on now: " + e.getMessage()); }
        } }, "admin-on-now").start();
    }
    private void stopOnNow() {
        net.frognet.ram.Follower a = nowMis, b = nowWho; nowMis = null; nowWho = null; nowMission = "";
        if (a != null) a.stop(); if (b != null) b.stop();
    }
    static final String API = "/FawcettInnovations.RINA.ram_interface.php";

    /** every member's configuration and answer; the current configuration is the newest seq among them */
    private Map<String, Object> read(boolean held) throws Exception {
        String q = "op=admin&app=" + URLEncoder.encode(app, "UTF-8") + (held ? "&after=" + last + "&wait=20" : "");
        Map<String, Object> st = door(q, null, held ? 35000 : 10000);
        // [DOOR_SAYS_ITS_BUILD_V1] an answer without the configurations is not the admin door: say which door answered
        if (!st.containsKey("configs")) {
            String d = Json.str(st.get("door"));
            throw new Exception("the door at " + door + " (" + (d.isEmpty() ? "a door with no build, from before dc-door-2026-11-01-r2" : d)
                    + ") has no admin op: it answered " + Json.write(st).substring(0, Math.min(200, Json.write(st).length())));
        }
        if (!held) say("the door at " + door + " is " + Json.str(st.get("door")));
        last = (long) Json.num(st.get("last"));
        Map<String, Object> best = null;
        for (Object c0 : Json.arr(st.get("configs"))) {
            Map<String, Object> c = Json.obj(c0);
            if (best == null || Json.num(c.get("seq")) > Json.num(best.get("seq"))) best = c;
        }
        if (best == null) throw new Exception("the door at " + door + " (" + Json.str(st.get("door")) + ") found no member's configuration of " + app + " in the network RAM");
        current = best;
        if (!held && key != null) deliverState(st);
        return st;
    }
    private void deliverState(Map<String, Object> st) {
        out("ev", "state", "configs", st.get("configs"), "applied", st.get("applied"), "served", st.get("served"), "current", current, "sent", sent,
            "children", st.get("children") == null ? Json.o() : st.get("children"));   // [CHILDREN_JOIN_V1] the teams that joined
    }
    private void startWatch() {
        final Thread t = new Thread(new Runnable() { public void run() {
            while (watcher == Thread.currentThread()) {
                try { Map<String, Object> st = read(true); if (watcher == Thread.currentThread()) deliverState(st); }
                catch (Exception e) {
                    if (watcher != Thread.currentThread()) return;
                    out("ev", "error", "text", "reading the members' answers: " + e.getMessage() + " (again in 2 s)");
                    try { Thread.sleep(2000); } catch (InterruptedException ie) { return; }
                }
            }
        } }, "admin-watch");
        watcher = t; t.setDaemon(true); t.start();
    }
    private void stopWatch() { Thread t = watcher; watcher = null; if (t != null) t.interrupt(); }
    public void close() { stopWatch(); stopOnNow(); }

    // ------------------------------------------------------------------ [ASSIGNMENTS_SAVE_V1] save and restore, by hand
    private static final String[] KEPT = { "rams", "ports", "partition", "login", "roles", "categories", "users", "select" };
    private void save() throws Exception {
        if (key == null) throw new Exception("not connected as the admin");
        if (!saves.isDirectory() && !saves.mkdirs()) throw new Exception("cannot make " + saves);
        Map<String, Object> c = Json.o();
        for (String k : KEPT) if (current.containsKey(k)) c.put(k, current.get(k));
        long seq = (long) Json.num(current.get("seq"));
        String name = app.replaceAll("[^A-Za-z0-9_-]", "_") + "-" + new java.text.SimpleDateFormat("yyyy-MM-dd-HHmmss").format(new java.util.Date()) + ".json";
        File f = new File(saves, name);
        OutputStream o = new FileOutputStream(f);
        try { o.write(Json.write(Json.o("app", app, "seq", seq, "saved", System.currentTimeMillis(), "through", door, "by", AdminCore.BUILD, "config", c)).getBytes(StandardCharsets.UTF_8)); }
        finally { o.close(); }
        say("saved " + app + "'s assignments (configuration " + seq + ", " + Json.obj(c.get("users")).size() + " users) to " + f);
        saves();
    }
    private void saves() {
        List<Object> list = new ArrayList<Object>();
        File[] fs = saves.listFiles();
        if (fs != null) {
            java.util.Arrays.sort(fs, java.util.Collections.reverseOrder());
            for (File f : fs) if (f.getName().startsWith(app.replaceAll("[^A-Za-z0-9_-]", "_") + "-") && f.getName().endsWith(".json")) list.add(f.getName());
        }
        out("ev", "saves", "dir", saves.toString(), "files", list);
    }
    private void restore(Map<String, Object> m) throws Exception {
        if (key == null) throw new Exception("not connected as the admin");
        String name = Json.str(m.get("file"));
        if (!name.matches("[A-Za-z0-9_.-]+\\.json")) throw new Exception("not a saved file: " + name);
        File f = new File(saves, name);
        InputStream in = new FileInputStream(f); ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        try { while ((n = in.read(k)) > 0) b.write(k, 0, n); } finally { in.close(); }
        Map<String, Object> s = Json.obj(Json.parse(new String(b.toByteArray(), StandardCharsets.UTF_8)));
        if (!Json.str(s.get("app")).equals(app)) throw new Exception(name + " holds " + Json.str(s.get("app")) + "'s assignments, not " + app + "'s");
        Map<String, Object> c = Json.obj(s.get("config"));
        restored = Json.obj(c.get("users"));
        out("ev", "restored", "file", name, "seq", s.get("seq"), "config", c);
        say("loaded " + name + " (configuration " + (long) Json.num(s.get("seq")) + "): review it, then Apply sends it to the members");
    }

    static boolean catOk(String c) { return c.length() > 0 && c.length() <= 40 && c.trim().equals(c) && c.matches("[A-Za-z0-9 _-]+"); }

    private void apply(Map<String, Object> m) throws Exception {
        if (key == null) throw new Exception("not connected as the admin");
        Map<String, Object> want = Json.obj(m.get("config")), pws = Json.obj(m.get("passwords"));
        List<Object> rams = new ArrayList<Object>();
        for (Object r : Json.arr(want.get("rams"))) { String s = Json.str(r).trim().toLowerCase(); if (!s.isEmpty() && !rams.contains(s)) rams.add(s); }
        if (!rams.contains("general")) rams.add(0, "general");
        // [CATEGORIES_V1] a role is a group in a category (a tab); the categories' order is the tabs'
        List<Object> cats = new ArrayList<Object>();
        for (Object c : Json.arr(want.get("categories"))) { String n = Json.str(c); if (!catOk(n)) throw new Exception("category '" + n + "': letters, digits, space, _ and -, at most 40"); if (!cats.contains(n)) cats.add(n); }
        Map<String, Object> roles = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> e : Json.obj(want.get("roles")).entrySet()) {
            String r = e.getKey().trim();
            if (!r.matches("[A-Za-z0-9_-]{1,40}") || r.equals("general")) throw new Exception("role '" + r + "': letters, digits, _ and - (not 'general')");
            // [TEAMS_AUTHORIZED_V1] a Team the admin authorizes has no category: its app's is the one it joins in, at its claim
            String cat = Json.str(Json.obj(e.getValue()).get("category"));
            if (!cat.isEmpty() && !catOk(cat)) throw new Exception("role '" + r + "': category '" + cat + "': letters, digits, space, _ and -, at most 40");
            if (!cat.isEmpty() && !cats.contains(cat)) cats.add(cat);
            roles.put(r, Json.o("default", Json.bool(Json.obj(e.getValue()).get("default")), "category", cat));
        }
        Map<String, Object> was = Json.obj(current.get("users")), users = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> e : Json.obj(want.get("users")).entrySet()) {
            String u = e.getKey().trim(), role = Json.str(Json.obj(e.getValue()).get("role")), pw = Json.str(pws.get(u));
            if (!roles.containsKey(role)) throw new Exception("user " + u + ": role '" + role + "' is not one of the roles");
            Object v = !pw.isEmpty() ? verifier(pw) : Json.obj(was.get(u)).get("verifier");
            if (v == null) v = Json.obj(restored.get(u)).get("verifier");     // [ASSIGNMENTS_SAVE_V1] brought back by a restore
            if (v == null) throw new Exception("user " + u + " needs a password");
            users.put(u, Json.o("role", role, "verifier", v, "lead", Json.bool(Json.obj(e.getValue()).get("lead"))));   // [ENROLL_V1]
        }
        String part = Json.str(want.get("partition")); if (!part.equals("fail")) part = "relocate";
        // [APP_SELECTION_V1] the app's own selection of each RAM's member: need and rank keys of the capability tuples
        Map<String, Object> sel = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> e : Json.obj(want.get("select")).entrySet()) {
            String r = e.getKey().trim();
            if (!r.equals("*") && !rams.contains(r)) throw new Exception("selection of '" + r + "': not one of the app's RAMs (or *)");
            List<Object> need = new ArrayList<Object>(), rank = new ArrayList<Object>();
            for (Object k : Json.arr(Json.obj(e.getValue()).get("need"))) { String n = Json.str(k); if (!n.matches("[A-Za-z0-9_]{1,40}")) throw new Exception("selection of " + r + ": need '" + n + "': a capability key"); need.add(n); }
            for (Object k : Json.arr(Json.obj(e.getValue()).get("rank"))) { String n = Json.str(k); if (!n.matches("-?[A-Za-z0-9_]{1,40}")) throw new Exception("selection of " + r + ": rank '" + n + "': a capability key, or -key"); rank.add(n); }
            if (!need.isEmpty() || !rank.isEmpty()) sel.put(r, Json.o("need", need, "rank", rank));
        }
        // [APP_SERVICES_V1] every AppRam is a service with its own port: the one it has, kept; a new RAM gets one picked at
        // random between 20000 and 65535 that no other RAM of the app has (each machine's AppHost refuses one in use there)
        Map<String, Object> had = Json.obj(current.get("ports")), ports = new LinkedHashMap<String, Object>();
        java.util.Set<Long> used = new java.util.HashSet<Long>();
        for (Object v : had.values()) used.add((long) Json.num(v));
        java.security.SecureRandom rnd = new java.security.SecureRandom();
        for (Object r : rams) {
            long p = had.containsKey(r) ? (long) Json.num(had.get(r)) : 0;
            while (p == 0 || (!had.containsKey(r) && used.contains(p))) p = 20000 + rnd.nextInt(65535 - 20000 + 1);
            used.add(p); ports.put(Json.str(r), p);
        }
        // [TEAMS_AUTHORIZED_V1] (John 2026-11-09: "we are authorizing Teams, and that's the trust boundary") each Team has its
        // own login and password: the EOC's one teams password ([CHILDREN_JOIN_V1] "join") is not written again
        Map<String, Object> cfgOut = Json.o("rams", rams, "ports", ports, "partition", part, "login", Json.bool(want.get("login")), "roles", roles, "categories", cats, "users", users, "select", sel);
        String text = Json.write(cfgOut);
        long seq = Math.max(System.currentTimeMillis(), (long) Json.num(current.get("seq")) + 1);
        door("op=admin&app=" + URLEncoder.encode(app, "UTF-8"),
             Json.write(Json.o("config", text, "seq", seq, "user", user, "mac", hmac(key, text + "\n" + seq))), 10000);
        sent = seq; restored = Json.o();
        say("sent to the members (configuration " + seq + "): each checks it with its own copy of the admin key and says so");
    }
}
