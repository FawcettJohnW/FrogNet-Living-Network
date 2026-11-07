// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart.core;

import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * The track and the driving rules, exactly kartdemo.py's: every game draws every kart from the same shared x/y, so the
 * oval, the speeds and the dead reckoning must be the same numbers in every game. [KART_AUTODRIVE_V1] [KART_WRITES_ON_CHANGE_V1]
 */
public final class Kart {
    // the track, in the game's layout units (kartdemo.py: W, H = 1180, 720; TRACK_W = 860)
    public static final double TRACK_W = 860, H = 720;
    public static final double CX = TRACK_W / 2, CY = H / 2, RX = TRACK_W / 2 - 120, RY = H / 2 - 110, ROAD = 62;
    public static final double VMAX = 240.0, ACC = 260.0, TURN = 2.8, FRICT = 0.92;
    public static final double TOL_XY = 2.0, TOL_H = 0.05;
    static final double TAU = 2 * Math.PI;

    public static double[] trackPoint(double t) { return new double[]{CX + RX * Math.cos(t), CY + RY * Math.sin(t)}; }
    public static double nearestT(double x, double y) { return Math.atan2((y - CY) / RY, (x - CX) / RX); }
    static double rem(double a) { return Math.IEEEremainder(a, TAU); }               // math.remainder(a, 2*pi)

    /** where readers draw a kart: its last written truth carried along its arc (heading, speed, turn rate) */
    public static double[] carried(Map<String, Object> b, double dt) {
        double x = Json.dbl(b.get("x")), y = Json.dbl(b.get("y")), h = Json.dbl(b.get("h")), v = Json.dbl(b.get("v")), w = Json.dbl(b.get("w"));
        if (Math.abs(w) < 1e-6) return new double[]{x + Math.cos(h) * v * dt, y + Math.sin(h) * v * dt, h};
        double h2 = h + w * dt;
        return new double[]{x + v / w * (Math.sin(h2) - Math.sin(h)), y - v / w * (Math.cos(h2) - Math.cos(h)), h2};
    }

    // ---- one kart this game drives, and is the only writer of: race.kart.<name>
    public final String name;
    public final int[] color;
    public final boolean robot;
    public final double skill;
    public double x, y, h, v, w;
    public int lap;
    double lane, dodge, wander, wanderRate, lastT, unwrapped;
    Map<String, Object> written = null;
    double writtenAt = 0;
    int writtenGen = -1;

    public Kart(String name, int[] color, boolean robot, double skill, Random rnd) {
        this.name = name; this.color = color; this.robot = robot; this.skill = skill;
        wander = rnd.nextDouble() * 6.28; wanderRate = 0.3 + rnd.nextDouble() * 0.4;
    }

    /** its slot on the grid the race server wrote: {slot, t0, lane, ...} */
    public void place(Map<String, Object> g) {
        double t0 = Json.dbl(g.get("t0")), ln = Json.dbl(g.get("lane"));
        double[] s = trackPoint(t0);
        double nx = Math.cos(t0 + Math.PI / 2), ny = Math.sin(t0 + Math.PI / 2);
        x = s[0] + nx * ln; y = s[1] + ny * ln; h = t0 + Math.PI / 2; v = 0; w = 0; lap = 0; lane = ln;
        lastT = nearestT(x, y); unwrapped = 0; written = null; writtenGen = -1;
    }

    /** steer -1..1, gas -1..1 (kartdemo.py drive) */
    public void drive(double steer, double gas, double dt) {
        v += gas * ACC * dt;
        if (gas == 0) v *= FRICT;
        if (gas == 0 && Math.abs(v) < 1.0) v = 0;
        v = Math.max(-VMAX * 0.4, Math.min(VMAX, v));
        w = steer * TURN * (Math.abs(v) > 5 ? v / VMAX : 0);
        h += w * dt;
        x += Math.cos(h) * v * dt; y += Math.sin(h) * v * dt;
        x = Math.max(10, Math.min(TRACK_W - 10, x)); y = Math.max(10, Math.min(H - 10, y));
        double tt = nearestT(x, y), d = rem(tt - lastT);
        unwrapped += d; lastT = tt;
        if (unwrapped >= TAU) { unwrapped -= TAU; lap++; }
    }

    /** {steer, gas}: kartdemo.py's autopilot -- follow a wandering lane, dodge a kart ahead */
    public double[] autopilot(double dt, List<double[]> others) {
        double fx = Math.cos(h), fy = Math.sin(h), blocked = 0;
        for (double[] o : others) {
            double dx = o[0] - x, dy = o[1] - y, ahead = dx * fx + dy * fy, side = -dx * fy + dy * fx;
            if (ahead > 0 && ahead < 70 && Math.abs(side) < 24) blocked = side > 0 ? -1.0 : 1.0;
        }
        if (blocked != 0) dodge += (blocked * 30.0 - dodge) * Math.min(1.0, dt * 4.0);
        else dodge += -dodge * Math.min(1.0, dt * 1.5);
        wander += dt * wanderRate;
        double ln = Math.max(-(ROAD - 16), Math.min(ROAD - 16, lane + 14 * Math.sin(wander) + dodge));
        double tt = nearestT(x, y) + 0.32;
        double tx = CX + (RX + ln) * Math.cos(tt), ty = CY + (RY + ln) * Math.sin(tt);
        double err = rem(Math.atan2(ty - y, tx - x) - h);
        double want = skill * VMAX * (1.0 - 0.35 * Math.min(1.0, Math.abs(err))) * (blocked != 0 ? 0.8 : 1.0);
        return new double[]{Math.max(-1.0, Math.min(1.0, err * 3.0)), v < want ? 1 : 0};
    }

    /** [KART_WRITES_ON_CHANGE_V1] write only when this kart departs from where readers draw it (its last write carried
     *  forward), or its lap changes, or the session is new. */
    public boolean publish(Region race, String machine, String owner, double now) {
        if (written != null && writtenGen == race.gen()) {
            double[] p = carried(written, now - writtenAt);
            if (Math.hypot(x - p[0], y - p[1]) <= TOL_XY && Math.abs(rem(h - p[2])) <= TOL_H && lap == Json.num(written.get("lap"))) return false;
        }
        Map<String, Object> bag = Json.o("x", x, "y", y, "h", h, "v", v, "w", w, "lap", (long) lap, "name", name,
                "machine", machine, "color", Arrays.asList((Object) (long) color[0], (long) color[1], (long) color[2]),
                "robot", robot, "owner", owner);
        int g = race.gen();
        if (race.write("kart", name, bag, true)) { written = bag; writtenAt = now; writtenGen = g; return true; }
        return false;
    }
}
