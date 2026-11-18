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
from core import wire_metrics as WM          # [WHERE_THE_TIME_GOES_V1]
from core.homa_liveness import node_of
from core.semcache_wire import try_parse, wrap_hello, wrap_rtt_pong, wrap_error, OP_HELLO, OP_RTT_PING
from daemon.engine import session as S
from daemon.engine.execution import ExecutionEngine
from daemon.upstream.client import HttpClient
from daemon.cache import semcache_db
from proxy.transport_homa import (DRV_MAGIC, REQ_HDR, REP_HDR, END_MAGIC, PIECE_MAGIC, MORE_MAGIC, SPARE_MAGIC, NOTHING,
                                  REQ_PIECE, REP_PIECE, MORE_HDR, SPARE_HDR, cut, WireManager)



def _ended_msg(sid: bytes, why: str) -> bytes:
    """What the driver answers in place of a reply on a session that has ended."""
    return END_MAGIC + ("session %s ended: %s" % (sid.hex(), why)).encode("utf-8")


class _StampedQueue(queue.Queue):
    """[WHERE_THE_TIME_GOES_V1] The session's reply queue, which also keeps WHEN the engine put each reply on it: the
    writer takes a reply some time after the engine made it, and that wait is one of the intervals."""

    def __init__(self):
        super().__init__()
        self.put_ns: Dict[int, int] = {}

    def put(self, item, *a, **k):
        if WM.ON and item[0] >= 0:
            self.put_ns[item[0]] = time.perf_counter_ns()
        super().put(item, *a, **k)


