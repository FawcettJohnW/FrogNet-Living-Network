# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[WE_OWN_THE_WIRE_V1] [CHORUS_V1] chorus: a driver's own wire, on a connection it holds open.

THE NAME, AND WHERE THIS CAME FROM. John, 2026-11-21: "We are no longer faithful to Homa in softhoma, and the
hardware run for softhoma is moot. Preserve all notes about the origin of softhoma, but call the protocol 'chorus'."

This driver was "soft Homa" (the word `softhoma`, this file as core/homa_frames.py) from 17 to 21 November 2026. It
began as Homa's own DATA frame, exactly as his kernel module's TCP hijacking puts it on a wire, carried on a TCP
connection the driver holds open, so that the Homa driver's two ends could run on a node with no Homa in its kernel
and be compared with the module on hardware. On 21 November the header stopped crossing the connection: at 4,800 baud
its 62 bytes were the whole of what made this driver slower than tcp, and it now crosses as a difference from the
header before it, with the first bytes of each message and the key's nonce (below, [THE_HEADER_IS_A_DIFFERENCE_V1]).
What is on the wire is no longer a Homa frame, a Homa module could not read it, and a run against the module would
compare two different protocols. So it has its own name.

What is still Homa's, and why the notes below still say so: pack() builds, and unpack() is handed, a whole Homa DATA
frame (the layout of homa_wire.h), and the numbering is Homa's RPC ids. That is the form a message has inside this
driver, between its packing and the wire. Everything written here before 21 November that says "Homa's frame on the
wire" was true when written and describes that form. The history is in ribbit/examples/homa/EXPLORATION-LOG.md, which
keeps the old name in every entry made under it. `softhoma.cpp` and `libsofthoma.so` in ribbit/examples/homa are a
different thing and keep their name: Homa's socket API answered on CroakOS memory, which is still Homa's API.
The kernel module's driver is still `homa` and is not this.

--- as written 18 November, when this was soft Homa ---

Homa's own data frame, on a connection this driver holds open.

John, 2026-11-18: "We own the wire. It is whatever connection the driver chooses to make, but it is a permanently
open connection where we control what is sent and received on the socket. The data on the socket is exactly that --
data ... It was provided by the packing method of the driver and on the other end will be handed unchanged to the
unpacking method." And of the kernel module: "the contract will be the same, but those functions are now done in
hardware and use real Homa frames instead of tcp hijack frames".

So this file is the whole of chorus under FrogNet's wire. There is no memory process, no cell and no imitation
socket under it (what was there until 18 Nov is in ribbit/examples/homa/EXPLORATION-LOG.md, and why it went).

  the connection  one TCP connection from a node to each daemon it talks to, on chorus's port, opened when there
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

_Conn is one end of one such connection, as the six calls of core/transport_driver.py. FrogTorch's RAM drivers are
built on it (agent_workload/tuplespace/frogtorch_ram_drivers.py). Its wire has one owner: send() hands a frame to the
connection's writer, and there is no lock.

Until 21 Nov this file also held ChorusOnTcp, the semantic wire's carrier: a connection for each driver (and then for
each memory), and the driver's own sealing. The 0.2 design removed all three. The semantic wire's carrier is
core/channel_carrier.py, one connection to each destination, and chorus is a protocol under its frame
(core/channel_frame.py; _frame_pack and _frame_unpack at the end of this file): the frame carries the length and the
packet ID and seals or does not, and what is left for chorus is a message's first bytes as a difference."""
import errno
import os
import queue
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

from core.homa_socket import Msg, RpcFailed, CarrierClosed, HomaUnavailable, PeerConnectionEnded
from core.transport_driver import TransportDriver, ConnectionGone

BUILD = "chorus-v3-20261121c"
# [ONE_PORT_THE_FRAME_SAYS_THE_PROTOCOL_V1] John, 2026-11-21: "The receivers should be able to examine the received
# TCP frame and determine the underlying protocol by itself." Until then chorus had a port of its own, 9131. It now
# arrives on the daemon's port with every other driver: the receiver reads the first four bytes of a connection, finds
# the driver that registered them (WIRES), and gives the connection to it. It does not consult the port, the sender's
# address or the channel's setting to know what it is looking at. The TCP driver's first bytes are a 4-byte length and
# then FNW1, which no driver's word begins like. (9131 was also a port a ham link's filter did not pass,
# usr/local/bin/ham_guardrail.sh; the daemon's port is one it does.)
SEGMENT = 240000                     # no unit on the connection carries more of a message than this
DATA = 0x10                          # homa_wire.h: enum homa_packet_type
HIJACK_FLAGS, HIJACK_URGENT = 6, 0xb97d      # homa_hijack.h: what marks a TCP frame as Homa's
# homa_common_hdr: sport, dport, sequence, ack[3], type, doff, flags, window, checksum, urgent, sender_id
COMMON = struct.Struct("!HHI3sBBBHHHQ")
# homa_data_hdr after the common header: msg_length, reserved1, ack (client_id, server_port), cutoff_version, reserved2,
# retransmit, pad[3]; then homa_seg_hdr: offset; then ours: the segment's length
DATA_HDR = struct.Struct("!IIQHHHB3xII")
HEADER = COMMON.size + DATA_HDR.size

