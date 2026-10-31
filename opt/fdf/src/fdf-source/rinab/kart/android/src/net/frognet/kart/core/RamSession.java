// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart.core;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;

/**
 * One FNW1 session to a Ribbit RAM host (here: the kart memory and a race memory, rina-host /ram.php), the plain contract the host answers for a client that opens no
 * data socket (ram_host.cpp handle_frame): two outbound TCP connections --
 *   request socket: HELLO token, then REQ_RAW frames, sequence = their order on this socket, from 0
 *   return socket:  HELLO RETURN:token, answered by the host's HELLO; replies arrive tagged with that sequence
 * Frame = [len u32 BE][body], body = "FNW1" + op + ...
 *   REQ_RAW  0x03: hash16 + [len u32] + {"method","path","body"}
 *   RESP_RAW 0x14: same_id16 + [len u32] + [status u16][hlen u16][header hlen][body]
 *   OP_ERROR 0x30: [status u16] + message
 * A reply carries [seq u32] before "FNW1". Held reads simply answer later; replies are matched by sequence.
 * Nothing falls back: a lost connection fails every call in flight and every call after it.
 */
public final class RamSession {
    public static final class Unreachable extends Exception { public Unreachable(String m) { super(m); } }
    public static final class Refused extends Exception { public Refused(String m) { super(m); } }

    private static final byte[] MAGIC = "FNW1".getBytes(StandardCharsets.US_ASCII);
    private static final int REQ_RAW = 0x03, RESP_SAME = 0x13, RESP_RAW = 0x14, REQ_MISS = 0x21, OP_ERROR = 0x30, OP_HELLO = 0x50;
    private static final int MAX_REPLY = 64 << 20;

    private final String host;
    private final int port;
    private final Socket req, ret;
    private final OutputStream reqOut;
    private final Object sendLock = new Object();
    private final Map<Integer, Slot> waiting = new HashMap<Integer, Slot>();
    private int seq = 0;
    private volatile String dead = null;
    private final Thread reader;

    private static final class Slot { int status; String body; String error; boolean done; }

    public RamSession(String host, int port, int connectTimeoutMs) throws Unreachable {
        this.host = host; this.port = port;
        String token = token();
        try {
            req = open(host, port, connectTimeoutMs);
            reqOut = req.getOutputStream();
            sendFrame(reqOut, hello(token));
            ret = open(host, port, connectTimeoutMs);
            OutputStream ro = ret.getOutputStream();
            sendFrame(ro, hello("RETURN:" + token));
            ret.setSoTimeout(connectTimeoutMs);
            byte[] f = recvFrame(new DataInputStream(ret.getInputStream()));
            if (f == null || f.length < 5 || !magic(f, 0) || (f[4] & 0xFF) != OP_HELLO)
                throw new Unreachable(host + ":" + port + " did not accept the session (no HELLO on the return socket)");
            ret.setSoTimeout(0);
        } catch (IOException e) {
            throw new Unreachable(host + ":" + port + ": " + e.getMessage());
        }
        reader = new Thread(new Runnable() { public void run() { readReplies(); } }, "ram-replies");
        reader.setDaemon(true);
        reader.start();
    }

    public String host() { return host; }
    public int port() { return port; }

    /** The local address of this session's connection: this machine's address toward the host. */
    public String localAddress() { return req.getLocalAddress().getHostAddress(); }

    private static Socket open(String host, int port, int timeoutMs) throws IOException {
        Socket s = new Socket();
        s.setTcpNoDelay(true);
        s.setKeepAlive(true);
        s.connect(new InetSocketAddress(host, port), timeoutMs);
        return s;
    }

    private static String token() {
        SecureRandom r = new SecureRandom();
        StringBuilder b = new StringBuilder();
        for (int k = 0; k < 16; k++) b.append("0123456789abcdef".charAt(r.nextInt(16)));
        return b.toString();
    }

    private static byte[] hello(String token) {
        byte[] t = token.getBytes(StandardCharsets.UTF_8);
        byte[] f = new byte[6 + t.length];
        System.arraycopy(MAGIC, 0, f, 0, 4);
        f[4] = (byte) OP_HELLO; f[5] = (byte) t.length;
        System.arraycopy(t, 0, f, 6, t.length);
        return f;
    }

