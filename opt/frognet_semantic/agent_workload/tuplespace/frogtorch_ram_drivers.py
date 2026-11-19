# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""frogtorch_ram_drivers.py -- [THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1] the transport drivers a session with the
FrogTorch RAM runs on: the six calls of core/transport_driver.py, and listen() on the RAM's end.

John, 17 November 2026: "The connection to the RAM host is expected and should be transport transparent." Until this
file the rank's session (frogtorch_ram_client._Session) and the RAM (frogtorch_ram.RamServer) opened TCP sockets
themselves, so the transport setting never reached them. Now neither touches a socket: each asks here for the driver
its word names and uses connect / send / receive / pack / unpack / close.

The word is the one core/homa_transport.py reads (/etc/frognet/semantic_transport): the word of the named RAM that is
on the RAM's machine now, else the node's word (homa_transport.word_for).

    tcp        one TCP connection from the rank to the RAM's port, held open. Packing: an 8-byte length and the
               frame (tensor_plane's `<!Q length><payload>`). These are the bytes this session has always put on
               the wire, so numbers measured before this file stand as the control. `auto` is this driver.
    softhoma   one TCP connection to the RAM's port PLUS ONE, held open. Packing: Homa's DATA frames, segments of at
               most 240,000 bytes (core/homa_frames._Conn, the same class the proxy-to-daemon wire uses).
    homa       kernel Homa. Not carried yet (the primer's step 4): a session told this word says so and stops.

[ONE_RULE_FOR_THE_PORT_V1] A FrogTorch RAM has one port, P: the one it is started with, announces, and is configured
by. Its tcp driver listens on P and its softhoma driver on P + 1. Nothing else is announced or negotiated.

A frame handed to pack() may be one bytes-like object or a tuple of them that are the frame in order, so that a
tensor is not copied to have a header put in front of it. unpack() gives the frame back as one bytes-like object.

No timers: receive() blocks until a unit is there or the connection has ended (ConnectionGone). No fallbacks: a
driver that cannot open its connection raises, and nothing is used in its place.
"""
from __future__ import annotations

import socket
import struct
from typing import Dict, List, Optional, Tuple

from core import homa_frames
from core.transport_driver import ConnectionGone, TransportDriver, TransportListener

BUILD = "frogtorch-ram-drivers-v1-20261119a"
LEN = struct.Struct("!Q")                      # tensor_plane.BULK_LEN_FMT: what this session has always used
MAX_FRAME = 1 << 30                            # tensor_plane.MAX_FRAME
SMALL = 4096                                   # tensor_plane._SMALL_TAIL: at or under this a tail is joined to its head
SOFTHOMA_PORT_OFFSET = 1                       # [ONE_RULE_FOR_THE_PORT_V1]


def _parts(frame) -> tuple:
    return tuple(frame) if isinstance(frame, (tuple, list)) else (frame,)


class TcpEnd(TransportDriver):
    """One end of the tcp driver's permanently open connection to a FrogTorch RAM."""

    name = "tcp"

    def __init__(self, far: Tuple[str, int], sock: Optional[socket.socket] = None):
        self.far, self._sock, self._n = far, sock, 0
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def connect(self) -> None:
        s = socket.socket()
        try:
            s.settimeout(10)                   # the connect alone, as core/homa_frames._Conn.connect; nothing after it
            s.connect(self.far)
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as e:
            s.close()
            raise ConnectionGone("no connection to %s:%d: %s" % (self.far[0], self.far[1], e)) from e
        self._sock = s

    def pack(self, n: int, frame) -> List:
        """`<!Q length>` and the frame. One unit: the head (the length and every part but the last) and the last part,
        which is sent as it is and never copied ([FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1], tensor_plane._send_frame)."""
        parts = _parts(frame)
        head = LEN.pack(sum(len(p) for p in parts)) + b"".join(bytes(p) for p in parts[:-1])
        tail = parts[-1] if parts else b""
        return [head + bytes(tail)] if len(tail) <= SMALL else [(head, tail)]

    def send(self, data) -> None:
        try:
            if isinstance(data, tuple):
                for p in data:
                    self._sock.sendall(p)
            else:
                self._sock.sendall(data)
        except (OSError, AttributeError) as e:              # AttributeError: closed here, _sock is None
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def _exact(self, n: int, into: bytearray, at: int) -> None:
        view = memoryview(into)
        while n:
            got = self._sock.recv_into(view[at:at + n], n)
            if not got:
                raise ConnectionGone("%s:%d closed the connection" % self.far)
            at += got
            n -= got

    def receive(self) -> bytearray:
        try:
            head = bytearray(LEN.size)
            self._exact(LEN.size, head, 0)
            (n,) = LEN.unpack(head)
            if n > MAX_FRAME:
                raise ConnectionGone("a length of %d from %s:%d is not a frame: the far end is not this driver"
                                     % (n, self.far[0], self.far[1]))
            unit = bytearray(LEN.size + n)
            unit[:LEN.size] = head
            self._exact(n, unit, LEN.size)
            return unit
        except (OSError, AttributeError) as e:
            raise ConnectionGone("receive from %s:%d: %s" % (self.far[0], self.far[1], e)) from e

    def unpack(self, data) -> Optional[Tuple[int, memoryview]]:
        self._n += 1                           # TCP numbers a unit by its place in arrival order
        return self._n, memoryview(data)[LEN.size:]

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.shutdown(socket.SHUT_RDWR)   # wakes a receive() held in another thread; close alone does not
            except OSError:
                pass
            s.close()


class SoftHomaEnd(homa_frames._Conn):
    """One end of the softhoma driver's connection to a FrogTorch RAM: core/homa_frames._Conn as it is. Its packing
    cuts a frame into segments, so a frame given as parts is joined first."""

    def pack(self, n: int, frame) -> List[bytes]:
        if isinstance(frame, (tuple, list)):
            frame = b"".join(bytes(p) for p in frame)
        return super().pack(n, bytes(frame) if not isinstance(frame, bytes) else frame)


class _Listener(TransportListener):
    """The RAM's end: where the ranks connect to. listen() blocks until the next permanent connection arrives."""

    end = None                                 # the driver class of an arrived connection

    def __init__(self, bind: str, port: int):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((bind, port))
            s.listen(1024)
        except OSError:
            s.close()
            raise
        self._ls, self.port = s, s.getsockname()[1]

    def listen(self) -> TransportDriver:
        try:
            conn, peer = self._ls.accept()
        except OSError as e:
            raise ConnectionGone("the %s listener on port %d has closed: %s" % (self.name, self.port, e)) from e
        return self.end((peer[0], peer[1]), sock=conn)

    def close(self) -> None:
        try:
            self._ls.shutdown(socket.SHUT_RDWR)             # wakes the thread in accept()
        except OSError:
            pass
        self._ls.close()


class TcpListener(_Listener):
    name, end = "tcp", TcpEnd


class SoftHomaListener(_Listener):
    name, end = "softhoma", SoftHomaEnd


def _driver_of(word: str, what: str) -> str:
    """The driver a word of the setting means for a session with a FrogTorch RAM."""
    if word in ("auto", "tcp"):
        return "tcp"
    if word == "softhoma":
        return "softhoma"
    raise ConnectionError("frogtorch: %s is set to %r, and a session with a FrogTorch RAM is carried on tcp or softhoma. "
                          "Kernel Homa under this session is not written yet (agent_workload/"
                          "PYTORCH-ON-THE-DRIVERS-PRIMER.md, step 4)." % (what, word))


def port_of(driver: str, ram_port: int) -> int:
    """[ONE_RULE_FOR_THE_PORT_V1]"""
    return ram_port + (SOFTHOMA_PORT_OFFSET if driver == "softhoma" else 0)


def word_for(host: str) -> str:
    """The setting's word for this node's connection to the RAM on the machine at `host` (an address), as the
    proxy-to-daemon wire reads it: the named RAM that is there now, else the node's word."""
    from core import homa_transport
    return homa_transport.word_for(host)


def end_for(host: str, ram_port: int) -> TransportDriver:
    """A rank's end of a session with the FrogTorch RAM at host:ram_port, not yet connected."""
    word = word_for(host)
    driver = _driver_of(word, "this node's connection to the RAM at %s" % host)
    far = (host, port_of(driver, ram_port))
    return TcpEnd(far) if driver == "tcp" else SoftHomaEnd(far)


def listeners_for(bind: str, ram_port: int) -> Dict[str, TransportListener]:
    """The RAM's listeners: one for every driver any word of this node's setting names (as a daemon listens on every
    carrier its setting names, homa_transport.carriers_listened), each on its port by the one rule. With ram_port 0
    (a test asking for any port) a P is taken at which every listener opens."""
    from core import homa_transport
    words = [homa_transport.semantic_transport()] + sorted(set(homa_transport._ram_words.values()))
    drivers = []
    for w in words:
        if w in ("homa", "homasim"):
            print("[frogtorch-ram] %s a word of this node's setting is %s: a FrogTorch RAM does not listen on kernel "
                  "Homa yet (the primer's step 4), so no session on that word reaches this RAM" % (BUILD, w), flush=True)
            continue
        d = _driver_of(w, "a word of this node's setting")
        if d not in drivers:
            drivers.append(d)
    kinds = {"tcp": TcpListener, "softhoma": SoftHomaListener}
    while True:
        out: Dict[str, TransportListener] = {}
        try:
            # tcp first, so that with ram_port 0 it is the one whose port becomes P
            for d in sorted(drivers, key=("tcp", "softhoma").index):
                p = ram_port
                if p == 0 and out:
                    p = next(iter(out.values())).port
                if p == 0 and d == "softhoma":             # softhoma alone on any port: P is the one below it
                    out[d] = kinds[d](bind, 0)
                    continue
                out[d] = kinds[d](bind, port_of(d, p))
            return out
        except OSError as e:
            for l in out.values():
                l.close()
            if ram_port != 0:
                raise OSError("frogtorch-ram: cannot listen for %s on %s, RAM port %d (tcp on it, softhoma on it "
                              "plus %d): %s" % (", ".join(drivers), bind, ram_port, SOFTHOMA_PORT_OFFSET, e)) from e
            # any port was asked for and the one above the port the system gave is taken: ask for another


def unreachable(end: TransportDriver, host: str, ram_port: int, e: BaseException) -> str:
    """What to say when a session's connection to a RAM could not be opened."""
    return ("cannot reach the RAM host at %s:%d on the %s driver (%s). A FrogTorch RAM listens with the drivers its own "
            "node's transport setting names when frogtorch-ram starts (tcp on its port, softhoma on its port plus one)"
            % (host, ram_port, end.name, e))


def ram_port_of(listeners: Dict[str, TransportListener]) -> int:
    """P, from the listeners that are open."""
    if "tcp" in listeners:
        return listeners["tcp"].port
    return listeners["softhoma"].port - SOFTHOMA_PORT_OFFSET
