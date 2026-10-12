// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayDeque;

/**
 * A call through the media server, the wire half (frogcomms/call.py, src/comms/call.cpp) with the codecs left to the
 * platform: two planes (video, audio), each opened with its PLANE frame ([TWO_PLANES_V1]: plane letter + one 8-byte
 * teardown tag for both + the session); receive loops per plane; the bearer and the bottom walk deciding the size the
 * encoder makes ([LADDER_RATE_V2], [BOTTOM_RUNG_SHRINKS_V1]); send-or-drop for every frame.
 *
 * The encoder is asynchronous on a phone, so the send loop of call.py is split in two: tick() runs once per camera
 * frame (the loop's bookkeeping and the bearer, then the size for the next frame) and sendEncoded() sends each packet
 * the encoder produces, counting what went and what was dropped for the next tick.
 */
public final class CallCore {
    public interface Listener {
        void onVideo(String src, Fnav.Video v);
        void onAudio(String src, byte[] opus);
        void onKeyRequest();
        void onPlaneEnded(String plane, String why);
    }

    public static final class Stats {                            // cumulative; each reader diffs
        public volatile long vSent, vDropped, vBytes, keysSent, keyreqs, vRecv, vRxBytes, aSent, aShed, aBytes, aRecv,
                aRxBytes, aRefused, decodeErrors;
        public volatile int rung, sendW, sendH, ceiling;
        public volatile String reason = "";
    }

    public final String name, session, host;
    public final int port, fps;
    public final Stats stats = new Stats();
    private final Plane vplane, aplane;
    private final Listener l;
    private volatile boolean stop = false;
    private volatile long throttleBps = 0;
    private Thread vrx, arx;

    // the send side's state (call.py _video_tx)
    private Bearer bearer;
    private int srcW, srcH;
    private double bucket, bucketT;
    private final ArrayDeque<Double> sentTimes = new ArrayDeque<Double>();
    private int rungFrames; private double rungT0;
    private double stressFrom = -1, cleanFrom = -1;
    static final int BOTTOM_FIRST = 3, BOTTOM_LAST = 6, BOTTOM_CLEAN_WINDOWS = 3, WARMUP_FRAMES = 15;
    static final double BOTTOM_STEP_S = 2.0, WINDOW_S = 2.0, WARMUP_S = 2.0;
    private int bottom, bottomOk, winRefused;
    private double bottomAt, winT0;
    private boolean inBottom, shed;
    private int pendingDropped, pendingAudioShed;
    private int size = -2;
    private volatile boolean wantKey = false;

    public CallCore(String host, int port, String name, String session, int fps, Listener l) throws Plane.Closed {
        this.host = host; this.port = port; this.name = name; this.session = session; this.fps = fps; this.l = l;
        SecureRandom r = new SecureRandom();
        StringBuilder t = new StringBuilder();
        for (int k = 0; k < 8; k++) t.append((char) ('a' + r.nextInt(26)));
        String tag = t.toString();                               // one teardown tag, both planes
        vplane = open(host, port, Fnav.PLANE_VIDEO, tag);
        Plane a;
        try { a = open(host, port, Fnav.PLANE_AUDIO, tag); } catch (Plane.Closed e) { vplane.close(); throw e; }
        aplane = a;
    }

    private Plane open(String host, int port, byte kind, String tag) throws Plane.Closed {
        Plane p = Plane.connect(host, port, 8000);                // [A_HANG_IS_THE_WORST_REPORT_V1]
        byte[] tg = tag.getBytes(StandardCharsets.US_ASCII), s = session.getBytes(StandardCharsets.UTF_8);
        byte[] pl = new byte[1 + tg.length + s.length];
        pl[0] = kind;
        System.arraycopy(tg, 0, pl, 1, tg.length);
        System.arraycopy(s, 0, pl, 1 + tg.length, s.length);
        try {
            if (p.send(Fnav.packTyped(Fnav.KIND_PLANE, name, pl), Plane.CONTROL) != Plane.WHOLE) {
                p.close(); throw new Plane.Closed("the media server did not take the " + (char) kind + " plane's first frame");
            }
        } catch (Fnav.FrameError e) { p.close(); throw new Plane.Closed(e.getMessage()); }
        return p;
    }

    public void start() {
        vrx = new Thread(new Runnable() { public void run() { videoRx(); } }, "call-video-rx");
        arx = new Thread(new Runnable() { public void run() { audioRx(); } }, "call-audio-rx");
        vrx.setDaemon(true); arx.setDaemon(true);
        vrx.start(); arx.start();
    }

