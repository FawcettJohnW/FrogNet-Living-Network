#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
proxy/transport_homa.py -- [HOMA_TRANSPORT_V1] the proxy's end of the FNW1 wire when the transport is Homa.

The transport is the transport. Homa owns the wire and how it works: every message here is one Homa RPC, scheduled,
retransmitted and delivered as Homa sees fit, in whatever order Homa delivers it. This file is the DRIVER: it presents
that wire to the semantic engine as the bus the engine was written against, and the engine does not change.

THE BUS CONTRACT the engine has always had from its transport (read from _DaemonWorker and SemanticSession, the TCP
driver; proxy/test_homa_ordering_oracle.py holds this driver to it):

  1. whole frames               a frame goes in whole and comes out whole.
  2. in order, each way         frames reach the daemon's engine in the order the proxy's engine submitted them, and
                                replies reach the proxy's engine in the order the daemon's engine queued them
                                ([REFERENCES_MOVE_IN_WIRE_ORDER_V1] rests on this and on nothing else).
  3. many in flight             submit() never waits on the network; any number of frames are outstanding at once.
  4. a reply finds its request  the caller of submit() gets the reply to the frame it submitted.
  5. broken is broken           when the wire to a peer fails, every frame outstanding on it fails, now, with the
                                reason, and the worker is finished. Nothing is re-sent. [NO_FALLBACK_V1]

How each is kept on Homa, which gives 1 and 4 itself and none of the others:

  2. [DRIVER_KEEPS_WIRE_ORDER_V1] Every request message is  "FNH1" + session(8) + seq(8) + frame : the driver's own
     numbering, counted in submit() (the caller holds the engine's lock there, so the count IS the engine's order).
     The daemon's driver hands frames up in seq order, holding one that arrived early until those before it are in.
     Every reply message is  "FNH1" + rseq(8) + frame , counted by the daemon's writer as it takes replies off the
     engine's queue; this file hands replies up in rseq order the same way. The header is the driver's, as the 4-byte
     length and the 4-byte reply sequence are the TCP driver's. No frame is read by either driver.
  3. A frame is one RPC, sent in submit(). Nothing is held back from the wire.
  5. [LIVENESS_IS_A_HELD_READ_V1] Whether the peer is there is not asked of Homa. core/homa_liveness holds one read
     on the (homa, can) family in the ControlRam and reads the hosts block at each network event; when the peer's
     daemon end stops, or is replaced, or its machine leaves the network, every session with it ends at once: every
     outstanding frame fails with that reason (PeerGone), the requests are aborted in Homa, and the worker is
     retired. The next request to the peer starts a new session, as a new connection does on TCP. This does not
     depend on Homa's per-RPC timeout and works with it removed. If the carrier does report an RPC failed (the
     module as shipped: ETIMEDOUT after timeout_ticks of silence), that one failure leaves a hole in the order that
     can never fill, so it ends the session the same way.
     The peer is named by the .1 its daemon gives in its HELLO reply (as the TCP driver's sessions are named by the
     HELLO), and until that arrives by the .1 of the address the session was opened to.
     The daemon's driver ends a session too, when ITS liveness says this proxy end is gone (or it has no such session
     any more): in place of a reply it sends "FNHE" + the reason, and the session here ends with that reason.

Not carried over from the TCP driver, because they are that driver's and not the contract: its retry (one _Rpc sent
up to three times; Homa retransmits and runs a request at most once), its second RETURN connection, and its ping
ladder. One Homa socket serves every peer (Homa has no connection); one thread receives for all of them.
"""
from __future__ import annotations

import itertools
import os
import struct
import threading
import time
from typing import Any, Dict, Optional, Tuple

from core import homa_socket
from core.homa_liveness import node_of
from core.semcache_wire import wrap_hello, try_parse, OP_HELLO
from proxy import transport_semantic as TS

_SAFETY_CAP = 60.0          # the same bound _DaemonWorker._wait_resolved puts on a caller's wait
DRV_MAGIC = b"FNH1"
REQ_HDR = struct.Struct("!4s8sQ")       # magic, session, seq
REP_HDR = struct.Struct("!4sQ")         # magic, reply seq
END_MAGIC = b"FNHE"                     # the daemon's driver, in place of a reply: "FNHE" + why this session has ended
MAX_FRAME = homa_socket.HOMA_MAX_MESSAGE_LENGTH - REQ_HDR.size


class PeerGone(ConnectionError):
    """The peer's daemon end is not there any more (core/homa_liveness said so, with the reason)."""


