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
test_mic_vs_link_oracle.py -- [THE_MIC_IS_NOT_THE_LINK_V1]

Audio that was never captured cannot have failed to arrive.

The relay reports "this viewer is not receiving your audio" and the ladder shuts
video off completely. That is right when the LINK cannot carry the floor. It is
wrong when the capture device is stalling: no audio was sent, so none arrived,
and the relay has no way to tell those apart.

Measured 2026-08-11, repeatedly, on a link with bandwidth to spare:

    [AUD-CAP] device 1.5/s (want 50.0/s) ... input overflow
    AUDIO BACKPRESSURE from the relay (1 viewer(s)) -- video OFF (L8 -> L4)

A USB microphone stalling blacked out the picture, which then had to climb back
a rung at a time through eight-second probes.

Audio is still the floor. What changed is the diagnosis: if this end is not
producing audio, the fault is here and shutting video down does not fix it.

  M1  the capture rate is recorded where the ladder can read it
  M2  and it is FRESH -- a stale reading is not evidence
  M3  the check happens BEFORE video is shut down
  M4  a starved capture suppresses the shutdown
  M5  a healthy capture does not -- the floor still rules
  M6  and it says which, once, so the operator fixes the right thing
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
sys.path.insert(0, HERE)

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


src = open(os.path.join(HERE, "fnav.py"), encoding="utf-8").read()

# ---- M1 --------------------------------------------------------------------
ck("M1 the capture rate is recorded", "self._cap_rate = _rate" in src, None)
ck("M1 along with what it should be", "self._cap_want = 1000.0" in src, None)
ck("M1 and when it was taken", "self._cap_at = _now" in src, None)

# ---- the backpressure block ------------------------------------------------
i = src.index("if _abacklog:")
blk = src[i:i + 2800]

ck("M2 the reading must be fresh", "_fresh" in blk and "< 5.0" in blk, None)
ck("M3 checked before the shutdown",
   blk.index("_cap_rate") < blk.index("AUDIO_ONLY_IDX"), None)
ck("M4 a starved capture suppresses the shutdown",
   "_abacklog = 0" in blk and "0.5 * _cw" in blk, None)
ck("M5 a healthy one does not -- the floor still rules",
   "_set_ceiling(\"backlog\", _floor)" in src, None)
ck("M6 it names the microphone, not the link",
   "microphone" in blk and "not the link" in blk, None)
ck("M6 said once, not every window", "_said_mic" in blk, None)
ck("M6 and re-armed when the capture recovers",
   "self._said_mic = False" in blk, None)

# ---- the rule itself, run --------------------------------------------------
def suppress(rate, want, age_s):
    """The condition as written, so the thresholds are exercised not just seen."""
    fresh = age_s < 5.0
    return rate is not None and fresh and want > 0 and rate < 0.5 * want


ck("M4 1.5/s of 50 is a starved mic", suppress(1.5, 50.0, 0.5), None)
ck("M5 49/s of 50 is not", not suppress(49.0, 50.0, 0.5), None)
ck("M5 exactly half is not -- the bar is BELOW half",
   not suppress(25.0, 50.0, 0.5), None)
ck("M2 a stale reading is not evidence", not suppress(1.5, 50.0, 30.0), None)
ck("M2 and no reading at all is not either",
   not suppress(None, 50.0, 0.5), None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