    public void stop() {
        if (stop) return;
        stop = true;
        vplane.shutdown(); aplane.shutdown();
        try { if (vrx != null) vrx.join(2000); if (arx != null) arx.join(2000); } catch (InterruptedException ignored) { }
        vplane.close(); aplane.close();
    }

    public boolean stopped() { return stop; }

    /** DEMO / the link: caps the uplink to bps (0 = unlimited); frames over budget are shed and the bearer walks down. */
    public void setThrottle(long bps) { throttleBps = Math.max(0, bps); }

    public boolean takeWantKey() { boolean k = wantKey; wantKey = false; return k; }

    // ---- receive -------------------------------------------------------------------------------------------------
    private void videoRx() {
        Fnav.Reassembler reasm = new Fnav.Reassembler();
        String why = "hung up";
        try {
            while (!stop) {
                int st = vplane.recv(200);
                if (st == Plane.CLOSED) { why = "the media server closed the video plane"; break; }
                if (st != Plane.FRAME) continue;
                Fnav.Typed t;
                try { t = Fnav.unpackTyped(vplane.body()); } catch (Fnav.FrameError e) { continue; }
                if (t.kind == Fnav.KIND_KEYREQ) { wantKey = true; stats.keyreqs++; l.onKeyRequest(); continue; }
                byte[] payload; int off, len;
                if (t.kind == Fnav.KIND_VIDEO) { payload = t.body; off = t.off; len = t.len; }
                else if (t.kind == Fnav.KIND_VSEG) {
                    payload = reasm.feed(t.src, t.body, t.off, t.len);
                    if (payload == null) continue;
                    off = 0; len = payload.length;
                } else continue;
                Fnav.Video v;
                try { v = Fnav.unpackVideo(payload, off, len); } catch (Fnav.FrameError e) { continue; }
                stats.vRecv++; stats.vRxBytes += len;               // [RX_IS_PER_SOURCE_V1] counted before decoding
                try { l.onVideo(t.src, v); } catch (RuntimeException e) { stats.decodeErrors++; }   // one frame's problem
            }
        } catch (Plane.Closed e) {
            why = e.getMessage();
        } catch (java.nio.channels.ClosedSelectorException e) {
            why = "hung up";
        }
        if (!stop) l.onPlaneEnded("video", why);
    }

    private void audioRx() {
        String why = "hung up";
        try {
            while (!stop) {
                int st = aplane.recv(200);
                if (st == Plane.CLOSED) { why = "the media server closed the audio plane"; break; }
                if (st != Plane.FRAME) continue;
                Fnav.Typed t;
                try { t = Fnav.unpackTyped(aplane.body()); } catch (Fnav.FrameError e) { continue; }
                if (t.kind == Fnav.KIND_AUDIO_BACKPRESSURE) { stats.aRefused++; continue; }
                if (t.kind != Fnav.KIND_AUDIO || t.len < 1) continue;
                stats.aRecv++; stats.aRxBytes += t.len;             // [RECEIVE_IS_MEASURED_V1] before the mixer
                if ((t.body[t.off] & 0xFF) != Fnav.FMT_OPUS) continue;   // [AUDIO_IS_OPUS_V1] refused, not guessed
                byte[] pkt = new byte[t.len - 1];
                System.arraycopy(t.body, t.off + 1, pkt, 0, pkt.length);
                try { l.onAudio(t.src, pkt); } catch (RuntimeException e) { stats.decodeErrors++; }
            }
        } catch (Plane.Closed e) {
            why = e.getMessage();
        } catch (java.nio.channels.ClosedSelectorException e) {
            why = "hung up";
        }
        if (!stop) l.onPlaneEnded("audio", why);
    }

    // ---- send: audio ----------------------------------------------------------------------------------------------
    public void sendAudio(byte[] opus) throws Plane.Closed {
        byte[] p = new byte[1 + opus.length];
        p[0] = (byte) Fnav.FMT_OPUS;
        System.arraycopy(opus, 0, p, 1, opus.length);
        byte[] body;
        try { body = Fnav.packTyped(Fnav.KIND_AUDIO, name, p); } catch (Fnav.FrameError e) { throw new Plane.Closed(e.getMessage()); }
        if (aplane.send(body, Plane.AUDIO) == Plane.WHOLE) { stats.aSent++; stats.aBytes += body.length; }
        else { stats.aShed++; synchronized (this) { pendingAudioShed++; } }
    }

