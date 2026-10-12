// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * The fnav wire (FNAV-SPEC.md; the same bytes as src/fnav/frame.* and frogcomms/fnav.py):
 *   frame = [len u32 BE][kind u8][srclen u16 BE][src utf-8][payload]
 *   VIDEO = [level u32 BE: rung, high bit = keyframe][codec u8][codec packet]
 *   VSEG  = [frame_id u16 BE][index u16 BE][flags u8] + piece
 *   AUDIO = [format u8: 1 Opus][packet]
 * A part-written frame is padded so that it ENDS with DE AD BE EF ([ABORT_PAD_ENDS_WITH_THE_SENTINEL_V1]).
 */
public final class Fnav {
    public static final int KIND_AUDIO = 0, KIND_VIDEO = 1, KIND_BACKPRESSURE = 2, KIND_PLANE = 3, KIND_KEYREQ = 4,
            KIND_AUDIO_BACKPRESSURE = 5, KIND_VSEG = 6;
    public static final byte PLANE_AUDIO = 'A', PLANE_VIDEO = 'V';
    public static final long KEYFRAME_BIT = 0x80000000L;
    public static final int VSEG_LAST = 1, VSEG_ABORT = 2, VSEG_BYTES = 1368, VSEG_WHOLE_MAX = 8192;
    public static final byte[] ABORT_SENTINEL = {(byte) 0xDE, (byte) 0xAD, (byte) 0xBE, (byte) 0xEF};
    public static final int CODEC_VP8 = 0, CODEC_H264_SW = 1, CODEC_H264_HW = 2;
    public static final int FMT_PCM = 0, FMT_OPUS = 1;

    public static final class FrameError extends Exception { public FrameError(String m) { super(m); } }

    public static final class Typed {
        public final int kind; public final String src; public final byte[] body; public final int off, len;
        Typed(int k, String s, byte[] b, int o, int l) { kind = k; src = s; body = b; off = o; len = l; }
        public byte[] payload() { byte[] p = new byte[len]; System.arraycopy(body, off, p, 0, len); return p; }
    }

    public static final class Video {
        public final int level; public final boolean key; public final int codec; public final byte[] packet;
        Video(int l, boolean k, int c, byte[] p) { level = l; key = k; codec = c; packet = p; }
    }

    private Fnav() { }

    public static byte[] packTyped(int kind, String src, byte[] payload) throws FrameError {
        byte[] s = src.getBytes(StandardCharsets.UTF_8);
        if (s.length > 0xFFFF) throw new FrameError("source name longer than 65535 bytes");
        byte[] b = new byte[3 + s.length + payload.length];
        b[0] = (byte) kind; b[1] = (byte) (s.length >> 8); b[2] = (byte) s.length;
        System.arraycopy(s, 0, b, 3, s.length);
        System.arraycopy(payload, 0, b, 3 + s.length, payload.length);
        return b;
    }

    public static Typed unpackTyped(byte[] body) throws FrameError {
        if (body.length == 0) throw new FrameError("empty frame");
        if (body.length < 3) throw new FrameError("short frame: " + body.length + " bytes");
        int n = ((body[1] & 0xFF) << 8) | (body[2] & 0xFF);
        if (3 + n > body.length) throw new FrameError("source length " + n + " past the end of the frame");
        return new Typed(body[0] & 0xFF, new String(body, 3, n, StandardCharsets.UTF_8), body, 3 + n, body.length - 3 - n);
    }

    public static byte[] packVideo(int level, byte[] packet, int codec, boolean key) {
        byte[] b = new byte[5 + packet.length];
        long raw = (level & 0x7FFFFFFFL) | (key ? KEYFRAME_BIT : 0);
        RamSession.putU32(b, 0, raw);
        b[4] = (byte) codec;
        System.arraycopy(packet, 0, b, 5, packet.length);
        return b;
    }

    public static Video unpackVideo(byte[] p, int off, int len) throws FrameError {
        if (len < 5) throw new FrameError("video payload shorter than its 5-byte header: " + len);
        long raw = RamSession.u32(p, off);
        byte[] pkt = new byte[len - 5];
        System.arraycopy(p, off + 5, pkt, 0, pkt.length);
        return new Video((int) (raw & 0x7FFFFFFFL), (raw & KEYFRAME_BIT) != 0, p[off + 4] & 0xFF, pkt);
    }

