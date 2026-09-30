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
test_force_ceiling_oracle.py - the demo FORCE LOW-SPEED lever actually pins the rung.

force_ceiling on the producer caps the served rung at or below a chosen level, regardless
of measured bandwidth (bearer). This guards the demo control: 'force low-speed' must hold
the stream at the grayscale floor even on a fat link.

Proves:
  F1  no force, no bearer -> serves the capability ceiling (L7)
  F2  force_ceiling=5 on a fat link (bearer high) -> serves L5 (pinned down, the demo)
  F3  force_ceiling=4 -> serves L4 audio-only (video shed) even though camera/mic present
  F4  force None -> back to adaptive (serves ceiling again)
  F5  lower-of-two: force_ceiling=6 but bearer=5 -> serves L5 (bearer still wins when lower)
  F6  force_ceiling never RAISES above capability ceiling (force=7 on audio-only node stays L4)
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import sotf_ladder as L
import media_stream as MS

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


class FakeControl:
    """Minimal control exposing a settable bearer (what the producer reads)."""
    def __init__(self): self._b = None
    def bearer(self): return self._b
    def set_bearer(self, b): self._b = b


def make_producer(have_camera=True, have_mic=True):
    # Build a MediaProducer-like object far enough to exercise send_level. We only need
    # ladder + have_camera/have_mic + control + force_ceiling; construct minimally.
    p = MS.MediaProducer.__new__(MS.MediaProducer)
    p.ladder = L
    p.have_camera = have_camera
    p.have_mic = have_mic
    p.control = FakeControl()
    p.force_ceiling = None
    return p


print("=== force-low-speed (force_ceiling) demo lever ===")
p = make_producer(have_camera=True, have_mic=True)

# F1 baseline
ck("F1 no force, no bearer -> ceiling L7", p.send_level() == 7, p.send_level())

# F2 force down on a fat link
p.control.set_bearer(7)              # link is great
p.force_ceiling = 5
ck("F2 force_ceiling=5 on fat link -> L5 grayscale floor", p.send_level() == 5, p.send_level())

# F3 force to audio-only
p.force_ceiling = 4
ck("F3 force_ceiling=4 -> L4 audio-only", p.send_level() == 4, p.send_level())

# F4 release
p.force_ceiling = None
ck("F4 force None -> adaptive, back to L7", p.send_level() == 7, p.send_level())

# F5 lower-of-two: force above the bearer; bearer still caps
p.control.set_bearer(5)
p.force_ceiling = 6
ck("F5 force=6 but bearer=5 -> L5 (lower wins)", p.send_level() == 5, p.send_level())

# F6 force can't raise above capability ceiling (audio-only node: no camera -> ceiling L4)
pa = make_producer(have_camera=False, have_mic=True)
pa.control.set_bearer(7)
pa.force_ceiling = 7                  # try to force HIGH
ck("F6 force can't exceed capability ceiling (no camera -> L4)",
   pa.send_level() == 4, pa.send_level())

print(f"\n=== {_p} passed, {_f} failed ===")
print("ORACLE GREEN" if _f == 0 else "ORACLE RED")
sys.exit(1 if _f else 0)
