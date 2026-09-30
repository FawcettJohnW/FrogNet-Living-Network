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
test_bullfrog_oracle.py -- [BULLFROG_V1]

L8 BULLFROG, 1024x768, above CHORUS.

Adding a rung at the TOP is cheap in a way that inserting one in the middle is
not: no existing index changes meaning, so a frame labelled L7 still means what
it meant. The index does travel on the wire in pack_video(), and a peer built
before this will not know L8 -- on receive the index is used for REPORTING only
(the decoder takes geometry from the bitstream), so an old peer mislabels the
rung and decodes the picture correctly.

  K1  L8 exists, is named BULLFROG, and is the top
  K2  a camera+mic box now ceilings at L8
  K3  L8 is a video rung needing a camera and no mic
      ([VIDEO_DOES_NOT_NEED_A_MIC_V1] still holds at the new top)
  K4  RUNG_VIDEO has a geometry for it
  K5  the ladder's own invariant holds: wire cost is monotonically
      non-increasing from the top down. NOT pixel count -- 1024x768 is 15%
      FEWER pixels than 720p and this is the assertion that says so out loud
  K6  the quality menu can reach it: the label list and QUALITY_CAP agree
  K7  nothing hardcodes 7 as the top -- send_level and the ceiling follow MAX_IDX
  K8  no existing index changed meaning
