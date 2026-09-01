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
test_bottom_rung_oracle.py -- [BOTTOM_RUNG_SHRINKS_V1]

The step below 640x360 is a smaller picture, not no picture.

Observed 2026-08-10, John's run: the ladder held L5 @ 640x360 with drops
2-4/s, then went straight to `video shed (rung L4); audio only`. 640x360 was
the smallest thing ever encoded outside the connect-time floor walk, because
the send loop's `send_l < 5` test sheds video entirely and RUNG_VIDEO has no
entry below L5. Nothing in between existed.

  B1  the bottom list descends by pixel count and starts at the table entry
  B2  _video_treatment at the bottom rung follows the current step
  B3  shrinking is rate-limited to one step per window, so a congested second
      cannot walk the whole list
  B4  shrinking stops at the smallest geometry and then reports exhaustion --
      which is what makes audio-only honest
  B5  growing walks back up and stops at the table entry
  B6  a measured floor smaller than the current step still wins
  B7  rungs above the bottom are untouched by any of it
"""
import os
import sys, time

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

if not hasattr(fnav.Call, "BOTTOM_GEOS"):
    print("  FAIL  no BOTTOM_GEOS: below L5 the send loop sheds video entirely")
    print("\nFAILED: the step under 640x360 is 'no picture'.")
    sys.exit(1)

C = fnav.Call
BOT = min(fnav.RUNG_VIDEO)

# [ASPECT_IS_CHOSEN_V1] BOTTOM_GEOS is per-call now -- which entries apply
# depends on where the chosen family's L5 sits -- so it is asked of an instance
# rather than read off the class.
def _call0(aspect=None):
    c = C.__new__(C)
    c.name = "John"; c.fps = 24; c.have_cam = True
    c.aspect = aspect or fnav.DEFAULT_ASPECT
    c._bottom = 0; c._bottom_at = 0.0; c._bottom_ok = 0; c._floor_geo = None; c._cmd_geo = None
    return c

G = _call0().BOTTOM_GEOS


def call():
    return _call0()


# ---- B1 --------------------------------------------------------------------
ck("B1 the bottom list descends by pixel count",
   all(G[i]["w"] * G[i]["h"] > G[i + 1]["w"] * G[i + 1]["h"]
       for i in range(len(G) - 1)), [(g["w"], g["h"]) for g in G])
ck("B1 and starts at the chosen family's L5",
   (G[0]["w"], G[0]["h"]) == (fnav.RUNG_GEO[fnav.DEFAULT_ASPECT][BOT]["w"],
                              fnav.RUNG_GEO[fnav.DEFAULT_ASPECT][BOT]["h"]),
   (G[0]["w"], G[0]["h"]))
ck("B1 and every aspect's walk ends at the 4:3 floor",
   all((_call0(a).BOTTOM_GEOS[-1]["w"], _call0(a).BOTTOM_GEOS[-1]["h"]) == (160, 120)
       for a in fnav.RUNG_GEO),
   {a: (_call0(a).BOTTOM_GEOS[-1]["w"], _call0(a).BOTTOM_GEOS[-1]["h"])
    for a in fnav.RUNG_GEO})

# ---- B2 --------------------------------------------------------------------
c = call()
t = c._video_treatment(BOT)
ck("B2 step 0 encodes the L5 geometry", (t["w"], t["h"]) == (G[0]["w"], G[0]["h"]),
   (t["w"], t["h"]))
c._bottom = 1
t = c._video_treatment(BOT)
ck("B2 step 1 encodes the next size down",
   (t["w"], t["h"]) == (G[1]["w"], G[1]["h"]), (t["w"], t["h"]))
ck("B2 and carries that step's bitrate", t["bitrate"] == G[1]["bitrate"],
   t["bitrate"])

# ---- B3 --------------------------------------------------------------------
c = call()
now = 1000.0
# [SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1] added sheds=1 to every
# call below: this oracle is about the WALK, and without wire evidence the
# shrink now correctly declines to move. The no-evidence case is B6 in
# test_no_burst_oracle.
ck("B3 the first shrink is taken", c._bottom_shrink(now=now, sheds=1) and c._bottom == 1,
   c._bottom)
ck("B3 an immediate second shrink stays on video but does not step",
   c._bottom_shrink(now=now + 0.1, sheds=1) is True and c._bottom == 1, c._bottom)
ck("B3 after a window it steps again",
   c._bottom_shrink(now=now + C.BOTTOM_STEP_S, sheds=1) and c._bottom == 2, c._bottom)

# ---- B4 --------------------------------------------------------------------
c = call()
t = 1000.0
for i in range(len(G) - 1):
    c._bottom_shrink(now=t, sheds=1); t += C.BOTTOM_STEP_S
ck("B4 shrinking reaches the smallest geometry", c._bottom == len(G) - 1, c._bottom)
ck("B4 and then reports there is nowhere left to go",
   c._bottom_shrink(now=t + 100, sheds=1) is False, c._bottom)
ck("B4 which is the ONLY way audio-only is reached with a camera present",
   (G[c._bottom]["w"], G[c._bottom]["h"]) == (G[-1]["w"], G[-1]["h"]))

# ---- B5 --------------------------------------------------------------------
# [GROWTH_IS_EARNED_V1] every step up costs BOTTOM_CLEAN_WINDOWS clean windows,
# so the walk back up is fed credit here rather than just a clock.
def earn(c):
    for _ in range(C.BOTTOM_CLEAN_WINDOWS):
        c._bottom_clean(0)

c = call()
c._bottom = len(G) - 1
t = 2000.0
earn(c)
ck("B5 growing steps back up", c._bottom_grow(now=t) and c._bottom == len(G) - 2,
   c._bottom)
earn(c)
ck("B5 growing is rate-limited too",
   c._bottom_grow(now=t + 0.1) is True and c._bottom == len(G) - 2, c._bottom)
t += C.BOTTOM_STEP_S
guard = 0
while c._bottom > 0 and guard < 20:
    earn(c)
    c._bottom_grow(now=t); t += C.BOTTOM_STEP_S; guard += 1
ck("B5 the walk back up terminates", guard < 20, guard)
earn(c)
ck("B5 and stops at the L5 entry", c._bottom_grow(now=t + 100) is False
   and c._bottom == 0, c._bottom)

# ---- B6 --------------------------------------------------------------------
c = call()
c._bottom = 0                                   # 640x360
c._floor_geo = {"w": 320, "h": 200, "gray": True, "bitrate": 100_000}
t = c._video_treatment(BOT)
ck("B6 a measured floor smaller than the current step wins",
   (t["w"], t["h"]) == (320, 200), (t["w"], t["h"]))
c._bottom = len(G) - 1
c._floor_geo = {"w": 1280, "h": 720, "gray": False, "bitrate": 1_200_000}
t = c._video_treatment(BOT)
ck("B6 a measured floor LARGER than the current step does not undo the shrink",
   (t["w"], t["h"]) == (G[-1]["w"], G[-1]["h"]), (t["w"], t["h"]))

# ---- B7 --------------------------------------------------------------------
c = call()
c._bottom = len(G) - 1
for rung in sorted(fnav.RUNG_VIDEO):
    if rung == BOT:
        continue
    t = c._video_treatment(rung)
    ck("B7 rung %d is untouched by the bottom walk" % rung,
       (t["w"], t["h"]) == (fnav.RUNG_VIDEO[rung]["w"], fnav.RUNG_VIDEO[rung]["h"]),
       (t["w"], t["h"]))

# ---- B8: [GROWTH_IS_EARNED_V1] growth costs clean windows ------------------
# Shrinking is evidence-driven; growing used to be a timer, and that asymmetry
# was the oscillation. Measured 2026-08-10: shrink to 160x120, shed, recover,
# grow three steps in six seconds, collapse again -- twice in one call.
c = call()
c._bottom = 2
t = 3000.0
ck("B8 a grow with no clean windows does not step",
   c._bottom_grow(now=t) is True and c._bottom == 2, c._bottom)
for _ in range(C.BOTTOM_CLEAN_WINDOWS - 1):
    c._bottom_clean(0)
ck("B8 one window short still does not step",
   c._bottom_grow(now=t) is True and c._bottom == 2,
   (c._bottom, c._bottom_ok))
c._bottom_clean(0)
ck("B8 the quota'th clean window earns the step",
   c._bottom_grow(now=t) and c._bottom == 1, c._bottom)
ck("B8 and the next step must be earned again", c._bottom_ok == 0, c._bottom_ok)

# a shed anywhere in the run resets the credit
c = call()
c._bottom = 2
for _ in range(C.BOTTOM_CLEAN_WINDOWS - 1):
    c._bottom_clean(0)
c._bottom_clean(4)                      # one shed
ck("B8 a shed resets the credit", c._bottom_ok == 0, c._bottom_ok)
c._bottom_clean(0)
ck("B8 so a nearly-earned step is not banked through a shed",
   c._bottom_grow(now=t + 100) is True and c._bottom == 2, c._bottom)

# shrinking discards credit too
c = call()
for _ in range(C.BOTTOM_CLEAN_WINDOWS):
    c._bottom_clean(0)
c._bottom_shrink(now=t, sheds=1)
ck("B8 shrinking discards accumulated credit", c._bottom_ok == 0, c._bottom_ok)

# ---- B9: [VBV_OR_THE_BUDGET_IS_A_WISH_V1] ---------------------------------
# 160x120 asked for 60 kbps and produced 147 -- the same cost as the size above
# it -- because bit_rate alone is an average with no window.
enc = fnav.VideoEncoder.__new__(fnav.VideoEncoder)
enc.fps = 24
for cid, sw in ((fnav.CODEC_VP8, True), (fnav.CODEC_H264_SW, True),
                (fnav.CODEC_H264_HW, False)):
    enc.codec_id = cid
    _, opts = enc._codec_config(bitrate=60_000)
    if sw:
        ck("B9 %s gets a VBV at its target" % fnav.CODEC_NAME[cid],
           opts.get("maxrate") == "60000" and opts.get("bufsize") == "30000",
           opts)
    else:
        ck("B9 %s is left to its own rate control" % fnav.CODEC_NAME[cid],
           "maxrate" not in opts, opts)
enc.codec_id = fnav.CODEC_H264_SW
_, opts = enc._codec_config()
ck("B9 no target means no VBV rather than a bogus one",
   "maxrate" not in opts, opts)
ck("B9 bufsize is half a second, not a smoothing buffer",
   int(enc._codec_config(bitrate=300_000)[1]["bufsize"]) == 150_000,
   enc._codec_config(bitrate=300_000)[1]["bufsize"])

# ---- B10: [THE_CLIMB_BACK_IS_A_CLIMB_V1] -----------------------------------
# The way up must be earned the way the way down was. While _bottom > 0 the send
# loop clamps send_l to the bottom rung; the instant it reaches 0 the clamp goes
# and the bearer's ceiling applies -- and that ceiling never came down with the
# picture, because the shrink walk is GEOMETRY and the ceiling is RUNGS.
#
# Measured 2026-08-11, immediately after a link had shed everything:
#   bottom rung -> 640x360 (3 clean windows)
#   [VID-TX] 23.8/24fps,  29KB/s, rung L8 @ 1920x1080
#   [VID-TX] 23.8/24fps, 371KB/s, rung L8 @ 1920x1080
# 480x360 to 1080p in one frame; 29 KB/s to 371 KB/s.
src_f = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fnav.py"), encoding="utf-8").read()
# Anchor on the CALL SITE in the send loop, not the first textual occurrence --
# _bottom_grow is defined long before it is used, so slicing from the definition
# read the wrong region entirely and the check passed on nothing.
_i = src_f.index("                self._bottom_grow()")
_after = src_f[_i:_i + 2200]
ck("B10 finishing the bottom walk pins the ceiling to the bottom rung",
   "if self._bottom == 0:" in _after
   and 'self._set_ceiling("backlog", min(RUNG_VIDEO))' in _after, None)
ck("B10 and says so, naming the rung it pinned to",
   "the climb back is by probe" in _after, None)
ck("B10 the pin is the BOTTOM rung, not a fixed number",
   "min(RUNG_VIDEO)" in _after and "self._set_ceiling(\"backlog\", 5)"
   not in src_f, None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
