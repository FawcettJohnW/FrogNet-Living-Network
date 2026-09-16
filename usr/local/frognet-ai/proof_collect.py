#!/usr/bin/env python3
"""proof_collect.py -- fit every arm, check every claim, refuse what it can't.

    python3 proof_collect.py /tmp/proof

Reads the per-rank JSONs from every node and answers, per world size:
  * did every rank run the SAME code
  * is the fit good enough to read
  * what is the slope, per arm
  * how does psychedelic compare to gloo measured in the same session
  * does the stack/shard gap follow W/2 as the byte model predicts

Refusals, not caveats. A run that cannot be trusted is dropped and named.
"""
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

MIN_FIT = 0.98
MIN_RANKS_AGREE = 0.35     # per-size spread across ranks, fraction of median


def fit(points):
    n = len(points)
    if n < 3:
        return None, None, None
    sx = sum(p[0] for p in points); sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points); sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if den == 0:
        return None, None, None
    b = (n * sxy - sx * sy) / den
    a = (sy - b * sx) / n
    pred = [a + b * x for x, _ in points]
    ssr = sum((y - p) ** 2 for (_, y), p in zip(points, pred))
    sst = sum((y - sy / n) ** 2 for _, y in points) or 1.0
    return a, b, 1 - ssr / sst


def load(root):
    runs = defaultdict(list)      # (world, arm, rep) -> [rank json]
    for path in sorted(glob.glob(os.path.join(root, "**", "*.json"),
                                 recursive=True)):
        try:
            j = json.load(open(path))
        except Exception:
            continue
        if "sizes" not in j:
            continue
        d = os.path.basename(os.path.dirname(path))
        parts = d.split(".")
        if len(parts) < 4:
            continue
        world = int(parts[-3][1:]) if parts[-3].startswith("w") else j["world"]
        arm = parts[-2]
        rep = parts[-1]
        runs[(world, arm, rep)].append(j)
    return runs


def shas(j):
    return {u["module"].rsplit(".", 1)[-1]: u["sha"]
            for u in (j.get("under_test") or [])}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/tmp/proof"
    runs = load(root)
    if not runs:
        print("no results under %s" % root)
        return 1

    good = {}
    print("=" * 74)
    print("PER-RUN CHECKS")
    for key in sorted(runs):
        world, arm, rep = key
        rs = runs[key]
        label = "w%d %-18s %s" % (world, arm, rep)

        bad = [r for r in rs if r.get("status") != "ok"]
        if bad:
            err = (bad[0].get("error") or "").strip().splitlines()
            print("  DROP %s -- rank %s failed: %s"
                  % (label, bad[0]["rank"], (err[-1][:60] if err else "?")))
            continue
        if len(rs) != world:
            print("  DROP %s -- %d of %d ranks reported"
                  % (label, len(rs), world))
            continue

        # Every rank must have run the same code. A fleet where one node is
        # behind measures two implementations and reports one number.
        sig = {json.dumps(shas(r), sort_keys=True) for r in rs}
        if len(sig) > 1:
            print("  DROP %s -- ranks ran DIFFERENT code" % label)
            for r in rs:
                s = shas(r)
                print("        rank %s psy=%s plane=%s"
                      % (r["rank"], s.get("psychedelic_backend", "?"),
                         s.get("tensor_plane", "?")))
            continue

        # A collective's ranks finish together. Wide disagreement means they
        # were not measuring the same thing -- launch skew, or a straggler.
        skew_bad = None
        for sz in rs[0]["sizes"]:
            vals = [r["sizes"][sz]["median_ms"] for r in rs if sz in r["sizes"]]
            med = statistics.median(vals)
            if med > 0 and (max(vals) - min(vals)) / med > MIN_RANKS_AGREE:
                skew_bad = (sz, vals)
        if skew_bad:
            print("  DROP %s -- ranks disagree at n=%s: %s"
                  % (label, skew_bad[0],
                     " ".join("%.0f" % v for v in skew_bad[1])))
            continue

        worst = {}
        for r in rs:
            for sz, v in r["sizes"].items():
                worst[int(sz)] = max(worst.get(int(sz), 0), v["median_ms"])
        a, b, r2 = fit(sorted(worst.items()))
        if b is None:
            print("  DROP %s -- too few sizes" % label)
            continue
        if r2 < MIN_FIT:
            print("  DROP %s -- fit %.3f below %.2f" % (label, r2, MIN_FIT))
            continue
        good.setdefault((world, arm), []).append((a, b, r2))
        print("  keep %s  b=%7.1f ns/el  fit %.3f" % (label, b * 1e6, r2))

    if not good:
        print("\nnothing survived the checks.")
        return 1

    print()
    print("=" * 74)
    print("SLOPE BY ARM  (median of kept runs, ns per element)")
    print("%-6s %-20s %6s %10s %10s" % ("world", "arm", "runs", "median", "spread"))
    med = {}
    for (world, arm), vals in sorted(good.items()):
        bs = sorted(v[1] * 1e6 for v in vals)
        m = statistics.median(bs)
        med[(world, arm)] = m
        print("%-6d %-20s %6d %10.1f %9.0f%%"
              % (world, arm, len(bs), m,
                 (bs[-1] - bs[0]) / m * 100 if m else 0))

    print()
    print("=" * 74)
    print("AGAINST GLOO, MEASURED IN THE SAME SESSION")
    print("%-6s %-20s %10s %s" % ("world", "arm", "vs gloo", "reading"))
    for world in sorted({w for w, _ in med}):
        g = med.get((world, "gloo"))
        if g is None:
            print("%-6d no gloo control kept -- ratios cannot be computed"
                  % world)
            continue
        for arm in ("psychedelic-stack", "psychedelic-shard"):
            v = med.get((world, arm))
            if v is None:
                continue
            r = v / g
            print("%-6d %-20s %9.2fx %s"
                  % (world, arm, r,
                     "FASTER than gloo" if r < 1 else "slower than gloo"))

    print()
    print("=" * 74)
    print("BYTE MODEL: stack moves (W-1)n, shard moves 2(W-1)n/W")
    print("            so stack/shard should track W/2")
    print("%-6s %10s %10s %10s %s" % ("world", "stack", "shard", "ratio", "W/2"))
    for world in sorted({w for w, _ in med}):
        s = med.get((world, "psychedelic-stack"))
        h = med.get((world, "psychedelic-shard"))
        if s is None or h is None:
            continue
        pred = world / 2.0
        got = s / h
        if world == 2:
            note = "W=2 cannot discriminate: both move (W-1)n = n"
        elif abs(got - pred) / pred < 0.25:
            note = "tracks the byte model"
        elif got < pred:
            note = "below the model -- shard giving back some of the saving"
        else:
            note = "above the model -- something beyond bytes favours shard"
        print("%-6d %10.1f %10.1f %9.2fx %8.2fx  %s"
              % (world, s, h, got, pred, note))
    print()
    print("If stack/shard tracks W/2, the byte pattern is the mechanism and")
    print("the advantage grows with the fleet. If it is flat, the win is")
    print("concurrency and shard is buying something else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
