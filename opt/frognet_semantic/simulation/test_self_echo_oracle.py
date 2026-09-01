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
"""test_self_echo_oracle.py - the mediahost is a dumb fan: it broadcasts every frame to every
consumer, including the one that produced it. That self-echo eats the producing leg's downlink
and queues the PEER's audio behind it (the throb), even though the client discards its own
frames on receive. The server must drop a frame for the consumer whose node == the frame's src
(minus-self), for BOTH audio and video, matched by conn peer IP == the node's addr - without
ever dropping it for anyone else.

Drives the REAL _SessionServer._self_echo_drop_for against a fake downlink listener.
"""
import os, sys, threading, types
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.environ.get("FN_COMMUNICATOR_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator"))
sys.path.insert(0, BUNDLE)
import frognet_mediahost_server as S
from call_media import pack_av_src

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")


class FakeDL:
    def __init__(self, peer_of):
        self._lock = threading.Lock()
        self._peer_of = peer_of


def frame(src, *, audio=b"", video=b"\x10\x02"):
    payload = pack_av_src(src, 1, 0, bool(video), audio, video)
    return types.SimpleNamespace(payload=payload)


def drop_for(session_self, fr, addr_of_node):
    return S._SessionServer._self_echo_drop_for(session_self, fr, addr_of_node)


def main():
    # two consumers: john @ .1 (connA), gorp @ .20 (connB)
    connA, connB = object(), object()
    peer_of = {connA: ("10.250.250.1", 5001), connB: ("10.250.250.20", 5002)}
    sess = types.SimpleNamespace(_dl_listener=FakeDL(peer_of), session="t")
    addr = {"john": "10.250.250.1", "gorp": "10.250.250.20"}

    d = drop_for(sess, frame("john", video=b"\x10\x02\x03"), addr)
    ck("E1 video from john is dropped for john's conn only", d == {connA}, f"{d}")

    d = drop_for(sess, frame("gorp", video=b"\x10\x02\x03"), addr)
    ck("E2 video from gorp is dropped for gorp's conn only", d == {connB}, f"{d}")

    d = drop_for(sess, frame("john", audio=b"\x00" * 320, video=b""), addr)
    ck("E3 AUDIO from john is also dropped for john (minus-self covers audio)", d == {connA}, f"{d}")

    d = drop_for(sess, frame("mallory", video=b"\x10\x02\x03"), addr)
    ck("E4 frame from an unknown/absent node drops for nobody", d is None, f"{d}")

    d = drop_for(sess, frame("john", video=b"\x10\x02\x03"), {})
    ck("E5 no addr map yet -> no self-drop (echo until a leg_request lands)", d is None, f"{d}")

    # a third consumer at a different IP must never be collateral
    connC = object()
    peer3 = dict(peer_of); peer3[connC] = ("10.250.250.50", 5003)
    sess3 = types.SimpleNamespace(_dl_listener=FakeDL(peer3), session="t")
    d = drop_for(sess3, frame("john", video=b"\x10\x02\x03"), addr)
    ck("E6 other consumers are never collateral to a self-drop", d == {connA}, f"{d}")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
