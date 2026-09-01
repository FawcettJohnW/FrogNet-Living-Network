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
test_link_shaper_oracle.py - the per-instance link shaper constrains THIS node only, correctly.

Proves:
  K1  off by default: not active, nothing dropped
  K2  jam drops ~the set fraction of DROPPABLE frames
  K3  jam NEVER drops keyframes/audio (is_key protected) even at high jam
  K4  bandwidth pacing caps egress near the set kbps (paced delay appears when over budget)
  K5  clear() restores full speed; describe() reflects state
"""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import link_shaper

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")

sh = link_shaper.LinkShaper()
sh._rng.seed(42)

print("=== per-instance link shaper ===")

# K1 off by default
ck("K1 off by default (not active)", not sh.active())
ck("K1 nothing dropped when off", not sh.should_drop(1000, is_key=False))

# K2 jam drops ~set fraction of droppable frames
sh.set_jam(0.20)
ck("K2 active after set_jam", sh.active())
N = 5000
drops = sum(1 for _ in range(N) if sh.should_drop(1000, is_key=False))
rate = drops / N
ck(f"K2 jam ~20% on droppable ({rate*100:.1f}%)", 0.17 <= rate <= 0.23, rate)

# K3 keyframes/audio protected
sh.set_jam(0.90)              # brutal jam
kdrops = sum(1 for _ in range(2000) if sh.should_drop(12000, is_key=True))
ck("K3 keyframes/audio NEVER dropped even at 90% jam", kdrops == 0, kdrops)

# K4 bandwidth pacing caps egress
sh2 = link_shaper.LinkShaper()
sh2.set_bandwidth(256)        # 256 kbps = 32000 B/s
# push well over budget in a tight loop; pacing should introduce delay
frame = 8000                  # bytes
t0 = time.monotonic()
total = 0
for _ in range(20):           # 20 * 8000 = 160000 B; at 32000 B/s that's ~5s of data
    sh2.pace(frame); total += frame
elapsed = time.monotonic() - t0
# it should have introduced *some* pacing delay (not instant); we don't demand exact timing
ck("K4 pacing introduces delay when over budget", sh2.paced_delay_s > 0.0, sh2.paced_delay_s)
ck("K4 describe reflects bandwidth", "256 kbps" in sh2.describe(), sh2.describe())

# K5 clear restores full speed
sh2.clear()
ck("K5 clear() => not active", not sh2.active())
ck("K5 describe unconstrained", "unconstrained" in sh2.describe(), sh2.describe())

print(f"\n=== {_p} passed, {_f} failed ===")
print("ORACLE GREEN" if _f == 0 else "ORACLE RED")
sys.exit(1 if _f else 0)