def _peer_liveness(on_gone, on_network_event):
    """This process's liveness: the proxy end. A test replaces this function; nothing in the tree does."""
    from core.homa_liveness import PeerLiveness
    return PeerLiveness("proxy", 0, on_gone, "proxy semantic wire", on_network_event=on_network_event)


class _ClientMux:
    """The process's one client socket and its receiver thread. cookie -> the worker and _Rpc it belongs to."""

    def __init__(self, carrier):
        self.carrier = carrier
        self._cookies = itertools.count(1)
        self._lock = threading.Lock()
        self._waiting: Dict[int, Tuple["_MsgWorker", Any]] = {}
        self._workers: set = set()                     # the sessions that have not ended
        self.n_peers_gone = self.n_network_events = 0
        self.liveness = _peer_liveness(self._peer_gone, self._network_event)
        print("[SEM-HOMA] [LIVENESS_IS_A_HELD_READ_V1] a peer leaving is learned from the held read on (homa, can) and "
              "the hosts block; Homa's per-RPC timeout is not relied on", flush=True)
        self._rx = threading.Thread(target=self._receive, daemon=True, name="sem-homa-rx")
        self._rx.start()

    # -- liveness
    def attach(self, worker: "_MsgWorker") -> None:
        with self._lock:
            self._workers.add(worker)
        self.liveness.watch(worker.node)

    def renamed(self, worker: "_MsgWorker") -> None:
        self.liveness.watch(worker.node)               # its HELLO reply named the peer's .1

    def detach(self, worker: "_MsgWorker", ids) -> None:
        """A session ended: forget it, and give up in Homa the requests it had outstanding."""
        with self._lock:
            self._workers.discard(worker)
            for c in [c for c, (w, _r) in self._waiting.items() if w is worker]:
                self._waiting.pop(c, None)
        for rid in ids:
            try:
                self.carrier.abort(rid)
            except Exception as e:
                print("[SEM-HOMA] abort of rpc %d to %s:%d: %r" % (rid, worker.host, worker.port, e), flush=True)

    def _peer_gone(self, ip: str, why: str, machine_left: bool) -> None:
        """core/homa_liveness: the daemon end at node `ip` is gone. Every session with it ends now. [contract 5]"""
        with self._lock:
            hit = [w for w in self._workers if w.node == ip]
        self.n_peers_gone += 1
        print("[SEM-HOMA] peer %s is GONE (%s): %d session(s) end with it" % (ip, why, len(hit)), flush=True)
        for w in hit:
            w._broken(PeerGone("peer %s is gone: %s" % (ip, why)))
        if machine_left:
            # Only then is the address itself gone. A daemon that stopped is started again at the same address, and
            # what this socket has with the new one must stand; the old one's requests were aborted one by one.
            for addr in sorted({(w.host, w.port) for w in hit}):
                self.carrier.peer_gone(addr)

    def _network_event(self) -> None:
        self.n_network_events += 1
        print("[SEM-HOMA] network event: the ControlRam was rebuilt and this end wrote its own tuple again; sessions "
              "with peers that are still there carry on", flush=True)

    def send(self, worker: "_MsgWorker", rpc, wire: bytes) -> None:
        cookie = next(self._cookies)
        with self._lock:
            self._waiting[cookie] = (worker, rpc)
        rpc.sent_at[cookie] = time.time()
        rpc.attempts = 1
        rpc.retry_pending = False
        try:
            rpc.seqs.append(self.carrier.send_request((worker.host, worker.port), wire, cookie))
        except BaseException as e:
            with self._lock:
                self._waiting.pop(cookie, None)
            worker._broken(e)                          # its seq will never arrive: the order has a hole

    def _receive(self) -> None:
        why = "unset"
        try:
            while True:
                try:
                    m = self.carrier.recv()
                except homa_socket.RpcFailed as e:
                    with self._lock:
                        hit = self._waiting.pop(e.cookie, None)
                    if hit is not None:
                        hit[0]._broken(e)
                    continue
                if m.is_request:
                    print("[SEM-HOMA] a REQUEST (rpc %d, %dB from %s:%d) arrived on the proxy's client socket; the "
                          "proxy serves none and it is not answered" % (m.id, len(m.payload), m.peer[0], m.peer[1]),
                          flush=True)
                    continue
                with self._lock:
                    hit = self._waiting.pop(m.cookie, None)
                if hit is None:
                    print("[SEM-HOMA] a reply for cookie %d whose session has ended: dropped" % m.cookie, flush=True)
                    continue
                worker, rpc = hit
                sent = rpc.sent_at.get(m.cookie)
                if sent is not None:
                    worker._peer_latency.observe(time.time() - sent)
                worker._arrived(rpc, m.payload)
        except homa_socket.CarrierClosed:
            why = "socket closed"
        except BaseException as e:
            why = "exception %r" % (e,)
            raise
        finally:
            with self._lock:
                left = {id(w): w for w, _r in self._waiting.values()}; self._waiting.clear()
            print("[SEM-HOMA] receiver thread ended: %s; %d sessions end with it" % (why, len(left)), flush=True)
            for w in left.values():
                w._broken(ConnectionError("the semantic wire's Homa receiver ended: %s" % why))


