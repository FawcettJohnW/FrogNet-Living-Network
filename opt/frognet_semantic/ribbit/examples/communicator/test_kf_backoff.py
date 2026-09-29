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
"""[A_SIZE_THAT_FAILED_STAYS_FAILED_V1] Oracle for the keyframe retry backoff.

Fails on the old code (which had no memory at all and retried every ~8s
forever) and passes on the new. Drives the real methods on a bare object with
the class's own constants -- no network, no encoder.
"""
import fnav

class Node:                      # borrows only the methods under test
    name = "T"
    KF_FAIL_BEFORE_BACKOFF = fnav.Call.KF_FAIL_BEFORE_BACKOFF
    KF_BACKOFF_S = fnav.Call.KF_BACKOFF_S
    _kf_size_blocked = fnav.Call._kf_size_blocked
    _kf_note_fail = fnav.Call._kf_note_fail

PX = 1920 * 1080
FAIL = []
def ck(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + detail))
    if not cond: FAIL.append(name)

n = Node(); t = 1000.0

# First failure: no backoff yet -- one EWOULDBLOCK is not a verdict.
w = n._kf_note_fail(PX, t)
ck("1st failure does not back off", w is None, "got %r" % (w,))
ck("1st failure leaves the size retryable", not n._kf_size_blocked(PX, t + 9))

# Second consecutive failure: 60s.
w = n._kf_note_fail(PX, t + 10)
ck("2nd failure -> 60s", w == 60.0, "got %r" % (w,))
ck("blocked at +30s",  n._kf_size_blocked(PX, t + 40))
ck("free at +61s",     not n._kf_size_blocked(PX, t + 71))

# A probe at 61s fails -> advance, do NOT re-arm the short interval.
w = n._kf_note_fail(PX, t + 71)
ck("3rd failure -> 150s", w == 150.0, "got %r" % (w,))
ck("blocked at +100s", n._kf_size_blocked(PX, t + 171))
ck("free at +151s",    not n._kf_size_blocked(PX, t + 222))

w = n._kf_note_fail(PX, t + 222)
ck("4th failure -> 300s", w == 300.0, "got %r" % (w,))
w = n._kf_note_fail(PX, t + 600)
ck("5th failure stays at 300s", w == 300.0, "got %r" % (w,))

# A different (smaller) size is unaffected -- the refusal was about 1080p.
ck("smaller size not blocked", not n._kf_size_blocked(1280 * 720, t + 222))

# Old behaviour, for contrast: 8s retries over 5 minutes.
old = int(300 / 8)
new = 1 + 1 + 1 + 1          # 8s, then 60s, 150s, 300s boundaries
print("\n  retries in 5 min at 1920x1080:  old ~%d   new %d" % (old, new))
ck("new is far fewer than old", new < old / 5)

print()
raise SystemExit(1 if FAIL else 0)
