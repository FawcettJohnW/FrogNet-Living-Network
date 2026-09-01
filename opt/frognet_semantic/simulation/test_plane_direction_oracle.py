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
test_plane_direction_oracle.py   [READER_DOES_NOT_WRITE_V1]

Proves the directional invariant of the media planes, by driving the REAL
MediaServer methods with instrumented sockets that record every syscall.

    python3 test_plane_direction_oracle.py

FAILS on pre-[OUTPLANE_NO_PEEK_V1] source, PASSES after.

THE INVARIANT
-------------
A plane is one-directional and the socket under it is used in exactly one
direction:

    viewer socket  (OutPlane, _add_viewer)   -- WRITTEN, never read
    sender socket  (InPlane,  _relay_sender) -- READ, never written

That is not a style preference.  Reading a write-only socket is what forced
conn.setblocking(True) into _add_viewer -- recv(1, MSG_PEEK) needs blocking
mode -- and blocking is a property of the FILE DESCRIPTOR, not of a direction.
One recv() for EOF detection therefore made every OutPlane.sendall() on that
same fd block, and the fan-out stalled on the slowest viewer instead of
dropping frames.

The peek also consumed nothing: recv(1, MSG_PEEK) leaves the byte in the
buffer and nothing else ever reads that socket, so a viewer that sent anything
would have it sit there forever.  It was never header inspection -- it was an
EOF probe, and EOF is already known from the write side: send() classifies
is_peer_gone() and sets plane.alive = False.  A failed write PROVES the peer
is gone; a readable socket is only a hint.
"""
from __future__ import annotations
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frognet_media_planes as MP
from frognet_media_planes import MediaServer, OutPlane, InPlane, SendResult

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


class WatchedSocket:
    """Wraps a real socket and records which direction it is used in.

    Everything is delegated to the real socket, so the code under test gets
    genuine kernel behaviour -- this only counts.
    """

    def __init__(self, sock, label):
        object.__setattr__(self, "_sock", sock)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "reads", 0)
        object.__setattr__(self, "writes", 0)
        object.__setattr__(self, "blocking_flips", [])

    # -- recorded ---------------------------------------------------------
    def recv(self, *a, **k):
        object.__setattr__(self, "reads", self.reads + 1)
        return self._sock.recv(*a, **k)

    def recv_into(self, *a, **k):
        object.__setattr__(self, "reads", self.reads + 1)
        return self._sock.recv_into(*a, **k)

    def send(self, *a, **k):
        object.__setattr__(self, "writes", self.writes + 1)
        return self._sock.send(*a, **k)

    def sendall(self, *a, **k):
        object.__setattr__(self, "writes", self.writes + 1)
        return self._sock.sendall(*a, **k)

    def setblocking(self, flag):
        self.blocking_flips.append(flag)
        return self._sock.setblocking(flag)

    # -- passthrough ------------------------------------------------------
    def __getattr__(self, name):
        return getattr(self._sock, name)


def _pair(label_a, label_b):
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    return WatchedSocket(a, label_a), b


def test_viewer_socket_is_write_only():
    """_add_viewer must never read the socket it hands to OutPlane."""
    srv = MediaServer(port=0)
    a, b = _pair("viewer", "peer")
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.6)                       # let it settle into its wait loop

    check(a.reads == 0,
          f"A  viewer socket was NEVER read ({a.reads} recv calls)")
    check(a.blocking_flips == [False],
          f"A2 blocking mode set once, to non-blocking, by OutPlane "
          f"(flips={a.blocking_flips})")

    # and it is genuinely usable for writing
    plane = srv._sessions["sess"][0].plane
    r = plane.send(1, 3, b"\xaa" * 64)
    check(r is SendResult.SENT, f"A3 the plane can write ({r.name})")
    check(a.writes > 0, f"A4 writes went to the socket ({a.writes})")
    check(a.reads == 0,
          f"A5 still no reads after a send ({a.reads})")

    srv._stop.set()
    a.close(); b.close()
    t.join(timeout=3.0)


def test_sender_socket_is_read_only():
    """_relay_sender must never write the socket it hands to InPlane."""
    srv = MediaServer(port=0)
    a, b = _pair("sender", "peer")
    t = threading.Thread(target=srv._relay_sender, args=("sess", a, "s1"),
                         daemon=True)
    t.start()
    time.sleep(0.3)

    # feed it one real framed frame from the far end
    payload = b"\xbb" * 128
    b.sendall(MP.frame_bytes(7, 2, payload))
    time.sleep(0.4)

    check(a.writes == 0,
          f"B  sender socket was NEVER written ({a.writes} send calls)")
    check(a.reads > 0, f"B2 it was read ({a.reads} recv calls)")
    check(a.blocking_flips == [True],
          f"B3 blocking mode set once, to blocking, by InPlane "
          f"(flips={a.blocking_flips})")

    srv._stop.set()
    try:
        b.close()
    except OSError:
        pass
    a.close()
    t.join(timeout=3.0)


def test_eof_comes_from_the_write_side():
    """With no reads available, the viewer's departure is still detected --
    from send()'s is_peer_gone(), not from a socket probe."""
    srv = MediaServer(port=0)
    a, b = _pair("viewer", "peer")
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.4)
    plane = srv._sessions["sess"][0].plane

    b.close()                                    # peer vanishes
    for seq in range(200):
        if plane.send(seq, 3, b"\xcc" * 4096) is SendResult.FAILED:
            break

    check(plane.alive is False,
          "C  peer departure detected by the WRITE side (is_peer_gone)")
    check(a.reads == 0,
          f"C2 detected with zero reads of the socket ({a.reads})")

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and srv._sessions.get("sess"):
        time.sleep(0.1)
    check(not srv._sessions.get("sess"),
          "C3 and the plane is reaped from _sessions")

    srv._stop.set()
    a.close()
    t.join(timeout=3.0)


def main():
    print("\n[READER_DOES_NOT_WRITE_V1] plane direction invariant\n")
    test_viewer_socket_is_write_only()
    test_sender_socket_is_read_only()
    test_eof_comes_from_the_write_side()
    print()
    if _fails:
        print(f"ORACLE FAIL ({len(_fails)}):")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print("ORACLE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
