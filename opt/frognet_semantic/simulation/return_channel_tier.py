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
"""return_channel_tier.py -- the proxy's RETURN channel, against a real socket.

[RETURN_ONLY_V1] is the single reply path for every semantic RPC, and it has
had no simulator coverage at all. Every failure of it has been found by a
nine-hour benchmark campaign dying in its second arm:

    StoreBroken on databasehost.frognet:80: HTTP 503:
    RAW RPC failed: RuntimeError('peer 10.155.155.1: no daemon callback
    after 311s')

    RAW RPC failed: RuntimeError('peer 10.155.155.1: reader dead')

That is four campaigns spent discovering that a daemon did not complete a
two-connection handshake.

WHAT THIS PINS

A real `_DaemonWorker` is driven against a real listening socket that plays a
daemon behaving in each of the ways a daemon can fail. The worker's own
`_open_send_sock` and `_open_return_channel` run unmodified -- this does not
model the handshake, because a model of the handshake is exactly what would
have agreed with the code and still lost the campaign.

The invariant that matters is the last one. `_close()` zeroes `_send_sock_at`,
and the cleanup thread's dead-worker branch computes

    sock_age = (now - self._send_sock_at) if self._send_sock_at > 0 else 0

so after a RETURN failure the age is 0 and that branch can never fire. Which
means "no daemon callback after 311s" is NOT reachable from a RETURN that
raised: it requires a RETURN attempt that neither succeeded nor failed, or a
path that leaves the send socket installed with no reader. If this tier is
green and the fleet still reports that message, the handshake is not where it
came from -- and knowing that without another campaign is the point.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEM = os.path.dirname(_HERE)
if _SEM not in sys.path:
    sys.path.insert(0, _SEM)

FAILS = []
PASSES = 0


def check(ok, what):
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {what}")
    else:
        FAILS.append(what)
        print(f"  [FAIL] {what}")


class FakeDaemon:
    """A listener on loopback that plays one scripted daemon.

    `mode` decides what happens to the SECOND connection -- the RETURN. The
    first is always accepted and its HELLO read, because a daemon that is not
    answering 9009 at all is a different fault with a different message.

        healthy   RETURN accepted, HELLO read, HELLO reply sent
        slow      as healthy, but the reply is delayed by `delay`
        silent    RETURN accepted, HELLO read, nothing sent back ever
        dropped   RETURN accepted, then closed mid-handshake
        refused   listener stops accepting after the send sock
    """

    def __init__(self, mode: str, delay: float = 0.0):
        self.mode = mode
        self.delay = delay
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.conns = []
        self.hellos = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        from proxy.transport_semantic import _recv_frame, _send_frame
        from core.semcache_wire import wrap_hello
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            n = len(self.conns)
            self.conns.append(c)
            if n == 0:
                # the send sock: read its HELLO and hold the connection open,
                # which is what a real daemon does.
                try:
                    self.hellos.append(_recv_frame(c))
                except Exception:
                    pass
                continue
            # the RETURN
            if self.mode == "refused":
                try:
                    c.close()
                except OSError:
                    pass
                continue
            try:
                self.hellos.append(_recv_frame(c))
            except Exception:
                continue
            if self.mode == "dropped":
                try:
                    c.close()
                except OSError:
                    pass
            elif self.mode in ("healthy", "slow"):
                if self.delay:
                    time.sleep(self.delay)
                try:
                    _send_frame(c, wrap_hello("ACK"))
                except Exception:
                    pass
            # "silent": read the HELLO and say nothing, forever.

    def close(self):
        self._stop.set()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


def _stop_accepting(d: FakeDaemon):
    """For `refused`: close the listener so the RETURN connect is refused
    rather than accepted and dropped."""
    try:
        d.sock.close()
    except OSError:
        pass


def _worker(host, port):
    from proxy.transport_semantic import _DaemonWorker
    return _DaemonWorker(host, port)


def _retire(w):
    try:
        w._retire("tier_done")
    except Exception:
        pass


def plane_healthy():
    print("=== PLANE 1: a daemon that completes the handshake ===")
    d = FakeDaemon("healthy")
    w = _worker("127.0.0.1", d.port)
    try:
        t0 = time.time()
        s = w._get_or_connect()
        dt = time.time() - t0
        check(s is not None, "the send socket is returned")
        check(w._reader_alive, "the reader is registered")
        check(w._ever_had_reader,
              "ever_had_reader is set, so the dead-worker threshold drops "
              "to 5s for this peer")
        check(w._send_sock_at > 0, "the send socket's age starts running")
        check(dt < 5.0, f"and it does not take a timeout to get there ({dt:.1f}s)")
        check(len(d.hellos) == 2,
              f"the daemon saw two HELLOs, send and RETURN ({len(d.hellos)})")
        if len(d.hellos) == 2:
            check(d.hellos[1] != d.hellos[0],
                  "the RETURN HELLO is the RETURN form, not a repeat of the "
                  "send HELLO")
    finally:
        _retire(w)
        d.close()


def _failure_case(mode, label, want_phase):
    d = FakeDaemon(mode)
    w = _worker("127.0.0.1", d.port)
    try:
        if mode == "refused":
            # let the send sock land first, then take the listener away
            pass
        t0 = time.time()
        raised = None
        try:
            if mode == "refused":
                import proxy.transport_semantic as ts
                real = ts._DaemonWorker._open_return_channel

                def _kill_then_open(self):
                    _stop_accepting(d)
                    return real(self)
                ts._DaemonWorker._open_return_channel = _kill_then_open
                try:
                    w._get_or_connect()
                finally:
                    ts._DaemonWorker._open_return_channel = real
            else:
                w._get_or_connect()
        except Exception as e:
            raised = e
        dt = time.time() - t0
        check(raised is not None,
              f"{label}: _get_or_connect raises rather than returning a "
              f"socket with no reply path")
        check(dt < 20.0,
              f"{label}: it gives up in {dt:.1f}s, not on the RPC timeout")
        # THE INVARIANT.
        check(w._send_sock is None,
              f"{label}: the send socket is torn down")
        check(w._send_sock_at == 0.0,
              f"{label}: and its age is zeroed, so the cleanup thread's "
              f"dead-worker branch computes sock_age 0 and cannot report "
              f"'no daemon callback after N'")
        check(not w._ever_had_reader,
              f"{label}: the peer is not recorded as proven")
    finally:
        _retire(w)
        d.close()


def plane_silent_daemon():
    print("\n=== PLANE 2: the daemon accepts the RETURN and never replies ===")
    print("        (the phase=recv_hello_reply case: session match or return")
    print("         routing wedged on the daemon side)")
    _failure_case("silent", "silent RETURN", "recv_hello_reply")


def plane_dropped_return():
    print("\n=== PLANE 3: the daemon drops the RETURN mid-handshake ===")
    _failure_case("dropped", "dropped RETURN", "recv_hello_reply")


def plane_refused_return():
    print("\n=== PLANE 4: the daemon stops accepting before the RETURN ===")
    _failure_case("refused", "refused RETURN", "connect")


def plane_no_silent_installation():
    print("\n=== PLANE 5: no path installs a send socket without a reader ===")
    # Structural, over the real source: every assignment of _send_sock to a
    # live socket must be followed by a RETURN attempt whose failure calls
    # _close. Two such paths exist (_get_or_connect and the cleanup
    # reconnect); a third added later without the teardown is exactly how
    # "no daemon callback after 311s" becomes reachable again.
    import ast
    src = open(os.path.join(_SEM, "proxy", "transport_semantic.py"),
               encoding="utf-8").read()
    lines = src.splitlines()
    installs = [i + 1 for i, l in enumerate(lines)
                if l.strip().startswith("self._send_sock = ")
                and "None" not in l]
    check(len(installs) == 2,
          f"two paths install a send socket (lines {installs}) -- a new one "
          f"needs its own RETURN teardown and its own checkpoint here")
    for ln in installs:
        window = "\n".join(lines[ln - 1:ln + 40])
        check("_open_return_channel()" in window,
              f"line {ln}: opens the RETURN channel straight after")
        check("_close(" in window,
              f"line {ln}: tears the send socket down when the RETURN fails")


def plane_reconnect_window():
    print("\n=== PLANE 6: the cleanup thread must not reap a handshake that "
          "is still running ===")
    print("        _get_or_connect installs the send socket and stamps")
    print("        _send_sock_at BEFORE it opens the RETURN. For a peer that")
    print("        has already proven itself the dead-worker threshold is 5s,")
    print("        and the RETURN is allowed 5s of its own -- so the window")
    print("        between the stamp and _register_recv_sock can outlast the")
    print("        threshold that is watching it.")
    d = FakeDaemon("slow", delay=4.8)
    w = _worker("127.0.0.1", d.port)
    try:
        # A reconnect to a peer this worker has already talked to.
        w._ever_had_reader = True
        t0 = time.time()
        err = None
        try:
            w._get_or_connect()
        except Exception as e:
            err = e
        dt = time.time() - t0
        check(err is None,
              f"a reconnect whose RETURN takes {d.delay:.0f}s completes "
              f"(raised {err!r} after {dt:.1f}s)")
        check(w._reader_alive,
              "the reader is registered rather than reaped mid-handshake")
        check(w._send_sock is not None,
              "and the send socket it was installing is still installed")
    finally:
        _retire(w)
        d.close()


def main():
    plane_healthy()
    plane_silent_daemon()
    plane_dropped_return()
    plane_refused_return()
    plane_no_silent_installation()
    plane_reconnect_window()

    print()
    if FAILS:
        print(f"RETURN-CHANNEL TIER: {len(FAILS)} FAILED, {PASSES} passed")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"ALL RETURN-CHANNEL TIER CHECKPOINTS PASS ({PASSES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
