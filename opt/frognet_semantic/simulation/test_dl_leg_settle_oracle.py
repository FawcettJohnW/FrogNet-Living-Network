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
"""test_dl_leg_settle_oracle.py - the downlink leg drives a loop with real latency: publish
leg_request -> server reads the tuple -> server subsets -> fewer frames arrive -> intake depth
falls. On the box the controller re-decided every tick before the change landed, so it dove
L7->L0 and then climbed all the way back, oscillating. The settle cooldown (DL_LEG_SETTLE_S)
must make it converge to a stable level instead.

Models the loop with a LATENCY between a requested rung and its effect on depth, drives the REAL
LegController, and contrasts no-settle vs settle:
  G1 no settle: leg visits BOTH extremes (dives to floor AND climbs high) - oscillation
  G2 settle: after warmup the leg converges to a tight band and never collapses to the floor
  G3 settle: far fewer direction changes than no-settle
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import sotf_leg_adapt as A, sotf_ladder as L

MAXQ = 15
CONGEST = MAXQ // 2      # is_congested threshold
SUST = 3                 # rung the link actually sustains; above it depth grows, below it drains
LAT = 2                  # ticks between a requested rung and its effect on depth
SETTLE_TICKS = 4         # ~DL_LEG_SETTLE_S at the ~1Hz tick
TICKS = 200

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


def new_leg():
    allowed = L.allowed_levels(True, True)
    ceiling = L.ceiling(True, True)
    return A.LegController("self", ceiling_idx=ceiling, allowed=allowed,
                           start_idx=5, recover_after=2)


def simulate(settle: bool):
    lc = new_leg()
    depth = 0
    eff = [lc.served_rung()] * LAT      # pipeline: requested rungs in flight to the server
    last_change = -999
    legs = []
    for t in range(TICKS):
        # depth responds to the EFFECTIVE rung (the one that has reached the server)
        cur_eff = eff.pop(0)
        depth = max(0, min(MAXQ, depth + (cur_eff - SUST)))
        shedding = depth >= MAXQ
        prev = lc.served_rung()
        decide = (not settle) or (t - last_change >= SETTLE_TICKS)
        if decide:
            congested = shedding or depth >= CONGEST
            new = lc.on_congestion() if congested else lc.on_healthy()
            if new != prev:
                last_change = t
        eff.append(lc.served_rung())     # newly requested rung enters the pipeline
        legs.append(lc.served_rung())
    return legs


def changes(seq):
    d = [1 if seq[i] != seq[i-1] else 0 for i in range(1, len(seq))]
    return sum(d)


def main():
    no = simulate(settle=False)
    ys = simulate(settle=True)

    # G1 no-settle oscillates across the whole range: collapses to the audio-only floor AND
    # climbs back into the top rungs - the L7<->L0 thrash seen on the box.
    ck("G1 no-settle dives to the floor AND climbs high (oscillation)",
       min(no) == 0 and max(no) >= 6, f"min={min(no)} max={max(no)}")

    half = ys[len(ys)//2:]
    floor_no = sum(1 for x in no if x == 0)
    floor_ys = sum(1 for x in ys if x == 0)
    ck("G2 settle: does not collapse to the audio-only floor once settled",
       min(half) >= 1, f"min_secondhalf={min(half)}")
    ck("G2 settle: spends far less time pinned at the floor than no-settle",
       floor_ys * 3 <= floor_no, f"floor_settle={floor_ys} floor_no_settle={floor_no}")
    ck("G3 settle: far fewer leg changes than no-settle",
       changes(ys) * 2 < changes(no), f"settle_changes={changes(ys)} no_settle_changes={changes(no)}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
