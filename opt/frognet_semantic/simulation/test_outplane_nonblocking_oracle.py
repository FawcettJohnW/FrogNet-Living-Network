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
test_outplane_nonblocking_oracle.py   [OUTPLANE_STAYS_NONBLOCKING_V1]

Drives the REAL MediaServer._add_viewer and the REAL OutPlane over a real
socketpair.  No network, no root, no nodes.

    python3 test_outplane_nonblocking_oracle.py

FAILS on pre-[OUTPLANE_STAYS_NONBLOCKING_V1] source, PASSES after.

THE BUG
-------
MediaServer._add_viewer() constructs OutPlane(conn), which sets `conn`
non-blocking so the fan-out can drop whole frames and never stall.  It then
called conn.setblocking(True) on THE SAME FD to do a blocking
recv(1, MSG_PEEK) for EOF detection.

Blocking is a property of the file descriptor, not of a direction.  So every
registered viewer's OutPlane became blocking, and OutPlane.send()'s sendall()
waited for the peer to drain instead of raising BlockingIOError and returning
SendResult.DROPPED.  The whole fan-out backlogged behind the slowest viewer.

WHY THE PRE-CHECK DOES NOT SAVE IT
----------------------------------
send_buffer_free() is deliberately approximate -- it halves SO_SNDBUF to guess
the usable size (see its docstring).  When that guess is optimistic, the write
is attempted anyway and the BlockingIOError handler is what turns it into a
clean drop.  A blocking fd removes that handler's reachability entirely.
"""
from __future__ import annotations
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frognet_media_planes as MP
from frognet_media_planes import MediaServer, OutPlane, SendResult

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


def _pair():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    return a, b


def test_a_outplane_sets_nonblocking():
    """Baseline: OutPlane's own contract."""
    a, b = _pair()
    try:
        OutPlane(a, "sess", who="v1")
        check(a.getblocking() is False,
              "A  OutPlane() leaves the fd NON-BLOCKING")
    finally:
        a.close(); b.close()


def test_b_add_viewer_preserves_nonblocking():
    """The regression: does _add_viewer leave the fd as OutPlane set it?"""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.4)                     # let it reach its EOF-detect loop
    try:
        blocking = a.getblocking()
        check(blocking is False,
              "B  _add_viewer leaves the SAME fd non-blocking "
              f"(getblocking()={blocking})")
        registered = len(srv._sessions.get("sess", [])) == 1
        check(registered, "B2 the viewer is registered for fan-out")
    finally:
        srv._stop.set()
        a.close(); b.close()
        t.join(timeout=3.0)


def test_c_send_drops_instead_of_stalling():
    """The symptom: a viewer that never reads must not stall the sender.

    Stuff the socket until the kernel will take no more, then send one more
    frame and TIME it.  Non-blocking -> returns DROPPED in microseconds.
    Blocking -> sendall() waits on a peer that is not reading."""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.4)

    plane = srv._sessions["sess"][0].plane
    payload = b"\xab" * 4096
    stuffed = 0
    try:
        # b never reads -> a's send buffer fills.
        for seq in range(20000):
            r = plane.send(seq, 3, payload)
            if r is SendResult.DROPPED:
                break
            if r is SendResult.FAILED:
                break
            stuffed += 1
        else:
            check(False, "C0 buffer never filled - test inconclusive")
            return

        t0 = time.monotonic()
        r = plane.send(999999, 3, payload)
        elapsed = time.monotonic() - t0

        print(f"        stuffed {stuffed} frames, then send() took "
              f"{elapsed*1000:.1f} ms and returned {r.name}")
        check(elapsed < 0.25,
              f"C  send() on a full buffer returns promptly "
              f"({elapsed*1000:.1f} ms, must be < 250 ms)")
        check(r is SendResult.DROPPED,
              f"C2 it returns DROPPED, not a stall ({r.name})")
        check(plane._dropped > 0,
              f"C3 the drop is counted ({plane._dropped})")
    finally:
        srv._stop.set()
        a.close(); b.close()
        t.join(timeout=3.0)


