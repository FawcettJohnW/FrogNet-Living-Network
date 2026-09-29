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
test_bearer_sustain_oracle.py -- the bearer steps down when the rung is not being
PRODUCED, not only when the wire refuses frames.

Field evidence, 2026-08-02, a real call:

    [VID-TX]  5/30fps, 44KB/s, drops 0/s, rung L7 @ 1280x720
    [VID-TX]  3/30fps, 39KB/s, drops 0/s, rung L7 @ 1280x720
    [VID-TX]  4/30fps, 28KB/s, drops 0/s, rung L7 @ 1280x720

Three to twelve frames a second of a thirty frame target, zero drops, and the rung
never moved. The wire was never the problem, so nothing was shed, so the only signal
fnav's Bearer had -- `dropped > 0` -- never fired. depth() is hard-zero by design, so
the backlog arm could never fire either. The bearer read "clean", stepped UP, and held
the top rung producing a slideshow.

That contradicts the ladder's premise: send the highest rung the machine and the wire
can SUSTAIN. A rung nobody can produce is not being sustained.

The retired sotf_ladder_control judged three things -- lag-abs, lag-growing and
wire-drop. Only wire-drop survived into fnav. This restores the two that fnav can
measure, with that controller's own thresholds (lag_abs_ms = 250).

