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
test_media_planes.py - proof harness for the SotF media transport + ladder.

Two parts, each testing what it can test FAITHFULLY:

PART A - real localhost sockets. The non-blocking drop mechanism and the failure
  instrumentation are KERNEL behavior (send-buffer fill, EWOULDBLOCK, errno,
  SIOCOUTQ). The in-process sim abstracts that away, so the faithful test is real
  loopback: throttle the reader to fill the real kernel buffer and watch the plane
  drop WHOLE frames, classify peer-gone vs would-block, and emit full context. The
  receiver re-frames the stream to PROVE a dropped frame left zero bytes on the wire.

PART B - the faithful transport SIMULATOR (transport_sim_tier / transport_factories)
  under the HaLow 900 MHz NetworkParams profile. Drives the real sim wire into
  back-pressure and feeds the observed degradation into the ladder controller,
  proving it steps level_idx DOWN as the wire backs up and recovers UP when it clears.
  (On the box the controller's signal is ffmpeg/Opus telemetry; here it is the sim's
  observed RTT growth + timeouts - the same "the wire is backing up" signal. Stated
  plainly so it isn't overclaimed.)

Run:  FROGNET_LOG_LEVEL=TRACE python3 test_media_planes.py     (exit 0 = pass)
The harness turns the REAL frognet_log switch on, so it also proves the media
instrumentation rides the actual shared logger tree, not a parallel switch.
"""
from __future__ import annotations

import os
import select
import socket
import struct
import sys
import threading
import time

# -- paths: the planes (here, persistent) + the real frognet_log + the sim -----
_HERE = os.path.dirname(os.path.abspath(__file__))
_SEM  = os.path.dirname(_HERE); _ROOT = os.path.dirname(os.path.dirname(_SEM))
_CANDS = [_HERE, _SEM, _SEM+"/proxy", _SEM+"/core", _ROOT+"/etc/frognet_bundles/communicator",
    "/tmp/fn2/opt/frognet_semantic","/tmp/fn2/opt/frognet_semantic/simulation",
    "/tmp/fn2/opt/frognet_semantic/proxy","/tmp/fn2/opt/frognet_semantic/core",
    "/tmp/fn2/etc/frognet_bundles/communicator"]
for _p in _CANDS:
    if os.path.isdir(_p) and _p not in sys.path: sys.path.insert(0, _p)

os.environ.setdefault("FROGNET_LOG_LEVEL", "TRACE")   # flip the real switch ON

import logging

from frognet_media_planes import (
    OutPlane, InPlane, RecvResult, Frame, open_watcher, Role, SendResult,
    frame_bytes, _LEN, _HDR, _MAX_FRAME,
)

# -- capture everything the frognet tree logs, so we can assert on context -----
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.recs = []           # (levelname, message)

    def emit(self, r):
        try:
            self.recs.append((r.levelname, r.getMessage()))
        except Exception:
            pass


_CAP = _Capture()
for _name in ("frognet", "frognet.media"):
    lg = logging.getLogger(_name)
    lg.addHandler(_CAP)
    lg.setLevel(5)               # TRACE
try:
    import frognet_log
    frognet_log.set_level("TRACE")
except Exception:
    pass


def _saw(substr, level=None):
    return any(substr in m and (level is None or lv == level) for lv, m in _CAP.recs)


_FAILS = []
def ck(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond or not detail else f"  - {detail}"))
    if not cond:
        _FAILS.append(name)


# ==============================================================================
# A tiny content-blind loopback media server: two listeners (out = it receives the
# client's frames; in = it emits frames to the client). It interprets NOTHING.
# ==============================================================================
class LoopbackServer:
    def __init__(self):
        self.out_lsn = socket.socket(); self.out_lsn.bind(("127.0.0.1", 0)); self.out_lsn.listen(4)
        self.in_lsn = socket.socket(); self.in_lsn.bind(("127.0.0.1", 0)); self.in_lsn.listen(4)
        self.out_port = self.out_lsn.getsockname()[1]
        self.in_port = self.in_lsn.getsockname()[1]
        self.received = []          # complete frames reassembled from the out-plane
        self.recv_paused = threading.Event()   # set => stop reading (induce backpressure)
        self._stop = threading.Event()
        self._conns = []

    # accept one out-plane connection and reassemble its length-prefixed frames.
    def accept_out(self, rcvbuf=None):
        c, _ = self.out_lsn.accept()
        if rcvbuf:
            c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self._conns.append(c)
        self._skip_hello(c)
        t = threading.Thread(target=self._drain_out, args=(c,), daemon=True); t.start()
        return c

    def _read_hello(self, c):
        """Read the HELLO frame and RETURN its body, so a caller can tell whose
        connection this is. _skip_hello keeps discarding, for callers that do
        not care."""
        head = self._recv_exact(c, _LEN.size)
        n = _LEN.unpack(head)[0]
        return bytes(self._recv_exact(c, n))

    def _skip_hello(self, c):
        self._read_hello(c)         # discard HELLO body

    def _drain_out(self, c):
        try:
            while not self._stop.is_set():
                if self.recv_paused.is_set():
                    time.sleep(0.01); continue
                head = self._recv_exact(c, _LEN.size)
                if head is None:
                    return
                n = _LEN.unpack(head)[0]
                body = self._recv_exact(c, n)
                if body is None:
                    return
                seq, level = _HDR.unpack_from(body, 0)
                self.received.append((seq, level, body[_HDR.size:]))
        except OSError:
            return        # socket closed under us during a teardown test - expected, not a fault

    # accept an in-plane connection (participant or watcher) - we will feed it frames.
    def accept_in(self):
        c, _ = self.in_lsn.accept()
        self._conns.append(c)
        self._skip_hello(c)
        return c

    @staticmethod
    def _recv_exact(c, n):
        buf = bytearray()
        c.settimeout(5.0)
        while len(buf) < n:
            try:
                chunk = c.recv(n - len(buf))
            except socket.timeout:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def stop(self):
        self._stop.set()
        for c in self._conns:
            try: c.close()
            except OSError: pass
        for l in (self.out_lsn, self.in_lsn):
            try: l.close()
            except OSError: pass


def _participant_out(host, port, session, sndbuf=None):
    """Open just an out-plane to the server (we drive the server side manually)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sndbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    s.connect((host, port))
    # HELLO, same wire shape the planes use
    body = b"FNMP1:%s:%s" % (Role.PARTICIPANT.value.encode(), session.encode())
    s.sendall(_LEN.pack(len(body)) + body)
    return OutPlane(s, session, who="tester")


# ==============================================================================
def part_a():
    print("\n=== PART A - non-blocking media plane mechanism + instrumentation (real loopback) ===")
    srv = LoopbackServer()
    try:
        # A1: clean content-blind round-trip of arbitrary BINARY payloads.
        out = _participant_out("127.0.0.1", srv.out_port, "s-clean", sndbuf=1 << 20)
        srv.accept_out()
        sent_payloads = []
        for i in range(20):
            p = bytes((i * 7 + j) & 0xFF for j in range(800))   # binary, all byte values
            sent_payloads.append(p)
            r = out.send(seq=i, level_idx=4, payload=p)
            ck(f"A1 send #{i} == SENT", r is SendResult.SENT, str(r)) if i == 0 else None
        time.sleep(0.2)
        got = [pl for _, _, pl in srv.received]
        ck("A1 all frames delivered intact (content-blind binary round-trip)",
           got == sent_payloads, f"sent={len(sent_payloads)} got={len(got)}")
        out.close()

        # A2: backpressure -> genuine EWOULDBLOCK -> WHOLE-frame drop, no partial bytes.
        out = _participant_out("127.0.0.1", srv.out_port, "s-drop", sndbuf=16 * 1024)
        srv.recv_paused.set()                 # server stops reading => buffer fills
        srv.accept_out(rcvbuf=4 * 1024)
        results = []
        big = bytes(8 * 1024)                 # 8 KB frames; SNDBUF 16 KB => fills fast
        for i in range(200):
            results.append(out.send(seq=i, level_idx=4, payload=big + bytes([i & 0xFF])))
        n_sent = sum(1 for r in results if r is SendResult.SENT)
        n_drop = sum(1 for r in results if r is SendResult.DROPPED)
        ck("A2 backpressure produced DROPPED frames (drop-don't-stall)", n_drop > 0,
           f"sent={n_sent} dropped={n_drop}")
        ck("A2 drop is a RATE at DEBUG, not a per-event ERROR firehose",
           _saw("media_drop_rate", "DEBUG") and not _saw("media_drop", "ERROR"))
        ck("A2 sustained drops escalated to a single WARNING (drop storm)",
           _saw("media_drop_storm", "WARNING"))
        # now let the server read; every COMPLETE frame must be intact -> proves no
        # partial frame was ever written (a half frame would desync the re-framer).
        srv.received.clear()
        srv.recv_paused.clear()
        time.sleep(0.5)
        intact = all(len(pl) == len(big) + 1 for _, _, pl in srv.received)
        ck("A2 every delivered frame is whole (dropped frames left ZERO bytes on the wire)",
           intact and len(srv.received) >= 1,
           f"reassembled={len(srv.received)} all_whole={intact}")
        out.close()

        # A3: peer vanishes -> FAILED, ERROR with errno-by-name + socket context.
        out = _participant_out("127.0.0.1", srv.out_port, "s-gone", sndbuf=1 << 20)
        sc = srv.accept_out()
        sc.close()                            # server slams the connection
        # [A3_PEER_GONE_IS_NOT_A_RACE_V1] This was a coin flip: 12 of 20 runs
        # failed with SendResult.SENT. close() sends FIN, and writing to a socket
        # that has received FIN is LEGAL -- a half-close. The local socket only
        # errors once the peer RSTs in response to data for a closed socket, so
        # the failure needs a round trip. The old loop was 50 sends with no
        # delay: 12,800 bytes into a 1 MB SNDBUF, done in microseconds. If the
        # RST had not landed yet, all 50 succeeded and the check failed.
        # Iteration count is the wrong unit for "has the kernel noticed"; wall
        # time is. Measured: 0/20 failures after this change.
        #
        # The assertion is UNCHANGED -- the send must still return FAILED and
        # still log errno-by-name with socket context. Only the wait is made
        # adequate. If the plane stops reporting peer-gone, this still fails.
        #
        # SO_LINGER/RST on the server side was tried and rejected: measured, it
        # did NOT fix the flip on its own (12/20 still SENT) and it is a
        # different failure mode from the one being tested.
        rr = None
        _deadline = time.time() + 5.0
        i = 0
        while time.time() < _deadline:
            rr = out.send(seq=i, level_idx=4, payload=b"x" * 256)
            if rr is SendResult.FAILED:
                break
            i += 1
            time.sleep(0.005)                 # let the kernel notice the peer
        ck("A3 peer-gone send -> FAILED", rr is SendResult.FAILED, str(rr))
        ck("A3 fault logged at ERROR with errno-by-name + socket context",
           _saw("media_outplane_peer_gone", "ERROR") and
           (_saw("errno=EPIPE") or _saw("errno=ECONNRESET")) and _saw("fd="))
        out.close()

        # A4: oversize frame -> FAILED (caller/corruption error), loud ERROR.
        out = _participant_out("127.0.0.1", srv.out_port, "s-big", sndbuf=1 << 20)
        srv.accept_out()
        rr = out.send(seq=0, level_idx=4, payload=b"\x00" * (_MAX_FRAME + 1))
        ck("A4 oversize -> FAILED", rr is SendResult.FAILED, str(rr))
        ck("A4 oversize logged at ERROR with sizes", _saw("media_frame_oversize", "ERROR"))
        out.close()

        # A5: in-plane clean recv, then DESYNC on a corrupt length prefix.
        cin = srv.accept_in_async("s-in")
        ip_sock = socket.create_connection(("127.0.0.1", srv.in_port))
        body = b"FNMP1:%s:%s" % (Role.WATCHER.value.encode(), b"s-in")
        ip_sock.sendall(_LEN.pack(len(body)) + body)
        inp = InPlane(ip_sock, "s-in", who="tester")
        srv.feed_in(cin, frame_bytes(7, 3, b"hello-bytes"))     # one good frame
        f = inp.recv()
        ck("A5 in-plane receives a clean Frame (content-blind)",
           isinstance(f, Frame) and f.seq == 7 and f.payload == b"hello-bytes")
        srv.feed_in_raw(cin, _LEN.pack(_MAX_FRAME + 99))         # corrupt length prefix
        d = inp.recv()
        ck("A5 corrupt length prefix -> DESYNC", d is RecvResult.DESYNC, str(d))
        ck("A5 desync logged at ERROR with declared_len + context",
           _saw("media_inplane_desync", "ERROR") and _saw("declared_len="))
        inp.close()

        # A6: watcher opens IN-plane only; missing out-plane is EXPECTED, not a fault.
        cin2 = srv.accept_in_async("s-watch")
        _err_outplane_before = sum(1 for lv, m in _CAP.recs if lv == "ERROR" and "outplane" in m)
        ws = open_watcher("127.0.0.1", srv.in_port, "s-watch", who="watcher", timeout=3.0)
        ck("A6 watcher has NO out-plane (in-plane only)", ws.out is None)
        ck("A6 watcher role labeled correct-for-role (not a fault)",
           _saw("in-plane-only", "INFO") or _saw("watcher", "INFO"))
        _err_outplane_after = sum(1 for lv, m in _CAP.recs if lv == "ERROR" and "outplane" in m)
        ck("A6 watcher's absent out-plane logged NO new ERROR",
           _err_outplane_after == _err_outplane_before,
           f"before={_err_outplane_before} after={_err_outplane_after}")
        ws.close()

        # A7: a seq gap (what a receiver sees after the sender dropped) is TRACE, not ERROR.
        cin3 = srv.accept_in_async("s-gap")
        s3 = socket.create_connection(("127.0.0.1", srv.in_port))
        b3 = b"FNMP1:%s:%s" % (Role.WATCHER.value.encode(), b"s-gap")
        s3.sendall(_LEN.pack(len(b3)) + b3)
        inp3 = InPlane(s3, "s-gap", who="tester")
        srv.feed_in(cin3, frame_bytes(10, 4, b"a"))
        srv.feed_in(cin3, frame_bytes(15, 4, b"b"))             # gap of 4
        inp3.recv(); inp3.recv()
        ck("A7 seq gap traced at TRACE (expected after drops), never ERROR",
           _saw("media_seq_gap", "TRACE") and not _saw("media_seq_gap", "ERROR"))
        inp3.close()
    finally:
        srv.stop()


# small async-in helpers bolted onto the server for the in-plane tests
def _bolt_in_helpers():
    def accept_in_async(self, _session):
        """[ACCEPT_THE_SESSION_YOU_WERE_ASKED_FOR_V1] This took `_session` and
        ignored it: it handed back whatever connection arrived next on the
        listener. Earlier sections open in-plane connections and close them
        (A6's watcher), and a closed connection still sits in the accept
        backlog, so a later section could be handed a DEAD socket and die with

            BrokenPipeError: [Errno 32] Broken pipe   (feed_in -> sendall)

        Measured 7/20 on the shipped harness, and 20/20 once A3 was fixed and
        stopped masking it. Proven by printing peers: on every failure cin3's
        peer port was LOWER than s3's -- an earlier connection, not this one.

        Read the HELLO and claim only the session asked for. A connection that
        belongs to someone else is PARKED in a shared map, not discarded --
        discarding it just moves the failure to the waiter it belonged to
        (KeyError: 'c'), because its HELLO has already been consumed and no
        other thread will ever see it again.
        """
        box = {}
        want = (_session or "").encode()
        if not hasattr(self, "_pending_in"):
            self._pending_in = {}
            self._pending_lock = threading.Lock()

        def go():
            deadline = time.time() + 10.0
            while time.time() < deadline:
                with self._pending_lock:                 # already parked for us?
                    c = self._pending_in.pop(want, None)
                if c is not None:
                    box["c"] = c
                    return
                # Poll rather than block: a thread parked inside accept() never
                # re-checks the pending map, so a connection another waiter
                # parked FOR it would sit there until the deadline (KeyError:
                # 'c'). Measured 5/12 before this. select() keeps the loop live.
                try:
                    if self.in_lsn.fileno() < 0:      # listener closed at teardown
                        return
                    ready, _, _ = select.select([self.in_lsn], [], [], 0.1)
                except (OSError, ValueError):         # fd went to -1 mid-select
                    return
                if not ready:
                    continue
                try:
                    c, _ = self.in_lsn.accept()
                except OSError:
                    return
                self._conns.append(c)
                try:
                    hello = self._read_hello(c)
                except Exception:
                    continue                             # stale/closed: not ours
                sess = hello.rsplit(b":", 1)[-1]
                if not want or sess == want:
                    box["c"] = c
                    return
                with self._pending_lock:                 # someone else's: park it
                    self._pending_in[sess] = c
        t = threading.Thread(target=go, daemon=True); t.start()
        time.sleep(0.05)
        return box

    def feed_in(self, box, framed_or_none):
        for _ in range(50):
            if "c" in box: break
            time.sleep(0.02)
        box["c"].sendall(framed_or_none)
    def feed_in_raw(self, box, raw):
        box["c"].sendall(raw)
    LoopbackServer.accept_in_async = accept_in_async
    LoopbackServer.feed_in = feed_in
    LoopbackServer.feed_in_raw = feed_in_raw
_bolt_in_helpers()


# ==============================================================================
def part_b():
    print("\n=== PART B - ladder driven by the SIM transport under HaLow 900MHz ===")
    try:
        from transport_sim_tier import NetworkParams, wrap_req_full, _hash_for
        from transport_factories import connect_pair
        from sotf_ladder_control import LadderController, TelemetrySample, Action
    except Exception as e:
        ck("PART B sim import", False, f"{type(e).__name__}: {e} (sim unavailable here)")
        return

    # HaLow-class profiles. Large media-sized frames make the bandwidth bite; tiny
    # REQ_REPEATs would ride the SAME floor and never load the wire.
    tight = NetworkParams(latency_ms=20, jitter_ms=5, bandwidth_bps=24e3 / 8)   # ~3000 B/s, saturated
    clean = NetworkParams(latency_ms=1.0, bandwidth_bps=1e9)

    def measure(params, frame_bytes_size, n=3, timeout=2.0):
        """Push n media-sized FULL frames over a sim wire at `params`. Returns
        (mean_rtt_ms_or_timeout, timeout_rate). Rising RTT + timeouts = the wire
        backing up - on the box this same 'falling behind' is what ffmpeg/Opus report."""
        proxy, dsession, h1, h2 = connect_pair("10.255.254.1", mode="sim",
                                               fwd_params=params, rev_params=params)
        rtts, timeouts = [], 0
        try:
            for i in range(n):
                t0 = time.perf_counter()
                r = proxy.send_request(wrap_req_full(_hash_for(f"k{i}"), b"\x00" * frame_bytes_size),
                                       timeout=timeout)
                dt = (time.perf_counter() - t0) * 1000
                if r is None:
                    timeouts += 1
                else:
                    rtts.append(dt)
        finally:
            h1.close(); h2.close()
        mean = (sum(rtts) / len(rtts)) if rtts else (timeout * 1000)   # all-late => >= timeout
        return mean, (timeouts / n)

    base_lag, base_drop = measure(clean, 2200)
    print(f"  baseline (clean, 2.2KB frames): mean_rtt={base_lag:.1f}ms drop_rate={base_drop:.2f}")

    ctrl = LadderController(start_level=6)
    t = 0.0
    downgraded = False
    for k in range(6):
        lag, drop = measure(tight, 2200)          # ~700ms RTT at 3000 B/s -> abs-lag trigger
        sample = TelemetrySample(t=t, encode_drops=int(drop * 3),
                                 decode_lag_ms=lag, wire_drop_rate=drop)
        d = ctrl.observe(sample)
        print(f"  [sat k={k}] sim_rtt={lag:.0f}ms drop={drop:.2f} -> {d.action.value} "
              f"level={d.level_idx} ({d.reason})")
        if d.action is Action.DOWNGRADE:
            downgraded = True
        t += 2.5                                  # respect the controller's step-rate cap
    ck("PART B sim back-pressure drove a DOWNGRADE", downgraded, f"ended level={ctrl.level}")
    ck("PART B controller stepped below start under saturation", ctrl.level < 6, f"level={ctrl.level}")

    # clear the wire -> sustained health -> RECOVER UP.
    recovered_from = ctrl.level
    upgraded = False
    for k in range(30):
        d = ctrl.observe(TelemetrySample(t=t, encode_drops=0, decode_lag_ms=2.0, wire_drop_rate=0.0))
        if d.action is Action.UPGRADE:
            upgraded = True
        t += 0.6
    ck("PART B sustained health recovered the level UP",
       upgraded and ctrl.level > recovered_from, f"from={recovered_from} to={ctrl.level}")


def main():
    print("media planes proof - switch ON via FROGNET_LOG_LEVEL=%s" % os.environ.get("FROGNET_LOG_LEVEL"))
    part_a()
    part_b()
    print()
    if _FAILS:
        print("FAILED:", ", ".join(_FAILS))
        return 1
    print("ALL MEDIA-PLANE PROOFS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
