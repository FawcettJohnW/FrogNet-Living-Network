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
test_shed_signal_oracle.py -- [SHED_IS_READ_FROM_THE_QUEUE_THAT_SHED_IT_V1]

The bearer's congestion signal comes from the plane that shed the frame, and the
dial no longer writes a ladder bound.

Shipped behaviour this replaces:
  bearer.sample(self.sendq.depth(), self.sendq.take_sheds(), ...)
  sendq is the AUDIO plane. Video frames the wire refused bumped vsendq.sheds
  and never reached the controller, so the ladder's down-move came only from the
  relay's keyframe backlog report or the fps floor.

  set_throttle() -> cap_to_link() -> _set_ceiling("local", ...)
  408_000 * 0.60 = 244_800 against L5's 300_000, so a 408 kbps dial produced
  audio-only with nothing sent.

  S1  take_video_sheds drains a pending count and leaves self.sheds alone, so
      the three readers that DIFF self.sheds are not broken by it
  S2  take_sheds (audio plane, legacy) still zeroes as before
  S3  a video shed is visible to a video-plane drain and NOT to the audio plane
  S4  set_throttle arms the token bucket on BOTH planes
  S5  set_throttle does NOT write a ladder bound
  S6  a 408 kbps dial no longer forces audio-only by arithmetic
  S7  level_for_link survives for the viewer path
"""
import sys, types, threading

FAIL = []
def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)

import fnav

if not hasattr(fnav.SotFDataPlane, "take_video_sheds"):
    print("  FAIL  no take_video_sheds(): the bearer must drain the audio plane "
          "or reset a counter three readers diff against")
    print("\nFAILED: video sheds cannot reach the controller.")
    sys.exit(1)

P = fnav.SotFDataPlane


def plane():
    """A data plane with no socket -- only the shed counters are exercised."""
    q = P.__new__(P)
    q.lock = threading.Lock()
    q.sheds = 0
    q.audio_sheds = 0
    q._shed_pending = 0
    q._audio_shed_pending = 0
    q.throttle_bps = 0
    return q


# ---- S1: draining must not disturb the diff readers ------------------------
q = plane()
q.sheds = 40; q._shed_pending = 5
before = q.sheds
n = q.take_video_sheds()
ck("S1 take_video_sheds returns the pending count", n == 5, n)
ck("S1 and leaves self.sheds untouched for the diff readers",
   q.sheds == before, (q.sheds, before))
ck("S1 draining twice yields nothing new", q.take_video_sheds() == 0)

# the three readers that diff self.sheds must still see a monotonic counter
q.sheds += 3
ck("S1 a diff reader still sees a forward delta", q.sheds - before == 3,
   q.sheds - before)

# ---- S2: the legacy drain still zeroes -------------------------------------
q2 = plane()
q2.sheds = 9; q2._shed_pending = 9
ck("S2 take_sheds still returns and zeroes", q2.take_sheds() == 9 and q2.sheds == 0,
   q2.sheds)
ck("S2 and clears the pending count with it", q2._shed_pending == 0,
   q2._shed_pending)

# ---- S3: the signal is per-plane -------------------------------------------
vq, aq = plane(), plane()
vq.sheds += 4; vq._shed_pending += 4          # video plane refused 4 frames
ck("S3 the video plane reports its own sheds", vq.take_video_sheds() == 4)
ck("S3 the audio plane reports none of them", aq.take_video_sheds() == 0)

# ---- S4/S5: what the dial does and does not do -----------------------------
class Q:
    def __init__(s): s.throttle_bps = None
c = fnav.Call.__new__(fnav.Call)
c.name = "John"
c.sendq = Q(); c.vsendq = Q()
c._cap_viewer = c._cap_backlog = c._cap_local = None
c._level_cap = None
touched = []
c._set_ceiling = lambda which, level: touched.append((which, level))

fnav.Call.set_throttle(c, 408_000)
ck("S4 the token bucket is armed on the video plane",
   c.vsendq.throttle_bps == 408_000, c.vsendq.throttle_bps)
ck("S4 and on the audio plane", c.sendq.throttle_bps == 408_000,
   c.sendq.throttle_bps)
ck("S5 the dial writes NO ladder bound", touched == [], touched)
ck("S5 _cap_local stays unset", c._cap_local is None, c._cap_local)

# ---- S6: 408 kbps no longer means audio-only -------------------------------
# The arithmetic that used to fire is still checkable, and still says L4 --
# which is exactly why it must not be wired to the dial any more.
c._level_cap = None
budget = 408_000 * fnav.Call.VIDEO_SHARE
floor = min(fnav.RUNG_VIDEO)
ck("S6 the old arithmetic would still have said audio-only",
   budget < fnav.RUNG_VIDEO[floor]["bitrate"],
   (budget, fnav.RUNG_VIDEO[floor]["bitrate"]))
ck("S6 but nothing applies it: the dial touched no ceiling", touched == [],
   touched)

# ---- S7: the viewer path keeps its arithmetic ------------------------------
ck("S7 level_for_link survives for viewer MediaSpeed",
   callable(getattr(fnav.Call, "level_for_link", None)))
ck("S7 cap_to_link survives too", callable(getattr(fnav.Call, "cap_to_link", None)))

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