# [THE_HEADER_IS_A_DIFFERENCE_V1] John, 2026-11-21: "The Homa header should diff out." And: "The payload on the tcp
# frame is a Homa frame. All of the control should diff."
# Measured 21 Nov at 4,800 baud: a request and its reply took 880 ms on tcp and 1,121 ms on chorus, and the whole of
# the difference was control bytes: Homa's 62-byte DATA header on each frame (124 a request and reply) and, inside the
# message, the message session's own header (proxy/transport_homa.py: magic, session, seq: 20 on a request, 12 on a
# reply), where the TCP session spends 16 in all. From one frame to the next on a connection almost none of those bytes
# change. So what crosses the connection for a frame is:
#
#     the header's CHANGES   a varint saying which of its 20 fields are not what was expected, then each one's change
#                            as a varint. Expected: the field as it was in the last header, except the RPC id (the last
#                            one plus 2) and the segment's length (this message's length).
#     the first 24 bytes     of the segment as a difference against the first 24 of the segment before: 3 bytes saying
#                            which differ, then those. This is where the layer above keeps ITS control (the session's
#                            magic, id and seq); the driver does not know what they are, only that they repeat.
#     the rest of the segment
#
# In steady state that is 2 or 3 bytes of header and 4 or 5 of the session's, where 62 and 20 crossed.
# pack() still makes a whole Homa DATA frame and unpack() is still handed one: the difference is made in send() and
# undone in receive(), on the connection, which is ordered and is this driver's own. Nothing above the driver knows,
# and kernel Homa's frames are untouched. The end that opens a connection says so first (WIRE): an end of an older
# build is told apart from this one and the connection ends with that said.
WIRE = b"CHO\x01"
_FIELDS = 20
_ACK = 3                                  # the one field that is bytes, not a number: homa_common_hdr.ack[3]
_ID, _TOTAL, _LENGTH = 10, 11, 19         # the RPC id, the message's length, this segment's length
# the order the fields are named in: the ones that change first, so the usual "which" is one byte
_ORDER = (_TOTAL, _ID, _LENGTH, 2, 12, 18) + tuple(i for i in range(_FIELDS) if i not in (_TOTAL, _ID, _LENGTH, 2, 12, 18))
_ZERO = (0, 0, 0, b"\0\0\0", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
PREFIX = 24


# [BYTES_ON_THE_CONNECTION_V1] With FROGNET_WIRE_BYTES=1 every driver says, for each message it puts on a connection,
# how many bytes went on it: "[WIRE-BYTES] <time> <driver> <memory or -> <req|rep> <bytes>". A measurement's switch (the
# Roles exercise tier sets it); off, nothing is written.
WIRE_BYTES = os.environ.get("FROGNET_WIRE_BYTES") == "1"


def wire_bytes(driver: str, ram: str, what: str, n: int) -> None:
    print("[WIRE-BYTES] %.6f %s %s %s %d" % (time.time(), driver, ram or "-", what, n), flush=True)


def _fields(frame) -> tuple:
    return COMMON.unpack_from(frame) + DATA_HDR.unpack_from(frame, COMMON.size)


def _varint(z: int, out: bytearray) -> None:
    while z >= 0x80:
        out.append(0x80 | (z & 0x7f)); z >>= 7
    out.append(z)


def _expected(i: int, was: tuple, now) -> object:
    if i == _ID:
        return was[_ID] + 2
    if i == _LENGTH:
        return now[_TOTAL]                # _TOTAL comes before _LENGTH in _ORDER, so the far end has it by then
    return was[i]


def _header_as_difference(now: tuple, was: tuple) -> bytes:
    mask, out = 0, bytearray()
    for bit, i in enumerate(_ORDER):
        want = _expected(i, was, now)
        if now[i] == want:
            continue
        mask |= 1 << bit
        if i == _ACK:
            out += now[i]
            continue
        d = now[i] - want
        _varint((d << 1) if d >= 0 else ((-d << 1) - 1), out)     # zigzag: a small change either way is one byte
    head = bytearray()
    _varint(mask, head)
    return bytes(head + out)


def _prefix_as_difference(now: bytes, was: bytes) -> bytes:
    """The first bytes of a segment against the first bytes of the one before: which differ, then those."""
    n = len(now)
    bits, out = 0, bytearray()
    for k in range(n):
        if now[k] != was[k]:
            bits |= 1 << k
            out.append(now[k])
    return bits.to_bytes((n + 7) // 8, "little") + bytes(out)


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

    name = "chorus"
    WIRE = WIRE                                      # what the end that opens says first; a driver built on this one has its own

    def __init__(self, far: Tuple[str, int], sock: Optional[socket.socket] = None, src: str = ""):
        self.far, self._sock, self._src = far, sock, src
        self._join: Dict[int, List] = {}             # rpc id -> [msg_length, bytearray] while its segments arrive
        self.ports = (0, far[1])
        # [THE_HEADER_IS_A_DIFFERENCE_V1] the last header each way on this connection, and what has crossed it
        self._hdr_out, self._hdr_in = _ZERO, _ZERO
        self._pre_out, self._pre_in = bytes(PREFIX), bytes(PREFIX)
        self._said_wire = sock is None               # the end that opens says WIRE first; the end that accepts reads it
        self._rf = None
        self.header_bytes = [0, 0]                   # control bytes that crossed (header and first bytes), and what they were whole
        self.sent = 0                                # every byte this end has put on the connection after its first word
        # [A_CONNECTIONS_WIRE_HAS_ONE_OWNER_V1] John, 2026-11-21: "A connection's wire has one owner... No request
        # thread touches the socket." send() hands a frame to this connection's writer, which alone makes the
        # header's difference and writes: the difference is against the frame before it ON THE WIRE, and only the
        # one thread that writes knows what that was. There is no lock.
        self._todo: "queue.SimpleQueue" = queue.SimpleQueue()
        self._writer: Optional[threading.Thread] = None
        self._broken: Optional[str] = None
        if sock is not None:
            self._opened()

    def _opened(self) -> None:
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.ports = (self._sock.getsockname()[1], self._sock.getpeername()[1])
        self._rf = self._sock.makefile("rb", buffering=1 << 16)

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
        try:
            s.sendall(self.WIRE)                     # [THE_HEADER_IS_A_DIFFERENCE_V1] what this end sends, said first
        except OSError as e:
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def send(self, data: bytes) -> None:
        """One frame from pack(), handed to this connection's writer. Frames go on the wire in the order they are
        handed over. A writer that could not write says so here, at the next frame."""
        if self._broken is not None:
            raise ConnectionGone(self._broken)
        if self._writer is None:
            self._writer = threading.Thread(target=self._write, daemon=True, name="chorus-write-%s" % (self.far[0],))
            self._writer.start()
        self._todo.put(data)

    def _write(self) -> None:
        """The one owner of this connection's wire: each frame's header as a difference against the last one sent,
        then its bytes."""
        while True:
            data = self._todo.get()
            if data is None:
                return
            try:
                now = _fields(data)
                head = _header_as_difference(now, self._hdr_out)
                self._hdr_out = now
                n = min(PREFIX, len(data) - HEADER)
                first = bytes(data[HEADER:HEADER + n])
                head += _prefix_as_difference(first, self._pre_out)
                self._pre_out = first + self._pre_out[n:]
                self.header_bytes[0] += len(head); self.header_bytes[1] += HEADER + n
                self.sent += len(head) + len(data) - HEADER - n
                if len(data) - HEADER <= 1 << 16:
                    self._sock.sendall(head + bytes(data[HEADER + n:]))
                else:
                    self._sock.sendall(head)
                    self._sock.sendall(memoryview(data)[HEADER + n:])
            except (OSError, AttributeError) as e:
                self._broken = "send to %s:%d: %s" % (self.far[0], self.far[1], e)
                return

    def _read(self, n: int) -> bytes:
        b = self._rf.read(n)
        if b is None or len(b) < n:
            raise ConnectionGone("the far end closed the connection")
        return b

    def _varint_in(self) -> int:
        z, shift = 0, 0
        while True:
            c = self._read(1)[0]
            z |= (c & 0x7f) << shift
            shift += 7
            if not c & 0x80:
                return z
            if shift > 70:
                raise ConnectionGone("a number from %s:%d does not end: the far end is not this driver" % self.far)

    def receive(self) -> bytes:
        """One whole Homa DATA frame, its header put back from the difference that crossed."""
        try:
            if not self._said_wire:
                said = self._read(len(self.WIRE))
                if said != self.WIRE:
                    raise ConnectionGone("the far end at %s:%d did not begin with this driver's word (it began %r, %s begins "
                                         "%r): it is an older build, or not this driver"
                                         % (self.far[0], self.far[1], said, self.name, self.WIRE))
                self._said_wire = True
            mask = self._varint_in()
            if mask >> _FIELDS:
                raise ConnectionGone("a header from %s:%d names fields a Homa DATA header does not have: the far end is "
                                     "not this driver" % (self.far[0], self.far[1]))
            was, now = self._hdr_in, list(self._hdr_in)
            for bit, i in enumerate(_ORDER):
                want = _expected(i, was, now)
                if not mask >> bit & 1:
                    now[i] = want
                elif i == _ACK:
                    now[i] = self._read(3)
                else:
                    z = self._varint_in()
                    now[i] = want + ((z >> 1) if not z & 1 else -((z + 1) >> 1))
            self._hdr_in = tuple(now)
            length = now[_LENGTH]
            if not 0 <= length <= SEGMENT:
                raise ConnectionGone("a header from %s:%d gives its segment %d bytes: the far end is not this driver"
                                     % (self.far[0], self.far[1], length))
            n = min(PREFIX, length)
            bits = int.from_bytes(self._read((n + 7) // 8), "little") if n else 0
            first = bytearray(self._pre_in[:n])
            for k in range(n):
                if bits >> k & 1:
                    first[k] = self._read(1)[0]
            self._pre_in = bytes(first) + self._pre_in[n:]
            return COMMON.pack(*now[:11]) + DATA_HDR.pack(*now[11:]) + bytes(first) + (self._read(length - n) if length > n else b"")
        except (OSError, AttributeError, ValueError, struct.error) as e:
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
        total, _keyed, _c, _p, _cv, _r2, _rt, off, length = DATA_HDR.unpack_from(data, COMMON.size)
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
        """What was handed to the writer before this is written first, unless the far end is not taking it: then the
        socket's close is what ends the writer."""
        w = self._writer
        if w is not None and w is not threading.current_thread() and self._broken is None:
            self._todo.put(None)
            w.join(5.0)
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
            # (the buffered reader is left to the thread that reads: closing it here, under a blocked read, raises
            # in the wrong thread; its read ends with the socket)


# [ONE_FRAME_UNDER_EVERY_PROTOCOL_V1] Chorus as a protocol under the frame (core/channel_frame.py; the design's 10.3,
# walkthrough version 0.3). The frame carries a message's length and its packet ID, as small numbers, and seals it or
# not: what was Homa's DATA header on this driver's own connections is the frame's now, and no driver seals. What is
# left for chorus is what it did to the message itself: its first PREFIX bytes, where the layer above keeps ITS
# control (the session's magic, id and seq), cross as a difference against the message before it for the same Role in
# the same direction. `state` is the frame's, one for a Role in a direction, empty after a switch of protocol.
FRAME_WORD = b"CHO\x03"


def _frame_pack(state: dict, pid: int, message: bytes) -> bytes:
    first, was = bytes(message[:PREFIX]), state.get("first", bytes(PREFIX))
    state["first"] = first + was[len(first):]
    return bytes([len(first)]) + _prefix_as_difference(first, was) + bytes(message[PREFIX:])


def _frame_unpack(state: dict, pid: int, data: bytes) -> bytes:
    was = state.get("first", bytes(PREFIX))
    k = data[0] if data else PREFIX + 1
    nb = (k + 7) // 8
    if k > PREFIX or len(data) < 1 + nb + bin(int.from_bytes(data[1:1 + nb], "little")).count("1"):
        raise ConnectionGone("packet %d opened to something that is not chorus's" % pid)
    bits, at, first = int.from_bytes(data[1:1 + nb], "little"), 1 + nb, bytearray(was[:k])
    for i in range(k):
        if bits >> i & 1:
            first[i] = data[at]; at += 1
    state["first"] = bytes(first) + was[k:]
    return bytes(first) + bytes(data[at:])


def _register_under_the_frame() -> None:
    from core import channel_frame
    channel_frame.register_driver("chorus", FRAME_WORD, _frame_pack, _frame_unpack)


_register_under_the_frame()