_mux: Optional[_ClientMux] = None
_mux_lock = threading.Lock()


def _client_carrier():
    """The proxy's Homa socket. A test replaces this function to put a stand-in with the same calls under the driver;
    nothing in the tree does."""
    from core.homa_transport import open_homa_socket
    return open_homa_socket("proxy semantic wire (client socket)")


def _get_mux() -> _ClientMux:
    global _mux
    with _mux_lock:
        if _mux is None:
            _mux = _ClientMux(_client_carrier())
        return _mux


class _MsgWorker:
    """One session with one peer: the engine's transport object. See the module docstring for the contract."""

    #: [DRIVER_KEEPS_WIRE_ORDER_V1] True is the driver. The ordering oracle sets it False, on both ends, to show what
    #: the engine does on a wire that is not kept in order.
    KEEP_ORDER = True

    def __init__(self, host: str, port: int, set_id: int = 0):
        self.host, self.port, self._set_id = host, int(port), set_id
        self.node = node_of(host)                      # the peer, for liveness; its HELLO reply names its own .1
        self._mux = _get_mux()
        self._lock = threading.Lock()
        self.session = os.urandom(8)
        self._seq = 0                                  # next request seq (submit order)
        self._next_r = 0                               # next reply seq to hand up
        self._early: Dict[int, Tuple[Any, bytes]] = {}  # replies that arrived before their turn
        self._out = set()                              # frames outstanding
        self._peer_latency = TS._PeerLatency(budget_floor=TS._BUDGET_FLOOR, budget_mult=TS._BUDGET_MULT,
                                             budget_ceiling=TS._BUDGET_CEILING)
        self._stopped = threading.Event()
        self._stopped_reason = ""
        self._stopped_at = 0.0
        self._hello = None                             # the session's own HELLO, until its reply is in
        self.n_sent = self.n_replies = self.n_early = self.n_failed = 0
        self.max_out = self.max_early = 0
        print("[SEM-HOMA] session %s to %s:%d set=%d opened" % (self.session.hex(), host, self.port, set_id), flush=True)
        self._mux.attach(self)
        self._submit(wrap_hello(TS._get_local_gw()), None, hello=True)     # frame 0 of the session, as on TCP; its reply is in order

    # -- what the registry (_get_worker) asks of a worker
    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    def stopped_reason(self) -> str:
        return self._stopped_reason

    def stop(self, err: Optional[BaseException] = None) -> None:
        self._broken(err if err is not None else ConnectionError("stopped"))

    # -- the engine's three calls
    def call(self, packet: bytes) -> bytes:
        return self.wait(self.submit(packet))

    def submit(self, packet: bytes, gate=None):
        return self._submit(packet, gate)

    def _submit(self, packet: bytes, gate, hello: bool = False):
        if len(packet) > MAX_FRAME:
            raise ValueError("a frame of %d bytes is over what one Homa message carries (%d after the driver's header); "
                             "it is not split" % (len(packet), MAX_FRAME))
        rpc = TS._Rpc(packet, max_attempts=1, gate=gate)
        with self._lock:
            if self._stopped.is_set():
                raise RuntimeError("peer %s:%d set=%d: session %s ended (%s) - not sending"
                                   % (self.host, self.port, self._set_id, self.session.hex(), self._stopped_reason))
            seq = self._seq
            self._seq += 1
            self._out.add(rpc)
            if hello:
                self._hello = rpc
            self.n_sent += 1
            self.max_out = max(self.max_out, len(self._out))
        self._mux.send(self, rpc, REQ_HDR.pack(DRV_MAGIC, self.session, seq) + packet)
        return rpc

    def wait(self, rpc) -> bytes:
        if not rpc.done.wait(timeout=_SAFETY_CAP):
            raise TimeoutError("RPC to %s:%d: no reply in %.0fs and Homa reported no failure (the peer's kernel is "
                               "answering, its daemon has not)" % (self.host, self.port, _SAFETY_CAP))
        if rpc.err:
            raise rpc.err
        if rpc.reply is None:
            raise RuntimeError("RPC to %s:%d: signaled but no reply" % (self.host, self.port))
        return rpc.reply

    # -- the receiver thread's two calls
    def _hand_up(self, rpc, frame: bytes) -> None:
        """One reply, in its turn. Under self._lock, so tickets are stamped in the order replies are handed up."""
        rpc.reply = frame
        if rpc is self._hello:
            self._hello = None
            self._named(frame)
        if rpc.gate is not None:
            rpc.ticket = TS._gate_stamp(rpc.gate)      # [REFERENCES_MOVE_IN_WIRE_ORDER_V1] as _DaemonWorker's reader does
        self._out.discard(rpc)
        self.n_replies += 1
        rpc.done.set()

    def _named(self, frame: bytes) -> None:
        """The daemon's HELLO reply: the .1 it names itself by. The session's own frame, as the TCP driver's HELLO is."""
        try:
            msg = try_parse(frame)
            gw = (msg.body or b"").decode("ascii", "replace").split("#", 1)[0] if msg is not None and msg.op == OP_HELLO else ""
        except Exception as e:
            gw = ""
            print("[SEM-HOMA] session %s: the HELLO reply from %s could not be read (%r); the peer stays named %s"
                  % (self.session.hex(), self.host, e, self.node), flush=True)
        p = gw.split(".")
        if len(p) == 4 and all(x.isdigit() for x in p) and gw != "0.0.0.0" and gw != self.node:
            print("[SEM-HOMA] session %s to %s: its daemon names itself %s" % (self.session.hex(), self.host, gw), flush=True)
            self.node = gw
            self._mux.renamed(self)

    def _arrived(self, rpc, payload: bytes) -> None:
        if payload[:4] == END_MAGIC:
            self._broken(PeerGone("the daemon at %s:%d ended this session: %s"
                                  % (self.host, self.port, payload[4:].decode("utf-8", "replace"))))
            return
        if len(payload) < REP_HDR.size or payload[:4] != DRV_MAGIC:
            self._broken(ConnectionError("a reply from %s:%d without the driver's header (%r): the far end is not this "
                                         "driver" % (self.host, self.port, payload[:16])))
            return
        rseq = REP_HDR.unpack_from(payload)[1]
        frame = payload[REP_HDR.size:]
        with self._lock:
            if self._stopped.is_set():
                return
            if not self.KEEP_ORDER:
                self._hand_up(rpc, frame)
                return
            if rseq != self._next_r:
                self.n_early += 1
            self._early[rseq] = (rpc, frame)
            self.max_early = max(self.max_early, len(self._early))
            while self._next_r in self._early:
                r, f = self._early.pop(self._next_r)
                self._next_r += 1
                self._hand_up(r, f)

    def _broken(self, err: BaseException) -> None:
        """[contract 5] The wire to this peer failed. Every outstanding frame fails with the reason; the session is over."""
        with self._lock:
            if self._stopped.is_set():
                return
            self._stopped_reason = "%s: %s" % (type(err).__name__, err)
            self._stopped_at = time.time()
            self._stopped.set()
            left = list(self._out); self._out.clear(); self._early.clear()
            self.n_failed += len(left)
        print("[SEM-HOMA] session %s to %s:%d ENDED: %s; %d outstanding frames fail with it (sent=%d replies=%d)"
              % (self.session.hex(), self.host, self.port, self._stopped_reason, len(left), self.n_sent, self.n_replies),
              flush=True)
        for rpc in left:
            rpc.err = err if isinstance(err, OSError) else ConnectionError(self._stopped_reason)
            rpc.done.set()
        self._mux.detach(self, [rid for rpc in left for rid in rpc.seqs])

    def stats(self) -> Dict[str, Any]:
        return {"host": self.host, "session": self.session.hex(), "sent": self.n_sent, "replies": self.n_replies,
                "replies_arrived_early": self.n_early, "most_held_early": self.max_early,
                "most_outstanding": self.max_out, "failed": self.n_failed}