    private static boolean magic(byte[] f, int off) {
        return f.length >= off + 4 && f[off] == 'F' && f[off + 1] == 'N' && f[off + 2] == 'W' && f[off + 3] == '1';
    }

    private static void sendFrame(OutputStream o, byte[] body) throws IOException {
        byte[] b = new byte[4 + body.length];
        putU32(b, 0, body.length);
        System.arraycopy(body, 0, b, 4, body.length);
        o.write(b);
        o.flush();
    }

    private static byte[] recvFrame(DataInputStream in) throws IOException {
        int n;
        try { n = in.readInt(); } catch (java.io.EOFException e) { return null; }
        if (n < 5 || n > MAX_REPLY) throw new IOException("bad frame length " + n);
        byte[] f = new byte[n];
        in.readFully(f);
        return f;
    }

    static void putU32(byte[] b, int o, long v) {
        b[o] = (byte) (v >>> 24); b[o + 1] = (byte) (v >>> 16); b[o + 2] = (byte) (v >>> 8); b[o + 3] = (byte) v;
    }

    static long u32(byte[] b, int o) {
        return ((b[o] & 0xFFL) << 24) | ((b[o + 1] & 0xFFL) << 16) | ((b[o + 2] & 0xFFL) << 8) | (b[o + 3] & 0xFFL);
    }

    static int u16(byte[] b, int o) { return ((b[o] & 0xFF) << 8) | (b[o + 1] & 0xFF); }

    /** ram_host.cpp's hash16: two 64-bit FNV-style lanes, big-endian. The host keys its request cache with it. */
    static byte[] hash16(byte[] s) {
        long a = 1469598103934665603L, b = 0x9E3779B97F4A7C15L;
        for (byte x : s) {
            int c = x & 0xFF;
            a = (a ^ c) * 1099511628211L;
            b = (b ^ (c + 0x5b)) * 0x100000001B3L;
            b ^= b >>> 29;
        }
        byte[] o = new byte[16];
        for (int k = 0; k < 8; k++) { o[7 - k] = (byte) a; a >>>= 8; o[15 - k] = (byte) b; b >>>= 8; }
        return o;
    }

    private void readReplies() {
        String why = "the return socket closed";
        try {
            DataInputStream in = new DataInputStream(ret.getInputStream());
            while (true) {
                byte[] f = recvFrame(in);
                if (f == null) break;
                if (f.length < 9 || !magic(f, 4)) { why = "a reply that is not FNW1"; break; }
                int s = (int) u32(f, 0);
                int op = f[8] & 0xFF;
                Slot slot;
                synchronized (waiting) { slot = waiting.remove(s); }
                if (slot == null) continue;                       // its caller gave up (timed out)
                synchronized (slot) {
                    if (op == RESP_RAW && f.length >= 9 + 16 + 4 + 4) {
                        int len = (int) u32(f, 25);
                        int p = 29;
                        if (p + len > f.length) slot.error = "short RESP_RAW";
                        else {
                            slot.status = u16(f, p);
                            int hlen = u16(f, p + 2);
                            int bodyAt = p + 4 + hlen;
                            slot.body = new String(f, bodyAt, p + len - bodyAt, StandardCharsets.UTF_8);
                        }
                    } else if (op == OP_ERROR && f.length >= 11) {
                        slot.status = u16(f, 9);
                        slot.error = "host error " + slot.status + ": " + new String(f, 11, f.length - 11, StandardCharsets.UTF_8);
                    } else if (op == RESP_SAME || op == REQ_MISS) {
                        slot.error = "unexpected reply op 0x" + Integer.toHexString(op) + " to a REQ_RAW";
                    } else {
                        slot.error = "unknown reply op 0x" + Integer.toHexString(op);
                    }
                    slot.done = true;
                    slot.notifyAll();
                }
            }
        } catch (IOException e) {
            why = e.getMessage() == null ? e.toString() : e.getMessage();
        }
        fail(why);
    }

    private void fail(String why) {
        if (dead == null) dead = host + ":" + port + ": " + why;
        synchronized (waiting) {
            for (Slot sl : waiting.values()) synchronized (sl) { sl.error = dead; sl.done = true; sl.notifyAll(); }
            waiting.clear();
        }
    }