Run: python3 test_bearer_sustain_oracle.py
"""
import sys
import time

sys.path.insert(0, ".")
import fnav
import sotf_ladder as L

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


def walk(n=12, ceiling=7, **kw):
    """n samples of one condition. The 1s step cap is bypassed so the RULE is what is
    under test, not the clock."""
    b = fnav.Bearer(ceiling)
    out = []
    for _ in range(n):
        b._last_change = 0.0
        out.append(b.sample(**kw))
    return b, out


print("-- THE RULE: below 10 fps for MORE THAN 3 frames -> step down -------------")
b = fnav.Bearer(7)
seen = []
for i in range(6):
    b._last_change = 0.0
    seen.append(b.sample(0, 0, fps_sent=8, frame_age_s=0.01))
ck("holds for the first three frames", seen[:3] == [7, 7, 7], seen)
ck("steps down on the FOURTH", seen[3] == 6, seen)
ck("and names the rate", "below 10 fps" in b.last_reason, b.last_reason)

b = fnav.Bearer(7)
for _ in range(3):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=8, frame_age_s=0.01)
b._last_change = 0.0
b.sample(0, 0, fps_sent=25, frame_age_s=0.01)      # one good frame breaks the run
for _ in range(3):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=8, frame_age_s=0.01)
ck("a single good frame resets the run (three, then three, is not four)",
   b.idx == 7, b.idx)

b = fnav.Bearer(7)
for _ in range(8):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=10.0, frame_age_s=0.01)
ck("exactly 10 fps is not BELOW 10 and does not step down", b.idx == 7, b.idx)

# CONTROL: the drops-only bearer, on the numbers from the wire.
b0 = fnav.Bearer(7)
old = []
for _ in range(12):
    b0._last_change = 0.0
    old.append(b0.sample(0, 0))
ck("CONTROL: the drops-only bearer never moved on any of this", old[-1] == 7, old)


print("-- THE RULE: do not step up until ABOVE 20 fps for AT LEAST 5 frames ------")
b = fnav.Bearer(7)
b.idx = 5
seen = []
for i in range(7):
    b._last_change = 0.0
    seen.append(b.sample(0, 0, fps_sent=25, frame_age_s=0.01))
ck("holds for four frames", seen[:4] == [5, 5, 5, 5], seen)
ck("steps up on the FIFTH", seen[4] == 6, seen)

b = fnav.Bearer(7)
b.idx = 5
for _ in range(30):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=20.0, frame_age_s=0.01)
ck("exactly 20 fps is not ABOVE 20 and never climbs", b.idx == 5, b.idx)

b = fnav.Bearer(7)
b.idx = 5
for _ in range(30):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=15, frame_age_s=0.01)
ck("15 fps climbs nowhere and drops nowhere -- the dead band holds",
   b.idx == 5, b.idx)

b = fnav.Bearer(7)
b.idx = 5
for _ in range(4):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=25, frame_age_s=0.01)
b._last_change = 0.0
b.sample(0, 0, fps_sent=15, frame_age_s=0.01)      # one mediocre frame breaks the run
for _ in range(4):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=25, frame_age_s=0.01)
ck("a single mediocre frame resets the climb", b.idx == 5, b.idx)


print("-- a rung that just failed is not retried immediately ---------------------")
# [UPGRADE_COOLDOWN_V1] Below L5 no video is produced, so there is no rate to judge and
# the probe fires on the absence of other stress. Without a cooldown a box that could
# not hold L5 hunted L4-L5 every second or two, video flickering on and off. Measured
# on a real frame-rate trace: 22 rung changes in 49 seconds, down to 9 with the
# cooldown. The value is sotf_ladder_control.upgrade_cooldown_sec.
_real = time.time
_t = [1000.0]
fnav.time.time = lambda: _t[0]
b = fnav.Bearer(7)
for _ in range(8):                       # fail L7 down to L6
    _t[0] += 0.5
    b.sample(0, 0, fps_sent=5, frame_age_s=0.01)
fell_at = _t[0]
for _ in range(8):                       # instantly clean again
    _t[0] += 0.5
    b.sample(0, 0, fps_sent=30, frame_age_s=0.01)
ck("clean frames within the cooldown do NOT climb",
   b.idx < 7 and (_t[0] - fell_at) < fnav.Bearer.UPGRADE_COOLDOWN_S + 0.5, b.idx)
for _ in range(20):
    _t[0] += 0.5
    b.sample(0, 0, fps_sent=30, frame_age_s=0.01)
ck("once the cooldown has passed it climbs", b.idx == 7, b.idx)
fnav.time.time = _real


print("-- where there is no rate to judge ---------------------------------------")
# Below L5 no video is produced, and during warm-up none has been yet. The absence of
# other stress is then the only evidence there is, so the ladder may still probe up --
# otherwise a call that shed video could never get it back.
b = fnav.Bearer(7)
b.idx = 4
for _ in range(8):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=None, frame_age_s=None)
ck("with no telemetry it still probes up out of a shed rung", b.idx > 4, b.idx)


print("-- the original signal is untouched --------------------------------------")
b, r = walk(backlog=0, dropped=3)
ck("wire drops still step down", r[-1] < 7, r)
ck("and are still named", b.last_reason == "wire-drop", b.last_reason)
b, r = walk(backlog=9, dropped=0)
ck("a backlog still steps down", r[-1] < 7, r)
b, r = walk(backlog=0, dropped=0)
ck("no telemetry at all behaves exactly as before (holds)", r[-1] == 7, r)


print("-- it probes upward when conditions change -------------------------------")
# Not "recovery" and not "catching up": nothing is buffered, so nothing drains and the
# box was never behind. Clean samples only say the CURRENT rung is being met. Whether a
# higher one can be is unknown until it is tried, so the ladder steps up and steps back
# down if the higher rung is not sustained.
b = fnav.Bearer(7)
for _ in range(9):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=4, frame_age_s=0.01)
low = b.idx
ck("stepped down while the rung was not being produced", low < 7, low)
# [UPGRADE_COOLDOWN_V1] a rung that just failed is not retried for six seconds, so the
# clock has to move for this to be a fair test of climbing.
_real = time.time
# anchor the injected clock to when the step-down actually happened, or the cooldown
# comparison goes negative against a real-clock timestamp
_t = [b._last_down + 0.5]
fnav.time.time = lambda: _t[0]
for _ in range(200):
    _t[0] += 0.5
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=29, frame_age_s=0.01)
fnav.time.time = _real
ck("and probes back up to the ceiling while the target is met", b.idx == 7, b.idx)
ck("never above the ceiling", b.idx <= b.ceiling)

b = fnav.Bearer(7)
for _ in range(60):
    b._last_change = 0.0
    b.sample(0, 0, fps_sent=1, frame_age_s=0.01)
ck("never below the floor however bad it gets", b.idx >= L.MIN_IDX, b.idx)


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
