#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
daemon/engine/server_homa.py -- [HOMA_TRANSPORT_V1] the daemon's end of the FNW1 wire when the transport is Homa.

The driver's other half: read proxy/transport_homa.py first, which states the bus contract. Homa owns the wire; this
file presents it to SemanticSession as the bus it was written against. The engine is SemanticSession itself,
unchanged: _MsgSession replaces only its two socket loops.

  frames come up in order   [DRIVER_KEEPS_WIRE_ORDER_V1] a request message is "FNH1" + session + seq + frame. Frames
                            of one session are handed to SemanticSession._submit_frame in seq order, on the one
                            receiving thread, as the TCP reader thread hands them: one that arrives early is held
                            until those before it are in. The session's `seq` argument is the RPC id.
  replies go down in order  the session queues (seq, reply, wire_in) as it always has. The session's writer takes
                            them in queue order, numbers them, and answers RPC `seq` with "FNH1" + rseq + reply; the
                            proxy's driver hands them up in that order. EVERY request is answered through that queue,
                            the ones answered here too (HELLO, RTT_PING, a malformed frame), so the numbering has no
                            holes.
  a session                 one per (peer address, session id), made by its first message. Its peer is named by
                            the .1 in its HELLO (frame 0), as a TCP session is named by its HELLO's return address.

WHEN A PEER LEAVES  [LIVENESS_IS_A_HELD_READ_V1]. A Homa server is told nothing when a client goes away, and nothing
is asked of Homa. core/homa_liveness holds one read on the (homa, can) family in the ControlRam and reads the hosts
block at each network event. When a peer's proxy end stops or is replaced, or its machine leaves the network, every
session from it ends here: it answers nothing more, the requests it still owed an answer are answered with the driver's
ended message ("FNHE" + why; answering is the only way a Homa server lets go of a request), and a message that
arrives for it afterwards is answered the same way. When it was the MACHINE that left, this daemon then tells its local api.php remove_writer for
the machine's /24, the call the TCP driver makes when the machine's last socket closes ([WRITER_GONE_V1]): on a
RAM host that is what takes the machine's tuples out of the memory and fires the holds on them. A proxy that
only stopped has not left the network, and what its machine wrote stays.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Dict, Tuple

from core import homa_socket
from core.homa_liveness import node_of
from core.semcache_wire import try_parse, wrap_hello, wrap_rtt_pong, wrap_error, OP_HELLO, OP_RTT_PING
from daemon.engine import session as S
from daemon.engine.execution import ExecutionEngine
from daemon.upstream.client import HttpClient
from daemon.cache import semcache_db
from proxy.transport_homa import DRV_MAGIC, REQ_HDR, REP_HDR, END_MAGIC

MAX_REPLY = homa_socket.HOMA_MAX_MESSAGE_LENGTH - REP_HDR.size


def _ended_msg(sid: bytes, why: str) -> bytes:
    """What the driver answers in place of a reply on a session that has ended."""
    return END_MAGIC + ("session %s ended: %s" % (sid.hex(), why)).encode("utf-8")


