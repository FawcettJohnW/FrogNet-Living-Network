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

_Conn is one end of one such connection, as the six calls of core/transport_driver.py. ChorusOnTcp is what the
Homa driver's two ends (proxy/transport_homa.py, daemon/engine/server_homa.py) call a carrier: the same calls the
kernel module's socket gives them (core/homa_socket.HomaSocket), so nothing above knows which it is on."""
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
# [A_CONNECTION_IS_FOR_A_MEMORY_V1] (21 Nov, WIRE's last byte 1 -> 2) After the word the opener says which memory the
# connection is for: one byte of length and the memory's name ("" when it is for none: the machine's own connection).
# John, 2026-11-21: "The setting in a tuple": the setting is (channel, setting, <the memory's name>), so each end
# reads it by that name and never by which memory happens to be at an address. Two memories on one machine are two
# connections, each under its own setting.
WIRE = b"CHO\x02"
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

    def __init__(self, far: Tuple[str, int], sock: Optional[socket.socket] = None, src: str = "", ram: str = ""):
        self.far, self._sock, self._src = far, sock, src
        self.ram = ram                               # [A_CONNECTION_IS_FOR_A_MEMORY_V1] the memory it is for; the accepting end reads it
        self.key = (far[0], far[1], ram, self.name)  # what the carrier holds it under
        self.send_lock = threading.Lock()            # a message's segments go one after another
        self._join: Dict[int, List] = {}             # rpc id -> [msg_length, bytearray] while its segments arrive
        self.ports = (0, far[1])
        # [CHANNEL_SETTING_IS_A_TUPLE_V1] True on the semantic wire's connections (ChorusOnTcp sets it): the
        # channel's setting tuple says which key a message is sent under. False everywhere else, where nothing changes.
        self.channel = False
        self._req_key: Dict[int, tuple] = {}         # request id -> (the key's name, the algorithm) it arrived under: its reply's
        self._said_key: Optional[str] = None
        # [THE_HEADER_IS_A_DIFFERENCE_V1] the last header each way on this connection, and what has crossed it
        self._wire_lock = threading.Lock()
        self._hdr_out, self._hdr_in = _ZERO, _ZERO
        self._pre_out, self._pre_in = bytes(PREFIX), bytes(PREFIX)
        self._msg_out, self._msg_in = bytes(PREFIX), bytes(PREFIX)      # the same, of keyed messages before sealing
        self._said_wire = sock is None               # the end that opens says WIRE first; the end that accepts reads it
        self._rf = None
        self.header_bytes = [0, 0]                   # control bytes that crossed (header and first bytes), and what they were whole
        self.sent = 0                                # every byte this end has put on the connection after its first word
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
            nb = self.ram.encode()
            if len(nb) > 255:
                raise ConnectionGone("the memory's name %r is longer than a connection can say" % self.ram)
            # [THE_HEADER_IS_A_DIFFERENCE_V1] what this end sends, said first; then the memory the connection is for
            s.sendall(self.WIRE + bytes([len(nb)]) + nb)
        except OSError as e:
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def send(self, data: bytes) -> None:
        """One frame from pack(): its header goes as a difference against the last one sent, then its bytes."""
        try:
            with self._wire_lock:                    # the difference is against the frame before it ON THE WIRE
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
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e

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
                self.ram = self._read(self._read(1)[0]).decode("utf-8", "replace").lower()
                self.key = (self.far[0], self.far[1], self.ram, self.name)
                if self.ram:
                    print("[CHORUS] %s the connection from %s:%d is for %s" % (BUILD, self.far[0], self.far[1], self.ram), flush=True)
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
        keyed = 0
        if self.channel and isinstance(frame, (bytes, bytearray, memoryview)):
            from core import channel_setting
            how = self._req_key.pop(n - 1, None) if n & 1 else None
            name = how[0] if how else ("" if n & 1 else channel_setting.sending_key(self.far[0], self.ram))
            if name:
                # [THE_HEADER_IS_A_DIFFERENCE_V1] secured AFTER it is made small: the layer above keeps its control
                # in a message's first bytes, and once the message is sealed send() can no longer see them. So under
                # a key those bytes are made a difference here, against the last keyed message packed on this
                # connection (packing and sending a message are one step, under send_lock), and the result is sealed.
                frame = self._small(frame)
                frame, keyed = channel_setting.protect(n, frame, name, self.far[0], self.ram, how[1] if how else None), 1
            if not n & 1 and name != self._said_key:
                import time as _t
                print("[CHANNEL-KEY] %.6f requests to %s:%d%s now go under %s" % (_t.time(), self.far[0], self.far[1],
                      " for %s" % self.ram if self.ram else "", "the key %r" % name if name else "no key"), flush=True)
                self._said_key = name
        out, total = [], len(frame)
        for off in range(0, max(total, 1), SEGMENT):
            seg = frame[off:off + SEGMENT]
            out.append(COMMON.pack(self.ports[0], self.ports[1], off, b"\0\0\0", DATA, (HEADER // 4) << 4, HIJACK_FLAGS,
                                   0, 0, HIJACK_URGENT, n)
                       + DATA_HDR.pack(total, keyed, 0, 0, 0, 0, 0, off, len(seg)) + seg)
        return out

    def _small(self, frame) -> bytes:
        """A message about to be sealed, made small against the last one sealed on this connection: its first PREFIX
        bytes as a difference. A driver built on this one may make more of it small (core/openqasm.py)."""
        first = bytes(frame[:PREFIX])
        out = bytes([len(first)]) + _prefix_as_difference(first, self._msg_out) + bytes(frame[PREFIX:])
        self._msg_out = first + self._msg_out[len(first):]
        return out

    def _large(self, n: int, frame: bytes) -> bytes:
        """What _small() made, put back, after the message is opened."""
        k = frame[0] if frame else PREFIX + 1          # how many first bytes went as a difference
        nb = (k + 7) // 8
        if k > PREFIX or len(frame) < 1 + nb + bin(int.from_bytes(frame[1:1 + nb], "little")).count("1"):
            raise ConnectionGone("message %d from %s:%d opened to something that is not a message of this "
                                 "driver" % (n, self.far[0], self.far[1]))
        bits = int.from_bytes(frame[1:1 + nb], "little")
        first, at = bytearray(self._msg_in[:k]), 1 + nb
        for i in range(k):
            if bits >> i & 1:
                first[i] = frame[at]; at += 1
        self._msg_in = bytes(first) + self._msg_in[k:]
        return bytes(first) + bytes(frame[at:])

    def _whole(self, n: int, keyed: int, frame: bytes) -> Tuple[int, bytes]:
        """[CHANNEL_SETTING_IS_A_TUPLE_V1] a whole message as it arrived: opened when it came under a key."""
        if not self.channel:
            return n, frame
        from core import channel_setting
        try:
            if keyed:
                frame, name, how = channel_setting.opened(n, frame, not n & 1, self.far[0], self.ram)
                if not n & 1:
                    self._req_key[n] = (name, how)
                frame = self._large(n, frame)
            elif not n & 1 and channel_setting.refuses_no_key(channel_setting.here(self.ram)) \
                    and channel_setting.refuses_no_key(channel_setting.read_here(self.ram)):
                raise channel_setting.KeyRefused("a request arrived under no key and the channel%s accepts %s"
                                                 % (" of %s" % self.ram if self.ram else "",
                                                    list(channel_setting._accepted(channel_setting.here(self.ram)))))
        except channel_setting.KeyRefused as e:
            raise ConnectionGone("message %d from %s:%d: %s" % (n, self.far[0], self.far[1], e)) from e
        return n, frame

    def unpack(self, data: bytes) -> Optional[Tuple[int, bytes]]:
        _sp, _dp, _seq, _ack, typ, _doff, flags, _win, _ck, urgent, n = COMMON.unpack_from(data)
        if typ != DATA or flags != HIJACK_FLAGS or urgent != HIJACK_URGENT:
            raise ConnectionGone("a unit from %s:%d that is not a Homa DATA frame (type %#x flags %d urgent %#x): the far "
                                 "end is not this driver" % (self.far[0], self.far[1], typ, flags, urgent))
        total, keyed, _c, _p, _cv, _r2, _rt, off, length = DATA_HDR.unpack_from(data, COMMON.size)
        if off == 0 and length == total:
            return self._whole(n, keyed, data[HEADER:])
        got = self._join.setdefault(n, [total, bytearray()])
        if off != len(got[1]) or got[0] != total:
            raise ConnectionGone("message %d from %s:%d: segment at %d of %d arrived with %d held: the stream is not in "
                                 "order" % (n, self.far[0], self.far[1], off, total, len(got[1])))
        got[1] += data[HEADER:]
        if len(got[1]) < total:
            return None
        del self._join[n]
        return self._whole(n, keyed, bytes(got[1]))

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
            # (the buffered reader is left to the thread that reads: closing it here, under a blocked read, raises
            # in the wrong thread; its read ends with the socket)


# [A_CONNECTION_IS_OF_ITS_DRIVER_V1] the drivers that ride this carrier: the word a channel's setting names -> its
# connection class, and the first four bytes of a connection -> the class that reads it. A driver built on _Conn adds
# itself with register_conn (core/openqasm.py does).
CONNS: Dict[str, type] = {"chorus": _Conn}
WIRES: Dict[bytes, type] = {WIRE: _Conn}
SERVER = None                                        # this process's daemon end, when it has one: adopt() is its call
SHARED_PORT = False                                  # True when the daemon's TCP listener holds the port and hands connections over


def register_conn(cls) -> None:
    if len(cls.WIRE) != len(WIRE):
        raise ValueError("a driver's first word is %d bytes" % len(WIRE))
    CONNS[cls.name] = cls
    WIRES[cls.WIRE] = cls


def first_word(sock: socket.socket, wait_s: float = 45.0) -> bytes:
    """The first four bytes of a connection, looked at and left where they are for the driver that will read them.

    All four, however they arrive. On a slow link they arrive in parts: the first version of this (21 Nov) set a Python
    timeout on the socket, which makes the read return whatever has arrived, and at 4,800 baud it returned two bytes
    of a word, the connection was taken for another driver's and closed, and a hold on the channel's setting went with
    it. The wait is the kernel's (SO_RCVTIMEO), so the read is a blocking one and MSG_WAITALL means all. wait_s is how
    long a connection may say nothing before it is closed: the daemon's own bound for a frame on a slow link (its ping
    phase's 45 s). Fewer than four bytes back means the far end closed first."""
    sock.settimeout(None)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", int(wait_s), int((wait_s % 1) * 1000000)))
    try:
        return sock.recv(len(WIRE), socket.MSG_PEEK | socket.MSG_WAITALL)
    finally:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", 0, 0))


class ChorusOnTcp:
    """The carrier the Homa driver's two ends run on, for the word chorus: the calls of core/homa_socket.HomaSocket.
    With server_port it is the daemon's end (it is connected to, on the daemon's port); without, the proxy's (it
    connects to each daemon it has something for)."""

    max_message = 1 << 31                             # the packing cuts a message itself; nothing above needs to

    def __init__(self, bind_ip: str = "0.0.0.0", server_port: Optional[int] = None, src: str = ""):
        self.server = server_port is not None
        self.port = int(server_port) if self.server else 0
        self._src = src
        self._lock = threading.Lock()
        self._inbox: "queue.Queue" = queue.Queue()
        self._conns: Dict[Tuple[str, int, str], _Conn] = {}      # proxy: (daemon's address, the memory) -> its connection
        self._out: Dict[int, Tuple[int, Tuple[str, int]]] = {}   # proxy: request id -> (cookie, the daemon asked)
        self._owed: Dict[Tuple[Tuple[str, int], int], _Conn] = {}  # daemon: (asker, request id) -> its connection
        self._accepted: set = set()                              # daemon: the connections that are open to it
        self._next_id = 2
        self._closed = False
        self.n_req_out = self.n_req_in = self.n_rep_out = self.n_rep_in = self.n_failed = 0
        self.bytes_out = self.bytes_in = 0
        self._ls = None
        if self.server:
            global SERVER
            SERVER = self
        if self.server and not SHARED_PORT:
            # this node's daemon was told no TCP driver: this end holds the daemon's port itself
            ls = socket.socket()
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ls.bind((bind_ip, self.port))
                ls.listen(256)
            except OSError as e:
                ls.close()
                raise HomaUnavailable("chorus could not listen on %s:%d: %s" % (bind_ip, self.port, e)) from e
            self._ls = ls
            threading.Thread(target=self._accept, daemon=True, name="chorus-accept").start()

    # ------------------------------------------------------------------------------------------ the daemon's end
    def _accept(self) -> None:
        while True:
            try:
                s, addr = self._ls.accept()
            except OSError:
                return
            threading.Thread(target=self._first, args=(s, addr), daemon=True, name="chorus-first-%s" % addr[0]).start()

    def _first(self, s: socket.socket, addr) -> None:
        """A connection on a port this end holds alone: its first bytes say which driver it is."""
        try:
            word = first_word(s)
        except OSError as e:
            word = b""
            print("[CHORUS] %s a connection from %s:%d said nothing (%s): closed" % (BUILD, addr[0], addr[1], e), flush=True)
        if word and not self.adopt(s, addr, word):
            print("[CHORUS] %s a connection from %s:%d began %r, which no driver this daemon runs begins with (it runs: %s): "
                  "closed" % (BUILD, addr[0], addr[1], word, ", ".join(sorted(CONNS))), flush=True)
            word = b""
        if not word:
            try:
                s.close()
            except OSError:
                pass

    def adopt(self, s: socket.socket, addr, word: bytes) -> bool:
        """[ONE_PORT_THE_FRAME_SAYS_THE_PROTOCOL_V1] a connection whose first bytes are a driver's word: it is that
        driver's from here. False when no driver here registered the word."""
        cls = WIRES.get(bytes(word))
        if cls is None:
            return False
        c = cls((addr[0], addr[1]), sock=s)
        c.channel = True
        with self._lock:
            self._accepted.add(c)
        print("[CHORUS] %s a connection from %s:%d began %r: it is %s's" % (BUILD, addr[0], addr[1], bytes(word), cls.name), flush=True)
        threading.Thread(target=self._read, args=(c,), daemon=True, name="%s-read-%s" % (cls.name, addr[0])).start()
        return True

    def send_reply(self, dest: Tuple[str, int], rpc_id: int, payload: bytes) -> None:
        with self._lock:
            c = self._owed.pop((dest, rpc_id), None)
        if c is None:
            raise ConnectionGone("the reply to request %d from %s:%d has nowhere to go: its connection has ended"
                                 % (rpc_id, dest[0], dest[1]))
        with c.send_lock:
            was = c.sent
            for unit in c.pack(rpc_id + 1, payload):
                c.send(unit)
            if WIRE_BYTES:
                wire_bytes(c.name, c.ram, "rep", c.sent - was)
        self.n_rep_out += 1
        self.bytes_out += len(payload)

    # ------------------------------------------------------------------------------------------- the proxy's end
    takes_ram = True                                  # send_request() can be told the memory a request is for

    def send_request(self, dest: Tuple[str, int], payload: bytes, cookie: int, ram: str = "", word: str = "chorus") -> int:
        far = (dest[0], int(dest[1]))
        cls = CONNS.get(word)
        if cls is None:
            raise ConnectionGone("the driver %r is not one this carrier holds connections for (it has: %s)"
                                 % (word, ", ".join(sorted(CONNS))))
        key = (far[0], far[1], ram, word)
        with self._lock:
            c = self._conns.get(key)
            rid = self._next_id
            self._next_id += 2
        if c is None:                                 # something to send and no connection: open it (an event, not a timer)
            c = cls(far, src=self._src, ram=ram)
            c.channel = True
            c.connect()
            with self._lock:
                had = self._conns.get(key)
                if had is None:
                    self._conns[key] = c
            if had is not None:
                c.close()
                c = had
            else:
                print("[CHORUS] %s the %s connection to %s:%d%s is open; it is held open"
                      % (BUILD, word, far[0], far[1], " for %s" % ram if ram else ""), flush=True)
                threading.Thread(target=self._read, args=(c,), daemon=True, name="chorus-read-%s" % far[0]).start()
        with self._lock:
            self._out[rid] = (cookie, key)
        try:
            with c.send_lock:
                was = c.sent
                for unit in c.pack(rid, payload):
                    c.send(unit)
                if WIRE_BYTES:
                    wire_bytes(c.name, c.ram, "req", c.sent - was)
        except ConnectionGone:
            with self._lock:
                self._out.pop(rid, None)
            self._ended(c, "a send on it failed")
            raise
        self.n_req_out += 1
        self.bytes_out += len(payload)
        return rid

    def release(self, dest: Tuple[str, int], ram: str = "", word: str = "chorus") -> None:
        """The last session on a connection has ended with nothing outstanding (its channel moved to another driver):
        the connection is closed. Nothing is failed by it."""
        with self._lock:
            c = self._conns.pop((dest[0], int(dest[1]), ram, word), None)
        if c is not None:
            print("[CHORUS] %s the %s connection to %s:%d%s has no session left on it: closed"
                  % (BUILD, word, dest[0], int(dest[1]), " for %s" % ram if ram else ""), flush=True)
            c.close()

    def abort(self, rpc_id: int) -> None:
        with self._lock:
            self._out.pop(rpc_id, None)

    def peer_gone(self, peer: Tuple[str, int]) -> int:
        """The driver's word that a machine left: this end's connection to it is closed, and what was outstanding on it
        ends with it."""
        with self._lock:
            cs = [c for k, c in self._conns.items() if k[0] == peer[0]]      # every memory's connection to that machine
        return sum(self._ended(c, "its machine left") for c in cs)

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

    def _closing_all(self) -> bool:
        return bool(getattr(self, "_closed", False))

    def _ended(self, c: _Conn, why: str) -> int:
        """A connection has ended. Gone is gone: every request outstanding on it ends here, and nothing is used in its
        place. It is opened again when there is next something to send."""
        if c.channel and not self._closing_all():
            from core import channel_setting     # [CHANNEL_SETTING_IS_A_TUPLE_V1] an event: read what the channel is now
            channel_setting.connection_ended()
        with self._lock:
            mine = self._conns.get(c.key) is c
            if mine:
                del self._conns[c.key]
            failed = [(rid, ck) for rid, (ck, key) in self._out.items() if key == c.key] if mine else []
            for rid, _ck in failed:
                del self._out[rid]
            self._accepted.discard(c)
            owed = [k for k, v in self._owed.items() if v is c]
            for k in owed:
                del self._owed[k]
        c.close()
        if mine or owed or self.server:
            print("[CHORUS] %s the connection %s %s:%d ended (%s): %d request(s) outstanding on it end with it%s"
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
            e = PeerConnectionEnded(c.far, why)
            e.ram, e.word = c.ram, c.name             # the sessions for that memory on that driver are the ones that were on it
            self._inbox.put(e)
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


def startup_line(who: str, s: ChorusOnTcp) -> str:
    return ("[HOMA] %s build=%s on SOFT HOMA: Homa's DATA frames (header %d bytes, segments of at most %d) on TCP "
            "connections this driver holds open%s" % (who, BUILD, HEADER, SEGMENT,
                                                       (", arriving on the daemon's port %d%s" % (s.port, " (held by its TCP "
                                                        "listener, which reads each connection's first word)" if SHARED_PORT else ""))
                                                       if s.server else ""))