"""
import sys

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import sotf_ladder as L
import fnav
import comms_ui

# ---- K1 --------------------------------------------------------------------
ck("K1 MAX_IDX is 8", L.MAX_IDX == 8, L.MAX_IDX)
ck("K1 L8 is BULLFROG", L.code(8) == "L8" and L.name(8) == "BULLFROG",
   (L.code(8), L.name(8)))

# ---- K2/K3 -----------------------------------------------------------------
ck("K2 camera+mic ceilings at L8", L.ceiling(True, True) == 8,
   L.ceiling(True, True))
ck("K3 L8 needs a camera", L.BY_IDX[8]["needs_camera"] is True)
ck("K3 and does NOT need a mic", L.BY_IDX[8]["needs_mic"] is False)
ck("K3 a camera-only box can still source it", 8 in L.allowed_levels(True, False),
   sorted(L.allowed_levels(True, False)))
ck("K3 a box with no camera cannot", 8 not in L.allowed_levels(False, True),
   sorted(L.allowed_levels(False, True)))

# ---- K4 --------------------------------------------------------------------
ck("K4 RUNG_VIDEO has a geometry for L8", 8 in fnav.RUNG_VIDEO,
   sorted(fnav.RUNG_VIDEO))
ck("K4 and it is 1080p",
   (fnav.RUNG_VIDEO[8]["w"], fnav.RUNG_VIDEO[8]["h"]) == (1920, 1080),
   (fnav.RUNG_VIDEO[8]["w"], fnav.RUNG_VIDEO[8]["h"]))

# ---- K5: the invariant, and the thing that surprises ----------------------
idxs = sorted(fnav.RUNG_VIDEO, reverse=True)
ck("K5 wire cost is monotonically non-increasing down the video rungs",
   all(fnav.RUNG_VIDEO[idxs[i]]["bitrate"] >= fnav.RUNG_VIDEO[idxs[i + 1]]["bitrate"]
       for i in range(len(idxs) - 1)),
   [(i, fnav.RUNG_VIDEO[i]["bitrate"]) for i in idxs])

px8 = fnav.RUNG_VIDEO[8]["w"] * fnav.RUNG_VIDEO[8]["h"]
px7 = fnav.RUNG_VIDEO[7]["w"] * fnav.RUNG_VIDEO[7]["h"]
ck("K5 L8 is larger than L7 by pixel count as well as by cost", px8 > px7,
   (px8, px7))
ck("K5 pixel count is monotonically non-increasing down the video rungs",
   all(fnav.RUNG_VIDEO[idxs[i]]["w"] * fnav.RUNG_VIDEO[idxs[i]]["h"]
       > fnav.RUNG_VIDEO[idxs[i + 1]]["w"] * fnav.RUNG_VIDEO[idxs[i + 1]]["h"]
       for i in range(len(idxs) - 1)),
   [(i, fnav.RUNG_VIDEO[i]["w"] * fnav.RUNG_VIDEO[i]["h"]) for i in idxs])

# ---- K5b: [ASPECT_IS_CHOSEN_V1] / [CONSTRAINED_GOES_4_3_V1] ---------------
# Two geometry families above L5, operator-selected, widescreen by default; one
# 4:3 walk below it. A talking head is taller than it is wide, so at the sizes
# where every pixel counts 4:3 puts more of them on the face -- 160x120 is
# 19,200 pixels against 160x90's 14,400.
STANDARD = {(1920, 1080): "1080p", (1280, 720): "720p", (854, 480): "480p",
            (640, 360): "360p",
            (1600, 1200): "UXGA", (1280, 1024): "SXGA", (1024, 768): "XGA",
            (800, 600): "SVGA", (640, 480): "VGA", (480, 360): "4:3 360p",
            (320, 240): "QVGA", (160, 120): "QQVGA"}

def ratio(w, h):
    return round(w / float(h), 3)

ck("K5b the default aspect is widescreen", fnav.DEFAULT_ASPECT == "16:9",
   fnav.DEFAULT_ASPECT)
ck("K5b both families cover every video rung",
   all(set(fnav.RUNG_GEO[a]) == set(fnav.RUNG_VIDEO) for a in fnav.RUNG_GEO),
   {a: sorted(fnav.RUNG_GEO[a]) for a in fnav.RUNG_GEO})

wide = [(g["w"], g["h"]) for g in fnav.RUNG_GEO["16:9"].values()]
ck("K5b the widescreen family is 16:9 throughout",
   all(abs(ratio(w, h) - 16 / 9.0) < 0.01 for w, h in wide), wide)

four3 = [(g["w"], g["h"]) for i, g in sorted(fnav.RUNG_GEO["4:3"].items())]
ck("K5b the 4:3 family is 4:3, except SXGA which is 5:4 and known to be",
   all(abs(ratio(w, h) - 4 / 3.0) < 0.01 or (w, h) == (1280, 1024)
       for w, h in four3), four3)
ck("K5b SXGA is 5:4 and the ladder does not pretend otherwise",
   abs(ratio(1280, 1024) - 1.25) < 0.001, ratio(1280, 1024))

bottom = [(g["w"], g["h"]) for g in fnav.BOTTOM_4_3]
ck("K5b the constrained walk is 4:3 throughout",
   all(abs(ratio(w, h) - 4 / 3.0) < 0.01 for w, h in bottom), bottom)
ck("K5b and it descends", all(bottom[i][0] * bottom[i][1]
                              > bottom[i + 1][0] * bottom[i + 1][1]
                              for i in range(len(bottom) - 1)), bottom)
ck("K5b its floor is QQVGA, not 160x90 -- more pixels where the face is",
   bottom[-1] == (160, 120), bottom[-1])

everything = (wide + four3 + bottom)
unnamed = sorted({wh for wh in everything if wh not in STANDARD})
ck("K5b every geometry is a named standard size", not unnamed, unnamed)
ck("K5b 640x480 has no RUNG -- it is a step in the constrained walk only",
   (640, 480) not in wide and (640, 480) not in four3 and (640, 480) in bottom)

# both families must still be monotonic in pixels
for a in fnav.RUNG_GEO:
    idx = sorted(fnav.RUNG_GEO[a], reverse=True)
    px = [fnav.RUNG_GEO[a][i]["w"] * fnav.RUNG_GEO[a][i]["h"] for i in idx]
    ck("K5b %s descends in pixel count" % a,
       all(px[i] > px[i + 1] for i in range(len(px) - 1)), list(zip(idx, px)))

# per-call resolution
def mkcall(aspect):
    c = fnav.Call.__new__(fnav.Call)
    c.aspect = aspect; c.fps = 24; c._bottom = 0; c._floor_geo = None
    c._cmd_geo = None
    return c

c = mkcall("4:3")
t = c._video_treatment(7)
ck("K5b a 4:3 call encodes SXGA at L7", (t["w"], t["h"]) == (1280, 1024),
   (t["w"], t["h"]))
ck("K5b and pays L7's cost, not a different one",
   t["bitrate"] == fnav.RUNG_VIDEO[7]["bitrate"], t["bitrate"])
c = mkcall("16:9")
t = c._video_treatment(7)
ck("K5b a widescreen call encodes 720p at the same rung",
   (t["w"], t["h"]) == (1280, 720), (t["w"], t["h"]))

for a in ("16:9", "4:3"):
    c = mkcall(a)
    b = [(g["w"], g["h"]) for g in c.BOTTOM_GEOS]
    ck("K5b %s bottom walk starts at its own L5 and goes 4:3" % a,
       b[0] == (fnav.RUNG_GEO[a][5]["w"], fnav.RUNG_GEO[a][5]["h"])
       and b[-1] == (160, 120), b)
    ck("K5b %s bottom walk never steps UP" % a,
       all(b[i][0] * b[i][1] > b[i + 1][0] * b[i + 1][1]
           for i in range(len(b) - 1)), b)

# ---- K6: both places a rung has to be named -------------------------------
caps = comms_ui.PendingControls.QUALITY_CAP
ck("K6 QUALITY_CAP can reach L8", 8 in caps.values(), caps)
src = open("communicator_live.py", encoding="utf-8").read()
labels = [k for k in caps if k != "Auto"]
missing = [k for k in labels if '"%s"' % k not in src]
ck("K6 every QUALITY_CAP label appears in the menu", not missing, missing)
ck("K6 every named cap is a real rung",
   all(v in fnav.RUNG_VIDEO or v == L.MAX_IDX for k, v in caps.items()), caps)

# ---- K7 --------------------------------------------------------------------
ck("K7 send_level can return the new top",
   L.send_level(L.MAX_IDX, L.MAX_IDX, L.allowed_levels(True, True)) == 8,
   L.send_level(L.MAX_IDX, L.MAX_IDX, L.allowed_levels(True, True)))
ck("K7 a bearer below the top still pins it",
   L.send_level(L.MAX_IDX, 6, L.allowed_levels(True, True)) == 6)

# ---- K8: nothing below moved ----------------------------------------------
EXPECT = {0: "PULSE", 1: "BEACON", 2: "WHISPER", 3: "VOICE", 4: "SOLO",
          5: "DUET", 6: "ENSEMBLE", 7: "CHORUS"}
bad = [(i, L.name(i)) for i, n in EXPECT.items() if L.name(i) != n]
ck("K8 no existing index changed meaning", not bad, bad)
ck("K8 L4 is still the audio-only rung", fnav.Call.AUDIO_ONLY_IDX == 4,
   fnav.Call.AUDIO_ONLY_IDX)
ck("K8 the bottom video rung is still L5", min(fnav.RUNG_VIDEO) == 5,
   min(fnav.RUNG_VIDEO))

# ---- K9: [SMALLEST_WIRE_WINS_V1] ------------------------------------------
# Bytes on the wire decide; everything else ranks after. Gray is smaller at the
# same geometry (flat chroma planes cost a handful of bits per macroblock), so
# the bottom rungs are gray and the CPU cost of the conversion does not get a
# vote. Guarded because it was removed for one revision on that CPU argument
# and nothing failed when it went.
bottom_gray = [(g["w"], g["h"], g.get("gray")) for g in fnav.BOTTOM_4_3]
ck("K9 every constrained-walk step is gray",
   all(g[2] for g in bottom_gray), bottom_gray)
ck("K9 and so is the lowest rung of each family",
   all(fnav.RUNG_GEO[a][min(fnav.RUNG_GEO[a])].get("gray")
       for a in fnav.RUNG_GEO),
   {a: fnav.RUNG_GEO[a][min(fnav.RUNG_GEO[a])].get("gray") for a in fnav.RUNG_GEO})
ck("K9 the rungs above it are not -- gray is a bottom-end trade",
   not any(fnav.RUNG_VIDEO[i].get("gray")
           for i in fnav.RUNG_VIDEO if i > min(fnav.RUNG_VIDEO)),
   {i: fnav.RUNG_VIDEO[i].get("gray") for i in sorted(fnav.RUNG_VIDEO)})

# [TILE_TAKES_WHAT_IT_IS_GIVEN_V1] The renderer must not have opinions about
# the shape of what the decoder hands it. cvtColor(BGR2RGB) on a single-channel
# array raises, _fill catches it, and the tile keeps the old picture -- which
# reads as "the display stopped updating", not as an error.
live = open("communicator_live.py", encoding="utf-8").read()
ck("K9 the renderer handles a single-channel frame",
   "t.ndim == 2" in live and "COLOR_GRAY2RGB" in live)
ck("K9 and a four-channel one", "COLOR_BGRA2RGB" in live)
ck("K9 without dropping the ordinary path", "COLOR_BGR2RGB" in live)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