class _MsgSession(S.SemanticSession):
    """SemanticSession with the Homa driver in place of its two sockets. Everything from _submit_frame up is inherited."""

    #: [DRIVER_KEEPS_WIRE_ORDER_V1] True is the driver. The ordering oracle sets it False to show what it is for.
    KEEP_ORDER = True
    keep_order_record = False        # the ordering oracle turns this on to read `order`

    def __init__(self, peer: Tuple[str, int], sid: bytes, resolver, http_client, carrier, fast_exec, db_exec):
        # the fields SemanticSession.__init__ sets, less the two sockets and their options
        self._encode_lock = threading.Lock()             # [REFERENCES_MOVE_IN_WIRE_ORDER_V1]
        self.peer_ip = peer[0]
        self.established = True
        self.on_established = None
        self.resolver = resolver
        self._recv_sock = None
        self._send_sock = None
        self._return_ip = None
        self._send_sock_event = threading.Event()
        self._http_client = http_client
        self.engine = ExecutionEngine(resolver=self.resolver, http_client=self._http_client)
        self._request_seq = 0
        self._reply_q: "queue.Queue[Tuple[int, bytes, int]]" = queue.Queue()
        self._alive = True
        self._preread_frame = None
        # the driver's
        self.sid = sid
        self.gw = ""
        self.peer = peer                                  # the client socket's address, for the carrier
        self.node = node_of(peer[0])                      # the peer, for liveness; its HELLO names its own .1
        self.on_named = None                              # the server's: called once the HELLO has named the peer
        self.ended = ""                                   # why this session ended, once it has
        self._carrier, self._fast, self._db = carrier, fast_exec, db_exec
        self._next = 0                                    # next request seq to hand up
        self._early: Dict[int, Tuple[int, Tuple[str, int], bytes]] = {}
        self._dest: Dict[int, Tuple[str, int]] = {}       # rpc id -> where its reply goes, until it has gone
        self._dest_lock = threading.Lock()
        self.n_in = self.n_out = self.n_early = 0
        self.max_early = 0
        self.order = []                                   # with keep_order_record: seqs in the order handed up
        self._writer = threading.Thread(target=self._write_loop, daemon=True, name="daemon-homa-writer-%s" % self.peer_ip)
        self._writer.start()

    # -- the receiving thread
    def arrive(self, seq: int, rpc_id: int, peer: Tuple[str, int], frame: bytes) -> None:
        if self.ended:
            self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, self.ended))
            return
        self.n_in += 1
        if not self.KEEP_ORDER:
            self._hand_up(seq, rpc_id, peer, frame)
            return
        if seq != self._next:
            self.n_early += 1
        self._early[seq] = (rpc_id, peer, frame)
        self.max_early = max(self.max_early, len(self._early))
        while self._next in self._early:
            rid, pr, fr = self._early.pop(self._next)
            s = self._next
            self._next += 1
            self._hand_up(s, rid, pr, fr)

    def _hand_up(self, seq: int, rpc_id: int, peer: Tuple[str, int], frame: bytes) -> None:
        """One frame, in its turn, to the engine. Every path queues exactly one reply for rpc_id."""
        with self._dest_lock:
            self._dest[rpc_id] = peer
        if self.ended:                                    # it ended while this frame was on its way up
            with self._dest_lock:
                owed = self._dest.pop(rpc_id, None)
            if owed is not None:
                self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, self.ended))
            return
        if self.keep_order_record:
            self.order.append(seq)
        t_recv = time.monotonic_ns()
        try:
            msg = try_parse(frame)
            if msg is not None and msg.op == OP_HELLO:
                self.gw = (msg.body or b"").decode("ascii", "replace")
                print("[DAEMON-HOMA] HELLO from %s session %s (its gw %s)" % (self.peer_ip, self.sid.hex(), self.gw), flush=True)
                g = self.gw.split("#", 1)[0]
                p = g.split(".")
                if len(p) == 4 and all(x.isdigit() for x in p) and g != "0.0.0.0":
                    self.node = g
                if self.on_named is not None:
                    self.on_named(self)
                self._reply_q.put((rpc_id, wrap_hello(S._get_local_gw()), len(frame)))
                return
            if msg is not None and msg.op == OP_RTT_PING:
                self._reply_q.put((rpc_id, wrap_rtt_pong(msg.ping_id, msg.proxy_t_send_ns, t_recv, time.monotonic_ns()), len(frame)))
                return
            S._is_fast_frame(frame)        # _submit_frame drops a frame this rejects WITHOUT a reply; an RPC must have one
        except (ValueError, S.FrameMalformed) as e:
            print("[DAEMON-HOMA] MALFORMED FRAME from %s rpc=%d %dB: %s" % (self.peer_ip, rpc_id, len(frame), e), flush=True)
            self._reply_q.put((rpc_id, wrap_error(400, "malformed FNW1 frame: %s" % e), len(frame)))
            return
        self._submit_frame(frame, rpc_id, len(frame), self._fast, self._db)

    def end(self, why: str) -> int:
        """The peer is gone. Runs on the liveness thread while the receiving thread may be in arrive(): `ended` is set
        first, and each side lets go of what it finds in _dest after that. Nothing more is answered in order. Each
        request still owed an answer gets the driver's ended message, which is how a Homa server lets go of a
        request. Returns how many were owed."""
        self.ended = why
        self._alive = False
        with self._dest_lock:
            owed = list(self._dest.items()); self._dest.clear()
        self._reply_q.put((-1, b"", 0))
        for rid, dest in owed:
            try:
                self._carrier.send_reply(dest, rid, _ended_msg(self.sid, why))
            except Exception as e:
                print("[DAEMON-HOMA] session %s: letting go of rpc %d to %s:%d: %r" % (self.sid.hex(), rid, dest[0], dest[1], e), flush=True)
        return len(owed)

    # -- the writer thread
    def _write_loop(self) -> None:
        print("[DAEMON-HOMA-WRITER] started for %s session %s" % (self.peer_ip, self.sid.hex()), flush=True)
        why, rseq = "unset", 0
        try:
            while True:
                seq, reply, wire_in = self._reply_q.get()
                if seq == -1:
                    why = "session ended"
                    break
                with self._dest_lock:
                    dest = self._dest.pop(seq, None)
                if dest is None:
                    if not self.ended:
                        print("[DAEMON-HOMA-WRITER] reply for rpc %d to %s has no request on record: not sent"
                              % (seq, self.peer_ip), flush=True)
                    continue
                if len(reply) > MAX_REPLY:
                    print("[DAEMON-HOMA-WRITER] reply of %dB for rpc %d to %s is over what one Homa message carries "
                          "(%d): answered ERROR 413 in its place" % (len(reply), seq, self.peer_ip, MAX_REPLY), flush=True)
                    reply = wrap_error(413, "reply of %d bytes is over what one Homa message carries (%d); it is not split"
                                       % (len(reply), MAX_REPLY))
                try:
                    self._carrier.send_reply(dest, seq, REP_HDR.pack(DRV_MAGIC, rseq) + reply)
                except Exception as e:
                    # the reply numbered rseq will never reach the proxy, so nothing after it can be handed up there:
                    # this session's reply order is broken. Said once, with everything; the session stops answering.
                    print("[DAEMON-HOMA-WRITER] SEND FAILED rpc=%d rseq=%d %dB to %s:%d: %s: %r - the session's reply "
                          "order is broken; it answers nothing more and its peer's outstanding requests will not complete"
                          % (seq, rseq, len(reply), dest[0], dest[1], type(e).__name__, e), flush=True)
                    why = "send failed"
                    break
                rseq += 1
                self.n_out += 1
                S.bump_semantic(peer_ip=self.peer_ip, wire_in=wire_in, wire_out=len(reply))
        except Exception as e:
            why = "exception %s: %r" % (type(e).__name__, e)
            raise
        finally:
            self._alive = False
            print("[DAEMON-HOMA-WRITER] exited for %s session %s: %s (in=%d out=%d)"
                  % (self.peer_ip, self.sid.hex(), why, self.n_in, self.n_out), flush=True)


