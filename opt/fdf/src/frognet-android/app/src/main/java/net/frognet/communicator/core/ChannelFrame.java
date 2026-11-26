// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * [THE_FRAME_IS_A_LIBRARY_V1] The protocol layer's frame, for the phone: ribbit/cpp/src/channel_frame.cpp in Java,
 * rule for rule and byte for byte (library build channel-frame-cpp-v6, the frame of core/channel_frame.py v13).
 * The names in brackets are that file's. A Codec is one end of one connection and touches no socket: the plane hands
 * it a message and gets the bytes to put on the connection, hands it the bytes that arrived and gets the messages.
 *
 *     on the connection   [length] [packet ID] [the data, sealed under the Role's current key, or not sealed]
 *     the data, opened    [the protocol's word, 4 bytes] [what that protocol packed]
 *
 * The key is computed, never stored and never sent: the first 32 bytes of HMAC-SHA384(the Role's secret on this
 * device, "frognet channel key\n" + the sender's ID + "\n" + the Role + "\n" + the setting's number).
 *
 * Threads, as in the C++: pack(), next() and undo() are the writer's, feed() is the reader's, setting() is anyone's.
 * The only guarded things are this end's copy of the settings and what the reader hands the writer.
 *
 * Java only: javax.crypto has AES-GCM, HMAC-SHA384 and (Android 9 / API 28 and later) ChaCha20-Poly1305.
 */
public final class ChannelFrame {
    public static final String BUILD = "channel-frame-java-v1-20261125a (the frame of channel-frame-cpp-v6-20261124f)";
    public static final String DEFAULT_PROTECTION = "aes-256-gcm";
    public static final int MAX_FRAME = 64 << 20, STREAMS = 256, SAY_RECEIVED_EVERY = 64, TAG = 16;

    /** The connection cannot go on; the reason is the message. */
    public static class Gone extends Exception { public Gone(String m) { super(m); } }
    /** This device cannot make the key or the check: no secret, no sender's ID, an algorithm it does not have. */
    public static final class DoesNotOpen extends Gone { public DoesNotOpen(String m) { super(m); } }

    public static final class Setting {
        public String driver = "tcp";
        public long number = 0;                       // 0: not sealed
        public String protection = DEFAULT_PROTECTION;
        public long step = -1;                        // the step of the Role's own secret; -1: the setting names none
        public Setting() { }
        public Setting(String driver, long number, String protection, long step) {
            this.driver = driver; this.number = number; this.protection = protection; this.step = step;
        }
    }

    public static final class Message { public String role; public int sequence; public long counter; public boolean reply; public byte[] data; }

    public interface Say { void say(String text); }

    private static final String SWITCH_PROTOCOL = "SWP\u0001", SWITCH_ENCRYPTION = "SWE\u0001", NOT_OPENED = "NOT\u0001",
            AGAIN = "AGN\u0001", RECEIVED = "RCV\u0001";
    private static final String DEFAULT_SECRET = "default", STEPPED = "FROGNET ROLE SECRET AT STEP ";
    private static final int NAMED = 0, CHANGED = 1, LOST = 2, IN_STEP = 3, TAKEN_UP = 4, SEND_AGAIN = 5, THEY_HAVE = 6, WE_HAVE = 7, ASK = 8;
    private static final Object FILES = new Object();          // reading a secret's step and replacing its file is one act

    private ChannelFrame() { }

    // ------------------------------------------------------------------------------------------------ small things
    static String lower(String s) {
        StringBuilder b = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) { char c = s.charAt(i); b.append(c >= 'A' && c <= 'Z' ? (char) (c + 32) : c); }
        return b.toString();
    }
    private static byte[] latin(String s) { return s.getBytes(StandardCharsets.ISO_8859_1); }
    private static byte[] utf8(String s) { return s.getBytes(StandardCharsets.UTF_8); }
    static String hex(byte[] p, int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) { b.append("0123456789abcdef".charAt((p[i] >> 4) & 15)); b.append("0123456789abcdef".charAt(p[i] & 15)); }
        return b.toString();
    }
    private static void varint(ByteArrayOutputStream out, long z) {
        while ((z & ~0x7fL) != 0) { out.write((int) (0x80 | (z & 0x7f))); z >>>= 7; }
        out.write((int) z);
    }
    private static byte[] withNumber(String call, long n) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.write(latin(call), 0, 4); varint(b, n);
        return b.toByteArray();
    }
    private static long numberIn(byte[] p, int from) throws Gone {
        long z = 0; int shift = 0;
        for (int i = from; i < p.length; i++) { z |= (long) (p[i] & 0x7f) << shift; shift += 7; if ((p[i] & 0x80) == 0) return z; }
        throw new Gone("a call's number does not end");
    }
    private static byte[] be64(long v) { byte[] o = new byte[8]; for (int i = 7; i >= 0; i--) { o[i] = (byte) v; v >>>= 8; } return o; }
    private static long packetId(long counter, int stream, boolean reply) { return (counter << 9) | ((long) stream << 1) | (reply ? 1 : 0); }
    private static String word(byte[] d) { return d.length < 4 ? "" : new String(d, 0, 4, StandardCharsets.ISO_8859_1); }

    static byte[] hmac(String algorithm, byte[] key, String text) throws DoesNotOpen {
        try {
            Mac m = Mac.getInstance(algorithm);
            m.init(new SecretKeySpec(key, algorithm));
            return m.doFinal(utf8(text));
        } catch (GeneralSecurityException e) {
            throw new DoesNotOpen("this device refused an HMAC (" + algorithm + "): " + e.getMessage());
        } catch (IllegalArgumentException e) {
            throw new DoesNotOpen("this device refused an HMAC (" + algorithm + "): " + e.getMessage());
        }
    }

    // ------------------------------------------------------------------------------------------------ the algorithms
    public static boolean protectionKnown(String name) { return "aes-256-gcm".equals(name) || "chacha20-poly1305".equals(name); }

    private static Cipher cipher(String protection, int mode, byte[] key, byte[] nonce, byte[] aad) throws GeneralSecurityException {
        Cipher c;
        if ("aes-256-gcm".equals(protection)) {
            c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(mode, new SecretKeySpec(key, "AES"), new GCMParameterSpec(TAG * 8, nonce));
        } else {
            c = Cipher.getInstance("ChaCha20-Poly1305");
            c.init(mode, new SecretKeySpec(key, "ChaCha20"), new IvParameterSpec(nonce));
        }
        c.updateAAD(aad);
        return c;
    }

    /** True when this device can seal with the algorithm (ChaCha20-Poly1305 needs Android 9 or later). */
    public static boolean protectionWorks(String name) {
        if (!protectionKnown(name)) return false;
        try { cipher(name, Cipher.ENCRYPT_MODE, new byte[32], new byte[12], new byte[8]); return true; }
        catch (GeneralSecurityException e) { return false; }
    }

    // ------------------------------------------------------------------------------------- the protocols under the frame
    private static final int PREFIX = 24, SPAN = 4096;        // core/chorus.py, core/openqasm.py
    private static final Map<String, String> WORD_OF = new HashMap<String, String>(), NAME_OF = new HashMap<String, String>();
    static {
        WORD_OF.put("tcp", "TCP\u0001"); WORD_OF.put("chorus", "CHO\u0003"); WORD_OF.put("openqasm", "OQ3\u0002");
        for (Map.Entry<String, String> e : WORD_OF.entrySet()) NAME_OF.put(e.getValue(), e.getKey());
    }
    public static boolean driverKnown(String name) { return WORD_OF.containsKey(name); }
    private static boolean isCall(String w) {
        return w.equals(SWITCH_PROTOCOL) || w.equals(SWITCH_ENCRYPTION) || w.equals(NOT_OPENED) || w.equals(AGAIN) || w.equals(RECEIVED);
    }

    private static byte[] packBy(String name, Map<String, byte[]> st, byte[] m) {
        if (name.equals("tcp")) return m;
        ByteArrayOutputStream out = new ByteArrayOutputStream(m.length + 8);
        if (name.equals("chorus")) {                           // a message's first PREFIX bytes as a difference against the one before
            byte[] was = st.get("first");
            if (was == null) { was = new byte[PREFIX]; st.put("first", was); }
            int k = Math.min(PREFIX, m.length), nb = (k + 7) / 8;
            byte[] head = new byte[1 + nb]; head[0] = (byte) k;
            ByteArrayOutputStream diff = new ByteArrayOutputStream();
            for (int i = 0; i < k; i++) if (m[i] != was[i]) { head[1 + i / 8] |= (byte) (1 << (i % 8)); diff.write(m[i]); }
            System.arraycopy(m, 0, was, 0, k);
            out.write(head, 0, head.length);
            out.write(diff.toByteArray(), 0, diff.size());
            out.write(m, k, m.length - k);
            return out.toByteArray();
        }
        byte[] was = st.get("was");                            // openqasm: the whole message, up to SPAN bytes, as a difference
        if (was == null) was = new byte[0];
        int n = Math.min(Math.min(m.length, was.length), SPAN), i = 0;
        varint(out, n);
        while (i < n) {
            int j = i;
            while (j < n && m[j] == was[j]) j++;
            varint(out, j - i);
            if (j >= n) break;
            int k = j;
            while (k < n && !(k + 3 <= n && m[k] == was[k] && m[k + 1] == was[k + 1] && m[k + 2] == was[k + 2])) k++;
            varint(out, k - j);
            out.write(m, j, k - j);
            i = k;
        }
        out.write(m, n, m.length - n);
        st.put("was", Arrays.copyOf(m, Math.min(m.length, SPAN)));
        return out.toByteArray();
    }

    private static long takeNumber(byte[] d, int[] p, int end, long pid, String whose) throws Gone {
        long z = 0; int shift = 0;
        for (;;) {
            if (p[0] >= end || shift > 35) throw new Gone("packet " + pid + " opened to something that is not " + whose);
            int c = d[p[0]++] & 0xff; z |= (long) (c & 0x7f) << shift; shift += 7;
            if ((c & 0x80) == 0) return z;
        }
    }

    private static byte[] unpackBy(String name, Map<String, byte[]> st, long pid, byte[] d, int off) throws Gone {
        int n = d.length - off;
        if (name.equals("tcp")) return Arrays.copyOfRange(d, off, d.length);
        if (name.equals("chorus")) {
            byte[] was = st.get("first");
            if (was == null) { was = new byte[PREFIX]; st.put("first", was); }
            int k = n > 0 ? d[off] & 0xff : PREFIX + 1, nb = (k + 7) / 8, ones = 0;
            if (k <= PREFIX && n >= 1 + nb) for (int i = 0; i < k; i++) ones += (d[off + 1 + i / 8] >> (i % 8)) & 1;
            if (k > PREFIX || n < 1 + nb + ones) throw new Gone("packet " + pid + " opened to something that is not chorus's");
            int at = off + 1 + nb;
            byte[] first = Arrays.copyOf(was, k);
            for (int i = 0; i < k; i++) if (((d[off + 1 + i / 8] >> (i % 8)) & 1) != 0) first[i] = d[at++];
            System.arraycopy(first, 0, was, 0, k);
            byte[] out = new byte[k + (d.length - at)];
            System.arraycopy(first, 0, out, 0, k);
            System.arraycopy(d, at, out, k, d.length - at);
            return out;
        }
        byte[] was = st.get("was");
        if (was == null) was = new byte[0];
        int[] p = {off};
        long m = takeNumber(d, p, d.length, pid, "OpenQASM's");
        if (m > Math.min(was.length, SPAN)) throw new Gone("packet " + pid + " is a difference against " + m + " bytes and this end holds " + was.length);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        while (out.size() < m) {
            long same = takeNumber(d, p, d.length, pid, "OpenQASM's");
            int from = out.size(), to = (int) Math.min((long) was.length, from + same);
            if (to > from) out.write(was, from, to - from);
            if (out.size() >= m) break;
            long k = takeNumber(d, p, d.length, pid, "OpenQASM's");
            int take = (int) Math.min(k, (long) (d.length - p[0]));
            out.write(d, p[0], take); p[0] += take;
            if (same == 0 && k == 0) break;                    // nothing moved: the difference does not come to m
        }
        if (out.size() != m) throw new Gone("packet " + pid + ": its difference does not come to the " + m + " bytes it says");
        out.write(d, p[0], d.length - p[0]);
        byte[] whole = out.toByteArray();
        st.put("was", Arrays.copyOf(whole, Math.min(whole.length, SPAN)));
        return whole;
    }

    // ---------------------------------------------------------------------------------------- secrets, keys, checks
    public static final class Secret { public byte[] value; public long step; public boolean own; }

    private static byte[] readFile(File f) throws IOException {
        RandomAccessFile r = new RandomAccessFile(f, "r");
        try { byte[] b = new byte[(int) r.length()]; r.readFully(b); return b; } finally { r.close(); }
    }

    /** The Role's secret as its file is: [dir]/[role], or [dir]/default for a Role with no file of its own. */
    public static Secret readSecret(String role, String dir) throws DoesNotOpen {
        synchronized (FILES) {
            boolean own = role.length() > 0 && !role.equals(DEFAULT_SECRET) && new File(dir, role).isFile();
            File path = new File(dir, own ? role : DEFAULT_SECRET);
            byte[] raw;
            try { raw = readFile(path); }
            catch (IOException e) {
                throw new DoesNotOpen("this device holds no secret for '" + role + "' and no default secret (" + path + "): it does not carry that Role's sealed traffic");
            }
            Secret s = new Secret(); s.own = own;
            byte[] head = latin(STEPPED);
            if (own && raw.length >= head.length && Arrays.equals(Arrays.copyOf(raw, head.length), head)) {
                int nl = head.length;
                while (nl < raw.length && raw[nl] != '\n') nl++;
                String line = new String(raw, head.length, nl - head.length, StandardCharsets.ISO_8859_1);
                try { if (line.isEmpty() || line.charAt(0) == '+') throw new NumberFormatException(); s.step = Long.parseLong(line); }
                catch (NumberFormatException e) { throw new DoesNotOpen("the secret for '" + role + "' in " + path + " does not say its step"); }
                raw = nl >= raw.length ? new byte[0] : Arrays.copyOfRange(raw, nl + 1, raw.length);
            }
            if (raw.length < 32) throw new DoesNotOpen("the secret for '" + role + "' in " + path + " is " + raw.length + " bytes; a secret is at least 32");
            s.value = raw;
            return s;
        }
    }

    public static byte[] nextSecret(byte[] secret, String role, long step) throws DoesNotOpen {
        return hmac("HmacSHA384", secret, "next\n" + role + "\n" + step);
    }

    /** The Role's secret at a step, worked out in memory. step -1: as the file is. */
    public static Secret secretAt(String role, long step, String dir) throws DoesNotOpen {
        Secret s = readSecret(role, dir);
        if (!s.own || step < 0) return s;
        if (step < s.step) throw new DoesNotOpen("step " + step + " is lower than the step this device's secret for '" + role + "' is at (" + s.step + ")");
        byte[] v = s.value;
        for (long k = s.step + 1; k <= step; k++) v = nextSecret(v, role, k);
        Secret r = new Secret(); r.value = v; r.step = s.step; r.own = true;
        return r;
    }

    /** [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] Replaces the Role's own secret file with the secret at `step` and erases the old one. */
    public static boolean keepSecret(String role, byte[] secret, long step, String dir) throws Gone {
        synchronized (FILES) {
            if (readSecret(role, dir).step >= step) return false;
            File path = new File(dir, role), tmp = new File(dir, role + ".next");
            RandomAccessFile old = null;
            try {
                old = new RandomAccessFile(path, "rw");
                long size = old.length();
                FileOutputStream f = new FileOutputStream(tmp);
                try { f.write(latin(STEPPED + step + "\n")); f.write(secret); f.getFD().sync(); } finally { f.close(); }
                if (!tmp.renameTo(path)) throw new Gone("the secret for '" + role + "' could not be written at step " + step);
                old.seek(0); old.write(new byte[(int) size]); old.getFD().sync();   // erase the secret it replaced
                return true;
            } catch (IOException e) {
                throw new Gone("the secret for '" + role + "' could not be moved to step " + step + ": " + e.getMessage());
            } finally {
                if (old != null) try { old.close(); } catch (IOException ignored) { }
            }
        }
    }

    /** [KEY_AND_CHECK_USE_SHA_384_V1] the first 32 of the 48 bytes. */
    public static byte[] keyFor(byte[] secret, String sender, String role, long number) throws DoesNotOpen {
        return Arrays.copyOf(hmac("HmacSHA384", secret, "frognet channel key\n" + sender + "\n" + role + "\n" + number), 32);
    }

    /** What a screen or a log may show of a secret: 8 characters. */
    public static String secretFingerprint(byte[] secret) throws DoesNotOpen {
        return hex(hmac("HmacSHA256", secret, "frognet channel secret fingerprint"), 4);
    }

    /** The text a setting's check is made over: the Role's name, then each other field as name=value, sorted by name. */
    public static String checkText(String role, Map<String, String> fields) {
        StringBuilder t = new StringBuilder(lower(role));
        for (Map.Entry<String, String> e : new TreeMap<String, String>(fields).entrySet())
            if (!e.getKey().equals("check") && !e.getKey().equals("ts")) t.append('\n').append(e.getKey()).append('=').append(e.getValue());
        return t.toString();
    }

    public static String checkOf(byte[] secret, String role, Map<String, String> fields) throws DoesNotOpen {
        byte[] h = hmac("HmacSHA384", secret, checkText(role, fields));
        return hex(h, h.length);
    }

    public static final class Taken { public boolean taken, moved; public String why = "", fingerprint = ""; public Setting setting = new Setting(); }

    private static long number(String s) { try { return s.isEmpty() ? 0 : Long.parseLong(s); } catch (NumberFormatException e) { return 0; } }

    /**
     * [A_SETTING_PROVES_ITSELF_V1] A setting as it was read from its tuple, taken or ignored. `fields` is every field of
     * the tuple's JSON, "check" included, each value as text. A device that holds the Role's secret takes the setting
     * only if its check verifies under the secret at the setting's step, and then moves its own file to that step. A
     * device with no secret has nothing to verify with: it takes the setting and cannot open the Role's sealed frames.
     */
    public static Taken takeSetting(String roleAsNamed, Map<String, String> fields, String dir) throws Gone {
        String role = lower(roleAsNamed);
        Taken t = new Taken();
        String v;
        if ((v = fields.get("driver")) != null && !v.isEmpty()) t.setting.driver = v;
        if ((v = fields.get("protection")) != null && !v.isEmpty()) t.setting.protection = v;
        t.setting.number = number(fields.get("number") == null ? "" : fields.get("number"));
        t.setting.step = number(fields.get("step") == null ? "" : fields.get("step"));
        Secret s;
        try { s = secretAt(role, t.setting.step, dir); }
        catch (DoesNotOpen e) {
            if (e.getMessage().startsWith("step ")) { t.why = e.getMessage() + ": a setting does not take a Role's secret back"; return t; }
            t.taken = true;                                    // no secret here: nothing to verify with, nothing to protect
            return t;
        }
        String check = fields.get("check");
        if (!checkOf(s.value, role, fields).equals(check == null ? "" : check)) {
            t.why = "its check does not verify against this device's secret for " + role + (s.own ? " at step " + t.setting.step : "") + "; nothing is written";
            return t;
        }
        if (s.own && t.setting.step > s.step) t.moved = keepSecret(role, s.value, t.setting.step, dir);
        t.taken = true;
        t.fingerprint = secretFingerprint(s.value);
        return t;
    }

    // ------------------------------------------------------------------------------------------------------ the Codec
    public static final class Counts {
        public long sent, received, notOpened, sentAgain;
        public final long[] protocol = new long[2], encryption = new long[2], notOpenedCalls = new long[2], again = new long[2], receivedCalls = new long[2];
        Counts copy() {
            Counts c = new Counts(); c.sent = sent; c.received = received; c.notOpened = notOpened; c.sentAgain = sentAgain;
            for (int i = 0; i < 2; i++) { c.protocol[i] = protocol[i]; c.encryption[i] = encryption[i]; c.notOpenedCalls[i] = notOpenedCalls[i]; c.again[i] = again[i]; c.receivedCalls[i] = receivedCalls[i]; }
            return c;
        }
    }

    private static final class Kept { final long pid; final byte[] message; Kept(long pid, byte[] m) { this.pid = pid; this.message = m; } }

    private static final class Side {
        String role = "", fnid = "", protection = DEFAULT_PROTECTION, word = "";
        long number, count, got, skip, base, since;
        boolean sealed, named, owed, lost, spoke;
        byte[] key;
        Map<String, byte[]> state = new HashMap<String, byte[]>();
        ArrayList<Kept> kept = new ArrayList<Kept>();
        Side copy() {
            Side s = new Side();
            s.role = role; s.fnid = fnid; s.protection = protection; s.word = word;
            s.number = number; s.count = count; s.got = got; s.skip = skip; s.base = base; s.since = since;
            s.sealed = sealed; s.named = named; s.owed = owed; s.lost = lost; s.spoke = spoke; s.key = key;
            for (Map.Entry<String, byte[]> e : state.entrySet()) s.state.put(e.getKey(), e.getValue().clone());
            s.kept = new ArrayList<Kept>(kept);
            return s;
        }
    }

    private static final class Item {
        final int kind, stream; final long n; final boolean flag; final String role;
        Item(int kind, int stream, long n, boolean flag, String role) { this.kind = kind; this.stream = stream; this.n = n; this.flag = flag; this.role = role; }
    }

    private static final class Owed {
        final int kind, stream; long n; boolean started; final String role;
        Owed(int kind, int stream, long n, boolean started, String role) { this.kind = kind; this.stream = stream; this.n = n; this.started = started; this.role = role; }
    }

    /** Everything the writer changes: copied whole for undo(). */
    private static final class Out {
        TreeMap<Integer, Side> side = new TreeMap<Integer, Side>();
        HashMap<String, Integer> stream = new HashMap<String, Integer>();
        TreeMap<Integer, String> inRole = new TreeMap<Integer, String>();
        HashMap<Integer, Long> count = new HashMap<Integer, Long>();
        TreeMap<Integer, Long> parked = new TreeMap<Integer, Long>();
        HashSet<Integer> asked = new HashSet<Integer>();
        ArrayDeque<Owed> owed = new ArrayDeque<Owed>();
        int last = -1;
        Counts counts = new Counts();
        Out copy() {
            Out o = new Out();
            for (Map.Entry<Integer, Side> e : side.entrySet()) o.side.put(e.getKey(), e.getValue().copy());
            o.stream.putAll(stream); o.inRole.putAll(inRole); o.count.putAll(count); o.parked.putAll(parked); o.asked.addAll(asked);
            for (Owed w : owed) o.owed.add(new Owed(w.kind, w.stream, w.n, w.started, w.role));
            o.last = last; o.counts = counts.copy();
            return o;
        }
    }

    public static final class Codec {
        private final boolean opener;
        private final String far, secrets;
        private String fnid;
        private final Say say;
        private volatile Runnable wake;
        private final Object guard = new Object();             // this end's settings and what is handed to the writer; nothing else
        private final HashMap<String, Setting> settings = new HashMap<String, Setting>();
        private ArrayDeque<Item> handed = new ArrayDeque<Item>();
        private Out o = new Out(), before = null;
        private final SecureRandom random = new SecureRandom();
        private final byte[] nonceHead = new byte[8];
        private int nonceN = 0;
        // the reader's
        private final HashMap<Integer, Side> in = new HashMap<Integer, Side>();
        private final HashMap<Integer, Long> countIn = new HashMap<Integer, Long>();
        private int lastIn = -1;
        private byte[] buf = new byte[0];
        private int at = 0, end = 0;
        private volatile long received, notOpened;
        private final long[] callsIn = new long[5];

        /**
         * opener: true at the end that opened the connection (it gives each Role its sequence). far: the other end's
         * name, for what is said. sender: this device's ID, which is in every key it sends under. secrets: the
         * directory of the Roles' secrets.
         */
        public Codec(boolean opener, String far, String sender, String secrets, Say say) {
            this.opener = opener; this.far = far; this.fnid = sender == null ? "" : sender; this.secrets = secrets; this.say = say;
            random.nextBytes(nonceHead);
        }

        /** The Role's setting as the program holds it now (null: none). Call it again whenever the held read on it fires. */
        public void setting(String role, Setting s) {
            synchronized (guard) { settings.put(role, s); handed.add(new Item(CHANGED, 0, 0, false, role)); }
            Runnable w = wake; if (w != null) w.run();
        }

        /** Called, from the reader's thread or whoever changed a setting, when the writer owes the far end something. */
        public void whenOwing(Runnable wake) { this.wake = wake; }

        public String far() { return far; }

        private Setting settingOf(String role) { synchronized (guard) { return settings.get(role); } }

        private void hand(Item i) {
            boolean owes = i.kind != NAMED && i.kind != TAKEN_UP && i.kind != THEY_HAVE && i.kind != LOST;
            synchronized (guard) { handed.add(i); }
            Runnable w = wake; if (owes && w != null) w.run();
        }

        private void say(String text) { if (say != null) say.say(text); }

        private String mine() throws DoesNotOpen {
            if (fnid.isEmpty()) throw new DoesNotOpen("this device has no sender's ID: it cannot compute the key it sends a sealed Role under");
            return fnid;
        }

        private static long wantedNumber(Setting s) { return s == null ? 0 : s.number; }
        private static String wantedProtection(Setting s) { return s == null || s.protection == null || s.protection.isEmpty() ? DEFAULT_PROTECTION : s.protection; }

        /** The key for this number, under this algorithm. Nothing of the key before is kept. */
        private void rekey(Side s, long number, String protection) throws DoesNotOpen {
            if (!protectionKnown(protection)) throw new DoesNotOpen("the channel of '" + s.role + "' names the algorithm '" + protection + "', which this device does not have");
            s.protection = protection; s.number = number; s.key = null; s.sealed = false;
            if (number == 0) return;
            if (!protectionWorks(protection)) throw new DoesNotOpen("the channel of '" + s.role + "' names the algorithm '" + protection + "', which this version of Android does not have");
            Setting held = settingOf(s.role);
            s.key = keyFor(secretAt(lower(s.role), held != null ? held.step : -1, secrets).value, s.fnid, s.role, number);
            s.sealed = true;
        }

        // ---- the wire: one frame appended to the unit ([_put])
        private void put(ByteArrayOutputStream unit, long pid, byte[] body) {
            long counter = pid >>> 9; int stream = (int) ((pid >>> 1) & 0xff); boolean reply = (pid & 1) != 0;
            boolean same = stream == o.last;
            long x = 0;
            if (counter != 0) {
                Long c = o.count.get(stream);
                long d = counter - (c == null ? 0 : c) - 1;
                x = 1 + (d >= 0 ? d << 1 : ((-d) << 1) - 1);
                o.count.put(stream, counter);
            }
            int was = unit.size();
            varint(unit, body.length);
            varint(unit, (x << 2) | (reply ? 2 : 0) | (same ? 1 : 0));
            if (!same) unit.write(stream);
            unit.write(body, 0, body.length);
            o.last = stream;
            o.counts.sent += unit.size() - was;
        }

        private byte[] seal(Side s, long pid, byte[] data) throws Gone {
            if (!s.sealed) return data;
            // 12 bytes never used twice under any key: 8 drawn for this connection (and again when the count runs out)
            // and a count. An undone unit's nonce is not used again either: undo() does not put the count back.
            if (++nonceN == 0) { random.nextBytes(nonceHead); nonceN = 1; }
            byte[] nonce = new byte[12];
            System.arraycopy(nonceHead, 0, nonce, 0, 8);
            nonce[8] = (byte) (nonceN >>> 24); nonce[9] = (byte) (nonceN >>> 16); nonce[10] = (byte) (nonceN >>> 8); nonce[11] = (byte) nonceN;
            try {
                byte[] sealed = cipher(s.protection, Cipher.ENCRYPT_MODE, s.key, nonce, be64(pid)).doFinal(data);
                byte[] out = new byte[12 + sealed.length];
                System.arraycopy(nonce, 0, out, 0, 12);
                System.arraycopy(sealed, 0, out, 12, sealed.length);
                return out;
            } catch (GeneralSecurityException e) {
                throw new Gone("this device refused to seal with " + s.protection + ": " + e.getMessage());
            }
        }

        private String driverWord(Setting s, String role) throws Gone {
            String name = s != null && s.driver != null && !s.driver.isEmpty() ? s.driver : "tcp";
            String w = WORD_OF.get(name);
            if (w == null) throw new Gone("the channel of '" + role + "' names the protocol '" + name + "', which this device does not have");
            return w;
        }

        private static String runningOf(String word, long number, String protection, boolean sealed) {
            String name = NAME_OF.get(word);
            return "driver=" + (name == null ? "" : name) + " number=" + number + " protection=" + (sealed ? protection : "") + " sealed=" + (sealed ? "true" : "false");
        }

        private int outStream(String role, int stream) throws Gone {
            if (stream >= 0) return stream;
            Integer it = o.stream.get(role);
            if (it == null) {
                if (!opener) throw new Gone("this end did not open the connection to " + far + " and has no sequence for '" + role + "'");
                if (o.stream.size() >= STREAMS) throw new Gone("the connection to " + far + " already carries " + STREAMS + " Roles");
                it = o.stream.size();
                o.stream.put(role, it);
            }
            return it;
        }

        /** This end's sending side for a Role, named on the connection before its first frame ([_out_side]). */
        private Side outSide(ByteArrayOutputStream unit, String role, int stream) throws Gone {
            Side side = o.side.get(stream);
            if (side == null) { side = new Side(); o.side.put(stream, side); }
            if (!side.named) {
                side.role = role;
                Setting s = settingOf(role);
                long number = wantedNumber(s);
                // [A_SEQUENCE_NAMED_UNSEALED_SAYS_WHO_SENDS_IT_V1] the name always says who sends
                try { side.fnid = mine(); } catch (DoesNotOpen e) { if (number != 0) throw e; side.fnid = ""; }
                rekey(side, number, wantedProtection(s));
                side.word = driverWord(s, role);
                byte[] f = utf8(side.fnid), r = utf8(role);
                byte[] body = new byte[9 + f.length + r.length];
                System.arraycopy(be64(side.number), 0, body, 0, 8);
                body[8] = (byte) f.length;
                System.arraycopy(f, 0, body, 9, f.length);
                System.arraycopy(r, 0, body, 9 + f.length, r.length);
                put(unit, packetId(0, stream, false), body);
                side.named = true;
                say("to " + far + ", sequence " + stream + " is '" + role + "': " + runningOf(side.word, side.number, side.protection, side.sealed));
            }
            return side;
        }

        private void call(ByteArrayOutputStream unit, int stream, Side s, byte[] data) throws Gone {
            long pid = packetId(0, stream, true);
            put(unit, pid, seal(s, pid, data));
        }

        /** Bring this end's sending for a Role to its setting: each call goes out the old way; what follows is new ([_catch_up]). */
        private void catchUp(ByteArrayOutputStream unit, int stream, Side s) throws Gone {
            Setting held = settingOf(s.role);
            String word = driverWord(held, s.role);
            if (!word.equals(s.word)) {
                call(unit, stream, s, latin(SWITCH_PROTOCOL));
                o.counts.protocol[0]++;
                String was = NAME_OF.get(s.word);
                s.word = word; s.state.clear();
                say("to " + far + " for '" + s.role + "': SWITCH PROTOCOL sent; " + was + " until here, " + NAME_OF.get(word) + " from here");
            }
            long number = wantedNumber(held); String protection = wantedProtection(held);
            if (number != s.number || (number != 0 && !protection.equals(s.protection))) {
                call(unit, stream, s, latin(SWITCH_ENCRYPTION));
                o.counts.encryption[0]++;
                if (number != 0 && s.fnid.isEmpty()) s.fnid = mine();
                rekey(s, number, protection);
                s.state.clear();
                call(unit, stream, s, withNumber(AGAIN, s.base + s.kept.size()));   // the first thing under the new key says where this end is
                o.counts.again[0]++;
                say("to " + far + " for '" + s.role + "': SWITCH ENCRYPTION sent; now " + runningOf(s.word, s.number, s.protection, s.sealed));
            }
        }

        private void message(ByteArrayOutputStream unit, Side s, long pid, byte[] m) throws Gone {
            byte[] packed = packBy(NAME_OF.get(s.word), s.state, m);
            byte[] data = new byte[4 + packed.length];
            System.arraycopy(latin(s.word), 0, data, 0, 4);
            System.arraycopy(packed, 0, data, 4, packed.length);
            put(unit, pid, seal(s, pid, data));
        }

        private void askAgain(ByteArrayOutputStream unit, int stream) throws Gone {
            Long first = o.parked.get(stream); String there = o.inRole.get(stream);
            if (first == null || there == null) return;
            int st = outStream(there, stream);
            Side s = outSide(unit, there, st);
            catchUp(unit, st, s);
            call(unit, st, s, withNumber(NOT_OPENED, first));
            o.asked.add(stream);
            o.counts.notOpenedCalls[0]++;
            say("to " + far + " for '" + there + "': NOT OPENED sent: its messages from number " + first + " on did not open here; send them again");
        }

        /** What the reader, or whoever changed a setting, handed the writer: taken in the order it was handed. */
        private void takeHanded() {
            ArrayDeque<Item> items;
            synchronized (guard) { items = handed; handed = new ArrayDeque<Item>(); }
            for (Item i : items) {
                switch (i.kind) {
                case NAMED: o.inRole.put(i.stream, i.role); if (!opener && !o.stream.containsKey(i.role)) o.stream.put(i.role, i.stream); break;
                case CHANGED: o.owed.add(new Owed(CHANGED, 0, 0, false, i.role)); break;
                case LOST: if (!o.parked.containsKey(i.stream)) o.parked.put(i.stream, i.n); break;
                case IN_STEP: if (i.flag || !o.asked.contains(i.stream)) o.owed.add(new Owed(ASK, i.stream, 0, false, "")); break;
                case TAKEN_UP: o.parked.remove(i.stream); o.asked.remove(i.stream); break;
                case SEND_AGAIN: o.owed.add(new Owed(SEND_AGAIN, i.stream, i.n, false, "")); break;
                case WE_HAVE: o.owed.add(new Owed(WE_HAVE, i.stream, i.n, false, "")); break;
                case THEY_HAVE: {
                    // [A_REPEAT_ONCE_ANNOUNCED_IS_SENT_WHOLE_V1] what a repeat under way has still to send is kept,
                    // whatever the far end has said since that it has: sending fewer would make it skip new messages.
                    long have = i.n;
                    for (Owed w : o.owed) if (w.kind == SEND_AGAIN && w.started && w.stream == i.stream) have = Math.min(have, w.n);
                    Side s = o.side.get(i.stream);
                    if (s != null && have > s.base) {
                        int gone = (int) Math.min(have - s.base, (long) s.kept.size());
                        s.kept.subList(0, gone).clear();
                        s.base += gone;
                    }
                    break; }
                default: break;
                }
            }
        }

        /** One owed thing into the unit. False when the thing at the front is a message being sent again and callsOnly. */
        private boolean owedInto(ByteArrayOutputStream unit, boolean callsOnly) throws Gone {
            while (!o.owed.isEmpty()) {
                Owed e = o.owed.peekFirst();
                if (e.kind == CHANGED) {
                    o.owed.pollFirst();
                    for (Map.Entry<Integer, Side> kv : o.side.entrySet()) if (kv.getValue().named && kv.getValue().role.equals(e.role)) catchUp(unit, kv.getKey(), kv.getValue());
                    for (Integer stream : new ArrayList<Integer>(o.parked.keySet())) if (e.role.equals(o.inRole.get(stream))) askAgain(unit, stream);
                } else if (e.kind == ASK) {
                    o.owed.pollFirst();
                    askAgain(unit, e.stream);
                } else if (e.kind == WE_HAVE) {
                    o.owed.pollFirst();
                    String r = o.inRole.get(e.stream);
                    if (r != null) {
                        int st = outStream(r, e.stream);
                        Side s = outSide(unit, r, st);
                        catchUp(unit, st, s);
                        call(unit, st, s, withNumber(RECEIVED, e.n));
                        o.counts.receivedCalls[0]++;
                    }
                } else {                                       // SEND_AGAIN: the far end said NOT OPENED from e.n
                    Side s = o.side.get(e.stream);
                    if (s == null || !s.named) { o.owed.pollFirst(); continue; }
                    if (!e.started) {
                        if (e.n < s.base) e.n = s.base;        // asked twice for one loss: it has since said it has those
                        if (e.n > s.base + s.kept.size())
                            throw new Gone(far + " asks for '" + s.role + "'s messages again from number " + e.n + " and this end holds " + s.base + " to " + (s.base + s.kept.size()));
                        catchUp(unit, e.stream, s);
                        s.state.clear();
                        call(unit, e.stream, s, withNumber(AGAIN, e.n));
                        o.counts.again[0]++;
                        say("to " + far + " for '" + s.role + "': " + (s.base + s.kept.size() - e.n) + " message(s) to send again, from number " + e.n
                                + ", under " + runningOf(s.word, s.number, s.protection, s.sealed));
                        e.started = true;
                        if (e.n >= s.base + s.kept.size()) o.owed.pollFirst();
                        return true;                           // the call is a unit of its own; the messages follow, one a unit
                    }
                    if (callsOnly) return false;
                    if (e.n < s.base) throw new Gone("a repeat to " + far + " for '" + s.role + "' is at message " + e.n + " and this end keeps them only from " + s.base);
                    if (e.n >= s.base + s.kept.size()) { o.owed.pollFirst(); continue; }
                    Kept k = s.kept.get((int) (e.n - s.base));
                    message(unit, s, k.pid, k.message);
                    o.counts.sentAgain++;
                    e.n = e.n + 1;
                    if (e.n >= s.base + s.kept.size()) o.owed.pollFirst();
                    return true;
                }
                if (unit.size() > 0 && !callsOnly) return true;   // next(): one owed thing a unit
            }
            return true;
        }

        /** What the frame owes the far end with no message to carry it; null when nothing is owed. */
        public byte[] next() throws Gone {
            takeHanded();
            if (o.owed.isEmpty()) return null;
            before = o.copy();
            ByteArrayOutputStream unit = new ByteArrayOutputStream();
            owedInto(unit, false);
            if (unit.size() == 0) { before = null; return null; }   // what was owed needed nothing on the connection
            return unit.toByteArray();
        }

        /** The bytes that carry one message; null while messages are being sent again (send what next() gives first). */
        public byte[] pack(String role, byte[] m) throws Gone {
            takeHanded();
            Out was = o.copy();
            ByteArrayOutputStream unit = new ByteArrayOutputStream(m.length + 64);
            if (!owedInto(unit, true) || (!o.owed.isEmpty() && o.owed.peekFirst().kind == SEND_AGAIN)) { o = was; before = null; return null; }
            before = was;
            int stream = outStream(role, -1);
            Side s = outSide(unit, role, stream);
            long counter = ++s.count;
            catchUp(unit, stream, s);
            long pid = packetId(counter, stream, false);
            s.kept.add(new Kept(pid, m));                      // until the far end says it has it
            message(unit, s, pid, m);
            return unit.toByteArray();
        }

        /** Takes back the unit pack() or next() just returned: the plane did not put it on the connection whole. */
        public void undo() throws Gone {
            if (before == null) throw new Gone("undo() with no unit to take back");
            o = before; before = null;
        }

        /** Role -> what this end is sending it as (the writer's). */
        public Map<String, String> running() {
            Map<String, String> r = new TreeMap<String, String>();
            for (Side s : o.side.values()) if (s.named) r.put(s.role, runningOf(s.word, s.number, s.protection, s.sealed));
            return r;
        }

        public Counts counts() {
            Counts c = o.counts.copy();
            c.received = received; c.notOpened = notOpened;
            c.protocol[1] = callsIn[0]; c.encryption[1] = callsIn[1]; c.notOpenedCalls[1] = callsIn[2]; c.again[1] = callsIn[3]; c.receivedCalls[1] = callsIn[4];
            return c;
        }

        // ------------------------------------------------------------------------------------------- receiving
        private void rekeyIn(Side s, long number, String protection) {
            s.owed = false;
            try { rekey(s, number, protection); }
            catch (DoesNotOpen e) {
                say("from " + far + " for '" + s.role + "': " + e.getMessage() + "; its frames do not open here");
                s.number = number; s.key = null; s.sealed = false;
            }
        }

        /** The data of a frame, under the one key and algorithm this side holds. There is no other key to try ([_open]). */
        private byte[] open(Side s, long pid, byte[] body) {
            for (int attempt = 0; attempt < 2; attempt++) {
                if (!s.sealed) {
                    String w = word(body);
                    if (w.length() == 4 && (isCall(w) || NAME_OF.containsKey(w))) return body;
                } else if (body.length >= 12 + TAG) {
                    try {
                        return cipher(s.protection, Cipher.DECRYPT_MODE, s.key, Arrays.copyOf(body, 12), be64(pid)).doFinal(body, 12, body.length - 12);
                    } catch (GeneralSecurityException e) { /* it does not open */ }
                }
                if (attempt > 0 || !(s.owed || s.lost)) break;
                Setting held = settingOf(s.role);
                long number = wantedNumber(held); String protection = wantedProtection(held);
                if (number == s.number && (number != 0 ? protection : "").equals(s.number != 0 ? s.protection : "")) break;
                rekeyIn(s, number, protection);                // this end's setting has arrived since
            }
            return null;
        }

        private boolean take(long[] pidOut, byte[][] bodyOut) throws Gone {
            int p = at;
            long[] nz = new long[2];
            for (int which = 0; which < 2; which++) {
                long z = 0; int shift = 0;
                for (;;) {
                    if (p >= end) return false;
                    int b = buf[p++] & 0xff; z |= (long) (b & 0x7f) << shift; shift += 7;
                    if ((b & 0x80) == 0) break;
                    if (shift > 63) throw new Gone("a number from " + far + " does not end: the far end does not speak this frame");
                }
                nz[which] = z;
            }
            long n = nz[0], z = nz[1];
            if (n < 0 || n > MAX_FRAME) throw new Gone("a frame from " + far + " gives its length as " + n + ": the far end does not speak this frame");
            int stream = lastIn;
            if ((z & 1) == 0) { if (p >= end) return false; stream = buf[p++] & 0xff; }
            if (end - p < n) return false;
            bodyOut[0] = Arrays.copyOfRange(buf, p, p + (int) n);
            p += (int) n;
            received += p - at; at = p;
            lastIn = stream;
            long x = z >>> 2, counter = 0;
            if (x != 0) {
                x -= 1;
                Long c = countIn.get(stream);
                counter = (c == null ? 0 : c) + 1 + ((x & 1) != 0 ? -((x + 1) >>> 1) : (x >>> 1));
                countIn.put(stream, counter);
            }
            pidOut[0] = packetId(counter, stream, (z & 2) != 0);
            return true;
        }

        /** One frame taken in: a name, a call, or a message ([_one]). */
        private void frame(long pid, byte[] body, List<Message> out) throws Gone {
            long counter = pid >>> 9; int stream = (int) ((pid >>> 1) & 0xff); boolean reply = (pid & 1) != 0;
            if (counter == 0 && !reply) {                      // the first frame of a sequence, this direction
                if (body.length < 9 || body.length < 9 + (body[8] & 0xff)) throw new Gone("a sequence's first frame from " + far + " is " + body.length + " bytes");
                long number = 0; for (int i = 0; i < 8; i++) number = (number << 8) | (body[i] & 0xff);
                int fl = body[8] & 0xff;
                Side side = new Side();
                side.fnid = new String(body, 9, fl, StandardCharsets.UTF_8);
                side.role = new String(body, 9 + fl, body.length - 9 - fl, StandardCharsets.UTF_8);
                side.named = true;
                in.put(stream, side);
                hand(new Item(NAMED, stream, 0, false, side.role));
                rekeyIn(side, number, wantedProtection(settingOf(side.role)));
                say("from " + far + ", sequence " + stream + " is '" + side.role + "', from node " + (side.fnid.isEmpty() ? "(not said)" : side.fnid)
                        + (side.sealed ? ", sealed with " + side.protection : ", not sealed"));
                return;
            }
            Side side = in.get(stream);
            if (side == null) throw new Gone("a frame from " + far + " on sequence " + stream + ", which nothing has named");
            byte[] data = open(side, pid, body);
            if (data == null) {
                // [TWO_SETTINGS_AHEAD_IS_NOT_A_STRANGER_V1] fatal only from a far end nothing of whose has ever opened
                if (side.number == 0 && !side.owed && wantedNumber(settingOf(side.role)) == 0 && !side.spoke)
                    throw new Gone("data from " + far + " for '" + side.role + "', which is not sealed, begins with no registered protocol's word");
                notOpened++;
                if (!side.lost) {
                    side.lost = true; side.state.clear();
                    say("from " + far + " for '" + side.role + "': a frame did not open. It goes back for a retry: nothing of this sequence is taken from message number "
                            + side.got + " until the sender starts again");
                }
                hand(new Item(LOST, stream, side.got, false, ""));
                return;
            }
            side.spoke = true;
            if (counter == 0) {                                // a call
                String c = word(data);
                if (c.equals(SWITCH_PROTOCOL) && data.length == 4) {
                    callsIn[0]++; side.state.clear();
                    say("from " + far + " for '" + side.role + "': SWITCH PROTOCOL read");
                } else if (c.equals(SWITCH_ENCRYPTION) && data.length == 4) {
                    callsIn[1]++;
                    Setting held = settingOf(side.role);
                    long number = wantedNumber(held); String protection = wantedProtection(held);
                    if (number != side.number || (number != 0 && !protection.equals(side.protection))) {
                        rekeyIn(side, number, protection);
                        say("from " + far + " for '" + side.role + "': SWITCH ENCRYPTION read; this end's setting says " + (side.sealed ? "sealed with " + side.protection : "not sealed"));
                    } else {
                        side.owed = true;                      // the sender's setting changed and this end's has not yet
                        say("from " + far + " for '" + side.role + "': SWITCH ENCRYPTION read before this end's setting changed; what follows goes back for a retry until it has");
                    }
                } else if (c.equals(AGAIN)) {
                    callsIn[3]++;
                    long first = numberIn(data, 4);
                    side.state.clear();
                    if (first > side.got) { if (side.lost) hand(new Item(IN_STEP, stream, 0, true, "")); }
                    else if (side.lost || first < side.got) {
                        side.lost = false; side.skip = side.got - first;
                        hand(new Item(TAKEN_UP, stream, 0, false, ""));
                        say("from " + far + " for '" + side.role + "': the sender starts again from message number " + first + "; this end has " + side.got);
                    }
                } else if (c.equals(NOT_OPENED)) {
                    callsIn[2]++;
                    hand(new Item(SEND_AGAIN, stream, numberIn(data, 4), false, ""));
                } else if (c.equals(RECEIVED)) {
                    callsIn[4]++;
                    hand(new Item(THEY_HAVE, stream, numberIn(data, 4), false, ""));
                } else throw new Gone("a call from " + far + " for '" + side.role + "' that this end does not know");
                return;
            }
            // [A_SEALED_ROLE_TAKES_NO_UNSEALED_MESSAGE_V1] when the setting THIS end holds for the Role is sealed, an
            // unsealed message is not taken: it goes back for a retry like any frame that does not open.
            if (!side.sealed && wantedNumber(settingOf(side.role)) != 0) {
                notOpened++;
                if (!side.lost) {
                    side.lost = true; side.state.clear();
                    say("from " + far + " for '" + side.role + "': an UNSEALED message, and this end's setting for the Role is sealed. It is not taken: nothing of "
                            + "this sequence is, from message number " + side.got + ", until the sender starts again under the key");
                }
                hand(new Item(LOST, stream, side.got, false, ""));
                return;
            }
            if (side.lost) { hand(new Item(IN_STEP, stream, 0, false, "")); return; }   // it opens, and it is from before the sender started again
            String w = word(data);
            String name = NAME_OF.get(w);
            if (w.length() != 4 || name == null) throw new Gone("data from " + far + " for '" + side.role + "' begins with no registered protocol's word: refused");
            if (!w.equals(side.word)) { side.word = w; side.state.clear(); }
            Message m = new Message();
            m.data = unpackBy(name, side.state, pid, data, 4);
            if (side.skip > 0) { side.skip--; return; }        // sent again, and this end already had it
            side.got++;
            if (++side.since >= SAY_RECEIVED_EVERY) { side.since = 0; hand(new Item(WE_HAVE, stream, side.got, false, "")); }
            m.role = side.role; m.sequence = stream; m.counter = counter; m.reply = reply;
            out.add(m);
        }

        /** The bytes that arrived, in order. The messages that opened are added to `out`. */
        public void feed(byte[] p, int off, int n, List<Message> out) throws Gone {
            if (at == end) { at = 0; end = 0; }
            if (buf.length - end < n) {
                byte[] b = new byte[Math.max((end - at) + n, 4096)];
                System.arraycopy(buf, at, b, 0, end - at);
                end -= at; at = 0; buf = b;
            }
            System.arraycopy(p, off, buf, end, n); end += n;
            long[] pid = new long[1]; byte[][] body = new byte[1][];
            while (take(pid, body)) frame(pid[0], body[0], out);
        }
    }

    static Set<String> drivers() { return WORD_OF.keySet(); }
}