def test_d_fanout_slow_viewer_does_not_block_fast_one():
    """A stalled viewer must not delay delivery to a healthy one."""
    srv = MediaServer(port=0)
    slow_a, slow_b = _pair()
    fast_a, fast_b = _pair()
    ts = []
    for name, sk in (("slow", slow_a), ("fast", fast_a)):
        th = threading.Thread(target=srv._add_viewer,
                              args=("sess", sk, name), daemon=True)
        th.start(); ts.append(th)
    time.sleep(0.4)

    payload = b"\xcd" * 4096
    try:
        # fill the slow viewer only
        for v in srv._sessions["sess"]:
            if v.who != "slow":
                continue
            for seq in range(20000):
                if v.plane.send(seq, 3, payload) is not SendResult.SENT:
                    break

        drainer = threading.Thread(
            target=lambda: [fast_b.recv(65536) for _ in range(200)],
            daemon=True)
        drainer.start()

        t0 = time.monotonic()
        frame = MP.Frame(seq=1, level_idx=3, payload=payload)
        srv._fan_out("sess", frame, exclude_who="nobody")
        elapsed = time.monotonic() - t0
        print(f"        _fan_out with one stalled viewer took "
              f"{elapsed*1000:.1f} ms")
        check(elapsed < 0.25,
              f"D  fan-out is not delayed by a stalled viewer "
              f"({elapsed*1000:.1f} ms, must be < 250 ms)")
    finally:
        srv._stop.set()
        for s in (slow_a, slow_b, fast_a, fast_b):
            s.close()
        for th in ts:
            th.join(timeout=3.0)


def test_g_windows_no_siocoutq():
    """[EWOULDBLOCK_DISCARDS_V1] send_buffer_free() is fcntl/termios -- absent on
    Windows, where it returns -1. The plane must still work: the WRITE is the
    test, not a query. Previously free<0 killed the plane on its first frame."""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"), daemon=True)
    t.start(); time.sleep(0.4)
    plane = srv._sessions["sess"][0].plane
    orig = MP.send_buffer_free
    MP.send_buffer_free = lambda sock: -1          # pretend Windows
    try:
        r = plane.send(1, 3, b"\x11" * 2048)
        print(f"        no SIOCOUTQ: send() -> {r.name}, alive={plane.alive}")
        check(r is SendResult.SENT,
              f"G  a normal frame still SENDs with no SIOCOUTQ ({r.name})")
        check(plane.alive, "G2 the plane is NOT killed by an unqueryable buffer")
    finally:
        MP.send_buffer_free = orig
        srv._stop.set(); a.close(); b.close(); t.join(timeout=3.0)


def test_h_full_buffer_drops_not_fails():
    """A wire that will not take the frame is a DROP, not a dead plane."""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"), daemon=True)
    t.start(); time.sleep(0.4)
    plane = srv._sessions["sess"][0].plane
    payload = b"\x22" * 4096
    worst, drops = 0.0, 0
    try:
        for seq in range(400):
            t0 = time.monotonic()
            r = plane.send(seq, 3, payload)
            worst = max(worst, time.monotonic() - t0)
            if r is SendResult.DROPPED:
                drops += 1
            if r is SendResult.FAILED:
                break
        print(f"        400 frames, peer not reading: {drops} dropped, "
              f"worst send() {worst*1000:.1f} ms, alive={plane.alive}")
        check(drops > 0, f"H  a full buffer DROPS ({drops})")
        check(plane.alive, "H2 and the plane stays alive")
        check(worst < 0.25, f"H3 no send() stalled ({worst*1000:.1f} ms)")
    finally:
        srv._stop.set(); a.close(); b.close(); t.join(timeout=3.0)


