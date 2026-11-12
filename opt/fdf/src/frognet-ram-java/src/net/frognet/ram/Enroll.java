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
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.Base64;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

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
 *
 * [SEALED_ENROLL_V1] a lead's QR carries its sponsor key's fingerprint as a fifth part, <pre>  fdfenroll1 URL APP USER KEY</pre>
 * (the first 16 bytes of SHA-256 of the P-256 key's compressed point, base64url: 22 characters). The key itself
 * (44 characters, or X.509's 122) grew the code to version 9 or 10, where the frog QR missed turned, tilted, small or
 * blurred codes a plain code of the same line still read; at version 8 it misses none. The device asks the sponsor for
 * its key (op=key), takes it only when its fingerprint is the QR's, and seals to it. The lead's hotspot is shared by everyone who has the team's Wi-Fi password, so the
 * password going up and the invitation coming back are sealed to that key ({@link Seal}: ECDH with a key made for this
 * one request, HKDF-SHA256, AES-256-GCM); a Sponsor takes sealed requests only and seals every answer it gives after
 * opening one. A door's line (on the EOC's own LAN, the trusted first connection) has no KEY and is posted as before.
 */
public final class Enroll {
    public static final String BUILD = "frognet-enroll-2026-11-09-r6";   // [EOC_LEADS_V1] r6: the team's name with a lead's enrollment
    public static final String PREFIX = "fdfenroll1";
    private Enroll() { }

    /** a scanned enrollment line: url, app, user. [TEAM_JOIN_V1] USER "*" is a team's QR (one for the whole team): the one
     *  who scans it gives their own call sign and the team's password (one, the lead's) */
    public static final class Line {
        /** key: [SEALED_ENROLL_V1] the sponsor key's fingerprint (Seal.fingerprint), or "" for a door's line */
        public final String url, app, user, key;
        Line(String url, String app, String user) { this(url, app, user, ""); }
        Line(String url, String app, String user, String key) { this.url = url; this.app = app; this.user = user; this.key = key == null ? "" : key; }
        public String toString() { return PREFIX + " " + esc(url) + " " + esc(app) + " " + esc(user) + (key.isEmpty() ? "" : " " + key); }
        /** [TEAM_JOIN_V1] a team's QR: the name is the joiner's */
        public boolean team() { return user.equals(TEAM); }
        public Line as(String name) { return new Line(url, app, name, key); }
        public boolean sealed() { return !key.isEmpty(); }
    }
    public static final String TEAM = "*";
    private static String esc(String p) { return p.replace("%", "%25").replace(" ", "%20"); }
    public static Line line(String url, String app, String user) { return new Line(url, app, user); }
    public static Line line(String url, String app, String user, String key) { return new Line(url, app, user, key); }
    /** the line a QR carried, or null when TEXT is not one */
    public static Line parse(String text) {
        if (text == null) return null;
        String[] p = text.trim().split(" ");
        if ((p.length != 4 && p.length != 5) || !p[0].equals(PREFIX)) return null;
        try {
            String u = URLDecoder.decode(p[1].replace("+", "%2B"), "UTF-8"), a = URLDecoder.decode(p[2].replace("+", "%2B"), "UTF-8"),
                   n = URLDecoder.decode(p[3].replace("+", "%2B"), "UTF-8");
            if (p.length == 5 && !p[4].matches("[A-Za-z0-9_-]{22}")) return null;   // [SEALED_ENROLL_V1] a fifth part that is not a fingerprint: not a line
            return u.isEmpty() || n.isEmpty() ? null : new Line(u, a, n, p.length == 5 ? p[4] : "");
        } catch (Exception e) { return null; }
    }

    // ------------------------------------------------------------------ the device's side
    /** POST {user, password} to the sponsor; the answer, or an Exception saying the sponsor's why */
    public static Map<String, Object> enroll(Line l, String password) throws Exception { return enroll(l, password, ""); }
    /** [EOC_LEADS_V1] with TEAM: a lead the EOC added creates (or claims again) the team it names, as KIND (the app's view:
     *  team, hospital, ambulance ...: one of the roles the EOC gave the lead) */
    public static Map<String, Object> enroll(Line l, String password, String team) throws Exception { return enroll(l, password, team, ""); }
    public static Map<String, Object> enroll(Line l, String password, String team, String kind) throws Exception {
        String url = l.url + (l.url.contains("?") ? "&" : "?") + "op=enroll&app=" + URLEncoder.encode(l.app, "UTF-8");
        if (l.team()) throw new Exception("a team's QR: give your own call sign (Line.as)");
        Map<String, Object> a;
        if (l.sealed()) {                                     // [SEALED_ENROLL_V1] sealed to the key in the QR, both ways
            String key = Json.str(post(l.url + (l.url.contains("?") ? "&" : "?") + "op=key&app=" + URLEncoder.encode(l.app, "UTF-8"), "{}", 20000).get("key"));
            if (!Seal.fingerprint(key).equals(l.key)) throw new Exception("the key " + l.url + " gave is not the one in its QR: not enrolled there");
            Seal sl = Seal.to(key, l.app);
            Map<String, Object> r = postRaw(url, Json.write(sl.request(withTeam(Json.o("user", l.user, "password", password, "t", System.currentTimeMillis()), team, kind))), 60000);
            try { a = sl.answer(r); }
            catch (Exception e) {
                String why = Json.str(r.get("why"));
                throw new Exception("the answer was not sealed by the key in its QR" + (why.isEmpty() ? "" : " (it said: " + why + ")"));
            }
            if (!Json.bool(a.get("ok"))) { String why = Json.str(a.get("why")); throw new Exception(why.isEmpty() ? "refused" : why); }
        } else a = post(url, Json.write(withTeam(Json.o("user", l.user, "password", password), team, kind)), 60000);
        if (!Json.str(a.get("invite")).startsWith("fdfinv1.")) throw new Exception("the sponsor at " + l.url + " gave no invitation");
        return a;
    }
    private static Map<String, Object> withTeam(Map<String, Object> m, String team, String kind) {
        if (team != null && !team.trim().isEmpty()) m.put("team", team.trim());
        if (kind != null && !kind.trim().isEmpty()) m.put("kind", kind.trim());
        return m;
    }
    /** POST JSON; the JSON answer when ok:true, else an Exception with its why */
    public static Map<String, Object> post(String url, String body, int readMs) throws Exception {
        Map<String, Object> m = postRaw(url, body, readMs);
        if (!Json.bool(m.get("ok"))) {
            String why = Json.str(m.get("why")); if (why.isEmpty()) why = Json.str(m.get("error"));
            throw new Exception(why.isEmpty() ? "refused (HTTP " + Json.str(m.get("_http")) + ")" : why);
        }
        m.remove("_http");
        return m;
    }
    /** POST JSON; the JSON answer whatever it says (its HTTP status as _http), or an Exception when there is none */
    public static Map<String, Object> postRaw(String url, String body, int readMs) throws Exception {
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
        m.put("_http", String.valueOf(code));
        return m;
    }
    private static byte[] all(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] k = new byte[8192]; int n;
        while (in != null && (n = in.read(k)) > 0) b.write(k, 0, n);
        return b.toByteArray();
    }

    // ------------------------------------------------------------------ [SEALED_ENROLL_V1] the seal
    /**
     * One request's seal: an ephemeral P-256 key, ECDH with the sponsor's key, HKDF-SHA256 (salt SHA-256(epk || KEY),
     * info "fdfenroll1 seal APP") to an AES-256-GCM key; the request's AAD "req APP", the answer's "ans APP", each with
     * its own 12-byte IV. The request is {seal:1, epk, iv, ct}, the answer {seal:1, iv, ct}.
     */
    public static final class Seal {
        private static final Base64.Encoder B64 = Base64.getUrlEncoder().withoutPadding();
        private static final Base64.Decoder D64 = Base64.getUrlDecoder();
        private static final SecureRandom RNG = new SecureRandom();
        private final byte[] k; private final String app, epk;
        private Seal(byte[] k, String app, String epk) { this.k = k; this.app = app; this.epk = epk; }

        /** a new sponsor key pair (P-256) */
        public static KeyPair newPair() throws Exception {
            KeyPairGenerator g = KeyPairGenerator.getInstance("EC"); g.initialize(new ECGenParameterSpec("secp256r1"), RNG); return g.generateKeyPair();
        }
        /** the fingerprint a QR carries for KEY: SHA-256 of its compressed point, the first 16 bytes, base64url ("" : not a key) */
        public static String fingerprint(String key) {
            try { publicKey(key); return B64.encodeToString(java.util.Arrays.copyOf(MessageDigest.getInstance("SHA-256").digest(D64.decode(key)), 16)); }
            catch (Exception e) { return ""; }
        }
        /** KEY: the compressed point (02|03, X), base64url */
        public static String publicOf(KeyPair kp) { return B64.encodeToString(point(kp.getPublic())); }
        private static byte[] point(PublicKey k) {
            java.security.spec.ECPoint w = ((java.security.interfaces.ECPublicKey) k).getW();
            byte[] x = w.getAffineX().toByteArray(), out = new byte[33];
            out[0] = (byte) (w.getAffineY().testBit(0) ? 3 : 2);
            int n = Math.min(x.length, 32); System.arraycopy(x, x.length - n, out, 33 - n, n);
            return out;
        }
        private static volatile java.security.spec.ECParameterSpec P256;
        private static java.security.spec.ECParameterSpec p256() throws Exception {
            if (P256 == null) P256 = ((java.security.interfaces.ECPublicKey) newPair().getPublic()).getParams();
            return P256;
        }
        public static String privateOf(KeyPair kp) { return B64.encodeToString(kp.getPrivate().getEncoded()); }
        /** a pair kept as publicOf / privateOf */
        public static KeyPair pair(String pub, String priv) throws Exception {
            return new KeyPair(publicKey(pub), KeyFactory.getInstance("EC").generatePrivate(new PKCS8EncodedKeySpec(D64.decode(priv))));
        }
        /** KEY as a QR carries it; an Exception when it is not a P-256 public key */
        public static PublicKey publicKey(String b64) throws Exception {
            byte[] c = D64.decode(b64);
            if (c.length != 33 || (c[0] != 2 && c[0] != 3)) throw new Exception("not a P-256 key");
            java.security.spec.ECParameterSpec ps = p256();
            java.math.BigInteger p = ((java.security.spec.ECFieldFp) ps.getCurve().getField()).getP(), a = ps.getCurve().getA(), b = ps.getCurve().getB();
            java.math.BigInteger x = new java.math.BigInteger(1, java.util.Arrays.copyOfRange(c, 1, 33));
            if (x.compareTo(p) >= 0) throw new Exception("not a P-256 key");
            java.math.BigInteger rhs = x.pow(3).add(a.multiply(x)).add(b).mod(p);
            java.math.BigInteger y = rhs.modPow(p.add(java.math.BigInteger.ONE).shiftRight(2), p);      // p = 3 mod 4
            if (!y.multiply(y).mod(p).equals(rhs)) throw new Exception("not a point of P-256");
            if (y.testBit(0) != (c[0] == 3)) y = p.subtract(y);
            return KeyFactory.getInstance("EC").generatePublic(new java.security.spec.ECPublicKeySpec(new java.security.spec.ECPoint(x, y), ps));
        }
        private static byte[] derive(java.security.PrivateKey mine, PublicKey theirs, byte[] epk, byte[] key, String app) throws Exception {
            KeyAgreement ka = KeyAgreement.getInstance("ECDH"); ka.init(mine); ka.doPhase(theirs, true);
            byte[] z = ka.generateSecret();
            MessageDigest d = MessageDigest.getInstance("SHA-256"); d.update(epk); d.update(key);
            Mac h = Mac.getInstance("HmacSHA256"); h.init(new SecretKeySpec(d.digest(), "HmacSHA256"));
            byte[] prk = h.doFinal(z);
            h.init(new SecretKeySpec(prk, "HmacSHA256")); h.update(("fdfenroll1 seal " + app).getBytes(StandardCharsets.UTF_8)); h.update((byte) 1);
            return h.doFinal();
        }
        /** the device's side: a seal to the sponsor KEY */
        public static Seal to(String key, String app) throws Exception {
            PublicKey sp = publicKey(key); KeyPair e = newPair(); byte[] epk = point(e.getPublic());
            return new Seal(derive(e.getPrivate(), sp, epk, point(sp), app), app, B64.encodeToString(epk));
        }
        /** the sponsor's side: the seal of a request {seal, epk, ...} to this pair; an Exception when it is not one */
        public static Seal of(KeyPair mine, Map<String, Object> req, String app) throws Exception {
            String epk = Json.str(req.get("epk")); PublicKey e = publicKey(epk);
            return new Seal(derive(mine.getPrivate(), e, point(e), point(mine.getPublic()), app), app, epk);
        }
        public String epk() { return epk; }
        private Map<String, Object> close(Map<String, Object> m, String aad) throws Exception {
            byte[] iv = new byte[12]; RNG.nextBytes(iv);
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(k, "AES"), new GCMParameterSpec(128, iv));
            c.updateAAD((aad + " " + app).getBytes(StandardCharsets.UTF_8));
            return Json.o("seal", 1, "iv", B64.encodeToString(iv), "ct", B64.encodeToString(c.doFinal(Json.write(m).getBytes(StandardCharsets.UTF_8))));
        }
        private Map<String, Object> open(Map<String, Object> s, String aad) throws Exception {
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(k, "AES"), new GCMParameterSpec(128, D64.decode(Json.str(s.get("iv")))));
            c.updateAAD((aad + " " + app).getBytes(StandardCharsets.UTF_8));
            return Json.obj(Json.parse(new String(c.doFinal(D64.decode(Json.str(s.get("ct")))), StandardCharsets.UTF_8)));
        }
        public Map<String, Object> request(Map<String, Object> m) throws Exception { Map<String, Object> r = close(m, "req"); r.put("epk", epk); return r; }
        public Map<String, Object> openRequest(Map<String, Object> r) throws Exception { return open(r, "req"); }
        public Map<String, Object> sealAnswer(Map<String, Object> m) throws Exception { return close(m, "ans"); }
        public Map<String, Object> answer(Map<String, Object> r) throws Exception { return open(r, "ans"); }
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
        /** [TEAM_JOIN_V1] the team's password (the lead sets it): its verifier, or null when the team has none */
        default Map<String, Object> teamVerifier() { return null; }
        /** [TEAM_JOIN_V1] USER joined by the team's password: a member of the team now (no password of its own) */
        default void joined(String user) { }
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
        private final KeyPair pair;
        private volatile boolean open = true;
        /** [SEALED_ENROLL_V1] the epks seen in the last REPLAY_MS: a request sent again is refused */
        private final Map<String, Long> seen = new java.util.HashMap<String, Long>();
        static final long SKEW_MS = 300000, REPLAY_MS = 600000;

        /** a sponsor with a key of its own, made now (a QR shown for it is good while it runs) */
        public Sponsor(int port, String app, String role, Team team) throws Exception { this(port, app, role, team, Seal.newPair()); }
        /** [SEALED_ENROLL_V1] a sponsor with the lead's kept key pair: its QR stays good */
        public Sponsor(int port, String app, String role, Team team, KeyPair pair) throws java.io.IOException {
            this.app = app; this.role = role; this.team = team; this.pair = pair;
            ss = new ServerSocket(); ss.setReuseAddress(true); ss.bind(new java.net.InetSocketAddress((InetAddress) null, port));
            Thread t = new Thread(new Runnable() { public void run() { loop(); } }, "enroll-sponsor");
            t.setDaemon(true); t.start();
            team.log("[ENROLL_V1] " + BUILD + ": enrolling " + app + " " + role + "'s members on port " + ss.getLocalPort() + ", sealed: its QR's key " + fingerprint());
        }
        public int port() { return ss.getLocalPort(); }
        /** its public key (op=key answers it) */
        public String key() { return Seal.publicOf(pair); }
        /** what its QR carries (the line's fifth part): the key's fingerprint */
        public String fingerprint() { return Seal.fingerprint(key()); }
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
            if (first.contains("op=key")) return Json.o("ok", true, "key", key(), "app", app);   // [SEALED_ENROLL_V1] checked against the QR's fingerprint
            if (!first.startsWith("POST ") || !first.contains("op=enroll")) throw new Refused(400, "this is " + app + " " + role + "'s enrollment: POST ?op=enroll&app=" + app);
            if (!first.contains("app=" + app + " ") && !first.contains("app=" + app + "&")) throw new Refused(400, "this enrolls members of " + app + ", not that app");
            Map<String, Object> outer;
            try { outer = Json.obj(Json.parse(body)); } catch (IllegalArgumentException e) { throw new Refused(400, "a JSON body is needed"); }
            // [SEALED_ENROLL_V1] sealed requests only; one that opens is answered sealed, a refusal too
            if (outer.get("ct") == null || outer.get("epk") == null)
                throw new Refused(400, "this team takes sealed enrollments only: scan the team's QR (it carries the lead's key) with the app");
            Seal sl; Map<String, Object> in;
            try { sl = Seal.of(pair, outer, app); in = sl.openRequest(outer); }
            catch (Exception e) { team.log("[SEALED_ENROLL_V1] " + from + ": a request not sealed to this team's key"); throw new Refused(403, "not sealed to this team's key: scan the team's QR again"); }
            try {
                long t = (long) Json.num(in.get("t")), now = System.currentTimeMillis();
                if (Math.abs(now - t) > SKEW_MS) throw new Refused(403, "this request is " + Math.abs(now - t) / 1000 + " s from this phone's clock (at most " + SKEW_MS / 1000 + "): check the time on both phones");
                synchronized (seen) {
                    for (java.util.Iterator<Long> i = seen.values().iterator(); i.hasNext(); ) if (now - i.next() > REPLAY_MS) i.remove();
                    if (seen.containsKey(sl.epk())) { team.log("[SEALED_ENROLL_V1] " + from + ": a request sent again (replayed): refused"); throw new Refused(403, "this request was sent before: refused"); }
                    seen.put(sl.epk(), now);
                }
                Map<String, Object> a = enrolled(in, from);
                return seal(sl, a);
            } catch (Refused r) {
                return seal(sl, Json.o("ok", false, "why", r.getMessage()));
            }
        }
        private static Map<String, Object> seal(Seal sl, Map<String, Object> a) throws Refused {
            try { return sl.sealAnswer(a); } catch (Exception e) { throw new Refused(500, "the answer could not be sealed: " + e); }
        }
        private Map<String, Object> enrolled(Map<String, Object> in, String from) throws Refused {
            String user = Json.str(in.get("user")).trim();
            Map<String, Object> m = team.member(user);
            // [TEAM_JOIN_V1] the team's password (one, the lead's) with the joiner's own call sign -- new, or one that joined that
            // way; never a member the lead made with a password of its own -- or a member's own password
            boolean own = m != null && m.get("verifier") instanceof Map;
            Map<String, Object> tv = team.teamVerifier();
            boolean byTeam = !own && (m == null || m.get("joined") != null) && tv != null && !user.isEmpty() && !user.equals(TEAM)
                    && user.matches("[A-Za-z0-9/._-]{1,30}") && check(tv, Json.str(in.get("password")));
            if (byTeam && m == null) { team.joined(user); team.log("[TEAM_JOIN_V1] " + user + " joined " + role + " by the team's password (" + from + ")"); }
            if (!byTeam && (!own || !check(Json.obj(m.get("verifier")), Json.str(in.get("password"))))) {
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
