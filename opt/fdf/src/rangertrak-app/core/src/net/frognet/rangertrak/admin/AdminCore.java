// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.admin;

import net.frognet.ram.Json;

import javax.crypto.Mac;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.ByteArrayOutputStream;
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
 */
public final class AdminCore {
    public interface Ui { void deliver(String json); }
    public static final String BUILD = "rt-admin-2026-11-01-r1";

    private final Ui ui;
    private volatile String door = "", app = "", user = "";
    private volatile byte[] key;
    private volatile long last = -1, sent = 0;
    private volatile Thread watcher;
    private volatile Map<String, Object> current = Json.o();

    public AdminCore(Ui ui) { this.ui = ui; }
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
            else throw new Exception("unknown op " + op);
        } catch (Exception e) {
            out("ev", "error", "text", op + ": " + (e.getMessage() == null ? e.toString() : e.getMessage()));
        }
    }

    // ------------------------------------------------------------------ the door
    static String http(String method, String url, String body, int readMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000); c.setReadTimeout(readMs); c.setRequestMethod(method);
        if (body != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
            OutputStream o = c.getOutputStream(); o.write(body.getBytes(StandardCharsets.UTF_8)); o.close();
        }
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
            throw new Exception(why.isEmpty() ? "the door refused" : why);
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
        stopWatch();
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
    }

    /** every member's configuration and answer; the current configuration is the newest seq among them */
    private Map<String, Object> read(boolean held) throws Exception {
        String q = "op=admin&app=" + URLEncoder.encode(app, "UTF-8") + (held ? "&after=" + last + "&wait=20" : "");
        Map<String, Object> st = door(q, null, held ? 35000 : 10000);
        last = (long) Json.num(st.get("last"));
        Map<String, Object> best = null;
        for (Object c0 : Json.arr(st.get("configs"))) {
            Map<String, Object> c = Json.obj(c0);
            if (best == null || Json.num(c.get("seq")) > Json.num(best.get("seq"))) best = c;
        }
        if (best == null) throw new Exception("no member has written " + app + "'s configuration yet");
        current = best;
        if (!held && key != null) deliverState(st);
        return st;
    }
    private void deliverState(Map<String, Object> st) {
        out("ev", "state", "configs", st.get("configs"), "applied", st.get("applied"), "served", st.get("served"), "current", current, "sent", sent);
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
    public void close() { stopWatch(); }

    private void apply(Map<String, Object> m) throws Exception {
        if (key == null) throw new Exception("not connected as the admin");
        Map<String, Object> want = Json.obj(m.get("config")), pws = Json.obj(m.get("passwords"));
        List<Object> rams = new ArrayList<Object>();
        for (Object r : Json.arr(want.get("rams"))) { String s = Json.str(r).trim().toLowerCase(); if (!s.isEmpty() && !rams.contains(s)) rams.add(s); }
        if (!rams.contains("general")) rams.add(0, "general");
        Map<String, Object> roles = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> e : Json.obj(want.get("roles")).entrySet()) {
            String r = e.getKey().trim();
            if (!r.matches("[A-Za-z0-9_-]{1,40}") || r.equals("general")) throw new Exception("role '" + r + "': letters, digits, _ and - (not 'general')");
            roles.put(r, Json.o("default", Json.bool(Json.obj(e.getValue()).get("default"))));
        }
        Map<String, Object> was = Json.obj(current.get("users")), users = new LinkedHashMap<String, Object>();
        for (Map.Entry<String, Object> e : Json.obj(want.get("users")).entrySet()) {
            String u = e.getKey().trim(), role = Json.str(Json.obj(e.getValue()).get("role")), pw = Json.str(pws.get(u));
            if (!roles.containsKey(role)) throw new Exception("user " + u + ": role '" + role + "' is not one of the roles");
            Object v = !pw.isEmpty() ? verifier(pw) : Json.obj(was.get(u)).get("verifier");
            if (v == null) throw new Exception("user " + u + " needs a password");
            users.put(u, Json.o("role", role, "verifier", v));
        }
        String part = Json.str(want.get("partition")); if (!part.equals("fail")) part = "relocate";
        String text = Json.write(Json.o("rams", rams, "partition", part, "login", Json.bool(want.get("login")), "roles", roles, "users", users));
        long seq = Math.max(System.currentTimeMillis(), (long) Json.num(current.get("seq")) + 1);
        door("op=admin&app=" + URLEncoder.encode(app, "UTF-8"),
             Json.write(Json.o("config", text, "seq", seq, "user", user, "mac", hmac(key, text + "\n" + seq))), 10000);
        sent = seq;
        say("sent to the members (configuration " + seq + "): each checks it with its own copy of the admin key and says so");
    }
}
