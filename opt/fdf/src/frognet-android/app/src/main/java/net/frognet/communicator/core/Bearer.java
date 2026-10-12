// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.util.HashMap;
import java.util.Map;

/** The bearer: John's rule, 2026-08-03 ([LADDER_RATE_V2]) -- frogcomms/fnav.py Bearer, line for line. */
public final class Bearer {
    static final int BAD_READS = 3, FPS_DOWN_READS = 3, SEC_DROPS = 5, CLEAN_SECS = 3, BACKLOG_DIRTY = 8;
    static final double FPS_DOWN = 10.0, FPS_DOWN_FRAC = 0.75, FULL_RATE_FRAC = 0.98;
    static final double HOLD_BASE_S = 15.0, HOLD_MAX_S = 300.0, HOLD_CLEAR_S = 60.0;

    public int idx, ceiling, floor;
    public int badReads, slowReads, secDrops, cleanSecs;
    private Double secFpsMin = null;
    private double secT0, heldSince;
    private final Map<Integer, Integer> fails = new HashMap<Integer, Integer>();
    private final Map<Integer, Double> blocked = new HashMap<Integer, Double>();
    public String reason = "";

    public Bearer(int ceiling, int floor, double now) {
        this.idx = ceiling; this.ceiling = ceiling; this.floor = floor;
        secT0 = heldSince = now;
    }

    /** fpsSent null = not yet a measurement (warm-up). */
    public int sample(double now, int backlog, int dropped, Double fpsSent, double fpsTarget, int audioShed) {
        boolean dirty = dropped > 0 || backlog >= BACKLOG_DIRTY;
        badReads = dirty ? badReads + 1 : 0;
        double fpsFloor = FPS_DOWN;
        if (fpsTarget > 0) fpsFloor = Math.max(fpsFloor, fpsTarget * FPS_DOWN_FRAC);
        slowReads = (fpsSent != null && fpsSent < fpsFloor) ? slowReads + 1 : 0;
        secDrops += dropped;
        if (fpsSent != null) secFpsMin = secFpsMin == null ? fpsSent : Math.min(secFpsMin, fpsSent);
        Integer secClosed = null; Boolean secFullRate = null;
        if (now - secT0 >= 1.0) {
            secClosed = secDrops; secFullRate = true;
            if (secFpsMin != null && fpsTarget > 0) secFullRate = secFpsMin >= fpsTarget * FULL_RATE_FRAC;
            cleanSecs = (secClosed == 0 && secFullRate) ? cleanSecs + 1 : 0;
            secDrops = 0; secFpsMin = null; secT0 = now;
        }
        String r = "";
        if (audioShed > 0) r = "audio shed (" + audioShed + ")";
        else if (badReads > BAD_READS) r = badReads + " consecutive reads with drops";
        else if (secClosed != null && secClosed > SEC_DROPS) r = secClosed + " drops in one second";
        else if (slowReads > FPS_DOWN_READS) r = "below the frame-rate floor for " + slowReads + " reads";
        if (!r.isEmpty()) {
            int f = (fails.containsKey(idx) ? fails.get(idx) : 0) + 1;
            fails.put(idx, f);
            blocked.put(idx, now + Math.min(HOLD_MAX_S, HOLD_BASE_S * Math.pow(2.0, f - 1)));
            reason = r;
            badReads = slowReads = cleanSecs = secDrops = 0;
            secFpsMin = null;
            if (idx > floor) { idx--; heldSince = now; }
            return idx;
        }
        if (cleanSecs > 0 && now - heldSince >= HOLD_CLEAR_S && fails.containsKey(idx)) { fails.remove(idx); blocked.remove(idx); }
        if (cleanSecs >= CLEAN_SECS && idx < ceiling) {
            int target = idx + 1;
            Double until = blocked.get(target);
            if (until != null && now < until) reason = "holding L" + target;
            else { idx = target; heldSince = now; cleanSecs = 0; reason = ""; }
        } else if (secClosed != null && secClosed == 0 && Boolean.FALSE.equals(secFullRate)) {
            reason = "holding: not at full frame rate";
        }
        return idx;
    }

    public void resumeAt(int i, double now) {
        idx = i;
        badReads = slowReads = secDrops = cleanSecs = 0;
        secFpsMin = null; secT0 = now; heldSince = now;
        reason = "resumed at L" + i + " after the bottom walk";
    }
}