    // ---- send: video -------------------------------------------------------------------------------------------
    /** The source's picture size (the camera frame the encoder is fed from): sets the ceiling. */
    public synchronized void startVideo(int w, int h) {
        srcW = w; srcH = h;
        double now = now();
        bearer = new Bearer(Ladder.ceilingFor(w, h), 0, now);
        bucketT = now; rungT0 = now; winT0 = now;
        size = -2;
    }

    /** One encoded packet of the size tick() chose. Returns true if it went whole. */
    public boolean sendEncoded(byte[] packet, boolean key, Ladder.Size s) throws Plane.Closed {
        byte[] vp = Fnav.packVideo(s.level, packet, Fnav.CODEC_VP8, key);
        long bps = throttleBps;
        if (bps > 0) {                                            // DEMO: a smaller wire -- frames over budget are shed
            synchronized (this) {
                double t = now();
                bucket = Math.min(bps / 8.0, bucket + (t - bucketT) * bps / 8.0); bucketT = t;
                if (vp.length > bucket && !key) { pendingDropped++; stats.vDropped++; return false; }
                bucket -= vp.length;
            }
        }
        if (vplane.sendVideo(name, vp, key)) {
            stats.vSent++; stats.vBytes += vp.length; if (key) stats.keysSent++;
            synchronized (this) { sentTimes.addLast(now()); rungFrames++; }
            return true;
        }
        synchronized (this) { pendingDropped++; }
        stats.vDropped++;
        if (key) wantKey = true;
        return false;
    }

    /** Once per camera frame: the bookkeeping, the bearer, the bottom walk; returns the SIZES index for the next frame,
     *  or -1 for audio only. */
    public synchronized int tick() {
        if (bearer == null) return -1;
        double t = now();
        int dropped = pendingDropped; pendingDropped = 0;
        while (!sentTimes.isEmpty() && t - sentTimes.peekFirst() > 1.0) sentTimes.pollFirst();
        Double fpsSent = (size >= 0 && rungFrames >= WARMUP_FRAMES && t - rungT0 >= WARMUP_S) ? Double.valueOf(sentTimes.size()) : null;
        winRefused += dropped;
        if (t - winT0 >= WINDOW_S) { bottomOk = winRefused > 0 ? 0 : bottomOk + 1; winRefused = 0; winT0 = t; }
        int before = bearer.idx;
        int ashed = pendingAudioShed; pendingAudioShed = 0;
        bearer.sample(t, 0, dropped, fpsSent, fps, ashed);
        int r = bearer.idx;
        if (shed) {
            if (r >= 5) { shed = false; inBottom = true; bottom = BOTTOM_LAST - BOTTOM_FIRST; bottomAt = t; bottomOk = 0; bearer.resumeAt(5, t); }
        } else if (r < 5) {
            if (!inBottom) { inBottom = true; bottomAt = t; }
            else if (dropped > 0 && t - bottomAt >= BOTTOM_STEP_S) {
                if (BOTTOM_FIRST + bottom < BOTTOM_LAST) { bottom++; bottomAt = t; bottomOk = 0; }
                else { shed = true; inBottom = false; bottom = 0; }      // 160x120 refused too: audio only
            }
        } else if (r > 5 && bottom > 0) {
            if (bottomOk >= BOTTOM_CLEAN_WINDOWS && t - bottomAt >= BOTTOM_STEP_S) {
                bottom--; bottomAt = t; bottomOk = 0;
                if (bottom == 0) { inBottom = false; bearer.resumeAt(5, t); }
            } else bearer.resumeAt(5, t);                         // hold the ladder at L5 while the picture grows
        } else if (r >= 5 && bottom == 0) inBottom = false;
        if (bearer.idx != before) { sentTimes.clear(); rungFrames = 0; rungT0 = t; }   // [RATE_IS_MEASURED_AT_THIS_RUNG_V1]
        // the size for the next frame
        if (shed) size = -1;
        else if (inBottom || bottom > 0) size = BOTTOM_FIRST + bottom;
        else size = Ladder.sizeForRung(bearer.idx, srcW, srcH);
        stats.rung = shed ? bearer.idx : (inBottom || bottom > 0) ? 5 : bearer.idx;
        stats.ceiling = bearer.ceiling;
        stats.reason = bearer.reason;
        if (size >= 0) { stats.sendW = Ladder.SIZES[size].w; stats.sendH = Ladder.SIZES[size].h; }
        else { stats.sendW = 0; stats.sendH = 0; }
        return size;
    }

    public long planeAbortedIn() { return vplane.abortedIn + aplane.abortedIn; }

    private static double now() { return System.nanoTime() / 1e9; }
}
