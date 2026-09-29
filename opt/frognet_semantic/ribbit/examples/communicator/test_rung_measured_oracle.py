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
test_rung_measured_oracle.py -- [RUNG_IS_MEASURED_V1] [PROBE_IS_GRADED_ON_FRAMES_V1]

The ladder moves on what the wire actually did, not on a constant.

Shipped behaviour this replaces:
  - level_for_link() multiplies the declared rate by VIDEO_SHARE = 0.60 and
    compares against RUNG_VIDEO's bitrate column. At 408 kbps that reserves
    163 kbps for costs measuring ~26, and refuses video entirely.
  - the climb raises the `backlog` bound to cap+1 without checking whether that
    bound is the one binding, so with local=4 it is a silent no-op.
  - a probe is scored as survived when no backlog report arrives -- which at L4
    is guaranteed, because nothing is sent and nothing can back up.

  M1  a measured rung reports its real keyframe size, marked 'measured'
  M2  an unvisited rung is scaled by pixel ratio from the nearest measured one,
      marked so the caller can say which, and scaling DOWN overstates (the safe
      direction for a go/no-go)
  M3  a stale measurement is not used
  M4  a window that straddled a rung change teaches nothing
  M5  sustained/refused are learned from windows, not declared
  M6  _probe_fits refuses a rung whose keyframe cannot go out whole
  M7  _probe_fits refuses a rung needing more than a rate already refused
  M8  _probe_fits allows when nothing has ever been refused (unknown ceiling ->
      go and find out)
  M9  a failed probe records the REAL numbers for the rung it failed at, so the
      next fit test is made against what happened rather than the estimate