class MsgDaemonServer:
    """The daemon's listener when the transport is Homa. Same constructor and run() as DaemonServer."""

    def __init__(self, host: str, port: int, resolver):
        self.host, self.port, self.resolver = host, int(port), resolver
        self._http_client = HttpClient()
        self._sessions: Dict[Tuple[str, bytes], _MsgSession] = {}
        self._ended: Dict[Tuple[str, bytes], str] = {}   # sessions that ended -> why (the last 4096 of them)
        self._lock = threading.Lock()
        self._carrier = None
        self.liveness = None
        self.n_peers_gone = self.n_network_events = 0

    def _peer_liveness(self):
        """This process's liveness: the daemon end. A test replaces this method; nothing in the tree does."""
        from core.homa_liveness import PeerLiveness
        from core.homa_transport import carriers_here
        return PeerLiveness("daemon", self.port, self._peer_gone, "daemon semantic wire",
                            on_network_event=self._network_event, carriers=carriers_here())

    def _writer_gone(self, ip: str) -> None:
        """[WRITER_GONE_V1] the TCP driver's own call, made when a machine's last socket closes. A test replaces this
        method."""
        from daemon.engine.server import _writer_gone
        _writer_gone(ip)

    def _named(self, s: "_MsgSession") -> None:
        self.liveness.watch(s.node)

    def _peer_gone(self, ip: str, why: str, machine_left: bool) -> None:
        """core/homa_liveness: the proxy end at node `ip` is gone. Every session from it ends now."""
        with self._lock:
            hit = [(k, s) for k, s in self._sessions.items() if s.node == ip]
            for k, s in hit:
                self._sessions.pop(k, None)
                self._ended[k] = why
            while len(self._ended) > 4096:
                self._ended.pop(next(iter(self._ended)))
        self.n_peers_gone += 1
        owed = sum(s.end(why) for _k, s in hit)
        print("[DAEMON-HOMA] peer %s is GONE (%s): %d session(s) from it ended, %d request(s) it was owed answered 'ended'; "
              "%d sessions remain" % (ip, why, len(hit), owed, len(self._sessions)), flush=True)
        if machine_left:
            # The ended messages above go to a machine that will not take them; a carrier that keeps no timeout of
            # its own is told the address is gone. (When only the proxy END stopped they are left to arrive: if this
            # daemon was wrong about it, that message is how the proxy learns its session is over.)
            for addr in sorted({s.peer for _k, s in hit}):
                self._carrier.peer_gone(addr)
            threading.Thread(target=self._writer_gone, args=(ip,), daemon=True, name="writer-gone-%s" % ip).start()

    def _network_event(self) -> None:
        self.n_network_events += 1
        print("[DAEMON-HOMA] network event: the ControlRam was rebuilt and this end wrote its own tuple again; sessions "
              "from peers that are still there carry on", flush=True)

    def _server_carrier(self):
        """The daemon's Homa socket. A test replaces this method to put a stand-in with the same calls under the
        driver; nothing in the tree does."""
        from core.homa_transport import open_homa_socket
        return open_homa_socket("daemon semantic wire (server socket %s:%d)" % (self.host, self.port),
                                bind_ip=self.host, server_port=self.port)

    def run(self) -> None:
        self._carrier = self._server_carrier()
        self.liveness = self._peer_liveness()
        print("[DAEMON-HOMA] [HOMA_TRANSPORT_V1] semantic wire on Homa at %s:%d. [LIVENESS_IS_A_HELD_READ_V1] a peer "
              "leaving is learned from the held read on (homa, can) and the hosts block; when a machine leaves, "
              "[WRITER_GONE_V1] is told as on TCP." % (self.host, self.port), flush=True)
        try:
            semcache_db.ensure_table()                    # as SemanticSession.run does before its first frame
        except Exception as e:
            print("[Daemon] semcache ensure_table failed: %r -- this node will answer RESP_DIFF only" % (e,), flush=True)
        fast_exec, db_exec = S._get_executor(), S._get_db_executor()
        why, new = "unset", None
        try:
            while True:
                try:
                    m = self._carrier.recv()
                except homa_socket.RpcFailed as e:
                    print("[DAEMON-HOMA] an incoming RPC failed in the kernel before delivery: %s" % e, flush=True)
                    continue
                if not m.is_request:
                    print("[DAEMON-HOMA] a RESPONSE (rpc %d, cookie %d) arrived on the daemon's server socket; the "
                          "daemon sends no requests: dropped" % (m.id, m.cookie), flush=True)
                    continue
                if len(m.payload) < REQ_HDR.size or m.payload[:4] != DRV_MAGIC:
                    print("[DAEMON-HOMA] a request from %s:%d without the driver's header (%r): the far end is not "
                          "this driver; answered ERROR 400 outside any session" % (m.peer[0], m.peer[1], m.payload[:16]), flush=True)
                    self._carrier.send_reply(m.peer, m.id, wrap_error(400, "not the FNW1-on-Homa driver's message"))
                    continue
                _magic, sid, seq = REQ_HDR.unpack_from(m.payload)
                key = (m.peer[0], sid)
                with self._lock:
                    s = self._sessions.get(key)
                    was = self._ended.get(key) if s is None else None
                    if s is None and was is None:
                        s = _MsgSession(m.peer, sid, self.resolver, self._http_client, self._carrier, fast_exec, db_exec)
                        s.on_named = self._named
                        self._sessions[key] = s
                        print("[DAEMON-HOMA] session %s from %s opened (%d sessions)" % (sid.hex(), m.peer[0], len(self._sessions)), flush=True)
                        new = s
                if s is None:
                    self._carrier.send_reply(m.peer, m.id, _ended_msg(sid, was))
                    continue
                if new is s:
                    self.liveness.watch(s.node)
                    new = None
                s.arrive(seq, m.id, m.peer, m.payload[REQ_HDR.size:])
        except homa_socket.CarrierClosed:
            why = "socket closed"
        except Exception as e:
            why = "exception %s: %r" % (type(e).__name__, e)
            raise
        finally:
            print("[DAEMON-HOMA] receive loop ended: %s" % why, flush=True)
            with self._lock:
                for s in self._sessions.values():
                    s._reply_q.put((-1, b"", 0))

    def stats(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            return {"%s/%s" % (ip, sid.hex()): {"in": s.n_in, "out": s.n_out, "arrived_early": s.n_early,
                                                "most_held_early": s.max_early} for (ip, sid), s in self._sessions.items()}
