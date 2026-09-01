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
test_rung_output_oracle.py - the media host's per-rung ENCODE args match the canonical
sotf ladder treatment. Guards the wiring of sotf_leg_adapt into rung_output_args.

Proves, on the rung indices the per-leg controller actually emits (L0-L7):
  - L7 CHORUS: video present, full color (no grayscale), libvpx, ivf container
  - L6 ENSEMBLE: video present, fps cap 24
  - L5 DUET: video present AND 360p AND grayscale AND fps 10 - the video floor
  - L4 SOLO / L3 VOICE: NO video map, audio-only, ogg container
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import sys as _s, types as _t
# stub frognet_tuples so importing the host module is side-effect free
_ft = _t.ModuleType("frognet_tuples")
_ft.put = lambda *a, **k: None
_ft.get_all = lambda *a, **k: []
_ft.DEFAULT_DBHOST = "databasehost_control.frognet"
_s.modules.setdefault("frognet_tuples", _ft)

import frognet_mediahost as MH

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")

def args_for(level):
    # vout/aout are filtergraph output labels; any non-empty label exercises the video path
    return MH.rung_output_args(level, "[vout]", "[aout]", 9100)

print("=== rung_output_args: encode matches canonical ladder per rung ===")

a7 = args_for(7); s7 = " ".join(a7)
ck("L7 has video (libvpx)", "-c:v" in a7 and "libvpx" in a7, s7)
ck("L7 full color (no grayscale)", "format=gray" not in s7, s7)
ck("L7 ivf container", "ivf" in a7, s7)

a6 = args_for(6); s6 = " ".join(a6)
ck("L6 has video", "-c:v" in a6, s6)
ck("L6 fps cap 24", "fps=24" in s6, s6)

a5 = args_for(5); s5 = " ".join(a5)
ck("L5 has video", "-c:v" in a5, s5)
ck("L5 scales to 360", "scale=360" in s5, s5)
ck("L5 GRAYSCALE (format=gray)", "format=gray" in s5, s5)
ck("L5 fps floor 10", "fps=10" in s5, s5)
ck("L5 ivf container (still video)", "ivf" in a5, s5)

a4 = args_for(4); s4 = " ".join(a4)
ck("L4 NO video map (audio-only)", "-c:v" not in a4, s4)
ck("L4 has audio (libopus)", "libopus" in a4, s4)
ck("L4 ogg container (no video)", "ogg" in a4, s4)

a3 = args_for(3); s3 = " ".join(a3)
ck("L3 NO video map (audio-only)", "-c:v" not in a3, s3)
ck("L3 ogg container", "ogg" in a3, s3)

print(f"\n=== {_p} passed, {_f} failed ===")
print("ORACLE GREEN" if _f == 0 else "ORACLE RED")
sys.exit(1 if _f else 0)
