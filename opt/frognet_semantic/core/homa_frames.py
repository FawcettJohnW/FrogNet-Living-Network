# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[WE_OWN_THE_WIRE_V1] soft Homa: Homa's own data frame, on a connection this driver holds open.

John, 2026-11-18: "We own the wire. It is whatever connection the driver chooses to make, but it is a permanently
open connection where we control what is sent and received on the socket. The data on the socket is exactly that --
data ... It was provided by the packing method of the driver and on the other end will be handed unchanged to the
unpacking method." And of the kernel module: "the contract will be the same, but those functions are now done in
hardware and use real Homa frames instead of tcp hijack frames".

So this file is the whole of soft Homa under FrogNet's wire. There is no memory process, no cell and no imitation
socket under it (what was there until 18 Nov is in ribbit/examples/homa/EXPLORATION-LOG.md, and why it went).

  the connection  one TCP connection from a node to each daemon it talks to, on soft Homa's port, opened when there
                  is first something to send and held open. When it ends, everything outstanding on it ends with it
                  (gone is gone) and it is opened again when there is next something to send. Never on a timer.
  the packing     Homa's DATA frame as his module's TCP hijacking puts it on the wire (homa_wire.h: the common
                  header laid over TCP's, flags 6 and urgent 0xb97d marking it, then the data header: the message's
                  length and this segment's offset), then the segment's bytes. A message goes as segments of at most
                  SEGMENT bytes (John, 17 Nov: "Just don't send more than 240K in a single message and do message
                  splitting and reconstruction like the Communicator does"). One field is ours: the segment's own
                  length, which a packet's size says on a network and nothing says on a stream.
  the numbering   Homa's own: the RPC id in the header (sender_id). A request has an even id; its reply has that id
                  plus one, which is how a reply finds its request.

_Conn is one end of one such connection, as the six calls of core/transport_driver.py. HomaFramesOnTcp is what the
Homa driver's two ends (proxy/transport_homa.py, daemon/engine/server_homa.py) call a carrier: the same calls the
kernel module's socket gives them (core/homa_socket.HomaSocket), so nothing above knows which it is on."""
import errno
import queue
import socket
import struct
import threading
from typing import Dict, List, Optional, Tuple

from core.homa_socket import Msg, RpcFailed, CarrierClosed, HomaUnavailable, PeerConnectionEnded
from core.transport_driver import TransportDriver, ConnectionGone

BUILD = "homa-frames-on-tcp-v3-20261118c"
PORT = 9131                          # soft Homa's port (the firewall already has it)
SEGMENT = 240000                     # no unit on the connection carries more of a message than this
DATA = 0x10                          # homa_wire.h: enum homa_packet_type
HIJACK_FLAGS, HIJACK_URGENT = 6, 0xb97d      # homa_hijack.h: what marks a TCP frame as Homa's
# homa_common_hdr: sport, dport, sequence, ack[3], type, doff, flags, window, checksum, urgent, sender_id
COMMON = struct.Struct("!HHI3sBBBHHHQ")
# homa_data_hdr after the common header: msg_length, reserved1, ack (client_id, server_port), cutoff_version, reserved2,
# retransmit, pad[3]; then homa_seg_hdr: offset; then ours: the segment's length
DATA_HDR = struct.Struct("!IIQHHHB3xII")
HEADER = COMMON.size + DATA_HDR.size


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        part = sock.recv(min(1 << 20, n - len(buf)))
        if not part:
            raise ConnectionGone("the far end closed the connection")
        buf += part
    return bytes(buf)


class _Conn(TransportDriver):
    """One end of one permanently open connection carrying Homa's frames."""

    name = "softhoma"

    def __init__(self, far: Tuple[str, int], sock: Optional[socket.socket] = None, src: str = ""):
        self.far, self._sock, self._src = far, sock, src
        self.send_lock = threading.Lock()            # a message's segments go one after another
        self._join: Dict[int, List] = {}             # rpc id -> [msg_length, bytearray] while its segments arrive
        self.ports = (0, far[1])
        if sock is not None:
            self._opened()

    def _opened(self) -> None:
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.ports = (self._sock.getsockname()[1], self._sock.getpeername()[1])

    def connect(self) -> None:
        s = socket.socket()
        try:
            if self._src:
                s.bind((self._src, 0))               # from this node's own address, as a node's traffic is
            s.settimeout(10)
            s.connect(self.far)
            s.settimeout(None)
        except OSError as e:
            s.close()
            raise ConnectionGone("no connection to %s:%d: %s" % (self.far[0], self.far[1], e)) from e
        self._sock = s
        self._opened()

    def send(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def receive(self) -> bytes:
        try:
            head = _recv_exact(self._sock, HEADER)
            return head + _recv_exact(self._sock, DATA_HDR.unpack_from(head, COMMON.size)[8])
        except OSError as e:
            raise ConnectionGone("receive from %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def pack(self, n: int, frame: bytes) -> List[bytes]:
        """One message (RPC id n) as Homa DATA frames of at most SEGMENT bytes each."""
        out, total = [], len(frame)
        for off in range(0, max(total, 1), SEGMENT):
            seg = frame[off:off + SEGMENT]
            out.append(COMMON.pack(self.ports[0], self.ports[1], off, b"\0\0\0", DATA, (HEADER // 4) << 4, HIJACK_FLAGS,
                                   0, 0, HIJACK_URGENT, n)
                       + DATA_HDR.pack(total, 0, 0, 0, 0, 0, 0, off, len(seg)) + seg)
        return out

    def unpack(self, data: bytes) -> Optional[Tuple[int, bytes]]:
        _sp, _dp, _seq, _ack, typ, _doff, flags, _win, _ck, urgent, n = COMMON.unpack_from(data)
        if typ != DATA or flags != HIJACK_FLAGS or urgent != HIJACK_URGENT:
            raise ConnectionGone("a unit from %s:%d that is not a Homa DATA frame (type %#x flags %d urgent %#x): the far "
                                 "end is not this driver" % (self.far[0], self.far[1], typ, flags, urgent))
        total, _r1, _c, _p, _cv, _r2, _rt, off, length = DATA_HDR.unpack_from(data, COMMON.size)
        if off == 0 and length == total:
            return n, data[HEADER:]
        got = self._join.setdefault(n, [total, bytearray()])
        if off != len(got[1]) or got[0] != total:
            raise ConnectionGone("message %d from %s:%d: segment at %d of %d arrived with %d held: the stream is not in "
                                 "order" % (n, self.far[0], self.far[1], off, total, len(got[1])))
        got[1] += data[HEADER:]
        if len(got[1]) < total:
            return None
        del self._join[n]
        return n, bytes(got[1])

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


class HomaFramesOnTcp:
    """The carrier the Homa driver's two ends run on, for the word softhoma: the calls of core/homa_socket.HomaSocket.
    With server_port it is the daemon's end (it is connected to, on soft Homa's port); without, the proxy's (it
    connects to each daemon it has something for)."""

    max_message = 1 << 31                             # the packing cuts a message itself; nothing above needs to

    def __init__(self, bind_ip: str = "0.0.0.0", server_port: Optional[int] = None, src: str = ""):
        self.server = server_port is not None
        self.port = PORT if self.server else 0
        self._src = src
        self._lock = threading.Lock()
        self._inbox: "queue.Queue" = queue.Queue()
        self._conns: Dict[Tuple[str, int], _Conn] = {}           # proxy: daemon's address -> its connection
        self._out: Dict[int, Tuple[int, Tuple[str, int]]] = {}   # proxy: request id -> (cookie, the daemon asked)
        self._owed: Dict[Tuple[Tuple[str, int], int], _Conn] = {}  # daemon: (asker, request id) -> its connection
        self._accepted: set = set()                              # daemon: the connections that are open to it
        self._next_id = 2
        self._closed = False
        self.n_req_out = self.n_req_in = self.n_rep_out = self.n_rep_in = self.n_failed = 0
        self.bytes_out = self.bytes_in = 0
        self._ls = None
        if self.server:
            ls = socket.socket()
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ls.bind((bind_ip, PORT))
                ls.listen(256)
            except OSError as e:
                ls.close()
                raise HomaUnavailable("soft Homa could not listen on %s:%d: %s" % (bind_ip, PORT, e)) from e
            self._ls = ls
            threading.Thread(target=self._accept, daemon=True, name="softhoma-accept").start()

    # ------------------------------------------------------------------------------------------ the daemon's end
    def _accept(self) -> None:
        while True:
            try:
                s, addr = self._ls.accept()
            except OSError:
                return
            c = _Conn((addr[0], addr[1]), sock=s)
            with self._lock:
                self._accepted.add(c)
            print("[SOFTHOMA] %s a connection from %s:%d is open on soft Homa's port" % (BUILD, addr[0], addr[1]), flush=True)
            threading.Thread(target=self._read, args=(c,), daemon=True, name="softhoma-read-%s" % addr[0]).start()

    def send_reply(self, dest: Tuple[str, int], rpc_id: int, payload: bytes) -> None:
        with self._lock:
            c = self._owed.pop((dest, rpc_id), None)
        if c is None:
            raise ConnectionGone("the reply to request %d from %s:%d has nowhere to go: its connection has ended"
                                 % (rpc_id, dest[0], dest[1]))
        with c.send_lock:
            for unit in c.pack(rpc_id + 1, payload):
                c.send(unit)
        self.n_rep_out += 1
        self.bytes_out += len(payload)

    # ------------------------------------------------------------------------------------------- the proxy's end
    def send_request(self, dest: Tuple[str, int], payload: bytes, cookie: int) -> int:
        far = (dest[0], PORT)
        with self._lock:
            c = self._conns.get(far)
            rid = self._next_id
            self._next_id += 2
        if c is None:                                 # something to send and no connection: open it (an event, not a timer)
            c = _Conn(far, src=self._src)
            c.connect()
            with self._lock:
                had = self._conns.get(far)
                if had is None:
                    self._conns[far] = c
            if had is not None:
                c.close()
                c = had
            else:
                print("[SOFTHOMA] %s the connection to %s:%d is open; it is held open" % (BUILD, far[0], far[1]), flush=True)
                threading.Thread(target=self._read, args=(c,), daemon=True, name="softhoma-read-%s" % far[0]).start()
        with self._lock:
            self._out[rid] = (cookie, far)
        try:
            with c.send_lock:
                for unit in c.pack(rid, payload):
                    c.send(unit)
        except ConnectionGone:
            with self._lock:
                self._out.pop(rid, None)
            self._ended(c, "a send on it failed")
            raise
        self.n_req_out += 1
        self.bytes_out += len(payload)
        return rid

    def abort(self, rpc_id: int) -> None:
        with self._lock:
            self._out.pop(rpc_id, None)

    def peer_gone(self, peer: Tuple[str, int]) -> int:
        """The driver's word that a machine left: this end's connection to it is closed, and what was outstanding on it
        ends with it."""
        with self._lock:
            c = self._conns.get((peer[0], PORT))
        if c is None:
            return 0
        return self._ended(c, "its machine left")

    # --------------------------------------------------------------------------------------------------- both ends
    def _read(self, c: _Conn) -> None:
        why = "unset"
        try:
            while True:
                got = c.unpack(c.receive())
                if got is None:
                    continue
                n, payload = got
                self.bytes_in += len(payload)
                if self.server:
                    with self._lock:
                        self._owed[(c.far, n)] = c
                    self.n_req_in += 1
                    self._inbox.put(Msg(n, 0, c.far, payload, True))
                else:
                    with self._lock:
                        hit = self._out.pop(n - 1, None)
                    if hit is None:
                        continue                      # aborted, or its session ended: not delivered
                    self.n_rep_in += 1
                    self._inbox.put(Msg(n - 1, hit[0], (c.far[0], hit[1][1]), payload, False))
        except ConnectionGone as e:
            why = str(e)
        except Exception as e:
            why = "%s: %r" % (type(e).__name__, e)
        self._ended(c, why)

    def _ended(self, c: _Conn, why: str) -> int:
        """A connection has ended. Gone is gone: every request outstanding on it ends here, and nothing is used in its
        place. It is opened again when there is next something to send."""
        with self._lock:
            mine = self._conns.get(c.far) is c
            if mine:
                del self._conns[c.far]
            failed = [(rid, ck) for rid, (ck, far) in self._out.items() if far == c.far] if mine else []
            for rid, _ck in failed:
                del self._out[rid]
            self._accepted.discard(c)
            owed = [k for k, v in self._owed.items() if v is c]
            for k in owed:
                del self._owed[k]
        c.close()
        if mine or owed or self.server:
            print("[SOFTHOMA] %s the connection %s %s:%d ended (%s): %d request(s) outstanding on it end with it%s"
                  % (BUILD, "from" if self.server else "to", c.far[0], c.far[1], why, len(failed),
                     "" if self.server else "; it is opened again when there is next something to send"), flush=True)
        for rid, ck in failed:
            self.n_failed += 1
            self._inbox.put(RpcFailed(rid, ck, errno.EHOSTUNREACH))
        # [A_SESSION_ENDS_WITH_ITS_CONNECTION_V1] found on topo_two_aps_ham_radio, 18 Nov: a machine left with nothing
        # outstanding to it, came back, and the proxy went on with its old session on a new connection; the daemon,
        # new, waited for that session's first frames, which had gone to its predecessor. The driver is told the
        # connection ended, and ends the sessions that were on it.
        if (mine or self.server) and not self._closed:
            self._inbox.put(PeerConnectionEnded(c.far, why))
        return len(failed)

    def recv(self) -> Msg:
        """The next whole message. Blocks: it is a held read."""
        item = self._inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def counters(self) -> dict:
        return {"req_out": self.n_req_out, "req_in": self.n_req_in, "rep_out": self.n_rep_out, "rep_in": self.n_rep_in,
                "failed": self.n_failed, "bytes_out": self.bytes_out, "bytes_in": self.bytes_in, "port": self.port}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ls is not None:
            try:
                self._ls.shutdown(socket.SHUT_RDWR)   # wakes the thread in accept(); without it the port goes on taking connections nobody reads
            except OSError:
                pass
            self._ls.close()
        with self._lock:
            conns = list(self._conns.values()) + list(self._accepted)
        for c in conns:
            c.close()
        self._inbox.put(CarrierClosed("closed"))


def startup_line(who: str, s: HomaFramesOnTcp) -> str:
    return ("[HOMA] %s build=%s on SOFT HOMA: Homa's DATA frames (header %d bytes, segments of at most %d) on TCP "
            "connections this driver holds open%s" % (who, BUILD, HEADER, SEGMENT,
                                                       ", listening on port %d" % PORT if s.server else ""))