def test_e_no_fallback_exists():
    """[NO_FALLBACK_V1] There is no attempt-and-classify path at all."""
    check(not hasattr(OutPlane, "_send_fallback"),
          "E  OutPlane has NO _send_fallback (no fallback path)")

    # free < 0 is a FAULT, not a reason to try anyway: it means whole-or-nothing
    # cannot be guaranteed, and discovering that after bytes move is the desync
    # the pre-check exists to prevent.
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start(); time.sleep(0.4)
    plane = srv._sessions["sess"][0].plane
    orig = MP.send_buffer_free
    MP.send_buffer_free = lambda sock: -1          # cannot query
    try:
        t0 = time.monotonic()
        r = plane.send(1, 3, b"\xef" * 4096)
        dt = time.monotonic() - t0
        print(f"        free<0: send() returned {r.name} after {dt*1000:.1f} ms")
        check(r is SendResult.FAILED,
              f"E2 unqueryable send buffer -> FAILED, never attempted ({r.name})")
        check(plane.alive is False, "E3 the plane is tossed, not left half-usable")
        check(dt < 0.25, f"E4 it fails immediately, no drain loop ({dt*1000:.1f} ms)")
    finally:
        MP.send_buffer_free = orig
        srv._stop.set(); a.close(); b.close(); t.join(timeout=3.0)


def test_e_old_fallback_path_stalls_when_blocking():
    """The path where the blocking fd actually bites.

    OutPlane.send() only reaches sendall()/_send_fallback when the pre-check
    thinks there is room.  send_buffer_free() returns -1 when SIOCOUTQ is
    unavailable (its own docstring), and then _send_fallback runs: it sends,
    and if the socket blocks BEFORE any byte it raises BlockingIOError for a
    clean drop.  On a BLOCKING fd that raise never happens -- send() waits on
    the peer instead, up to _send_fallback's 2.0s deadline.

    Force free == -1 and time it."""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.4)
    plane = srv._sessions["sess"][0].plane
    payload = b"\xef" * 4096

    orig = MP.send_buffer_free
    MP.send_buffer_free = lambda sock: -1        # SIOCOUTQ unavailable
    try:
        for seq in range(20000):                 # fill it (b never reads)
            t0 = time.monotonic()
            r = plane.send(seq, 3, payload)
            dt = time.monotonic() - t0
            if dt > 0.25 or r is not SendResult.SENT:
                break
        print(f"        fallback path: send() returned {r.name} "
              f"after {dt*1000:.1f} ms")
        check(dt < 0.25,
              f"E  _send_fallback returns promptly on a full buffer "
              f"({dt*1000:.1f} ms, must be < 250 ms)")
        check(r is SendResult.DROPPED,
              f"E2 it returns DROPPED, not a stall ({r.name})")
    finally:
        MP.send_buffer_free = orig
        srv._stop.set()
        a.close(); b.close()
        t.join(timeout=3.0)


def test_f_dead_plane_is_reaped():
    """A viewer whose peer vanished must be removed from _sessions.

    plane.alive is set by send()'s is_peer_gone() branch, but nothing used to
    consume it -- the plane stayed registered and _fan_out kept calling send()
    on it every frame.  _add_viewer now parks on it."""
    srv = MediaServer(port=0)
    a, b = _pair()
    t = threading.Thread(target=srv._add_viewer, args=("sess", a, "v1"),
                         daemon=True)
    t.start()
    time.sleep(0.4)
    check(len(srv._sessions.get("sess", [])) == 1, "F0 viewer registered")

    b.close()                                   # peer vanishes
    plane = srv._sessions["sess"][0].plane
    for seq in range(50):                       # writes until EPIPE/ECONNRESET
        if plane.send(seq, 3, b"\x11" * 4096) is SendResult.FAILED:
            break
    check(plane.alive is False, "F  send() marked the plane dead on peer-gone")

    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline and srv._sessions.get("sess"):
        time.sleep(0.1)
    check(not srv._sessions.get("sess"),
          "F2 the dead plane is REAPED from _sessions (no leak)")
    srv._stop.set(); a.close(); t.join(timeout=3.0)


def main():
    print("\n[OUTPLANE_STAYS_NONBLOCKING_V1] media fan-out backlog\n")
    test_a_outplane_sets_nonblocking()
    test_b_add_viewer_preserves_nonblocking()
    test_c_send_drops_instead_of_stalling()
    test_d_fanout_slow_viewer_does_not_block_fast_one()
    test_g_windows_no_siocoutq()
    test_h_full_buffer_drops_not_fails()
    test_f_dead_plane_is_reaped()
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
