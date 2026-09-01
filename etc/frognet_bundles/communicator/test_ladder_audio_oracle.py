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
Oracle for the SotF ladder hysteresis + audio-priority rules.

Proves, against the REAL fnav.Bearer and fnav.SotFDataPlane (no ffmpeg/cv2/portaudio):

  LADDER (John's spec):
    - DOWN after 3 consecutive stressed samples, and it keeps stepping down while
      stress persists  -> going DOWN is fast.
    - UP only after 5 consecutive clean samples at the current rung, one step per
      clean streak       -> coming UP is slow.
    - A single clean sample breaking a stress run prevents the downgrade (true
      consecutive counting, not a leaky average).
    - The bearer never steps BELOW the protected audio floor on congestion alone.

  AUDIO PRIORITY (SotFDataPlane):
    - Under a demo throttle that sheds video, AUDIO is never dropped by the throttle
      (audio_sheds stays 0) and every audio frame reaches the wire; video sheds.

This oracle uses the NEW Bearer(floor=...) API and the NEW audio_sheds counter, so it
ERRORS on the old code (no floor arg, no audio_sheds) in addition to the behavioural
checks below - fail-on-old / pass-on-new.
"""
import os, sys, types, socket

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Stub the audio DSP module so fnav imports without PortAudio/numpy/samplerate.
_stub = types.ModuleType("fnphone_pa")
_stub.AUDIO_RATE = 16000
_stub.AUDIO_CH = 1
sys.modules.setdefault("fnphone_pa", _stub)

import fnav  # the real module under test
import sotf_ladder as L

FAILS = []
def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)

def feed(b, n, dropped):
    last = b.idx
    for _ in range(n):
        last = b.sample(0, dropped)
    return last

print("[ladder] hysteresis")

# A) sustained stress steps DOWN fast: 9 consecutive stressed -> >= 3 steps down.
b = fnav.Bearer(7, floor=0)
feed(b, 9, dropped=1)
check("9 stressed -> stepped down >=3 rungs (fast down)", b.idx <= 7 - 3)

# B) coming UP is slow + gated at 5 clean, even right after a step (no wall-clock cap masking).
b = fnav.Bearer(7, floor=0)
feed(b, 3, dropped=1)             # one downgrade -> idx 6, _last change 'just now'
start = b.idx
feed(b, 4, dropped=0)            # 4 clean: NOT enough to climb
check("4 clean -> no upgrade (need 5)", b.idx == start)
b.sample(0, 0)                  # 5th clean -> exactly one step up
check("5 clean -> exactly one step up", b.idx == start + 1)

# C) consecutive counting: a clean sample breaks the stress run -> no downgrade.
b = fnav.Bearer(7, floor=0)
b.sample(0, 1); b.sample(0, 1); b.sample(0, 0); b.sample(0, 1); b.sample(0, 1)
check("stress run broken by a clean sample -> no downgrade", b.idx == 7)

# D) protected audio floor: congestion never silences audio (mic present -> floor L3).
b = fnav.Bearer(7, floor=3)
feed(b, 60, dropped=1)
check("sustained stress floors at audio L3, never below", b.idx == 3)

print("[audio] priority under demo throttle")

# Real socketpair so writability is real. Big rcvbuf so sends never block.
s_send, s_recv = socket.socketpair()
s_recv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
plane = fnav.SotFDataPlane(s_send)
plane.throttle_bps = 8000        # ~1 KB/s: a constrained wire that must shed video

N = 25
for i in range(N):
    apayload = fnav.pack_typed(fnav.KIND_AUDIO, "spk", b"A" * 320)
    vpayload = fnav.pack_typed(fnav.KIND_VIDEO, "cam", b"V" * 4000)
    plane.put(apayload, droppable=False)   # audio: protected
    plane.put(vpayload, droppable=True)    # video: droppable

check("audio never shed by throttle (audio_sheds == 0)", getattr(plane, "audio_sheds", -1) == 0)
check("video sheds under throttle (sheds > 0)", plane.sheds > 0)

# Drain the wire and count audio frames that actually made it across.
plane.close()
s_send.close()
got_audio = 0
buf = b""
s_recv.setblocking(True)
s_recv.settimeout(1.0)
try:
    while True:
        chunk = s_recv.recv(65536)
        if not chunk:
            break
        buf += chunk
except socket.timeout:
    pass
# parse length-prefixed frames
i = 0
while i + 4 <= len(buf):
    (ln,) = fnav._LEN.unpack(buf[i:i+4]); i += 4
    if i + ln > len(buf):
        break
    kind, src, payload = fnav.unpack_typed(buf[i:i+ln]); i += ln
    if kind == fnav.KIND_AUDIO:
        got_audio += 1
check(f"all {N} audio frames reached the wire (got {got_audio})", got_audio == N)

print()
if FAILS:
    print(f"ORACLE FAILED ({len(FAILS)}): " + "; ".join(FAILS))
    sys.exit(1)
print("ORACLE PASSED")
