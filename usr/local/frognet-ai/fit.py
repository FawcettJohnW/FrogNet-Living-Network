#!/usr/bin/env python3
"""fit.py -- fit a range of runs and compare the arms honestly.

    python3 fit.py /tmp/nb3 mesh200 mesh208

Fits on MINIMA by default, and says why: on this mesh the tail is long and
time-localised, so a median over 15 samples lands inside whatever queueing
happened during that block. One measured run had a median fit of 0.013 and a
median slope three times below its own minimum slope -- the median was not
measuring the collective, it was measuring the minute.

The minimum is the closest thing to the cost of moving those bytes with
nothing in the way. Everything above it is contention, which is worth
reporting separately rather than averaging into the number.
"""
import glob
import json
import os
import statistics
import sys


def fit(points):
    n = len(points)
    if n < 3:
        return None, None, None
    sx = sum(p[0] for p in points); sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points); sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if den == 0:
        return None, None, None
    b = (n * sxy - sx * sy) / den
    a = (sy - b * sx) / n
    pred = [a + b * x for x, _ in points]
    ssr = sum((y - p) ** 2 for (_, y), p in zip(points, pred))
    sst = sum((y - sy / n) ** 2 for _, y in points) or 1.0
    return a, b, 1 - ssr / sst


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nb3"
    lo = int(sys.argv[2].replace("mesh", "")) if len(sys.argv) > 2 else 0
    hi = int(sys.argv[3].replace("mesh", "")) if len(sys.argv) > 3 else 10 ** 9

    runs = {}
    for f in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        base = os.path.basename(f)
        if not base.startswith("mesh") or ".r" not in base:
            continue
        try:
            n = int(base.split(".r")[0].replace("mesh", ""))
        except ValueError:
            continue
        if not (lo <= n <= hi):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("sizes"):
            runs.setdefault(n, []).append(d)

    if not runs:
        print("no runs in %s between mesh%d and mesh%d" % (root, lo, hi))
        return 1

    per_arm = {}
    print("%-9s %-20s %-3s %10s %8s %10s %8s" %
          ("run", "arm", "W", "min ns/el", "fit", "med ns/el", "fit"))
    for n in sorted(runs):
        rs = [d for d in runs[n]
              if d.get("status") == "ok" and len(d.get("sizes") or {}) >= 3]
        if not rs:
            print("mesh%-5d (incomplete: %d/%d ranks ok)"
                  % (n, len(rs), runs[n][0]["world"]))
            continue
        W = rs[0]["world"]
        if len(rs) != W:
            print("mesh%-5d (only %d of %d ranks reported -- dropped)"
                  % (n, len(rs), W))
            continue
        arm = "%s/%s" % (rs[0]["backend"], rs[0].get("reduce") or "-")
        row = []
        for stat in ("min_ms", "median_ms"):
            worst = {}
            for d in rs:
                for k, v in d["sizes"].items():
                    worst[int(k)] = max(worst.get(int(k), 0), v[stat])
            row.append(fit(sorted(worst.items())))
        (amin, bmin, rmin), (amed, bmed, rmed) = row
        print("mesh%-5d %-20s %-3d %10.0f %8.3f %10.0f %8.3f"
              % (n, arm, W, bmin * 1e6, rmin, bmed * 1e6, rmed))
        per_arm.setdefault((W, arm), []).append(bmin * 1e6)

    print()
    print("=" * 70)
    print("SLOPE BY ARM, minima (ns per element)")
    print("%-3s %-20s %5s %10s %10s %10s" %
          ("W", "arm", "runs", "median", "lowest", "highest"))
    med = {}
    for (W, arm), vals in sorted(per_arm.items()):
        v = sorted(vals)
        med[(W, arm)] = statistics.median(v)
        print("%-3d %-20s %5d %10.0f %10.0f %10.0f"
              % (W, arm, len(v), statistics.median(v), v[0], v[-1]))

    worlds = sorted({w for w, _ in med})
    for W in worlds:
        g = med.get((W, "gloo/-"))
        if g is None:
            continue
        print()
        print("world %d, against gloo measured in the same runs:" % W)
        for arm in ("psychedelic/shard", "psychedelic/stack"):
            v = med.get((W, arm))
            if v:
                print("  %-20s %.2fx   %s" % (arm, v / g,
                      "FASTER" if v < g else "slower"))

    if len(worlds) >= 2:
        lo_w, hi_w = worlds[0], worlds[-1]
        print()
        print("=" * 70)
        print("WHAT THE EXTRA NODE COSTS EACH ARM  (world %d -> world %d)"
              % (lo_w, hi_w))
        print("This is the comparison that survives the mesh changing between")
        print("sessions: a ratio of ratios, both halves measured together.")
        for arm in sorted({a for _, a in med}):
            a1, a2 = med.get((lo_w, arm)), med.get((hi_w, arm))
            if a1 and a2:
                print("  %-20s %8.0f -> %8.0f   %.1fx" % (arm, a1, a2, a2 / a1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
