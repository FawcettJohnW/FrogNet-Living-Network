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
"""test_spatial_band_oracle.py - the SENDER's spatial-band controller (sotf_band) governs the
expensive encoder-restart axis. It must: open at the conservative floor, DROP fast when the
uplink can't sustain the band (encoder behind -> fps collapse, OR send queue backed up), CLIMB
only after sustained health (hysteresis, so restarts are rare and never thrash), treat a wedged
encoder (fps~0) as overload, and honor a weakest-consumer ceiling clamp.

Pure model test - no ffmpeg, no sockets.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import sotf_band as B

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")

GOOD = (30, 0)        # healthy sample: high fps, empty send queue
BAD = (5, 99)         # overload sample: fps below floor, queue over ceiling
WEDGED = (0, 0)       # encoder emitting nothing
NEUTRAL = (17, 30)    # between floor/good and between ok/ceil - neither


def feed(c, sample, n):
    out = c.idx
    for _ in range(n):
        out = c.on_sample(*sample)
    return out


def main():
    # S1 conservative start
    c = B.BandController()
    ck("S1 opens at the conservative floor (band 0)", c.idx == 0, f"idx={c.idx}")

    # S2 climb is slow + hysteretic: nothing until UP_AFTER, then one step
    c = B.BandController(start=0)
    feed(c, GOOD, B.UP_AFTER - 1)
    ck("S2 no climb before UP_AFTER healthy samples", c.idx == 0, f"idx={c.idx}")
    c.on_sample(*GOOD)
    ck("S2 climbs one band at UP_AFTER", c.idx == 1, f"idx={c.idx}")
    feed(c, GOOD, B.UP_AFTER)
    ck("S2 climbs to top and clamps", c.idx == B.MAX_BAND, f"idx={c.idx}")
    feed(c, GOOD, B.UP_AFTER)
    ck("S2 never exceeds MAX_BAND", c.idx == B.MAX_BAND, f"idx={c.idx}")

    # S3 drop is fast: DOWN_AFTER overload samples step down; floors at 0
    c = B.BandController(start=B.MAX_BAND)
    feed(c, BAD, B.DOWN_AFTER)
    ck("S3 drops one band at DOWN_AFTER overload", c.idx == B.MAX_BAND - 1, f"idx={c.idx}")
    feed(c, BAD, B.DOWN_AFTER * (B.MAX_BAND + 2))
    ck("S3 floors at band 0", c.idx == 0, f"idx={c.idx}")

    # S4 wedged encoder counts as overload
    c = B.BandController(start=B.MAX_BAND)
    feed(c, WEDGED, B.DOWN_AFTER)
    ck("S4 wedged encoder (fps~0) drops the band", c.idx < B.MAX_BAND, f"idx={c.idx}")

    # S5 react-fast-but-not-instant: a single overload blip does not drop
    c = B.BandController(start=B.MAX_BAND)
    c.on_sample(*BAD)
    c.on_sample(*GOOD)            # health resets the bad streak
    ck("S5 single overload blip does NOT drop (needs DOWN_AFTER consecutive)",
       c.idx == B.MAX_BAND, f"idx={c.idx}")

    # S6 no thrash under alternating noise
    c = B.BandController(start=1)
    for i in range(40):
        c.on_sample(*(BAD if i % 2 else GOOD))
    ck("S6 alternating overload/health does not thrash the band", c.idx == 1, f"idx={c.idx}")

    # S7 neutral zone holds station
    c = B.BandController(start=1)
    feed(c, NEUTRAL, 30)
    ck("S7 neutral samples hold the band", c.idx == 1, f"idx={c.idx}")

    # S8 consumer-ceiling clamp
    c = B.BandController(start=B.MAX_BAND)
    c.set_ceiling(0)
    ck("S8 tightened ceiling forces immediate drop", c.idx == 0, f"idx={c.idx}")
    c = B.BandController(start=0, ceiling=1)
    feed(c, GOOD, B.UP_AFTER * 4)
    ck("S8 health cannot climb past the consumer ceiling", c.idx == 1, f"idx={c.idx}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
