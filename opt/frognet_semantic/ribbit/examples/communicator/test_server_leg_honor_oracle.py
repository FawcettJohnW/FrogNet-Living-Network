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
test_server_leg_honor_oracle.py - the LIVE media server honors per-leg requests AND does
non-blocking whole-frame drop on the downlink.

Proves:
  H1  broadcast is NON-BLOCKING whole-frame: a consumer whose send buffer is full (not
      writable) has the WHOLE frame dropped; other consumers still receive it
  H2  drop_for skips named conns entirely (per-leg: this rung not for this leg)
  H3  _legs_without_video maps a client addr that requested an audio-only rung (<=L4) to its
      downlink conn, so video frames are dropped for that leg while a full-rung leg keeps them
  H4  audio frames (no video) are never in drop_for (the call survives the downgrade)
"""
import os, sys, types
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# stub frognet_tuples before importing the server
_ft = types.ModuleType("frognet_tuples")
_ft.put = lambda *a, **k: None
_ft.get_all = lambda *a, **k: []
_ft.get = lambda *a, **k: []
_ft.my_ip = lambda *a, **k: "10.250.250.1"
_ft.DEFAULT_DBHOST = "databasehost.frognet"
sys.modules.setdefault("frognet_tuples", _ft)

import select as _select
import frognet_mediahost_server as S
from call_media import pack_av_src

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


class FakeSock:
    """Fake socket exposing just what broadcast() uses: select-writability + sendall.
    writable=False simulates a full send buffer (congested downlink)."""
    def __init__(self, writable=True):
        self.writable = writable
        self.sent = 0
        self.closed = False
    def sendall(self, b): self.sent += len(b)
    def fileno(self): return -1
    def close(self): self.closed = True

# monkeypatch select.select to consult our fake writability
_real_select = _select.select
def fake_select(r, w, x, t):
    return ([], [s for s in w if getattr(s, "writable", True)], [])
_select.select = fake_select

print("=== live server: non-blocking whole-frame drop + per-leg honor ===")

# H1: one healthy consumer, one congested (buffer full). Frame goes to healthy, dropped for congested.
dl = S._DownlinkListener("0.0.0.0", 9999)
good = FakeSock(writable=True)
congested = FakeSock(writable=False)
dl._conns = [good, congested]
dl._peer_of = {good: ("10.0.0.2", 5000), congested: ("10.0.0.3", 5001)}
payload = pack_av_src("alice", 1, 0, True, b"AUDIO", b"VIDEODATA")
dl.broadcast(payload)
ck("H1 healthy consumer received the frame", good.sent > 0, good.sent)
ck("H1 congested consumer got WHOLE frame dropped (nothing sent)", congested.sent == 0, congested.sent)
ck("H1 would-block drop counted", dl._wouldblock_drops == 1, dl._wouldblock_drops)

# H2: drop_for skips a conn entirely
good.sent = 0; congested.writable = True; congested.sent = 0
dl.broadcast(payload, drop_for={congested})
ck("H2 drop_for skips that conn", congested.sent == 0 and good.sent > 0, (good.sent, congested.sent))

# H3/H4: _legs_without_video maps an audio-only leg's addr -> its conn
sess = S._SessionServer.__new__(S._SessionServer)
sess.session = "alice-grp-1"
sess._dl_listener = dl
# alice requested audio-only (L4), gorp requested full (L7)
leg_rungs = {"10.0.0.3": 4, "10.0.0.2": 7}      # by client addr (peer IP)
dl._conns = [good, congested]
dl._peer_of = {good: ("10.0.0.2", 5000), congested: ("10.0.0.3", 5001)}
drop = sess._legs_without_video(leg_rungs)
ck("H3 audio-only leg (L4 @ .3) -> its conn in drop_for", congested in (drop or set()), drop)
ck("H3 full leg (L7 @ .2) -> NOT dropped", good not in (drop or set()), drop)

# H4: an audio-only frame (no video) should not trigger video-dropping
audio_only = pack_av_src("alice", 2, 0, False, b"AUDIOONLY", b"")
ck("H4 audio-only frame has no video (no video-drop applies)", not sess._frame_has_video(
   types.SimpleNamespace(payload=audio_only)))
ck("H4 a video frame is detected as video", sess._frame_has_video(
   types.SimpleNamespace(payload=payload)))

_select.select = _real_select
print(f"\n=== {_p} passed, {_f} failed ===")
print("ORACLE GREEN" if _f == 0 else "ORACLE RED")
sys.exit(1 if _f else 0)