    public boolean alive() { return dead == null; }
    public String deadReason() { return dead; }

    /** One request: method + path (with its query) + body. Returns the host's JSON answer; ok:false is Refused. */
    public Map<String, Object> call(String method, String path, String body, double timeoutS) throws Unreachable, Refused {
        if (dead != null) throw new Unreachable(dead);
        String reqJson = Json.write(Json.o("method", method, "path", path, "body", body == null ? "" : body));
        byte[] r = reqJson.getBytes(StandardCharsets.UTF_8);
        byte[] f = new byte[4 + 1 + 16 + 4 + r.length];
        System.arraycopy(MAGIC, 0, f, 0, 4);
        f[4] = (byte) REQ_RAW;
        System.arraycopy(hash16(r), 0, f, 5, 16);
        putU32(f, 21, r.length);
        System.arraycopy(r, 0, f, 25, r.length);
        Slot slot = new Slot();
        int mine;
        synchronized (sendLock) {
            mine = seq++;
            synchronized (waiting) { waiting.put(mine, slot); }
            try { sendFrame(reqOut, f); }
            catch (IOException e) { fail("send: " + e.getMessage()); throw new Unreachable(dead); }
        }
        long deadline = System.currentTimeMillis() + (long) (timeoutS * 1000);
        synchronized (slot) {
            while (!slot.done) {
                long left = deadline - System.currentTimeMillis();
                if (left <= 0) {
                    synchronized (waiting) { waiting.remove(mine); }
                    throw new Unreachable(host + ":" + port + ": no reply in " + timeoutS + " s to " + method + " " + path);
                }
                try { slot.wait(left); } catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new Unreachable("interrupted"); }
            }
        }
        if (slot.error != null) {
            if (dead != null && slot.error.equals(dead)) throw new Unreachable(slot.error);
            throw new Refused(slot.error);
        }
        Map<String, Object> o;
        try { o = Json.obj(Json.parse(slot.body)); }
        catch (IllegalArgumentException e) { throw new Refused("reply is not JSON (" + e.getMessage() + "): " + slot.body); }
        if (!Json.bool(o.get("ok"))) throw new Refused(method + " " + path + ": " + Json.str(o.get("error")));
        return o;
    }

    /** [KART_POSTED_WRITE_V1] A request whose answer nobody waits for: a write that goes out and is never waited on
     *  (the C++ session posts its writes the same way). The host still answers; the reply finds no slot and is dropped. */
    public void post(String method, String path, String body) throws Unreachable {
        if (dead != null) throw new Unreachable(dead);
        String reqJson = Json.write(Json.o("method", method, "path", path, "body", body == null ? "" : body));
        byte[] r = reqJson.getBytes(StandardCharsets.UTF_8);
        byte[] f = new byte[4 + 1 + 16 + 4 + r.length];
        System.arraycopy(MAGIC, 0, f, 0, 4);
        f[4] = (byte) REQ_RAW;
        System.arraycopy(hash16(r), 0, f, 5, 16);
        putU32(f, 21, r.length);
        System.arraycopy(r, 0, f, 25, r.length);
        synchronized (sendLock) {
            seq++;
            try { sendFrame(reqOut, f); }
            catch (IOException e) { fail("send: " + e.getMessage()); throw new Unreachable(dead); }
        }
    }

    /** Ends the session now: a held read in another thread returns at once ([A_HELD_READ_ENDS_AT_ONCE_V1]). */
    public void close() {
        try { req.shutdownOutput(); } catch (IOException ignored) { }
        try { req.close(); } catch (IOException ignored) { }
        try { ret.close(); } catch (IOException ignored) { }
        fail("closed");
    }

    public static String urlenc(String s) {
        StringBuilder o = new StringBuilder();
        for (byte x : s.getBytes(StandardCharsets.UTF_8)) {
            int c = x & 0xFF;
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
                o.append((char) c);
            else o.append('%').append("0123456789ABCDEF".charAt(c >> 4)).append("0123456789ABCDEF".charAt(c & 15));
        }
        return o.toString();
    }

    @SuppressWarnings("unused")
    private static int readFully(InputStream in, byte[] b) throws IOException {
        int o = 0;
        while (o < b.length) { int n = in.read(b, o, b.length - o); if (n < 0) return o; o += n; }
        return o;
    }
}
