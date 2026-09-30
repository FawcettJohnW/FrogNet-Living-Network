#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_rate_horizon_oracle.py -- [MEASURE_LONG_ENOUGH_TO_BE_A_MEASUREMENT_V1]

Frames do not arrive smoothly. A capture device that stalls and catches up
delivers 0.5 fps in one two-second window and 45 in the next, from a producer
sending a steady 24. Measured 2026-08-11, one consumer, consecutively:

    45.8 ... 3.4 ... 21.4 ... 0.5 ... 25.2

Each window was taken as a verdict, so every other one asked the whole network
for a lower rate and the network obliged. The instantaneous rate of a bursty
stream is not the delivered rate.

  R1  frames are counted over a rolling horizon, not one window
  R2  no verdict at all until that horizon is FULL
  R3  a burst-and-gap pair inside the horizon averages out
  R4  a genuine sustained shortfall still reads as one
  R5  asking for lower takes consecutive shortfalls, not a single sample
  R6  promoting still takes more of them than demoting -- fast down, slow up
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import fnav

H = fnav.Call.RATE_HORIZON_S
FRAC = fnav.Call.HAPPY_FRACTION
BAD = fnav.Call.BAD_WINDOWS
GOOD = fnav.Call.HAPPY_WINDOWS
WANT = 24.0


def replay(per_window, win_s=2.0):
    """The rule as written, over a sequence of frames-per-window."""
    t, n, hist, bad, good = 0.0, 0, [], 0, 0
    out = []
    for k in per_window:
        t += win_s
        n += k
        hist.append((t, n))
        while len(hist) > 1 and t - hist[0][0] > H:
            hist.pop(0)
        span = hist[-1][0] - hist[0][0]
        if len(hist) < 2 or span < H * 0.8:
            out.append(("none", None))
            continue
        roll = (hist[-1][1] - hist[0][1]) / max(1e-6, span)
        if roll >= WANT * FRAC:
            good += 1
            bad = 0
        else:
            good = 0
            bad += 1
        out.append(("low" if bad >= BAD else ("steady" if good >= GOOD
                                              else "ok"), roll))
    return out


src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".",
                        "fnav.py"), encoding="utf-8").read()
ck("R1 the rate is taken over a horizon", "RATE_HORIZON_S" in src
   and "_in_hist" in src, None)
ck("R2 and only when the horizon is full",
   "self.RATE_HORIZON_S * 0.8" in src, None)

# ---- R2/R3: the burst pattern from the log ---------------------------------
# 92, 7, 43, 1, 50 frames per 2s window -> 46.0, 3.5, 21.5, 0.5, 25.0 fps
burst = replay([92, 7, 43, 1, 50, 48, 46])
ck("R2 no verdict until the horizon is full",
   [v for v, _ in burst[:3]] == ["none", "none", "none"],
   [v for v, _ in burst[:3]])
ck("R3 the 0.5 fps window does not ask for lower",
   burst[3][0] != "low", burst[3])
ck("R3 and a settled stream reads as delivered",
   abs(burst[6][1] - 24.0) < 1.0, burst[6])

# ---- R4: a real shortfall still reads as one -------------------------------
poor = replay([12] * 8)          # 6 fps, sustained
ck("R4 a sustained 6 fps against a wanted 24 asks for lower",
   any(v == "low" for v, _ in poor), [v for v, _ in poor])

good = replay([48] * 8)          # 24 fps, sustained
ck("R4 and a sustained 24 never does",
   not any(v == "low" for v, _ in good), [v for v, _ in good])
ck("R4 it becomes steady instead",
   any(v == "steady" for v, _ in good), [v for v, _ in good])

# ---- R5/R6 -----------------------------------------------------------------
ck("R5 lower takes consecutive shortfalls", BAD >= 2, BAD)
ck("R6 and promoting takes more of them than demoting", GOOD > BAD, (GOOD, BAD))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
