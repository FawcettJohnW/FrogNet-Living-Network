// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.graphics.Matrix;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Handler;
import android.os.Looper;
import android.graphics.SurfaceTexture;
import android.view.Surface;
import android.view.TextureView;

import net.frognet.communicator.core.Fnav;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The far end's picture: VP8 packets of the shown source -> MediaCodec -> the TextureView, aspect kept.
 * A decoder starts on a keyframe, at the keyframe's size; a keyframe of another size (the media server moved this
 * viewer to another rung) starts a new one. Frames that cannot be decoded in time are dropped back to the next
 * keyframe -- never fed with a gap. Arrivals are counted per source before decoding ([RX_IS_PER_SOURCE_V1]).
 */
final class RemoteVideo {
    private final TextureView view;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile Surface surface;
    private volatile boolean surfaceChanged = false;
    private final ArrayBlockingQueue<Object[]> q = new ArrayBlockingQueue<Object[]>(48);
    private Thread t;
    private volatile boolean run = false;
    private MediaCodec dec;
    private int decW, decH;
    private boolean needKey = true;
    volatile String shown = "";
    private final Map<String, Long> lastSeen = new HashMap<String, Long>();
    volatile int picW, picH;
    volatile long decoded, dropped, errors, notVp8;
    volatile String decoderName = "";

    RemoteVideo(TextureView v) { view = v; }

    private SurfaceTexture owner;
    private volatile SurfaceTexture dropping;

    /** A surface to draw into, and the TextureView's SurfaceTexture behind it. */
    void setSurface(SurfaceTexture st) { owner = st; surface = new Surface(st); surfaceChanged = true; }

    /** The TextureView let go: stop rendering into it, then release it ([TextureView contract]). */
    void dropSurface(SurfaceTexture st) {
        if (!run) { st.release(); return; }
        surface = null; dropping = st; surfaceChanged = true;
    }

    void start(String prefer) {
        shown = prefer == null ? "" : prefer;
        run = true;
        t = new Thread(new Runnable() { public void run() { loop(); } }, "video-decode");
        t.start();
    }

    void stop() {
        run = false;
        if (t != null) { t.interrupt(); try { t.join(1500); } catch (InterruptedException ignored) { } t = null; }
        q.clear();
        lastSeen.clear();
        picW = picH = 0;
    }

    List<String> sources() {
        synchronized (lastSeen) {
            List<String> out = new ArrayList<String>();
            long now = System.currentTimeMillis();
            for (Map.Entry<String, Long> e : lastSeen.entrySet()) if (now - e.getValue() < 5000) out.add(e.getKey());
            return out;
        }
    }

    /** The next source with pictures arriving (a group call). */
    void cycle() {
        List<String> s = sources();
        if (s.size() < 2) return;
        int i = s.indexOf(shown);
        shown = s.get((i + 1) % s.size());
        needKey = true;
        Diag.log("[video] showing " + shown);
    }

    /** On the call's video receive thread. */
    void onVideo(String src, Fnav.Video v) {
        long now = System.currentTimeMillis();
        synchronized (lastSeen) {
            lastSeen.put(src, now);
            Long s = lastSeen.get(shown);
            if (shown.isEmpty() || s == null || now - s > 3000) {     // the shown source went quiet: show one that is not
                if (!src.equals(shown)) {
                    shown = src;
                    synchronized (this) { needKey = true; }        // another stream: its next keyframe, not our references
                    Diag.log("[video] showing " + src);
                }
            }
        }
        if (!src.equals(shown)) return;
        if (v.codec != Fnav.CODEC_VP8) { notVp8++; return; }
        if (!q.offer(new Object[]{v})) {                                // the decoder fell behind: back to a keyframe
            q.clear(); dropped++;
            synchronized (this) { needKey = true; }
        }
    }

