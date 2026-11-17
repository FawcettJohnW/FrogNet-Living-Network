#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/homa_sim.py -- [HOMA_SIM_V1] a Homa simulator: Homa's protocol in user space, over UDP, behind Homa's socket calls.

WHAT IT IS FOR. The Homa kernel module cannot be loaded in the sandbox or the simulator, and may not build on every
node's kernel. This gives every Homa driver in the tree (core/homa_socket.HomaSocket's callers) a socket with the
same calls that runs anywhere Python runs, between processes and between machines, so the drivers, the oracles and
the simulator tiers run through Homa's BEHAVIOUR without the module. It is selected by configuration
(core/sem_transport.py: the word "homasim"), never probed, and is never used in place of the module.

WHAT IT IS. The protocol of HomaModule's protocol.md (commit 07ce63e), written from that document:

  messages          a request and a response per RPC, each up to 1,000,000 bytes, cut into DATA packets
  unscheduled bytes the first unsched_bytes of a message go without permission
  grants            the rest go only as the RECEIVER grants them: it keeps unsched_bytes granted-but-not-received
                    for at most max_overcommit incoming messages at once, shortest remaining first
  SRPT              a sender with several messages ready sends from the one with the fewest bytes left
  no order          nothing orders one message against another
  RESEND / BUSY     a receiver that has heard nothing of an RPC for resend_ticks asks again every resend_interval;
                    a sender with nothing it may send answers BUSY, which resets the receiver's clock
  timeout           after timeout_ticks of silence the RPC is aborted: ETIMEDOUT at the client, dropped at the server
  UNKNOWN           a server asked to resend for an RPC it has never heard of says so, and the client starts over
  at most once      a server holds an RPC's state until the client's ACK, and a request seen again is not run again

The parameters are the module's sysctls by name, with its defaults (homa_utils.c): unsched_bytes 40000,
resend_ticks 5, resend_interval 5, timeout_ticks 100, max_overcommit 8. The timer rule is homa_timer_check_rpc's:
an RPC is "waiting" only for bytes it has been granted and not received, or (a client) once it has sent all it may.
THE TICK IS 10 ms HERE, NOT THE MODULE'S 1 ms (tick_ms): Python threads cannot turn a packet round in under the
module's 5 ms resend time under load, and at 1 ms the simulator asked for bytes that were merely in its own queues,
re-sent them, and timed RPCs out with no loss at all (measured: 17 of 300 at 32 in flight). So every time here is
10 x the module's -- a silent peer is ETIMEDOUT after 1 s, not 100 ms -- and the tick COUNTS are the module's.
FROGNET_HOMASIM_<NAME> overrides a parameter for a process. Impairment for tests: FROGNET_HOMASIM_LOSS (fraction of packets not sent).

[HELD_WAITS_V1] held_waits=1 is Homa with one change, the first of the CroakOS methods applied to it. As shipped, a
client whose request is in and whose reply is not yet written asks again every resend_interval and is told BUSY every
time, for as long as it waits: polling. With held_waits the wait is a held read. When the server has the whole
request it says so once (HELD); the client then sends nothing until the response arrives, which is the write that
fires the hold. The sender of a response owns getting it there: unacknowledged, it sends its first packet again every
resend_interval. A peer that has gone is one event for all of its waits, peer_gone(peer), raised by whoever owns
membership (the session above), not a timeout discovered once per request.

[TIMERS_FROM_THE_MEASURED_PATH_V1] adaptive=1 is the second: Homa's timers are constants (resend 5 ticks, timeout
100) chosen for a datacenter's round trip. Here each socket measures the round trip to each peer itself, from its own
sends and what comes back (an EWMA, kept in its own space, as the tensor plane sizes records and the proxy sets its
RPC budget), and asks again after 2 round trips, never sooner than resend_ticks, with the timeout 20 times that. A
peer not yet measured gets 30 x timeout_ticks for its first answer and is asked again at doubling intervals. Nothing
is exchanged to do this.

