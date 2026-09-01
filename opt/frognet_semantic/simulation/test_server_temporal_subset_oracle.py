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
"""test_server_temporal_subset_oracle.py - drives the REAL server method
_SessionServer._video_drop_for to prove per-consumer temporal subsetting: each connected
consumer receives exactly the temporal layers within its cap, keyframes (TID 0) and audio
reach everyone, audio-only legs get no video, and a consumer that hasn't published a
leg_request yet defaults to the conservative base cap (call opens LOW).
"""
import os, sys, threading
from types import SimpleNamespace
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import call_media as C
import frognet_mediahost_server as S

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


def make_stub(peer_of):
    dl = SimpleNamespace(_lock=threading.Lock(), _peer_of=peer_of)
    return SimpleNamespace(_dl_listener=dl)


def vframe(tid, key=False):
    return SimpleNamespace(payload=C.pack_av_src("gorp", 1, 0, key, b"", b"V", tid=tid), seq=1)


def aframe():
    return SimpleNamespace(payload=C.pack_av_src("gorp", 2, 0, False, b"AUD", b""), seq=2)


def main():
    # A=full(L7) B=half(L6) C=base(L5) D=audio-only(L3) E=no request (defaults to base)
    peer_of = {"A": ("10.0.0.1", 9), "B": ("10.0.0.2", 9), "C": ("10.0.0.3", 9),
               "D": ("10.0.0.4", 9), "E": ("10.0.0.5", 9)}
    leg_rungs = {"10.0.0.1": 7, "10.0.0.2": 6, "10.0.0.3": 5, "10.0.0.4": 3}   # E absent
    stub = make_stub(peer_of)
    drop_for = S._SessionServer._video_drop_for

    def dropped(fr):
        d = drop_for(stub, fr, leg_rungs)
        return set() if d is None else set(d)

    # keyframe / base layer (TID 0): only the audio-only leg D loses video; everyone else keeps it
    ck("TID0 (keyframe/base): only audio-only leg dropped", dropped(vframe(0, key=True)) == {"D"})
    # mid layer (TID 1): base leg C and default-base leg E drop it; A,B keep; D dropped
    ck("TID1 (mid): base+default+audio-only dropped", dropped(vframe(1)) == {"C", "D", "E"})
    # top layer (TID 2): only full leg A keeps it
    ck("TID2 (top): all but full-cap leg dropped", dropped(vframe(2)) == {"B", "C", "D", "E"})
    # audio frame: nobody is dropped - continuous stream reaches all
    ck("audio frame reaches everyone", drop_for(stub, aframe(), leg_rungs) is None)

    # conservative start: with NO leg requests at all, everyone defaults to base - a call opens
    # LOW (only base-layer video flows until consumers raise their own requests)
    ck("no leg requests -> default base: TID0 to all", dropped_with(vframe(0, key=True), {}) == set())
    ck("no leg requests -> default base: TID2 to none", dropped_with(vframe(2), {}) == {"A", "B", "C", "D", "E"})

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


def dropped_with(fr, legs):
    peer_of = {"A": ("10.0.0.1", 9), "B": ("10.0.0.2", 9), "C": ("10.0.0.3", 9),
               "D": ("10.0.0.4", 9), "E": ("10.0.0.5", 9)}
    stub = make_stub(peer_of)
    d = S._SessionServer._video_drop_for(stub, fr, legs)
    return set() if d is None else set(d)


if __name__ == "__main__":
    sys.exit(main())