    private void loop() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        long pts = 0;
        while (run) {
            Object[] item;
            try { item = q.poll(100, TimeUnit.MILLISECONDS); } catch (InterruptedException e) { break; }
            if (surfaceChanged) {
                surfaceChanged = false; release(); synchronized (this) { needKey = true; }
                SurfaceTexture d = dropping; dropping = null;
                if (d != null) d.release();
            }
            if (item == null) { drain(info); continue; }
            Fnav.Video v = (Fnav.Video) item[0];
            byte[] p = v.packet;
            boolean key = p.length >= 10 && (p[0] & 1) == 0 && (p[3] & 0xFF) == 0x9d && (p[4] & 0xFF) == 0x01 && (p[5] & 0xFF) == 0x2a;
            synchronized (this) {
                if (needKey && !key) { dropped++; continue; }
                needKey = false;
            }
            if (key) {
                int w = ((p[7] & 0x3F) << 8) | (p[6] & 0xFF), hh = ((p[9] & 0x3F) << 8) | (p[8] & 0xFF);
                if (dec == null || w != decW || hh != decH) {
                    release();
                    Surface s = surface;
                    if (s == null) { synchronized (this) { needKey = true; } continue; }
                    try {
                        dec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_VP8);
                        MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_VP8, w, hh);
                        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, Math.max(256 * 1024, w * hh));   // a keyframe's worst case
                        dec.configure(f, s, null, 0);
                        dec.start();
                        decW = w; decH = hh;
                        decoderName = dec.getName();
                        Diag.log("[video] decoding " + shown + " at " + w + "x" + hh + " with " + decoderName);
                        setPicture(w, hh);
                    } catch (Exception e) {
                        errors++;
                        Diag.log("[video] cannot decode " + w + "x" + hh + ": " + e);
                        release();
                        synchronized (this) { needKey = true; }
                        continue;
                    }
                }
            }
            if (dec == null) continue;
            try {
                int idx = dec.dequeueInputBuffer(20_000);
                if (idx < 0) { dropped++; synchronized (this) { needKey = true; } continue; }
                ByteBuffer in = dec.getInputBuffer(idx);
                in.clear();
                if (p.length > in.remaining()) {                 // refused whole, and back to the next keyframe
                    dec.queueInputBuffer(idx, 0, 0, 0, 0); errors++;
                    Diag.log("[video] a " + p.length + " B frame exceeds the decoder's " + in.remaining() + " B input");
                    synchronized (this) { needKey = true; }
                    continue;
                }
                in.put(p);
                pts += 41_666;
                dec.queueInputBuffer(idx, 0, p.length, pts, 0);
                drain(info);
            } catch (RuntimeException e) {                              // one frame's problem; the decoder restarts on a key
                errors++;
                release();
                synchronized (this) { needKey = true; }
            }
        }
        release();
        SurfaceTexture d = dropping; dropping = null;
        if (d != null) d.release();
    }

    private void drain(MediaCodec.BufferInfo info) {
        if (dec == null) return;
        try {
            int o;
            while ((o = dec.dequeueOutputBuffer(info, 0)) != MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat f = dec.getOutputFormat();
                    int w = f.getInteger(MediaFormat.KEY_WIDTH), hh = f.getInteger(MediaFormat.KEY_HEIGHT);
                    if (f.containsKey("crop-right") && f.containsKey("crop-left") && f.containsKey("crop-bottom") && f.containsKey("crop-top")) {
                        w = f.getInteger("crop-right") - f.getInteger("crop-left") + 1;
                        hh = f.getInteger("crop-bottom") - f.getInteger("crop-top") + 1;
                    }
                    setPicture(w, hh);
                    continue;
                }
                if (o < 0) continue;
                dec.releaseOutputBuffer(o, true);
                decoded++;
            }
        } catch (RuntimeException e) {
            errors++;
        }
    }

    private void release() {
        if (dec != null) {
            try { dec.stop(); } catch (RuntimeException ignored) { }
            dec.release();
            dec = null;
        }
        decW = decH = 0;
    }

    private void setPicture(final int w, final int hh) {
        picW = w; picH = hh;
        ui.post(new Runnable() { public void run() { fit(); } });
    }

    /** true: the picture fills the screen (centred, the overflow cropped); false: the whole picture, black around it.
     *  Either way its shape is kept. */
    volatile boolean fill = true;

    void fit() {
        int vw = view.getWidth(), vh = view.getHeight();
        if (vw == 0 || vh == 0 || picW == 0 || picH == 0) return;
        float s = fill ? Math.max((float) vw / picW, (float) vh / picH) : Math.min((float) vw / picW, (float) vh / picH);
        Matrix m = new Matrix();
        m.setScale(picW * s / vw, picH * s / vh, vw / 2f, vh / 2f);
        view.setTransform(m);
    }
}
