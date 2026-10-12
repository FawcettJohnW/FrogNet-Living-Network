// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

/** The size ladder: the media server's SIZES (src/media/transcode.cpp), largest first; the bottom four 4:3 and gray. */
public final class Ladder {
    public static final class Size {
        public final int w, h, bitrate, level; public final boolean gray;
        Size(int w, int h, int b, boolean g, int l) { this.w = w; this.h = h; bitrate = b; gray = g; level = l; }
        public String toString() { return w + "x" + h; }
    }

    public static final Size[] SIZES = {
            new Size(1920, 1080, 3000000, false, 8), new Size(1280, 720, 1200000, false, 7),
            new Size(854, 480, 600000, false, 6), new Size(640, 360, 300000, true, 5),
            new Size(480, 360, 250000, true, 5), new Size(320, 240, 150000, true, 5), new Size(160, 120, 60000, true, 5)};

    private Ladder() { }

    /** The index into SIZES for a rung, no larger than the source; -1 below L5 (no video). */
    public static int sizeForRung(int rung, int srcW, int srcH) {
        if (rung < 5) return -1;
        int i = 8 - rung;
        while (i < 3 && (SIZES[i].w > srcW || SIZES[i].h > srcH)) i++;
        return i;
    }

    /** [EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1] the highest rung this source can fill. */
    public static int ceilingFor(int w, int h) {
        for (int r = 8; r > 4; r--) if (SIZES[8 - r].w <= w && SIZES[8 - r].h <= h) return r;
        return 5;
    }
}
