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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
openqasm.py -- [OPENQASM_DRIVER_V1] the OpenQASM driver: the third protocol of the semantic wire.

John, 2026-11-21: "OpenQASM is the protocol to implement. If the payload right now doesn't send anything, put in
something that does." And: wants the quantum language made "an embedded protocol, secured after compression and
before decompression by encoding the bits just before it goes on the wire".

A channel whose setting names the driver `openqasm` is a channel built for quantum-circuit traffic. It is two things
selected together by that one word:

    the codex    core/openqasm_handler.py, called from the semantic codec: a circuit crosses as which circuit it is and
                 the values of its parameters. It is above the driver, because the driver never sees a whole circuit.
    this driver  the six calls of core/transport_driver.py. Its connection is its own and begins with its own word,
                 OQ3 and a version byte, which is how a receiver knows it (core/chorus.WIRES: the receiver reads the
                 first four bytes of a connection and gives it to the driver that registered them). Control crosses as
                 a difference, as in chorus, whose connection class this one is built on. It seals EVERY message:
                 pack() fails with the reason when the channel's setting names no key, and unpack() ends the connection
                 when a message arrives unsealed.

It rides the process's one message carrier (core/chorus.ChorusOnTcp), which holds connections by memory and by driver
word, so a chorus connection and an openqasm connection to one machine are two connections.
"""
from typing import List, Optional, Tuple

from core import chorus
from core.transport_driver import ConnectionGone

BUILD = "openqasm-driver-v1-20261121a"
WORD = "openqasm"
WIRE = b"OQ3\x01"


class OpenQasmConn(chorus._Conn):
    """One end of one permanently open OpenQASM connection."""

    name = WORD
    WIRE = WIRE

    def pack(self, n: int, frame: bytes) -> List[bytes]:
        if self.channel:
            from core import channel_setting
            name = self._req_key.get(n - 1) if n & 1 else channel_setting.sending_key(self.far[0], self.ram)
            if not name:
                raise ConnectionGone("the OpenQASM channel%s seals every message and its setting names no key to send under: "
                                     "message %d is not sent" % (" of %s" % self.ram if self.ram else "", n))
        return super().pack(n, frame)

    def _whole(self, n: int, keyed: int, frame: bytes) -> Tuple[int, bytes]:
        if self.channel and not keyed:
            raise ConnectionGone("message %d from %s:%d arrived unsealed on an OpenQASM connection%s, which refuses it"
                                 % (n, self.far[0], self.far[1], " for %s" % self.ram if self.ram else ""))
        return super()._whole(n, keyed, frame)


    # [THE_WHOLE_MESSAGE_IS_A_DIFFERENCE_V1] John, 2026-11-21: "All of the control should diff." Measured the same day:
    # with the circuit down to 11 bytes, a write of one still put 145 bytes on the connection, and nearly all of it was
    # the semantic wire's own envelope, the same from one write to the next: the tuple's name and service, where the
    # request came from and is for, the field order's print. Chorus makes a message's first 24 bytes a difference
    # against the message before it. This driver, which seals every message and so always does this before sealing,
    # makes the whole message one, up to SPAN bytes: runs of bytes that are the same as at that place in the last
    # message sealed on this connection are counted, and only the others cross.
    SPAN = 4096

    def _small(self, frame) -> bytes:
        frame, was = bytes(frame), self._msg_out
        m = min(len(frame), len(was), self.SPAN)
        out = bytearray()
        chorus._varint(m, out)
        i = 0
        while i < m:
            j = i
            while j < m and frame[j] == was[j]:
                j += 1
            chorus._varint(j - i, out)                           # this many are as they were
            if j >= m:
                break
            k = j
            while k < m and not (frame[k] == was[k] and frame[k + 1:k + 3] == was[k + 1:k + 3] and k + 3 <= m):
                k += 1                                           # a run of others ends at three in a row that are the same
            chorus._varint(k - j, out)
            out += frame[j:k]
            i = k
        self._msg_out = frame[:self.SPAN]
        return bytes(out) + frame[m:]

    def _large(self, n: int, frame: bytes) -> bytes:
        was, p = self._msg_in, 0

        def number():
            nonlocal p
            z, shift = 0, 0
            while True:
                if p >= len(frame) or shift > 35:
                    raise ConnectionGone("message %d from %s:%d opened to something that is not a message of this driver"
                                         % (n, self.far[0], self.far[1]))
                c = frame[p]; p += 1
                z |= (c & 0x7f) << shift
                shift += 7
                if not c & 0x80:
                    return z
        m = number()
        if m > min(len(was), self.SPAN):
            raise ConnectionGone("message %d from %s:%d is a difference against %d bytes and this end holds %d"
                                 % (n, self.far[0], self.far[1], m, len(was)))
        out = bytearray()
        while len(out) < m:
            same = number()
            out += was[len(out):len(out) + same]
            if len(out) >= m:
                break
            d = number()
            out += frame[p:p + d]; p += d
        if len(out) != m:
            raise ConnectionGone("message %d from %s:%d: its difference does not come to the %d bytes it says"
                                 % (n, self.far[0], self.far[1], m))
        whole = bytes(out) + bytes(frame[p:])
        self._msg_in = whole[:self.SPAN]
        return whole


chorus.register_conn(OpenQasmConn)


def register() -> None:
    """The word a channel's setting may name ([A_DRIVER_REGISTERS_ITS_WORD_V1]): a message session, on this carrier."""
    from core import homa_transport
    homa_transport.register_driver(WORD, "homa")
    homa_transport.ON_CHORUS.add(WORD)