    public static byte[] packSeg(int fid, int idx, int flags, byte[] src, int off, int len) {
        byte[] b = new byte[5 + len];
        b[0] = (byte) (fid >> 8); b[1] = (byte) fid; b[2] = (byte) (idx >> 8); b[3] = (byte) idx; b[4] = (byte) flags;
        System.arraycopy(src, off, b, 5, len);
        return b;
    }

    public static boolean isAbort(byte[] body) {
        int n = body.length;
        return n >= 4 && body[n - 4] == ABORT_SENTINEL[0] && body[n - 3] == ABORT_SENTINEL[1]
                && body[n - 2] == ABORT_SENTINEL[2] && body[n - 1] == ABORT_SENTINEL[3];
    }

    /** The padding for the rest of a part-written frame, aligned so that it ENDS with the sentinel. */
    public static byte[] abortPad(int remaining) {
        byte[] p = new byte[remaining];
        for (int j = 0; j < remaining; j++) p[j] = ABORT_SENTINEL[(4 - ((remaining - j) % 4)) % 4];
        return p;
    }

    /** Opus packet duration in ms from its TOC byte ([AUDIO_SECONDS_NOT_FRAMES_V1]); refuses what it cannot read. */
    public static double opusMs(byte[] pkt, int off, int len) throws FrameError {
        if (len < 1) throw new FrameError("empty Opus packet");
        int toc = pkt[off] & 0xFF, config = toc >> 3, code = toc & 3;
        double per;
        if (config < 12) per = new double[]{10, 20, 40, 60}[config & 3];
        else if (config < 16) per = (config & 1) == 0 ? 10 : 20;
        else per = new double[]{2.5, 5, 10, 20}[config & 3];
        int frames;
        if (code == 0) frames = 1;
        else if (code == 1 || code == 2) frames = 2;
        else { if (len < 2) throw new FrameError("Opus code 3 packet without its count byte"); frames = pkt[off + 1] & 0x3F; }
        return per * frames;
    }

    // ---- segmentation ----------------------------------------------------------------------------------------
    /** Bytes the socket ACCEPTED per second: fnav.py _note_sent, exactly (a = 1 - exp(-dt / 2 s)). */
    public static final class RateMeter {
        public double rate = 0; private double last = -1;
        public void onAccepted(long nbytes, double now) {
            if (last < 0) { last = now; rate = 0; return; }
            double dt = now - last; last = now;
            if (dt <= 0) return;
            double a = 1.0 - Math.exp(-dt / 2.0);
            rate = (1.0 - a) * rate + a * (nbytes / dt);
        }
    }

    /** [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] what the socket takes in one 20 ms audio block at its accepted rate. */
    public static int wholeFrameMax(double rate, int sndbuf) {
        if (rate <= 0) return VSEG_BYTES;
        return (int) Math.max(VSEG_BYTES, Math.min(sndbuf / 2, rate * 0.020));
    }

    public static final class Reassembler {
        private static final class Cur { int fid, next; java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream(); }
        private final Map<String, Cur> cur = new HashMap<String, Cur>();
        public long done, aborted, partial, gap, superseded, bad;

        /** One VSEG payload; returns the completed VIDEO payload or null. */
        public byte[] feed(String src, byte[] p, int off, int len) {
            if (len < 5) { bad++; return null; }
            int fid = ((p[off] & 0xFF) << 8) | (p[off + 1] & 0xFF), idx = ((p[off + 2] & 0xFF) << 8) | (p[off + 3] & 0xFF);
            int flags = p[off + 4] & 0xFF;
            Cur c = cur.get(src);
            if ((flags & VSEG_ABORT) != 0) {
                if (c != null) { cur.remove(src); aborted++; }
                return null;
            }
            if (c == null || c.fid != fid) {
                if (idx != 0) { partial++; cur.remove(src); return null; }
                if (c != null) superseded++;
                c = new Cur(); c.fid = fid; c.next = 1; c.buf.write(p, off + 5, len - 5);
                cur.put(src, c);
            } else {
                if (idx != c.next) { gap++; cur.remove(src); return null; }
                c.buf.write(p, off + 5, len - 5); c.next++;
            }
            if ((flags & VSEG_LAST) != 0) { cur.remove(src); done++; return c.buf.toByteArray(); }
            return null;
        }
    }
}
