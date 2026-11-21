# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[WE_OWN_THE_WIRE_V1] What a transport driver is: the calls every driver implements, and nothing else.

John, 2026-11-18: "We own the wire. It is whatever connection the driver chooses to make, but it is a permanently
open connection where we control what is sent and received on the socket. The data on the socket is exactly that --
data, and the transport doesnt need to know anything about it. It was provided by the packing method of the driver
and on the other end will be handed unchanged to the unpacking method. The packing and unpacking methods are part of
the driver." And: "So that means that things like connection, send, and receive must be part of the driver".
And of kernel Homa: "the contract will be the same, but those functions are now done in hardware and use real Homa
frames instead of tcp hijack frames".

So a driver is six calls. Everything above it (the semantic engine, the proxy's fan-out and the daemon's fan-in,
HELLO, the RTT ping, liveness) calls these and never touches a socket. Everything below it (TCP, the Homa module)
carries what pack() made and does not know what it is.

    connect()      open this end's permanently open connection to the other end, by whatever means the driver
                   chooses. Called once, and again at a network event when the connection has gone. Never on a timer.
    send(data)     put one packed unit on the connection. `data` is exactly what pack() returned.
    receive()      the next packed unit from the connection, exactly as the other end's send() was given it. It
                   blocks until one is there: it is a held read, not a poll. Raises ConnectionGone when the
                   connection has ended.
    pack(n, frame) the driver's own framing of one frame of the engine's, numbered n by the layer above (so a reply
                   finds its request). Returns the packed units to send, in order: one, or several when the driver
                   cuts a frame (Homa: no unit over its message limit).
    unpack(data)   the other direction: one received unit in; (n, frame) out when a frame is whole, None while
                   the driver is still joining its pieces. The frame is byte for byte what pack() was given.
    close()        end the connection for good.

The daemon's end has one more, because it is the end that is connected to:

    listen()       wait for the other ends; returns a driver object (these same six calls) for each permanent
                   connection as it arrives. Blocks: a held read.

What is NOT a driver's: which frame answers which (the number n is the layer above's), the order frames are handed
up, HELLO and the RTT ping (frames like any other), who is alive (the held read on the capability tuple), and the
contents of a frame.

The drivers, in these terms (the code is being brought to this shape; where it is not there yet is said):

    tcp        connect: two TCP connections to the daemon (one each way), held open. pack: a 4-byte length and the
               number. Today on the proxy end this is proxy/transport_semantic._DaemonWorker and on the daemon end the
               sockets are inside the engine's own session class (daemon/engine/session.SemanticSession), which is
               the part to be split out.
    homa       kernel Homa. connect: one Homa socket. send/receive: the module's sendmsg/recvmsg, real Homa frames;
               the module does the cutting, pacing and resending. pack: the number and the frame.
    chorus   (soft Homa until 21 Nov 2026) a connection we hold open: connect: one TCP connection to the daemon.
               pack: Homa's own data frame, no unit over Homa's message limit, cut and joined here. send/receive:
               the frame's header and first bytes cross as a difference from the frame before, so what is on the
               wire is this driver's own and not Homa's (core/chorus.py). Nothing else is under it: no memory
               process and no imitation socket.
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

BUILD = "transport-driver-interface-v1-20261118a"


class ConnectionGone(ConnectionError):
    """The driver's permanent connection has ended. It is opened again at the next network event, by connect()."""


class TransportDriver(ABC):
    """One end of one permanently open connection. See this module's text for what each call must do."""

    name = ""                                  # the word the transport setting uses for this driver

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def send(self, data: bytes) -> None: ...

    @abstractmethod
    def receive(self) -> bytes: ...

    @abstractmethod
    def pack(self, n: int, frame: bytes) -> List[bytes]: ...

    @abstractmethod
    def unpack(self, data: bytes) -> Optional[Tuple[int, bytes]]: ...

    @abstractmethod
    def close(self) -> None: ...


class TransportListener(ABC):
    """The daemon's end: where the other ends connect to."""

    name = ""

    @abstractmethod
    def listen(self) -> TransportDriver: ...

    @abstractmethod
    def close(self) -> None: ...
