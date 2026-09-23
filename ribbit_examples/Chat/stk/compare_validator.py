#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""compare_validator.py REFERENCE.log CANDIDATE.log [--pos-tol M] [--vel-tol M/S]

Two logs from StateValidator: the stock run and the run under test. Rows are
matched on (tick, kart). Reports, per kart and overall: rows compared, rows
missing on either side, the worst and the mean position error, the worst
velocity error, the tick at which the worst occurred, and whether every kart
finished in both. Exit 1 if any kart exceeds tolerance or rows are missing.

A third mode,  --self REFERENCE.log OTHER_REFERENCE.log , is the same
arithmetic between two STOCK runs: that is the noise floor the tolerance has
to come from, not a number picked in advance.
"""
import math, sys


def load(path):
    rows = {}
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        f = line.split()
        rows[(int(f[0]), int(f[1]))] = [float(x) for x in f[2:]]
    return rows


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    opt = dict(a.lstrip("-").split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    if len(args) != 2:
        sys.exit(__doc__)
    pos_tol, vel_tol = float(opt.get("pos-tol", 0.5)), float(opt.get("vel-tol", 2.0))
    ref, cand = load(args[0]), load(args[1])
    karts = sorted({k for _, k in ref} | {k for _, k in cand})
    bad = False
    print("kart  rows  missing  worst_pos(m) @tick   mean_pos(m)  worst_vel(m/s)  finished ref/cand")
    for k in karts:
        keys = sorted(t for t, kk in ref if kk == k)
        missing = sum(1 for t in keys if (t, k) not in cand) + sum(1 for t, kk in cand if kk == k and (t, k) not in ref)
        worst = (0.0, -1); total = 0.0; n = 0; wv = 0.0
        for t in keys:
            c = cand.get((t, k))
            if c is None:
                continue
            r = ref[(t, k)]
            dp = math.dist(r[0:3], c[0:3]); dv = math.dist(r[7:10], c[7:10])
            total += dp; n += 1; wv = max(wv, dv)
            if dp > worst[0]:
                worst = (dp, t)
        fin_r = max((ref[(t, k)][16] for t in keys), default=0)
        fin_c = max((c[16] for (t, kk), c in cand.items() if kk == k), default=0)
        fail = missing or worst[0] > pos_tol or wv > vel_tol or fin_r != fin_c
        bad |= bool(fail)
        print("%4d %5d %8d  %11.3f %6d  %11.3f  %14.3f  %d/%d%s"
              % (k, n, missing, worst[0], worst[1], total / n if n else 0.0, wv, fin_r, fin_c, "   <-- FAIL" if fail else ""))
    print("\n%s  (pos tolerance %.3f m, vel tolerance %.3f m/s)" % ("FAIL" if bad else "PASS", pos_tol, vel_tol))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
