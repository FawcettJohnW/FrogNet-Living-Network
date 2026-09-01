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
test_ladder_rate_v2_oracle.py   [LADDER_RATE_V2]

John's ladder rule, 2026-08-03, against the real fnav.Bearer.

    DOWN immediately on ANY of:
        more than 3 consecutive samples carrying drops
        a one-second window with more than 10 drops
        audio shed by the wire (the protected floor was refused)

    UP only when BOTH:
        3 whole seconds at zero drops
        the sender is making the FULL frame rate at the current rung

The numbers come from a live [VID-TX] capture: sustained 10-13 drops/s at
1280x720 while the ladder oscillated L6/L7 and never walked down. sample() is
called ONCE PER FRAME, ~23/s, so ten drops arrive scattered among thirteen
clean samples -- a pure consecutive-run rule finds runs of both inside one
second. The per-second window is what makes a RATE visible to a per-frame
sampler.

This SUPERSEDES test_ladder_audio_oracle.py's ladder half (its 3-stressed-down
/ 5-clean-up checks encode the earlier rule). Its AUDIO half still applies and
still passes.
"""
import sys, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import fnav

F = []
def ck(n, c):
    print(("  PASS  " if c else "  FAIL  ") + n)
    if not c: F.append(n)

B = fnav.Bearer
print("\n[LADDER_RATE_V2] ladder\n")

b = B(7, floor=3)
for sec in range(3):
    for i in range(23):
        b.sample(0, 1 if i % 2 == 0 else 0, fps_sent=13.0, fps_target=24.0)
    time.sleep(1.01)
    b.sample(0, 0, fps_sent=13.0, fps_target=24.0)
ck(f"A  sustained ~11 drops/s walks DOWN (idx {b.idx}, was 7)", b.idx < 7)
ck(f"A2 and does not oscillate back to the ceiling (idx {b.idx})", b.idx <= 5)

b = B(7, floor=0)
for _ in range(4): b.sample(0, 1)
ck(f"B  4 consecutive reads with drops -> down (idx {b.idx})", b.idx == 6)
b = B(7, floor=0)
for _ in range(3): b.sample(0, 1)
ck(f"B2 3 consecutive is not MORE than 3 -> hold (idx {b.idx})", b.idx == 7)

b = B(7, floor=0); b._sec_t0 = time.time() - 1.5
b.sample(0, 11)
ck(f"C  >10 drops in one second -> down (idx {b.idx})", b.idx == 6)

b = B(7, floor=0)
b.sample(0, 0, audio_shed=1)
ck(f"D  audio shed -> immediate video downgrade (idx {b.idx})", b.idx == 6)

def whole_sec(b, drops, fps, n=1):
    """One whole second of samples, then close it."""
    for _ in range(n):
        b._sec_t0 = time.time()
        for i in range(23):
            b.sample(0, 1 if (drops and i < drops) else 0,
                     fps_sent=fps, fps_target=24.0)
        b._sec_t0 = time.time() - 1.5
        b.sample(0, 0, fps_sent=fps, fps_target=24.0)

# Up is harder than down. A second joins the streak only if it was clean AND ran
# at full rate THROUGHOUT -- zero drops alone is not health, because a box making
# 13 fps sheds nothing on a fast LAN and would otherwise accumulate clean seconds
# while failing the rung it is already on.
b = B(7, floor=0); b.idx = 5
whole_sec(b, 0, 13.0, n=6)
ck(f"E  6 clean seconds at 13/24 fps -> NO upgrade (idx {b.idx})", b.idx == 5)

b = B(7, floor=0); b.idx = 5
whole_sec(b, 0, 23.8, n=3)
ck(f"E2 3 seconds clean AND at full rate -> upgrade (idx {b.idx})", b.idx == 6)

b = B(7, floor=0); b.idx = 5
whole_sec(b, 0, 23.8, n=2); whole_sec(b, 0, 13.0, n=1); whole_sec(b, 0, 23.8, n=2)
ck(f"E3 one short second resets the streak (idx {b.idx})", b.idx == 5)

b = B(7, floor=0); b.idx = 5
whole_sec(b, 0, 23.8, n=2); whole_sec(b, 3, 23.8, n=1); whole_sec(b, 0, 23.8, n=2)
ck(f"E4 one second with drops resets the streak (idx {b.idx})", b.idx == 5)

b = B(7, floor=0); b._sec_t0 = time.time() - 1.5
b.sample(0, 6, fps_sent=23.0, fps_target=24.0)
ck(f"E5 more than 5 drops in a second -> down (idx {b.idx})", b.idx == 6)

# Below the video floor rate is a downgrade on its own. A box producing 8 fps
# sheds nothing on a fast LAN, so no drop signal ever fires and it would sit
# there rendering a slideshow at a rung it cannot make.
b = B(7, floor=3)
for _ in range(3): b.sample(0, 0, fps_sent=8.0, fps_target=24.0)
ck(f"F  3 reads below 10 fps is not MORE than 3 -> hold (idx {b.idx})", b.idx == 7)
b.sample(0, 0, fps_sent=8.0, fps_target=24.0)
ck(f"F2 4 reads below 10 fps -> down (idx {b.idx})", b.idx == 6)

b = B(7, floor=3)
for f in (8.0, 8.0, 23.0, 8.0, 8.0):
    b.sample(0, 0, fps_sent=f, fps_target=24.0)
ck(f"F3 one fast read breaks the slow run -> hold (idx {b.idx})", b.idx == 7)

b = B(7, floor=3)
for _ in range(200): b.sample(0, 1)
ck(f"F4 floors at the protected audio rung (idx {b.idx})", b.idx == 3)

print("\n[AUDIO_RETRIES_V1] wire\n")
import socket
sa, sb = socket.socketpair()
p = fnav.SotFDataPlane(sa)
ck(f"G  audio is retried at least twice ({fnav.SotFDataPlane.AUDIO_RETRIES})",
   fnav.SotFDataPlane.AUDIO_RETRIES >= 2)
big = b"\xaa" * 60000
for _ in range(200):
    p.put(big, droppable=True)
    if p.dead: break
before = p.audio_sheds
for _ in range(5):
    p.put(b"\xbb" * 8000, droppable=False)
    if p.dead: break
ck(f"H  a shed audio frame arms a video downgrade "
   f"(pending={p._audio_shed_pending})",
   p._audio_shed_pending > 0 or p.audio_sheds == before)
n = p.take_audio_sheds()
ck(f"H2 take_audio_sheds() drains ({n} then {p.take_audio_sheds()})",
   p.take_audio_sheds() == 0)
sa.close(); sb.close()

print()
if F:
    print(f"ORACLE FAIL ({len(F)}):")
    for x in F: print("  -", x)
    sys.exit(1)
print("ORACLE PASS")
