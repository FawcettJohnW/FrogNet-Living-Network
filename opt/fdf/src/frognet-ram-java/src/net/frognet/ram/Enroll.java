// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.ram;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Map;

/**
 * [ENROLL_V1] the common way a device joins a region with an app, for any app.
 *
 * An enrollment QR (frogqr.js draws it; the app's admin, or a team's lead, shows it) carries one line:
 * <pre>  fdfenroll1 URL APP USER</pre>
 * (a '%' or space inside a part is %25 / %20). The device scans it, its user types the password, and
 * {@link #enroll} POSTs {user, password} to URL?op=enroll&amp;app=APP. The answer is
 * {ok, user, role, lead, invite, at?, ...} (at: the app door's address, when the sponsor is not the door itself;
 * a door's own "door" field is its build, never an address): invite is the device's own fdfinv1 invitation to the region (the device keeps
 * it; its member joins with it, once, and every other app's hub then admits it by its membership), role is what the user
 * is in APP, lead says the user may sponsor its own team's members.
 *
 * URL is any sponsor: the app's door on a node (/dc/ram.php, which asks the region's hub for the invitation), or a
 * team lead's device, whose {@link Sponsor} checks its member's password against the verifier it keeps and asks the door
 * for the member's invitation as the lead (op=identity). Both answer the same way, so the device needs no other code.
 *
 * Passwords are never kept by a sponsor: {@link #verifier} makes what is kept (PBKDF2-SHA256, its own salt), the same
 * record the door and fdf-app keep.
 */
public final class Enroll {
    public static final String BUILD = "frognet-enroll-2026-11-02-r2";
    public static final String PREFIX = "fdfenroll1";
    private Enroll() { }

    /** a scanned enrollment line: url, app, user */
    public static final class Line {
        public final String url, app, user;
        Line(String url, String app, String user) { this.url = url; this.app = app; this.user = user; }
        public String toString() { return PREFIX + " " + esc(url) + " " + esc(app) + " " + esc(user); }
    }
    private static String esc(String p) { return p.replace("%", "%25").replace(" ", "%20"); }
    public static Line line(String url, String app, String user) { return new Line(url, app, user); }
    /** the line a QR carried, or null when TEXT is not one */
    public static Line parse(String text) {
        if (text == null) return null;
        String[] p = text.trim().split(" ");
        if (p.length != 4 || !p[0].equals(PREFIX)) return null;
        try {
            String u = URLDecoder.decode(p[1].replace("+", "%2B"), "UTF-8"), a = URLDecoder.decode(p[2].replace("+", "%2B"), "UTF-8"),
                   n = URLDecoder.decode(p[3].replace("+", "%2B"), "UTF-8");
            return u.isEmpty() || n.isEmpty() ? null : new Line(u, a, n);
        } catch (Exception e) { return null; }
    }