class _MsgSession(S.EngineSession):
    """The engine's session (EngineSession) on the Homa driver. Everything from _submit_frame up is inherited."""

    #: [DRIVER_KEEPS_WIRE_ORDER_V1] True is the driver. The ordering oracle sets it False to show what it is for.
    KEEP_ORDER = True
    keep_order_record = False        # the ordering oracle turns this on to read `order`

    def __init__(self, peer: Tuple[str, int], sid: bytes, resolver, http_client, carrier, fast_exec, db_exec):
        S.EngineSession.__init__(self, peer[0], resolver, http_client)      # the engine's side; no socket is its
        self.established = True
        self._reply_q: "queue.Queue[Tuple[int, bytes, int]]" = _StampedQueue()
        self._tr_seq: Dict[int, object] = {}              # [WHERE_THE_TIME_GOES_V1] request seq -> its trail, until it goes to the engine
        self._tr: Dict[int, object] = {}                  # rpc id -> its trail, until the writer has its reply
        self._tr_out: Dict[int, tuple] = {}               # rpc id -> (trail, is the reply's last message), until it is sent
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
        # [FRAMES_GO_IN_PIECES_V1] (proxy/transport_homa.py has the format)
        self.max_message = int(getattr(carrier, "max_message", 0) or homa_socket.max_message())
        self._parts: Dict[int, Dict[int, Tuple[int, Tuple[str, int], bytes]]] = {}   # request seq -> its pieces so far
        self._open: Dict[int, list] = {}                  # a request's rpc id -> its other RPCs, still unanswered
        self._seq_of: Dict[int, int] = {}                 # a request's rpc id -> its seq (what a "more" names it by)
        self._kept: Dict[Tuple[int, int], bytes] = {}     # (request seq, index) -> a reply piece waiting for its RPC
        self._spares: list = []                           # [THE_RPCS_ARE_ALREADY_OPEN_V1] the proxy's spare RPCs, open
        self.wire_out = None                              # [THE_WIRE_MANAGER_SENDS_V1] the server's; set before a frame arrives
        self.send_failed = ""                             # why a reply could not be sent, once one could not
        self.n_joined = self.n_cut = self.n_on_spares = self.n_kept = 0
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
        tr = self._tr_seq.get(seq)
        if tr is None:
            tr = self._tr_seq[seq] = WM.start("daemon", "request", requests_owed=len(self._dest), threads=threading.active_count())
        WM.mark(tr, "frame_whole", held_for_order=len(self._early))
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

    def piece(self, seq: int, index: int, count: int, rpc_id: int, peer: Tuple[str, int], part: bytes) -> None:
        """[FRAMES_GO_IN_PIECES_V1] One piece of a request. When all are in, the frame arrives as one; the RPC of piece 0
        is the request's, and the others stay open for reply pieces."""
        if self.ended:
            self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, self.ended))
            return
        got = self._parts.setdefault(seq, {})
        if seq not in self._tr_seq:
            self._tr_seq[seq] = WM.start("daemon", "request", requests_owed=len(self._dest), threads=threading.active_count())
        WM.mark(self._tr_seq[seq], "piece_arrived.last", pieces=count)
        got[index] = (rpc_id, peer, part)
        if len(got) < count:
            return
        del self._parts[seq]
        rid0, peer0, _p = got[0]
        with self._dest_lock:
            self._open[rid0] = [(got[i][0], got[i][1]) for i in range(1, count)]
            for i in range(1, count):
                self._dest[got[i][0]] = got[i][1]         # so end() lets go of them too
        self.n_joined += 1
        if self.ended:                                    # it ended while the pieces were coming in: let go of them all
            with self._dest_lock:
                rest = self._open.pop(rid0, [])
                for rid, _d in rest:
                    self._dest.pop(rid, None)
            for rid, d in [(rid0, peer0)] + rest:
                self._carrier.send_reply(d, rid, _ended_msg(self.sid, self.ended))
            return
        self.arrive(seq, rid0, peer0, b"".join(got[i][2] for i in range(count)))

    def spare(self, rpc_id: int, peer: Tuple[str, int]) -> None:
        """[THE_RPCS_ARE_ALREADY_OPEN_V1] An RPC of no request, left open for a reply piece to go on."""
        if self.ended:
            self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, self.ended))
            return
        with self._dest_lock:
            self._spares.append((rpc_id, peer))
            self._dest[rpc_id] = peer                     # so end() lets go of it

    def more(self, seq: int, index: int, rpc_id: int, peer: Tuple[str, int]) -> None:
        """[FRAMES_GO_IN_PIECES_V1] The proxy's RPC for reply piece `index` of request `seq`. The piece was kept before
        piece 0 went, so it is here."""
        with self._dest_lock:
            part = self._kept.pop((seq, index), None)
        if part is None:
            why = self.ended or "it asked for reply piece %d of request %d, which this session does not hold" % (index, seq)
            self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, why))
            if not self.ended:
                print("[DAEMON-HOMA] session %s from %s: %s; answered 'ended'" % (self.sid.hex(), self.peer_ip, why), flush=True)
            return
        self.wire_out.give((self, peer, rpc_id, part), True)

    def _hand_up(self, seq: int, rpc_id: int, peer: Tuple[str, int], frame: bytes) -> None:
        """One frame, in its turn, to the engine. Every path queues exactly one reply for rpc_id."""
        with self._dest_lock:
            self._dest[rpc_id] = peer
            self._seq_of[rpc_id] = seq
        if self.ended:                                    # it ended while this frame was on its way up
            with self._dest_lock:
                owed = self._dest.pop(rpc_id, None)
            if owed is not None:
                self._carrier.send_reply(peer, rpc_id, _ended_msg(self.sid, self.ended))
            return
        if self.keep_order_record:
            self.order.append(seq)
        tr = self._tr_seq.pop(seq, None)
        if tr is not None:
            tr.bytes_in = len(frame)
            self._tr[rpc_id] = tr
        WM.mark(tr, "to_engine", replies_waiting_for_writer=self._reply_q.qsize())
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
        WM.mark(tr, "engine_took_it")

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
                tr = self._tr.pop(seq, None)
                put_at = self._reply_q.put_ns.pop(seq, None)
                if tr is not None:
                    if put_at:
                        tr.marks["engine_replied"] = put_at
                    tr.bytes_out = len(reply)
                WM.mark(tr, "writer_took", replies_waiting_for_writer=self._reply_q.qsize())
                if seq == -1:
                    why = "session ended"
                    break
                if seq == -2 or self.send_failed:
                    why = "send failed: " + self.send_failed
                    break
                with self._dest_lock:
                    dest = self._dest.pop(seq, None)
                if dest is None:
                    if not self.ended:
                        print("[DAEMON-HOMA-WRITER] reply for rpc %d to %s has no request on record: not sent"
                              % (seq, self.peer_ip), flush=True)
                    continue
                # [FRAMES_GO_IN_PIECES_V1] the request's RPCs, piece 0's first; a reply over one message goes as pieces
                with self._dest_lock:
                    rpcs = [(seq, dest)] + self._open.pop(seq, [])
                    for rid, _d in rpcs[1:]:
                        self._dest.pop(rid, None)
                    req_seq = self._seq_of.pop(seq, None)
                    if len(reply) + REP_HDR.size <= self.max_message:
                        out = [REP_HDR.pack(DRV_MAGIC, rseq) + reply]
                    else:
                        parts = cut(reply, self.max_message - REP_PIECE.size)
                        while len(rpcs) < len(parts) and self._spares:     # [THE_RPCS_ARE_ALREADY_OPEN_V1]
                            rid, d = self._spares.pop(0)
                            self._dest.pop(rid, None)
                            rpcs.append((rid, d))
                            self.n_on_spares += 1
                        sent = min(len(rpcs), len(parts))
                        out = [REP_PIECE.pack(PIECE_MAGIC, rseq, req_seq, i, len(parts), sent) + p for i, p in enumerate(parts)]
                        for i in range(sent, len(out)):               # kept BEFORE piece 0 goes: the proxy asks after it
                            self._kept[(req_seq, i)] = out[i]
                            self.n_kept += 1
                        self.n_cut += 1
                # (a reply that cannot be sent never reaches the proxy, so nothing after it can be handed up there: the
                # wire manager says so once, sets send_failed, and this loop stops above)
                # [THE_WIRE_MANAGER_SENDS_V1] this thread decides what goes; the server's wire manager places it
                WM.mark(tr, "reply_cut", reply_pieces=len(out), wm_whole_waiting=len(self.wire_out._whole), wm_pieces_waiting=len(self.wire_out._pieces))
                for i, (rid, d) in enumerate(rpcs):
                    msg = out[i] if i < len(out) else NOTHING
                    if tr is not None:
                        self._tr_out[rid] = (tr, i == len(rpcs) - 1)
                    self.wire_out.give((self, d, rid, msg), msg[:4] == PIECE_MAGIC)
                WM.mark(tr, "given_to_wm")
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
        self._stopping = False
        self._wire = WireManager("daemon-homa-wire", self._put_reply)     # [THE_WIRE_MANAGER_SENDS_V1]
        self.liveness = None
        self.n_peers_gone = self.n_network_events = 0
        self._opened_before = False

    def _peer_liveness(self):
        """This process's liveness: the daemon end. A test replaces this method; nothing in the tree does."""
        from core.homa_liveness import PeerLiveness
        from core.homa_transport import carriers_listened
        return PeerLiveness("daemon", self.port, self._peer_gone, "daemon semantic wire",
                            on_network_event=self._network_event, carriers=carriers_listened())

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
        # John 2026-11-17: a system restarting "is a network event that everything else on the network will see". A
        # listener that is down is waiting for exactly this, so it is told (run() opens it again if the memory is there).
        if getattr(self, "_changed", None) is not None:
            self._wake("the ControlRam was rebuilt")

    def _put_reply(self, s: "_MsgSession", dest: Tuple[str, int], rid: int, msg: bytes) -> None:
        """The wire manager's thread: one reply message onto the carrier."""
        tr, last = s._tr_out.pop(rid, (None, False))
        WM.mark(tr, "wm_took.first", first=True); WM.mark(tr, "wm_took.last")
        try:
            self._carrier.send_reply(dest, rid, msg)
            WM.mark(tr, "sent.first", first=True); WM.mark(tr, "sent.last")
            if last:
                WM.done(tr)
        except Exception as e:
            if not s.send_failed:
                print("[DAEMON-HOMA-WIRE] SEND FAILED rpc=%d %dB to %s:%d session %s: %s: %r - the session's reply order is "
                      "broken; it answers nothing more and its peer's outstanding requests will not complete"
                      % (rid, len(msg), dest[0], dest[1], s.sid.hex(), type(e).__name__, e), flush=True)
            s.send_failed = "%s: %r" % (type(e).__name__, e)
            s._reply_q.put((-2, b"", 0))                  # wake its writer so it stops

    def _server_carrier(self):
        """The daemon's Homa socket. A test replaces this method to put a stand-in with the same calls under the
        driver; nothing in the tree does."""
        from core.homa_transport import open_homa_socket
        return open_homa_socket("daemon semantic wire (server socket %s:%d)" % (self.host, self.port),
                                bind_ip=self.host, server_port=self.port)

    # ---- [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] soft Homa only. Its memory is on the machine that holds the SystemRam
    # role, and the role moves. Every node with soft Homa turned on is able to serve the memory (its own child process,
    # on soft Homa's port); the one the role is on is the one used. When the hosts file changes the driver asks where
    # the memory is now; if it moved, this listener's socket goes down, its sessions end, and it opens again there.
    def _memory_child(self) -> None:
        """Keep this node's own copy of soft Homa's memory process running, at this node's address as it is now. A
        test replaces this method."""
        lib = homa_socket.soft_homa_library()
        if lib is None:
            return
        import os, subprocess
        from core import frognet_tuples
        exe = os.path.join(os.path.dirname(os.path.abspath(lib._name)), "softhoma-ram")
        at = "%s:%d" % (frognet_tuples.my_ip(), homa_socket.SOFTHOMA_PORT)
        child = getattr(self, "_child", None)
        if child is not None and child[0].poll() is None and child[1] == at:
            return
        if child is not None and child[0].poll() is None:
            print("[DAEMON-HOMA] this node's address changed: its soft Homa memory process at %s is ended" % child[1], flush=True)
            child[0].terminate()
        elif child is not None:
            print("[DAEMON-HOMA] this node's soft Homa memory process at %s had ENDED (exit %s)" % (child[1], child[0].returncode), flush=True)
        try:
            p = subprocess.Popen([exe, at], stderr=subprocess.PIPE, text=True)
        except OSError as e:
            self._child = None
            print("[DAEMON-HOMA] soft Homa's memory process could not be started (%s at %s): %s. This node cannot hold "
                  "soft Homa's memory; it is tried again at the next network event." % (exe, at, e), flush=True)
            return
        # [THE_MEMORY_SAYS_WHEN_IT_IS_THERE_V1] found between machines (simulation/wire_between_machines_tier.py,
        # 17 Nov): this listener opened its socket before its own memory process was listening, was refused, and
        # nothing ever said the memory had come up. The process says when it holds the space (one line); that line is
        # waited for here, on its pipe. It is an event, not a timer: the read returns when the line is written, or
        # when the process ends without writing it.
        first = p.stderr.readline()
        if "held here" not in first:
            rest = first + (p.stderr.read() or "")
            self._child = None
            print("[DAEMON-HOMA] soft Homa's memory process ended without holding the space (%s at %s, exit %s): %s. It is "
                  "tried again at the next network event." % (exe, at, p.wait(), rest.strip()[-300:]), flush=True)
            return
        self._child = (p, at)
        print("[DAEMON-HOMA] [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] this node serves soft Homa's memory at %s (pid %d: %s); it "
              "is the one used while this node holds the SystemRam role" % (at, p.pid, first.strip()), flush=True)

        def say():                                        # what the memory process says after that, into this log
            for line in p.stderr:
                print(line.rstrip(), flush=True)
        threading.Thread(target=say, daemon=True, name="softhoma-ram-log").start()

    def _hosts_changed(self) -> None:
        """The liveness watcher's thread, each time the hosts file is rewritten."""
        try:
            self._memory_child()
            homa_socket.soft_memory_tell("the hosts file changed", event=True)
        except Exception as e:
            print("[DAEMON-HOMA] at the hosts change: %s: %s" % (type(e).__name__, e), flush=True)
        self._wake("this node's hosts file was rewritten")

    def _wake(self, why: str) -> None:
        """An event. A listener that is waiting to open tries again, and says which event it was."""
        self._woke_by = why
        self._changed.set()

    def run(self) -> None:
        """Listen for as long as the process runs. On kernel Homa the socket does not go away. On soft Homa it goes
        down when its memory goes or moves: the listener then opens again where the memory is, at once if it is
        there, else at the next network event (never on a timer)."""
        self._changed = threading.Event()
        # The watcher on the hosts file exists before the first try to open: found between machines (17 Nov), a
        # listener whose first open failed had no watcher, so the event it was waiting for could never reach it.
        if self.liveness is None:
            self.liveness = self._peer_liveness()
            self.liveness.on_hosts_change = self._hosts_changed
            # [A_RETURN_OF_THE_HOLD_IS_AN_EVENT_V1] found between machines, 17 Nov (the SystemRam's machine restarted):
            # a listener that is down has let go of soft Homa's memory, so nothing can reach it THROUGH that memory.
            # The events it can still see are its hosts file and its held read on the ControlRam. A machine coming
            # back writes its tuple there when its own listener opens (below), and every return of that read wakes a
            # listener that is waiting.
            self.liveness.on_read = lambda: self._wake("the held read on the ControlRam returned")
            if hasattr(self.liveness, "wake"):
                def arrived():                            # [A_SERVER_ARRIVING_IS_AN_EVENT_V1]: the hold is taken again, and a listener that is waiting tries
                    self.liveness.wake()
                    self._wake("a server arrived in soft Homa's memory")
                homa_socket.soft_on_servers_change(arrived, "daemon semantic wire")
        while not self._stopping:
            self._changed.clear()
            try:
                self._memory_child()
                self._listen()
                why = "its socket went down"
            except homa_socket.HomaUnavailable as e:
                why = "it could not open: %s" % e
                if homa_socket.soft_homa_library() is None:
                    raise
                print("[DAEMON-HOMA] the Homa listener is NOT listening (%s). It opens at the next network event." % why, flush=True)
                self._changed.wait()
                print("[DAEMON-HOMA] the listener that was not listening tries again: %s" % getattr(self, "_woke_by", "an event"), flush=True)
                continue
            if homa_socket.soft_homa_library() is None or self._stopping:
                return
            print("[DAEMON-HOMA] [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] the Homa listener ended (%s); opening it again where "
                  "soft Homa's memory is now" % why, flush=True)

    def stop(self) -> None:
        """End the listener for good: its socket closes, run() returns, and it is not opened again."""
        self._stopping = True
        c = self._carrier
        if c is not None:
            c.close()
        if getattr(self, "_changed", None) is not None:
            self._changed.set()

    def _listen(self) -> None:
        self._carrier = self._server_carrier()
        if self.liveness is None:
            self.liveness = self._peer_liveness()
            self.liveness.on_hosts_change = self._hosts_changed
        print("[DAEMON-HOMA] [HOMA_TRANSPORT_V1] semantic wire on Homa at %s:%d. [LIVENESS_IS_A_HELD_READ_V1] a peer "
              "leaving is learned from the held read on (homa, can) and the hosts block; when a machine leaves, "
              "[WRITER_GONE_V1] is told as on TCP." % (self.host, self.port), flush=True)
        if self.liveness is not None and hasattr(self.liveness, "reassert"):
            # [A_RETURN_OF_THE_HOLD_IS_AN_EVENT_V1] said each time the listener opens, the first time too: this
            # process wrote its tuple when it started, which can be before its memory was there to listen in.
            self.liveness.reassert("its Homa listener is open%s" % (" again" if self._opened_before else ""))
        self._opened_before = True
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
                except homa_socket.PeerConnectionEnded as e:     # [A_SESSION_ENDS_WITH_ITS_CONNECTION_V1]
                    with self._lock:
                        hit = [(k, x) for k, x in self._sessions.items() if x.peer == e.peer]
                        for k, x in hit:
                            self._sessions.pop(k, None)
                            self._ended[k] = str(e)
                    for _k, x in hit:
                        x.end(str(e))
                    print("[DAEMON-HOMA] %s: %d session(s) on it ended; %d sessions remain" % (e, len(hit), len(self._sessions)), flush=True)
                    continue
                if not m.is_request:
                    print("[DAEMON-HOMA] a RESPONSE (rpc %d, cookie %d) arrived on the daemon's server socket; the "
                          "daemon sends no requests: dropped" % (m.id, m.cookie), flush=True)
                    continue
                is_spare = m.payload[:4] == SPARE_MAGIC and len(m.payload) >= SPARE_HDR.size
                if not is_spare and (len(m.payload) < REQ_HDR.size or m.payload[:4] not in (DRV_MAGIC, PIECE_MAGIC, MORE_MAGIC)):
                    print("[DAEMON-HOMA] a request from %s:%d without the driver's header (%r): the far end is not "
                          "this driver; answered ERROR 400 outside any session" % (m.peer[0], m.peer[1], m.payload[:16]), flush=True)
                    self._carrier.send_reply(m.peer, m.id, wrap_error(400, "not the FNW1-on-Homa driver's message"))
                    continue
                magic = m.payload[:4]
                index = count = -1
                if magic == PIECE_MAGIC and len(m.payload) >= REQ_PIECE.size:
                    _magic, sid, seq, index, count = REQ_PIECE.unpack_from(m.payload)
                elif magic == MORE_MAGIC and len(m.payload) >= MORE_HDR.size:
                    _magic, sid, seq, index = MORE_HDR.unpack_from(m.payload)
                elif is_spare:
                    _magic, sid = SPARE_HDR.unpack_from(m.payload); seq = -1
                else:
                    _magic, sid, seq = REQ_HDR.unpack_from(m.payload)
                key = (m.peer[0], sid)
                with self._lock:
                    s = self._sessions.get(key)
                    was = self._ended.get(key) if s is None else None
                    if s is None and was is None:
                        s = _MsgSession(m.peer, sid, self.resolver, self._http_client, self._carrier, fast_exec, db_exec)
                        s.on_named = self._named
                        s.wire_out = self._wire
                        self._sessions[key] = s
                        print("[DAEMON-HOMA] session %s from %s opened (%d sessions)" % (sid.hex(), m.peer[0], len(self._sessions)), flush=True)
                        new = s
                if s is None:
                    self._carrier.send_reply(m.peer, m.id, _ended_msg(sid, was))
                    continue
                if new is s:
                    self.liveness.watch(s.node)
                    new = None
                if magic == PIECE_MAGIC:
                    s.piece(seq, index, count, m.id, m.peer, m.payload[REQ_PIECE.size:])
                elif magic == MORE_MAGIC:
                    s.more(seq, index, m.id, m.peer)
                elif magic == SPARE_MAGIC:
                    s.spare(m.id, m.peer)
                else:
                    s.arrive(seq, m.id, m.peer, m.payload[REQ_HDR.size:])
        except homa_socket.CarrierClosed as e:
            why = "socket closed (%s)" % e
        except Exception as e:
            why = "exception %s: %r" % (type(e).__name__, e)
            raise
        finally:
            print("[DAEMON-HOMA] receive loop ended: %s" % why, flush=True)
            with self._lock:
                ended = list(self._sessions.items()); self._sessions.clear()
                for k, s in ended:
                    self._ended[k] = "the daemon's Homa listener ended: %s" % why
                    s.ended = s.ended or self._ended[k]
                    s._reply_q.put((-1, b"", 0))
            try:
                self._carrier.close()
            except Exception as e:
                print("[DAEMON-HOMA] closing the listener's socket: %r" % (e,), flush=True)

    def stats(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            return {"%s/%s" % (ip, sid.hex()): {"in": s.n_in, "out": s.n_out, "arrived_early": s.n_early,
                                                "most_held_early": s.max_early} for (ip, sid), s in self._sessions.items()}
