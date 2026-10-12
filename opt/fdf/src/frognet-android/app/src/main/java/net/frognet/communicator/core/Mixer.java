// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * fnphone_pa's mixer (as the C++ and Python Mixer): one buffer per remote source, a playout cushion per source, the
 * backlog capped at 2x the cushion (at least 240 ms); trims and pads COUNTED ([MIXER_DAMAGE_IS_COUNTED_V1]).
 * 16 kHz mono s16 (little-endian shorts here).
 */
public final class Mixer {
    public static final int RATE = 16000, BLOCK_MS = 20, BLOCK_SAMPLES = RATE * BLOCK_MS / 1000;

    private static final class Buf { short[] a = new short[RATE]; int n = 0; boolean started = false; }
    private final Map<String, Buf> bufs = new LinkedHashMap<String, Buf>();
    private final int jitter, max;
    public long trimmed, trims, pads, padSamples, dry, outBlocks;

    public Mixer(int cushionMs) {
        jitter = RATE * cushionMs / 1000;
        max = Math.max(RATE * 240 / 1000, jitter * 2);
    }

    public synchronized void feed(String src, short[] pcm, int len) {
        Buf b = bufs.get(src);
        if (b == null) { b = new Buf(); bufs.put(src, b); }
        if (b.n + len > b.a.length) { short[] na = new short[Math.max(b.a.length * 2, b.n + len)]; System.arraycopy(b.a, 0, na, 0, b.n); b.a = na; }
        System.arraycopy(pcm, 0, b.a, b.n, len);
        b.n += len;
        if (b.n > max) {
            int drop = b.n - max;
            System.arraycopy(b.a, drop, b.a, 0, b.n - drop);
            b.n -= drop;
            trimmed += drop; trims++;
        }
    }

    public synchronized void forget(String src) { bufs.remove(src); }

    /** One 20 ms block: every started source's next block summed with clipping; a dry source is silence. */
    public synchronized short[] pullBlock() {
        int[] acc = new int[BLOCK_SAMPLES];
        int short_ = 0;
        for (Buf b : bufs.values()) {
            if (!b.started) {
                if (b.n < jitter) continue;               // still filling the cushion
                b.started = true;
            }
            int take = Math.min(BLOCK_SAMPLES, b.n);
            if (take == 0) { dry++; continue; }
            for (int k = 0; k < take; k++) acc[k] += b.a[k];
            System.arraycopy(b.a, take, b.a, 0, b.n - take);
            b.n -= take;
            if (take < BLOCK_SAMPLES) short_ += BLOCK_SAMPLES - take;
        }
        if (short_ > 0) { pads++; padSamples += short_; }
        short[] out = new short[BLOCK_SAMPLES];
        for (int k = 0; k < BLOCK_SAMPLES; k++) out[k] = (short) Math.max(-32768, Math.min(32767, acc[k]));
        outBlocks++;
        return out;
    }
}