    // ------------------------------------------------------------------ the device's side
    /** POST {user, password} to the sponsor; the answer, or an Exception saying the sponsor's why */
    public static Map<String, Object> enroll(Line l, String password) throws Exception {
        String url = l.url + (l.url.contains("?") ? "&" : "?") + "op=enroll&app=" + URLEncoder.encode(l.app, "UTF-8");
        Map<String, Object> a = post(url, Json.write(Json.o("user", l.user, "password", password)), 60000);
        if (!Json.str(a.get("invite")).startsWith("fdfinv1.")) throw new Exception("the sponsor at " + l.url + " gave no invitation");
        return a;
    }
    /** POST JSON; the JSON answer when ok:true, else an Exception with its why */
    public static Map<String, Object> post(String url, String body, int readMs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000); c.setReadTimeout(readMs); c.setRequestMethod("POST"); c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        OutputStream o = c.getOutputStream(); o.write(body.getBytes(StandardCharsets.UTF_8)); o.close();
        int code;   // [NO_ANSWER_IS_SAID_V1] (2026-11-02: the phone said only "timeout") who was waited on, and how long
        try { code = c.getResponseCode(); }
        catch (java.net.SocketTimeoutException e) { throw new Exception("no answer from " + url.replaceAll("\\?.*", "") + " in " + readMs / 1000 + " s"); }
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String r = new String(all(in), StandardCharsets.UTF_8);
        Map<String, Object> m;
        try { m = Json.obj(Json.parse(r)); }
        catch (IllegalArgumentException e) { throw new Exception(url.replaceAll("\\?.*", "") + " did not answer as a sponsor (HTTP " + c.getResponseCode() + ")"); }
        if (!Json.bool(m.get("ok"))) {
            String why = Json.str(m.get("why")); if (why.isEmpty()) why = Json.str(m.get("error"));
            throw new Exception(why.isEmpty() ? "refused (HTTP " + c.getResponseCode() + ")" : why);
        }
        return m;
    }
    private static byte[] all(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        while (in != null && (n = in.read(k)) > 0) b.write(k, 0, n);
        return b.toByteArray();
    }

    // ------------------------------------------------------------------ verifiers (never the password)
    public static final int ROUNDS = 200000;
    static byte[] pbkdf2(String pw, byte[] salt, int n) throws Exception {
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(new PBEKeySpec(pw.toCharArray(), salt, n, 256)).getEncoded();
    }
    static String hex(byte[] b) { StringBuilder s = new StringBuilder(); for (byte x : b) s.append(String.format("%02x", x & 255)); return s.toString(); }
    static byte[] unhex(String h) { byte[] b = new byte[h.length() / 2]; for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(h.substring(2 * i, 2 * i + 2), 16); return b; }
    /** what is kept for a password: {kdf, n, salt, hash} */
    public static Map<String, Object> verifier(String pw) throws Exception {
        byte[] salt = new byte[16]; new SecureRandom().nextBytes(salt);
        return Json.o("kdf", "pbkdf2-sha256", "n", ROUNDS, "salt", hex(salt), "hash", hex(pbkdf2(pw, salt, ROUNDS)));
    }
    public static boolean check(Map<String, Object> v, String pw) {
        try {
            byte[] h = hex(pbkdf2(pw, unhex(Json.str(v.get("salt"))), (int) Json.num(v.get("n")))).getBytes(StandardCharsets.UTF_8);
            return MessageDigest.isEqual(h, Json.str(v.get("hash")).getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) { return false; }
    }

    // ------------------------------------------------------------------ a sponsor on a device: a team lead's
    /** what a Sponsor needs from the app that runs it */
    public interface Team {
        /** the member's record, {verifier: ...}, or null: who may enroll here */
        Map<String, Object> member(String name);
        /** the member's invitation to the region: asked of the app's door as the lead (op=identity) */
        String identity(String name) throws Exception;
        /** anything else the member's device is told (the door, the team, the lead's RAM ...) */
        Map<String, Object> card();
        void log(String line);
    }

    /**
     * The lead's enroll server: POST ...?op=enroll&amp;app=APP {user, password}, answered as the door answers
     * ({ok, user, role, lead:false, invite} plus the team's card). One thread per request; the port is the app's.
     */
    public static final class Sponsor {
        private final ServerSocket ss;
        private final Team team;
        private final String app, role;
        private volatile boolean open = true;

        public Sponsor(int port, String app, String role, Team team) throws java.io.IOException {
            this.app = app; this.role = role; this.team = team;
            ss = new ServerSocket(); ss.setReuseAddress(true); ss.bind(new java.net.InetSocketAddress((InetAddress) null, port));
            Thread t = new Thread(new Runnable() { public void run() { loop(); } }, "enroll-sponsor");
            t.setDaemon(true); t.start();
            team.log("[ENROLL_V1] " + BUILD + ": enrolling " + app + " " + role + "'s members on port " + ss.getLocalPort());
        }
        public int port() { return ss.getLocalPort(); }
        public void close() { open = false; try { ss.close(); } catch (java.io.IOException ignored) { } }

        private void loop() {
            while (open) {
                final Socket s;
                try { s = ss.accept(); } catch (java.io.IOException e) { if (open) team.log("[ENROLL_V1] accept: " + e); return; }
                Thread t = new Thread(new Runnable() { public void run() { serve(s); } }, "enroll-request");
                t.setDaemon(true); t.start();
            }
        }
        private void serve(Socket s) {
            try {
                s.setSoTimeout(20000);
                InputStream in = s.getInputStream();
                String head = headers(in), first = head.split("\r\n", 2)[0];
                int len = 0;
                for (String h : head.split("\r\n")) if (h.toLowerCase().startsWith("content-length:")) len = Integer.parseInt(h.substring(15).trim());
                byte[] body = new byte[Math.min(len, 65536)]; int got = 0, n;
                while (got < body.length && (n = in.read(body, got, body.length - got)) > 0) got += n;
                int code = 200; Map<String, Object> ans;
                try { ans = answer(first, new String(body, 0, got, StandardCharsets.UTF_8), s.getInetAddress().getHostAddress()); }
                catch (Refused r) { code = r.code; ans = Json.o("ok", false, "why", r.getMessage()); }
                byte[] out = Json.write(ans).getBytes(StandardCharsets.UTF_8);
                OutputStream o = s.getOutputStream();
                o.write(("HTTP/1.0 " + code + (code == 200 ? " OK" : " Refused") + "\r\nContent-Type: application/json\r\nContent-Length: " + out.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                o.write(out); o.flush();
            } catch (Exception e) {
                team.log("[ENROLL_V1] a request failed: " + e);
            } finally { try { s.close(); } catch (java.io.IOException ignored) { } }
        }
        private static String headers(InputStream in) throws java.io.IOException {
            ByteArrayOutputStream b = new ByteArrayOutputStream(); int c, m = 0;
            while ((c = in.read()) >= 0) { b.write(c); m = (c == "\r\n\r\n".charAt(m)) ? m + 1 : (c == '\r' ? 1 : 0); if (m == 4 || b.size() > 16384) break; }
            return new String(b.toByteArray(), StandardCharsets.ISO_8859_1);
        }
        private static final class Refused extends Exception { final int code; Refused(int code, String why) { super(why); this.code = code; } }

        private Map<String, Object> answer(String first, String body, String from) throws Refused {
            if (!first.startsWith("POST ") || !first.contains("op=enroll")) throw new Refused(400, "this is " + app + " " + role + "'s enrollment: POST ?op=enroll&app=" + app);
            if (!first.contains("app=" + app + " ") && !first.contains("app=" + app + "&")) throw new Refused(400, "this enrolls members of " + app + ", not that app");
            Map<String, Object> in;
            try { in = Json.obj(Json.parse(body)); } catch (IllegalArgumentException e) { throw new Refused(400, "a JSON body {user, password} is needed"); }
            String user = Json.str(in.get("user")).trim();
            Map<String, Object> m = team.member(user);
            if (m == null || !check(Json.obj(m.get("verifier")), Json.str(in.get("password")))) {
                team.log("[ENROLL_V1] " + from + " gave a user and password that are not a member of " + role + " (" + user + ")");
                throw new Refused(403, "that user and password are not a member of " + role + "'s team");
            }
            String inv;
            try { inv = team.identity(user); }
            catch (Exception e) { throw new Refused(502, "the region did not give " + user + " an invitation: " + e.getMessage()); }
            Map<String, Object> a = new java.util.LinkedHashMap<String, Object>(team.card());   // the card, then what an answer is
            a.putAll(Json.o("ok", true, "user", user, "role", role, "lead", false, "invite", inv, "sponsor", BUILD));
            team.log("[ENROLL_V1] enrolled " + user + " (" + from + ") in " + role);
            return a;
        }
    }
}
