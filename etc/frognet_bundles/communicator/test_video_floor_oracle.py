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
test_video_floor_oracle.py -- [VIDEO_FLOOR_IS_MEASURED_V1]

There is no defined floor. Send a keyframe at 320, then 480, then 720, and stop
at the first stall that touches audio; the settled geometry is the one BELOW the
stall.

Shipped behaviour this replaces: RUNG_VIDEO's lowest entry (640x360 @ 300 kbps)
was treated as the floor, so `level_for_link` returned "audio only" for any link
whose budget came out under 300 kbps -- a fact about the table reported as a
fact about the wire. At 408 kbps that is 320x240 thrown away untested.

  F1  the walk starts at the smallest candidate
  F2  a candidate that passes advances to the next
  F3  a stall that touches AUDIO settles on the candidate below it
  F4  a stall at the smallest candidate means no video -- measured, not assumed
  F5  a keyframe the wire refuses outright also stops the walk
  F6  every candidate passing settles at the top
  F7  the keyframe size observed at each step is recorded
  F8  audio is read cumulatively and non-destructively, so a probe cannot steal
      the bearer's pending shed count
  F9  the candidate list is data: a walk over different geometries works with no
      other change
"""
import sys, types

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

if not hasattr(fnav, "VideoFloorProbe"):
    print("  FAIL  no VideoFloorProbe: the lowest table entry IS the floor")
    print("\nFAILED: 'video cannot pass' is a table lookup, not a measurement.")
    sys.exit(1)

P = fnav.VideoFloorProbe

# [FLOOR_IS_DATA_V1] Derived from the table, never duplicated. These assertions
# describe the WALK -- starts smallest, advances on a pass, settles below a
# stall -- so adding or removing a candidate geometry (the one edit this design
# is meant to make cheap) does not break the tests that guard it.
# [ASPECT_IS_CHOSEN_V1] The list is per-aspect now, so ask for it rather than
# reading a class constant that is empty by design.
CANDS = P.candidates_for(fnav.DEFAULT_ASPECT)
GEO = [(c["w"], c["h"]) for c in CANDS]
SMALLEST, TOP = GEO[0], GEO[-1]
ck("the candidate list is ascending by pixel count",
   all(CANDS[i]["w"] * CANDS[i]["h"] < CANDS[i + 1]["w"] * CANDS[i + 1]["h"]
       for i in range(len(CANDS) - 1)), GEO)
ck("every aspect produces an ascending walk",
   all(all(c[i]["w"] * c[i]["h"] < c[i + 1]["w"] * c[i + 1]["h"]
           for i in range(len(c) - 1))
       for c in (P.candidates_for(a) for a in fnav.RUNG_GEO)),
   {a: [(g["w"], g["h"]) for g in P.candidates_for(a)] for a in fnav.RUNG_GEO})


def walk(steps, candidates=None):
    """Drive a probe one CANDIDATE at a time.

    `steps` is a list of (video_shed, audio_shed_delta) applied to a candidate.
    A clean step feeds KEYS_PER_STEP clean keyframes, which is what a pass now
    costs; a dirty step fails on its first keyframe.
    """
    p = P(candidates)
    p.begin(audio_sheds_now=100)
    p._started = 0.0
    a = 100
    seen = []
    for vshed, ashed in steps:
        cur = p.current()
        if cur is None:
            break
        seen.append((cur["w"], cur["h"]))
        n = 1 if (vshed or ashed) else P.KEYS_PER_STEP
        for _ in range(n):
            if p.current() is None:
                break
            a += ashed
            p.observe(vshed, a, key_bytes=(cur["w"] * cur["h"]) // 20, now=0.0)
            p._started = 0.0
    return p, seen


# ---- F1/F2/F6: the ascent --------------------------------------------------
p, seen = walk([(False, 0)] * len(GEO))
ck("F1 the walk starts at the smallest candidate", seen[0] == SMALLEST, seen)
ck("F2 a passing candidate advances", seen == GEO, seen)
ck("F6 all passing settles at the top",
   p.done and p.result and (p.result["w"], p.result["h"]) == TOP, p.result)

# ---- F3: audio is the stop condition --------------------------------------
p, seen = walk([(False, 0)] * (len(GEO) - 1) + [(False, 1)])
ck("F3 an audio shed stops the walk", p.done and not p.active, (p.done, p.active))
ck("F3 and settles on the candidate BELOW the stall",
   p.result and (p.result["w"], p.result["h"]) == GEO[len(seen) - 2], p.result)
ck("F3 and says audio was the reason", "audio shed" in p.why, p.why)

p, seen = walk([(False, 0), (False, 2)])
ck("F3 a stall on the second candidate settles on the first",
   p.result and (p.result["w"], p.result["h"]) == GEO[0], p.result)

# ---- F4: no video, measured -----------------------------------------------
p, seen = walk([(False, 1)])
ck("F4 a stall at the smallest candidate means no video",
   p.done and p.result is None, p.result)
ck("F4 and it was measured at the smallest geometry, not assumed",
   seen == [SMALLEST] and ("%dx%d" % SMALLEST) in p.why, (seen, p.why))

# ---- F5: the wire refusing the keyframe also stops -------------------------
p, seen = walk([(False, 0), (True, 0)])
ck("F5 a refused keyframe stops the walk",
   p.done and p.result and (p.result["w"], p.result["h"]) == GEO[0], p.result)
ck("F5 and is distinguished from an audio stall",
   "refused" in p.why and "audio" not in p.why, p.why)

# ---- F7: measurements are kept --------------------------------------------
p, seen = walk([(False, 0)] * (len(GEO) - 1) + [(False, 1)])
ck("F7 keyframe sizes are recorded per candidate",
   set(p.measured) == set(range(len(seen))) and all(v > 0 for v in p.measured.values()),
   p.measured)

# ---- F8: audio counter is a watermark, never consumed ----------------------
class Plane:
    """Stands in for SotFDataPlane: cumulative audio_sheds plus the separate
    pending count the bearer drains."""
    def __init__(s): s.audio_sheds = 7; s._pending = 7
    def take_audio_sheds(s):
        n = s._pending; s._pending = 0; return n

pl = Plane()
p = P()
p.begin(pl.audio_sheds)
p._started = 0.0
p.observe(False, pl.audio_sheds, key_bytes=4096, now=0.0)
ck("F8 the probe reads the cumulative counter, not the pending one",
   pl._pending == 7 and pl.take_audio_sheds() == 7, pl._pending)

# ---- F9: the candidate list is data ---------------------------------------
tiny = ({"w": 160, "h": 120, "bitrate": 80_000, "gray": True},
        {"w": 320, "h": 240, "bitrate": 150_000, "gray": False})
p, seen = walk([(False, 0), (False, 1)], candidates=tiny)
ck("F9 a different candidate list walks with no other change",
   seen == [(160, 120), (320, 240)]
   and p.result and (p.result["w"], p.result["h"]) == (160, 120),
   (seen, p.result))

# ---- the encoder can actually be asked for a non-rung geometry -------------
class _Ctx:
    def __init__(s): s.width = s.height = 0; s.bit_rate = 0; s.pix_fmt = None
    time_base = None; options = None
_built = []
class _CC:
    @staticmethod
    def create(n, m):
        c = _Ctx(); _built.append(c); return c
_av = types.ModuleType("av"); _av.CodecContext = _CC
sys.modules["av"] = _av
enc = fnav.VideoEncoder(codec_id=fnav.CODEC_VP8)
_c0 = dict(CANDS[0]); _c0["fps"] = 24
enc._ensure(_c0)
ck("the encoder accepts a geometry no rung names",
   (_built[-1].width, _built[-1].height) == SMALLEST,
   (_built[-1].width, _built[-1].height))
ck("the smallest candidate is indeed not a rung",
   not any((g["w"], g["h"]) == SMALLEST
           for fam in fnav.RUNG_GEO.values() for g in fam.values()), SMALLEST)

# ---- F10: a pass costs KEYS_PER_STEP clean keyframes -----------------------
p = P()
p.begin(audio_sheds_now=0)
p._started = 0.0
for i in range(P.KEYS_PER_STEP - 1):
    p.observe(False, 0, key_bytes=4096, now=0.0)
    p._started = 0.0
ck("F10 one clean keyframe short of the quota does NOT advance",
   (p.current()["w"], p.current()["h"]) == GEO[0], p.current())
p.observe(False, 0, key_bytes=4096, now=0.0)
ck("F10 the quota'th clean keyframe advances",
   (p.current()["w"], p.current()["h"]) == GEO[1], p.current())

# ---- F11: a late stall inside a step still fails the step ------------------
p = P()
p.begin(audio_sheds_now=0)
p._started = 0.0
for _ in range(P.KEYS_PER_STEP):          # first candidate passes
    p.observe(False, 0, key_bytes=4096, now=0.0); p._started = 0.0
p.observe(False, 0, key_bytes=8192, now=0.0); p._started = 0.0   # 480, kf 1 ok
p.observe(False, 1, key_bytes=8192, now=0.0)                     # kf 2 sheds audio
ck("F11 a stall on a later keyframe still fails the step",
   p.done and p.result and (p.result["w"], p.result["h"]) == GEO[0], p.result)
ck("F11 and the reason names which keyframe", "of %d" % P.KEYS_PER_STEP in p.why,
   p.why)

# ---- F12: the walk is budgeted --------------------------------------------
p = P(budget_s=3.0)
_exp = p.budget_s / len(p.candidates)
ck("F12 the budget is spread across the candidates",
   abs(p.step_s - _exp) < 1e-9
   and abs(p.space_s - _exp / P.KEYS_PER_STEP) < 1e-9,
   (p.step_s, p.space_s))
# [F14] The advertised budget must be one the walk can actually meet. ready()
# floors keyframe spacing at SETTLE_S, so adding a candidate lengthens the walk
# whether or not BUDGET_S says so -- and a budget shorter than the floor is a
# number that describes nothing.
ck("F14 the advertised budget honours the settle floor",
   all(P(aspect=a).budget_s >= P.floor_walk_s(aspect=a) for a in fnav.RUNG_GEO),
   {a: (P(aspect=a).budget_s, P.floor_walk_s(aspect=a)) for a in fnav.RUNG_GEO})
ck("F14 spacing is never below the settle floor",
   all(P(aspect=a).space_s >= P.SETTLE_S - 1e-9 for a in fnav.RUNG_GEO),
   {a: P(aspect=a).space_s for a in fnav.RUNG_GEO})
ck("F14 asking for an impossible budget yields the achievable one",
   P(budget_s=0.1).budget_s == P.floor_walk_s(aspect=fnav.DEFAULT_ASPECT),
   P(budget_s=0.1).budget_s)
ck("F12 keyframe spacing never goes below the audio settle",
   P(budget_s=0.1).ready(now=P.SETTLE_S - 0.01) is False)

# ---- F13: the largest keyframe at a geometry is what is kept --------------
p = P()
p.begin(audio_sheds_now=0)
p._started = 0.0
p.observe(False, 0, key_bytes=4096, now=0.0); p._started = 0.0
p.observe(False, 0, key_bytes=9000, now=0.0); p._started = 0.0
p.observe(False, 0, key_bytes=5000, now=0.0)
ck("F13 the WORST keyframe is recorded, not the average",
   p.measured.get(0) == 9000, p.measured)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