"""
import sys, types, time

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

if not hasattr(fnav, "RungModel"):
    print("  FAIL  no RungModel: rung cost is a table lookup times a constant")
    print("\nFAILED: nothing measures what a rung costs on this wire.")
    sys.exit(1)

L5, L6, L7 = 5, 6, 7
PX = {lv: fnav.RUNG_VIDEO[lv]["w"] * fnav.RUNG_VIDEO[lv]["h"] for lv in (L5, L6, L7)}


def win(key_kb, delta_kb, v_kbps, a_kbps=4.4, key_n=1, levels=None):
    return {"key_kb": key_kb, "delta_kb": delta_kb, "v_kbps": v_kbps,
            "a_kbps": a_kbps, "key_n": key_n, "levels": levels or [],
            "seq": 1, "v_fps_sent": 24.0}


# ---- M1/M2/M3/M4: the model ------------------------------------------------
m = fnav.RungModel()
m.observe(L7, win(40.0, 6.0, 150.0))
b, src = m.keyframe_bytes(L7)
ck("M1 a measured rung reports its measurement",
   abs(b - 40.0 * 1024) < 1 and src == "measured", (b, src))

b5, src5 = m.keyframe_bytes(L5)
ratio = PX[L5] / float(PX[L7])
ck("M2 an unvisited rung is scaled by pixel ratio",
   abs(b5 - 40.0 * 1024 * ratio) < 1, (b5, ratio))
ck("M2 and says so", src5.startswith("scaled from"), src5)
ck("M2 the estimate is proportional to pixel count and monotonic",
   b5 > 0 and b5 < b, (b5, b))
# NOTE: the claim that linear-pixel scaling OVERSTATES a smaller rung rests on
# encoded size growing sub-linearly with resolution at a fixed quantizer. That
# is a property of the codec, not of this code, and is not asserted here --
# only that the scaling is proportional and marked as an estimate.

m2 = fnav.RungModel()
m2.observe(L7, win(40.0, 6.0, 150.0))
m2.seen[L7]["at"] = time.time() - (fnav.RungModel.STALE_S + 5)
ck("M3 a stale measurement is not used",
   m2.keyframe_bytes(L7) == (0.0, "unknown"), m2.keyframe_bytes(L7))

m3 = fnav.RungModel()
m3.observe(L7, win(40.0, 6.0, 150.0, key_n=0))
ck("M4 a window with no keyframe measured teaches nothing",
   m3.keyframe_bytes(L7) == (0.0, "unknown"), m3.keyframe_bytes(L7))


# ---- a Call stub carrying just the measured state --------------------------
class Q:
    def __init__(s): s._sndbuf = fnav.SotFDataPlane.SNDBUF; s.sheds = 0
class S:
    def __init__(s, d): s.d = d
    def snapshot(s): return s.d

def call(snap=None):
    c = fnav.Call.__new__(fnav.Call)
    c.name = "John"
    c._rungs = fnav.RungModel()
    c._sustained_bps = c._sustained_at = 0.0
    c._refused_bps = c._refused_at = 0.0
    c.vsendq = Q()
    c.stats = S(snap or win(0, 0, 0.0))
    return c


# ---- M5: capacity is learned ----------------------------------------------
c = call()
c._observe_window(win(30.0, 5.0, 60.0, levels=[L6]), sheds_delta=0)
ck("M5 a clean window sets a sustained floor", c._sustained_bps > 0,
   c._sustained_bps)
ck("M5 and teaches that rung", c._rungs.keyframe_bytes(L6)[1] == "measured",
   c._rungs.keyframe_bytes(L6))
c._observe_window(win(45.0, 7.0, 150.0, levels=[L7]), sheds_delta=12)
ck("M5 a shedding window sets a refused ceiling", c._refused_bps > 0,
   c._refused_bps)

c2 = call()
c2._observe_window(win(30.0, 5.0, 60.0, levels=[L6, L7]), sheds_delta=0)
ck("M4 a straddling window teaches no rung",
   c2._rungs.keyframe_bytes(L6)[1] == "unknown",
   c2._rungs.keyframe_bytes(L6))


# ---- M6: whole-frame ceiling ----------------------------------------------
c = call()
# a keyframe larger than _sndbuf//2 (128 KiB) can never go out whole
c._rungs.observe(L7, win(200.0, 8.0, 200.0))
ok, why = c._probe_fits(L7)
ck("M6 a keyframe over the whole-frame ceiling is refused", not ok, why)
ck("M6 and the reason names the ceiling", "whole-frame" in why, why)


# ---- M7/M8: measured ceiling ----------------------------------------------
c = call()
c._rungs.observe(L7, win(40.0, 6.0, 150.0))
ok, why = c._probe_fits(L7)
ck("M8 nothing refused yet -> probe allowed", ok, why)

c._refused_bps = 100.0 * 1024 * 8      # 100 KB/s has been refused
c._refused_at = time.time()
ok, why = c._probe_fits(L7)
ck("M7 a rung needing more than a refused rate is refused", not ok, why)
ck("M7 and the reason names the refusal", "refused" in why, why)

c._refused_at = time.time() - (fnav.Call.CAPACITY_STALE_S + 5)
ok, why = c._probe_fits(L7)
ck("M7 a stale refusal no longer blocks", ok, why)


# ---- M9: a failed probe records the real numbers --------------------------
c = call()
c._rungs.observe(L6, win(20.0, 4.0, 50.0))          # only L6 measured
est, src = c._rungs.keyframe_bytes(L7)
ck("M9 L7 starts as an estimate", src.startswith("scaled from"), src)
c._rungs.note_failure(L7, win(52.0, 9.0, 170.0))    # the probe went and broke
real, src2 = c._rungs.keyframe_bytes(L7)
ck("M9 after failure L7 is measured", src2 == "measured", src2)
ck("M9 and the real number replaced the estimate",
   abs(real - 52.0 * 1024) < 1 and abs(real - est) > 1, (real, est))

# ---- M10: a probe line must not do arithmetic on nothing -------------------
# Measured 2026-08-10: "probing L8: keyframe ~0KB (unknown), needs ~8 kbps,
# sustained 9 kbps". The decision was right -- unknown ceiling, go find out --
# but "needs ~8 kbps" for 1080p is a number with nothing behind it, and a
# number in a log reads as a finding.
c = call()
ok, why = c._probe_fits(8)
ck("M10 an unmeasured rung is still probed", ok, why)
ck("M10 but the reason says so instead of quoting an estimate",
   "nothing measured" in why and "kbps" not in why, why)

c = call()
c._rungs.observe(7, win(40.0, 6.0, 150.0))
ok, why = c._probe_fits(7)
ck("M10 a measured rung still reports its numbers",
   ok and "kbps" in why and "measured" in why, why)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
