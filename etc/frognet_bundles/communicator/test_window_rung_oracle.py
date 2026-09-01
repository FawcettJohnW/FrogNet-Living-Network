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
test_window_rung_oracle.py -- [WINDOW_KNOWS_ITS_RUNG_V1]

A measurement window is labelled with the rung it was MEASURED at.

Reproduces the defect from the 2026-08-09/10 logs. snapshot() returns the last
COMPLETED window; the rung log line fired on a rung CHANGE and printed that
window against the rung just entered. Result, in John's own logs, four runs, no
exceptions:

    12:58:57  rung L4 ... 24.0 fps  138.0 KB/s     <- L7's traffic, labelled L4
    13:00:35  rung L5 ...  0.0 fps    0.0 KB/s     <- L4's silence, labelled L5
    13:11:21  rung L4 ... 24.0 fps  149.4 KB/s     <- L7's traffic, labelled L4

  W1  a window spent entirely at L7 publishes levels == [7]
  W2  the NEXT window, spent entirely at L4 with video shed, publishes [4] and
      zero video -- the zeros are attributed to L4, not to whatever comes next
  W3  a window that straddles a change reports BOTH ends, not one
  W4  windows carry an increasing seq, so a settled call keeps producing lines
      instead of going silent after the last transition
  W5  an audio-only window is still labelled: note_level runs before the shed
      check, so L4 is not a hole in the record

Fails on shipped code: WireStats has no note_level, no levels, and no seq.
"""
import sys, time

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

for attr in ("note_level",):
    if not hasattr(fnav.WireStats, attr):
        print("  FAIL  WireStats has no %s(): a window cannot say which rung it "
              "was measured at" % attr)
        print("\nFAILED: the rung on the log line is the rung at PRINT time, "
              "not the rung the numbers came from.")
        sys.exit(1)

st = fnav.WireStats()
st.win = 0.05          # keep the oracle quick; the logic is window-size blind


def close_window():
    time.sleep(st.win + 0.01)
    st._roll()
    return st.snapshot()


# ---- W1: a window at L7 with video flowing --------------------------------
st.note_level(7)
for _ in range(6):
    st.on_video_sent(6000, False)
w1 = close_window()
ck("W1 window at L7 is labelled L7", w1.get("levels") == [7], w1.get("levels"))
ck("W1 window at L7 reports its video", w1.get("v_fps_sent", 0) > 0,
   w1.get("v_fps_sent"))

# ---- W2: the next window is audio-only at L4 ------------------------------
# The sender loop calls note_level on the shed path, so L4 is recorded even
# though no video call is ever made.
st.note_level(4)
w2 = close_window()
ck("W2 audio-only window is labelled L4, not L7", w2.get("levels") == [4],
   w2.get("levels"))
ck("W2 its zeros belong to L4", w2.get("v_fps_sent") == 0.0,
   w2.get("v_fps_sent"))
ck("W5 an audio-only window is not a hole in the record",
   w2.get("levels"), w2.get("levels"))

# the L7 numbers must NOT have leaked into the L4 window
ck("W2 L7's traffic is not reported against L4",
   w2.get("v_kbps") == 0.0, w2.get("v_kbps"))

# ---- W3: a window that straddles a change ---------------------------------
st.note_level(4)
st.on_video_sent(3000, False)
st.note_level(7)
st.on_video_sent(9000, True)
w3 = close_window()
ck("W3 a straddling window reports both ends", w3.get("levels") == [4, 7],
   w3.get("levels"))

# ---- W4: seq keeps moving on a settled call -------------------------------
seqs = [w1.get("seq"), w2.get("seq"), w3.get("seq")]
ck("W4 windows are sequenced", all(isinstance(x, int) for x in seqs)
   and seqs == sorted(seqs) and len(set(seqs)) == 3, seqs)

st.note_level(4)
w4a = close_window()
st.note_level(4)
w4b = close_window()
ck("W4 a settled call keeps producing windows (no rung change needed)",
   w4b.get("seq") == w4a.get("seq") + 1, (w4a.get("seq"), w4b.get("seq")))
ck("W4 and they stay labelled with the settled rung",
   w4a.get("levels") == [4] and w4b.get("levels") == [4],
   (w4a.get("levels"), w4b.get("levels")))

# ---- W6: the mismatch warning must not fire on correct behaviour ----------
# [ENCODED_SIZE_IS_MEASURED_V1] compared the encoded size against
# RUNG_VIDEO[send_l] -- the TABLE -- after geometry stopped coming from the
# table. Measured 2026-08-10: every frame of the bottom walk printed
# "*** NOT the rung's 640x360 ***" while running exactly the size it was told
# to. A warning that fires on correct behaviour is noise, and noise costs the
# NEXT diagnosis.
src = open("fnav.py", encoding="utf-8").read()
ck("W6 intent comes from the treatment, not the rung table",
   "_t = self._video_treatment(send_l) if send_l in RUNG_VIDEO else None" in src)
ck("W6 the table lookup is gone from the mismatch test",
   '_want = (RUNG_VIDEO[send_l]["w"], RUNG_VIDEO[send_l]["h"])' not in src)
# Check the FORMAT STRING, not the file: the comment above it quotes the old
# message on purpose, and an assertion that bans the word bans the explanation.
ck("W6 and the message says asked-for rather than the rung's",
   '*** NOT the asked-for %dx%d ***' in src
   and "*** NOT the rung's %dx%d ***" not in src)

# the shrunken case the log showed: bottom step 1, rung L5 -> no mismatch
c = fnav.Call.__new__(fnav.Call)
c.aspect = fnav.DEFAULT_ASPECT; c.fps = 24; c._floor_geo = None; c._cmd_geo = None
c._bottom = 1
_t = c._video_treatment(min(fnav.RUNG_VIDEO))
ck("W6 a shrunken bottom rung reports its own size as intent",
   (_t["w"], _t["h"]) != (fnav.RUNG_VIDEO[min(fnav.RUNG_VIDEO)]["w"],
                          fnav.RUNG_VIDEO[min(fnav.RUNG_VIDEO)]["h"]),
   (_t["w"], _t["h"]))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