[NO_RPC_TIMEOUT_V1] rpc_timeout=0 removes Homa's per-RPC timeout (John, 2026-11-16: "Completely unnecessary. Remove
it"). The timeout was Homa's crash detector: with no connection, silence on one RPC for timeout_ticks was its only
evidence a peer had died, so every RPC carried its own death watch and a live peer had to keep answering BUSY to
survive it. Whether a machine is there is membership's fact, known once for the machine: peer_gone(peer) is that
event, and it ends everything outstanding to that peer at both ends. With rpc_timeout=0 no RPC is ever abandoned for
silence, in either direction; loss is still found by asking again; an unacknowledged response is sent again at
doubling intervals by its writer until it is acknowledged or the peer is gone.

delay_ms holds every packet this socket sends for that long: a path with a round trip of 2 x delay_ms.

Not simulated: packet priorities (they need switch queues), CUTOFFS and FREEZE packets, the FIFO fraction, incast
handling, and NEED_ACK (a server keeps a finished RPC for 2 x timeout_ticks if no ACK comes). Packets are the
simulator's own format on UDP.
"""
from __future__ import annotations

import errno
import itertools
import os
import queue
import random
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

from core.homa_socket import Msg, RpcFailed, CarrierClosed, HOMA_MAX_MESSAGE_LENGTH

BUILD = "homa-sim-v1-20261116a"
DEFAULTS = {"unsched_bytes": 40000, "resend_ticks": 5, "resend_interval": 5, "timeout_ticks": 100, "max_overcommit": 8,
            "tick_ms": 10, "mtu_payload": 1400, "held_waits": 0, "adaptive": 0, "rpc_timeout": 1, "delay_ms": 0}

DATA, GRANT, RESEND, BUSY, UNKNOWN, ACK, HELD = 1, 2, 3, 4, 5, 6, 7
NAMES = {DATA: "data", GRANT: "grant", RESEND: "resend", BUSY: "busy", UNKNOWN: "unknown", ACK: "ack", HELD: "held"}
HDR = struct.Struct("!BBQII")          # type, from_client, rpc id (the client's), a, b
#   DATA   a = message length, b = offset        GRANT  a = bytes permitted        RESEND a = offset, b = length


def params() -> Dict[str, int]:
    p = dict(DEFAULTS)
    for k in p:
        v = os.environ.get("FROGNET_HOMASIM_" + k.upper())
        if v is not None:
            p[k] = int(v)
    return p


class _Out:
    """A message this end is sending."""
    __slots__ = ("data", "sent", "granted", "rexmit")

    def __init__(self, data: bytes, unsched: int):
        self.data, self.sent, self.granted, self.rexmit = data, 0, min(len(data), unsched), []

    def left(self) -> int:
        return len(self.data) - self.sent


class _In:
    """A message this end is receiving."""
    __slots__ = ("length", "buf", "have", "got", "granted")

    def __init__(self, length: int, unsched: int):
        self.length, self.buf, self.have, self.got = length, bytearray(length), [], 0
        self.granted = min(length, unsched)

    def add(self, off: int, chunk: bytes) -> None:
        end = off + len(chunk)
        if end > self.length:
            return
        self.buf[off:end] = chunk
        merged, s, e = [], off, end
        for a, b in self.have:
            if b < s or a > e:
                merged.append((a, b))
            else:
                s, e = min(s, a), max(e, b)
        merged.append((s, e))
        merged.sort()
        self.have = merged
        self.got = sum(b - a for a, b in merged)

    def complete(self) -> bool:
        return self.got >= self.length

    def first_gap(self) -> Tuple[int, int]:
        pos = 0
        for a, b in self.have:
            if a > pos:
                return pos, a - pos
            pos = b
        return pos, max(0, min(self.granted, self.length) - pos)


class _Rpc:
    __slots__ = ("rid", "peer", "client", "cookie", "out", "inc", "silent", "delivered", "done_at", "held", "t0")

    def __init__(self, rid, peer, client, cookie=0):
        self.rid, self.peer, self.client, self.cookie = rid, peer, client, cookie
        self.out: Optional[_Out] = None
        self.inc: Optional[_In] = None
        self.silent = 0
        self.delivered = False          # server: the request went up to the application (it is never run again)
        self.done_at = None             # server: tick the response was fully sent; kept until ACK
        self.held = False               # client, held_waits: the server has the request; this wait is a held read
        self.t0 = None                  # when the thing whose answer times the round trip left (monotonic)


class HomaSimSocket:
    """core/homa_socket.HomaSocket's calls, on the simulator."""

    def __init__(self, bind_ip: str = "0.0.0.0", server_port: Optional[int] = None):
        self.p = params()
        self.loss = float(os.environ.get("FROGNET_HOMASIM_LOSS", "0"))
        self._rng = random.Random(int(os.environ.get("FROGNET_HOMASIM_SEED", "1")))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20)
        self._sock.bind((bind_ip, int(server_port) if server_port is not None else 0))
        self.port = self._sock.getsockname()[1]
        self.is_server = server_port is not None
        self._lock = threading.Condition()
        self._ids = itertools.count(2, 2)
        self._rpcs: Dict[Tuple, _Rpc] = {}             # client: (None, id)   server: (peer, id)
        self._finished: Dict[Tuple, int] = {}          # server: RPCs answered and ACKed -> the tick; a late copy of
                                                       # such a request's packets must not start it again (at most once)
        self._inbox: "queue.Queue" = queue.Queue()
        self._closed = False
        self._tick = 0
        self._rtt: Dict[Tuple, float] = {}             # this socket's own measure of the round trip to each peer, in ticks
        self._late: List = []                          # delay_ms: packets waiting their time, (due, n, bytes, peer)
        self._late_n = itertools.count()
        if self.p["delay_ms"]:
            threading.Thread(target=self._delayed, daemon=True, name="homasim-delay-%d" % self.port).start()
        self.m = {("tx", n): 0 for n in NAMES.values()}
        self.m.update({("rx", n): 0 for n in NAMES.values()})
        self.m.update({"dropped_by_loss": 0, "timeouts": 0, "requests_delivered": 0, "responses_delivered": 0,
                       "duplicate_requests_not_rerun": 0, "bytes_resent": 0, "bytes_out": 0, "bytes_in": 0})
        for fn, nm in ((self._rx, "rx"), (self._tx, "tx"), (self._timer, "timer")):
            threading.Thread(target=fn, daemon=True, name="homasim-%s-%d" % (nm, self.port)).start()

    # ------------------------------------------------------------------------------------------- the socket's calls
    def send_request(self, dest: Tuple[str, int], payload: bytes, cookie: int) -> int:
        self._check(payload)
        with self._lock:
            rid = next(self._ids)
            r = _Rpc(rid, (dest[0], int(dest[1])), True, cookie)
            r.out = _Out(bytes(payload), self.p["unsched_bytes"])
            self._rpcs[(None, rid)] = r
            self.m["bytes_out"] += len(payload)
            self._lock.notify_all()
        return rid

    def send_reply(self, dest: Tuple[str, int], rpc_id: int, payload: bytes) -> None:
        self._check(payload)
        with self._lock:
            r = self._rpcs.get(((dest[0], int(dest[1])), rpc_id & ~1))
            if r is None or r.client or not r.delivered or r.out is not None:
                raise OSError(errno.EINVAL, "homasim: rpc %d from %s:%d is not a request this socket is holding for a "
                              "reply" % (rpc_id, dest[0], dest[1]))
            r.out = _Out(bytes(payload), self.p["unsched_bytes"])
            self.m["bytes_out"] += len(payload)
            self._lock.notify_all()

    def recv(self) -> Msg:
        m = self._inbox.get()
        if isinstance(m, BaseException):
            if isinstance(m, CarrierClosed):
                self._inbox.put(m)                      # every receiving thread gets it
            raise m
        return m

    def abort(self, rpc_id: int) -> None:
        with self._lock:
            self._rpcs.pop((None, rpc_id), None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        self._sock.close()
        self._inbox.put(CarrierClosed("homasim socket closed"))

    def peer_gone(self, peer: Tuple[str, int]) -> int:
        """The machine at `peer` has left: membership's one event for it. Everything this socket has outstanding with
        it ends now: each request this socket sent it fails with EHOSTUNREACH, and each request it sent this socket
        is dropped with its state. Returns how many RPCs ended."""
        peer = (peer[0], int(peer[1]))
        with self._lock:
            gone = [(k, r) for k, r in self._rpcs.items() if r.peer == peer]
            for k, r in gone:
                self._rpcs.pop(k, None)
                if r.client:
                    self._inbox.put(RpcFailed(r.rid, r.cookie, errno.EHOSTUNREACH))
            self._rtt.pop(peer, None)
        return len(gone)

    def counters(self) -> dict:
        with self._lock:
            return {("%s_%s" % k if isinstance(k, tuple) else k): v for k, v in self.m.items()}

    def _check(self, payload) -> None:
        if not 0 < len(payload) <= HOMA_MAX_MESSAGE_LENGTH:
            raise ValueError("homa: a message of %d bytes cannot be sent (1..%d); it is not split here"
                             % (len(payload), HOMA_MAX_MESSAGE_LENGTH))
        if self._closed:
            raise OSError(errno.ESHUTDOWN, "homasim socket closed")

    # ---------------------------------------------------------------------------------------------------- packets
    def _send(self, peer, typ: int, from_client: bool, rid: int, a: int = 0, b: int = 0, body: bytes = b"") -> None:
        self.m[("tx", NAMES[typ])] += 1
        if self.loss and self._rng.random() < self.loss:
            self.m["dropped_by_loss"] += 1
            return
        pkt = HDR.pack(typ, 1 if from_client else 0, rid, a, b) + body
        if self.p["delay_ms"]:
            import heapq
            heapq.heappush(self._late, (time.monotonic() + self.p["delay_ms"] / 1000.0, next(self._late_n), pkt, peer))
            return
        self._put(pkt, peer)

    def _put(self, pkt: bytes, peer) -> None:
        try:
            self._sock.sendto(pkt, peer)
        except OSError as e:
            if not self._closed:
                print("[HOMASIM] sendto %s:%d failed: %s" % (peer[0], peer[1], e), flush=True)

    def _delayed(self) -> None:
        import heapq
        while not self._closed:
            with self._lock:
                now, due = time.monotonic(), []
                while self._late and self._late[0][0] <= now:
                    due.append(heapq.heappop(self._late))
            for _d, _n, pkt, peer in due:
                self._put(pkt, peer)
            time.sleep(0.001)

    def _measured(self, peer, r) -> None:
        """One round trip to `peer`, timed by this socket from its own send to the first thing back."""
        if r.t0 is None:
            return
        ticks = (time.monotonic() - r.t0) * 1000.0 / self.p["tick_ms"]
        r.t0 = None
        old = self._rtt.get(peer)
        self._rtt[peer] = ticks if old is None else 0.8 * old + 0.2 * ticks

    def _ask_now(self, peer, waited: int, rt: int, ri: int) -> bool:
        """Is this tick one on which to ask again (or send again)? The module's rule: at resend_ticks and every
        resend_interval after. For a peer whose round trip this socket has not measured yet (adaptive): at
        resend_ticks, then at twice that, four times, eight -- it does not know how far away the peer is, so it does
        not keep asking at a datacenter's pace."""
        if waited < rt:
            return False
        if self.p["adaptive"] and peer not in self._rtt:
            q, rem = divmod(waited, rt)
            return rem == 0 and (q & (q - 1)) == 0
        return (waited - rt) % ri == 0

    def _timers(self, peer) -> Tuple[int, int, int]:
        """(resend_ticks, resend_interval, timeout_ticks) for a peer: the constants, or from the measured path."""
        rt, ri, tt = self.p["resend_ticks"], self.p["resend_interval"], self.p["timeout_ticks"]
        if not self.p["adaptive"]:
            return rt, ri, tt
        rtt = self._rtt.get(peer)
        if rtt is None:
            return rt, ri, 30 * tt
        e = max(rt, int(2 * rtt) + 1)
        return e, max(ri, int(rtt) + 1), 20 * e

    def _tx(self) -> None:
        """The sender: one packet at a time from the message with the fewest bytes left (SRPT); resends first."""
        mtu = self.p["mtu_payload"]
        while True:
            with self._lock:
                pick = None
                while not self._closed:
                    best = None
                    for r in self._rpcs.values():
                        o = r.out
                        if o is None:
                            continue
                        if o.rexmit:
                            pick = (r, "rexmit"); break
                        if o.sent < o.granted and (best is None or o.left() < best.out.left()):
                            best = r
                    if pick is None and best is not None:
                        pick = (best, "data")
                    if pick is not None:
                        break
                    self._lock.wait(0.5)
                if self._closed:
                    return
                r, kind = pick
                o = r.out
                if kind == "rexmit":
                    off, ln = o.rexmit.pop(0)
                    ln = min(ln, mtu)
                    self.m["bytes_resent"] += ln
                else:
                    off = o.sent
                    ln = min(mtu, o.granted - o.sent)
                    if off == 0 and r.client:
                        r.t0 = time.monotonic()
                    o.sent += ln
                    if not r.client and o.sent >= len(o.data) and r.done_at is None:
                        r.done_at = self._tick
                        r.t0 = time.monotonic()
                pkt = (r.peer, DATA, r.client, r.rid, len(o.data), off, o.data[off:off + ln])
                self._send(*pkt)

    def _grants(self) -> None:
        """Keep unsched_bytes granted-but-not-received for up to max_overcommit incoming messages, shortest first."""
        need = [r for r in self._rpcs.values() if r.inc is not None and not r.inc.complete()]
        need.sort(key=lambda r: r.inc.length - r.inc.got)
        for r in need[:self.p["max_overcommit"]]:
            i = r.inc
            want = min(i.length, i.got + self.p["unsched_bytes"])
            if want > i.granted:
                i.granted = want
                self._send(r.peer, GRANT, r.client, r.rid, want)

    def _rx(self) -> None:
        while True:
            try:
                pkt, peer = self._sock.recvfrom(65535)
            except OSError:
                return
            if len(pkt) < HDR.size:
                continue
            typ, from_client, rid, a, b = HDR.unpack_from(pkt)
            body = pkt[HDR.size:]
            with self._lock:
                if typ in NAMES:
                    self.m[("rx", NAMES[typ])] += 1
                key = (peer, rid) if from_client else (None, rid)
                if not from_client and typ == DATA:
                    r0 = self._rpcs.get(key)
                    if r0 is not None:
                        self._measured(r0.peer, r0)
                if key in self._finished:
                    if typ == DATA:
                        self.m["duplicate_requests_not_rerun"] += 1
                    continue                                          # answered and ACKed: nothing of it runs again
                r = self._rpcs.get(key)
                if typ == DATA:
                    self._on_data(r, key, peer, bool(from_client), rid, a, b, body)
                    self._lock.notify_all()
                    continue
                if r is not None and (r.client or typ == ACK):
                    self._measured(r.peer, r)
                if r is None:
                    if typ == RESEND and from_client and self.is_server:
                        self._send(peer, UNKNOWN, False, rid)         # never heard of it: the client starts over
                    continue
                elif typ == GRANT:
                    r.silent = 0
                    if r.out is not None and a > r.out.granted:
                        r.out.granted = min(len(r.out.data), a)
                elif typ == BUSY:
                    r.silent = 0
                elif typ == HELD:
                    r.silent = 0
                    r.held = True
                elif typ == RESEND:
                    o = r.out
                    if r.done_at is not None and not self.p["held_waits"]:
                        r.done_at = self._tick                        # still wanted: keep the finished RPC's state
                    if o is not None and a + b > o.granted:
                        o.granted = min(len(o.data), a + b)           # the receiver granted this; its GRANT was lost
                    if o is not None and a < o.sent:
                        o.rexmit.append((a, min(b if b else self.p["mtu_payload"], o.sent - a)))
                    elif self.p["held_waits"] and not r.client and r.delivered and o is None:
                        self._send(peer, HELD, False, rid)            # the HELD was lost: say it again, once asked
                    else:
                        self._send(peer, BUSY, r.client, rid)         # alive, and nothing it may send now
                elif typ == UNKNOWN:
                    if r.client and r.out is not None:
                        r.out.sent, r.out.granted, r.out.rexmit = 0, min(len(r.out.data), self.p["unsched_bytes"]), []
                        r.silent = 0
                elif typ == ACK:
                    if not r.client:
                        self._rpcs.pop(key, None)
                        self._finished[key] = self._tick
                self._lock.notify_all()

    def _on_data(self, r, key, peer, from_client: bool, rid: int, length: int, off: int, body: bytes) -> None:
        if from_client:                                # a request's packet, at the server
            if not self.is_server:
                return
            if r is None:
                r = _Rpc(rid, peer, False)
                self._rpcs[key] = r
            if r.delivered:
                self.m["duplicate_requests_not_rerun"] += 1   # at most once: it is not run again
                return
            if r.inc is None:
                r.inc = _In(length, self.p["unsched_bytes"])
            r.silent = 0
            r.inc.add(off, body)
            if r.inc.complete():
                r.delivered = True
                data, r.inc = bytes(r.inc.buf), None
                self.m["requests_delivered"] += 1
                self.m["bytes_in"] += len(data)
                if self.p["held_waits"]:
                    self._send(peer, HELD, False, rid)                # the whole request is here: the wait is held
                self._inbox.put(Msg(rid | 1, 0, peer, data, True))
        else:                                          # a response's packet, at the client
            if r is None or not r.client:
                return
            if r.inc is None:
                r.inc = _In(length, self.p["unsched_bytes"])
            r.silent = 0
            r.inc.add(off, body)
            if r.inc.complete():
                data = bytes(r.inc.buf)
                self._rpcs.pop(key, None)
                self.m["responses_delivered"] += 1
                self.m["bytes_in"] += len(data)
                self._send(peer, ACK, True, rid)
                self._inbox.put(Msg(rid, r.cookie, peer, data, False))
        self._grants()

    def _timer(self) -> None:
        tick_s = self.p["tick_ms"] / 1000.0
        nxt = time.monotonic()
        while not self._closed:
            nxt += tick_s
            time.sleep(max(0.0, nxt - time.monotonic()))
            with self._lock:
                self._tick += 1
                if self._tick % 1000 == 0:                             # forget finished RPCs after 20 x timeout_ticks
                    for k in [k for k, t in self._finished.items() if self._tick - t > 20 * 30 * self.p["timeout_ticks"]]:
                        del self._finished[k]
                for key, r in list(self._rpcs.items()):
                    rt, ri, tt = self._timers(r.peer)
                    # protocol.md: a receiver asks again only when it is actually expecting data -- not for a
                    # message it has itself not granted further (it is deliberately making that one wait)
                    if not r.client and r.done_at is not None:                             # response sent, awaiting ACK
                        age = self._tick - r.done_at
                        if not self.p["rpc_timeout"]:
                            q, rem = divmod(age, rt)
                            if age >= rt and rem == 0 and (q & (q - 1)) == 0:       # its writer's to deliver: doubling
                                r.out.rexmit.append((0, self.p["mtu_payload"]))
                                self._lock.notify_all()
                        elif age > 2 * tt:
                            self._rpcs.pop(key, None)
                            self._finished[key] = self._tick
                        elif self.p["held_waits"] and self._ask_now(r.peer, age, rt, ri):
                            r.out.rexmit.append((0, self.p["mtu_payload"]))   # the writer owns getting it there
                            self._lock.notify_all()
                        continue
                    if r.client and r.held and r.inc is None:
                        r.silent = 0                                                       # a held read: nothing is asked
                        continue
                    if r.inc is not None:
                        expecting = r.inc.got < r.inc.granted                              # granted bytes are due
                    elif r.client:
                        # nothing of the response yet: a client that has sent all it may is owed a grant or the
                        # response; one still working through its own backlog is owed nothing yet
                        expecting = r.out is not None and r.out.sent >= r.out.granted and not r.out.rexmit
                    else:
                        expecting = False                                                  # the application has it
                    if not expecting:
                        r.silent = 0
                        continue
                    r.silent += 1
                    if self.p["rpc_timeout"] and r.silent >= tt:
                        self._rpcs.pop(key, None)
                        self.m["timeouts"] += 1
                        if r.client:
                            self._inbox.put(RpcFailed(r.rid, r.cookie, errno.ETIMEDOUT))
                        continue
                    if self._ask_now(r.peer, r.silent, rt, ri):
                        off, ln = r.inc.first_gap() if r.inc is not None else (0, 0)
                        self._send(r.peer, RESEND, r.client, r.rid, off, ln)


def startup_line(who: str, s: HomaSimSocket) -> str:
    """[DIAGNOSTICS_SAY_IT_V1] what this process is running on, in its log."""
    return ("[HOMASIM] %s build=%s Homa simulator (core/homa_sim.py) udp_port=%d %s loss=%s"
            % (who, BUILD, s.port, " ".join("%s=%s" % kv for kv in sorted(s.p.items())), s.loss))
