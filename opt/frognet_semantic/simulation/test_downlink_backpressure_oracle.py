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
"""test_downlink_backpressure_oracle.py - the downlink feeder must NOT write frames into the
decoder faster than they decode. ffmpeg reads ahead, so a free-running feeder buries the
backlog in ffmpeg's opaque input buffer: lag grows unbounded (10-15s on the box), the intake
stays empty so it never sheds, and the leg loop reads "healthy" and climbs to full rate.

Credit-based backpressure (live: feeder gates on writes-decoded <= FEED_CREDIT) keeps ffmpeg's
in-flight backlog tiny so the surplus sits in the keyframe-aware intake instead - bounding lag
to ~(credit+maxq) frames, making the intake SHED (clean keyframe resync), and turning intake
depth into a truthful congestion signal for _auto_adapt_downlink.

Proves, against the REAL DecodeIntake, arrival faster than decode:
  B1 ungated control: ffmpeg backlog grows unbounded; intake never sheds (dead signal)
  B2 gated: in-flight bounded to ~FEED_CREDIT
  B3 gated: surplus moves into the intake -> it SHEDS (drops>0) -> congestion signal fires
  B4 gated: total lag bounded to ~(FEED_CREDIT + maxq), not unbounded
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import sotf_downlink_intake as DI

FEED_CREDIT = 3        # mirrors communicator.FEED_CREDIT
GOP = 12               # keyframe cadence (frames)
ARRIVE = 6             # frames arriving per tick
DECODE = 3             # frames the decoder drains per tick (slower than arrival)
TICKS = 200

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


def simulate(gated: bool):
    """Returns (max_inflight, intake.drops, max_total_lag, congested_ever)."""
    intake = DI.DecodeIntake()
    written = decoded = idx = 0
    max_inflight = max_lag = 0
    congested = False
    for _t in range(TICKS):
        for _ in range(ARRIVE):
            idx += 1
            intake.put(b"x", (idx % GOP) == 0)
        # feeder
        if gated:
            while (written - decoded) <= FEED_CREDIT:
                g = intake.get()
                if g is None:
                    break
                written += 1
        else:
            while True:                       # free-running: drain everything into ffmpeg
                g = intake.get()
                if g is None:
                    break
                written += 1
        inflight = written - decoded
        max_inflight = max(max_inflight, inflight)
        max_lag = max(max_lag, inflight + intake.depth())
        if DI.is_congested(intake.depth()):
            congested = True
        decoded = min(written, decoded + DECODE)   # decoder drains DECODE/tick
    return max_inflight, intake.drops, max_lag, congested


def main():
    # control: free-running feeder
    ci, cd, cl, _cc = simulate(gated=False)
    ck("B1 ungated: ffmpeg backlog grows unbounded", ci > 100, f"max_inflight={ci}")
    ck("B1 ungated: intake never sheds (dead congestion signal)", cd == 0, f"drops={cd}")

    # gated: credit backpressure
    gi, gd, gl, gc = simulate(gated=True)
    ck("B2 gated: in-flight bounded to ~FEED_CREDIT", gi <= FEED_CREDIT + 1, f"max_inflight={gi}")
    ck("B3 gated: surplus moves to intake -> it SHEDS", gd > 0, f"drops={gd}")
    ck("B3 gated: congestion signal fires", gc, "intake never crossed half-full")
    ck("B4 gated: total lag bounded to ~(credit+maxq)", gl <= FEED_CREDIT + DI.DEFAULT_MAXQ + ARRIVE,
       f"max_lag={gl} vs bound {FEED_CREDIT + DI.DEFAULT_MAXQ + ARRIVE}")
    ck("B4 gated lag << ungated lag", gl < cl // 4, f"gated={gl} ungated={cl}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
