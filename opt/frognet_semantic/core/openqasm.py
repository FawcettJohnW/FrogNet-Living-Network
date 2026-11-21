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
"""core/openqasm.py -- [OPENQASM_DRIVER_V2] the OpenQASM protocol, under the frame.

The design's 10.3 (walkthrough, version 0.3): "OpenQASM: the same as Chorus, with the circuit codex above it."
It is a protocol under core/channel_frame.py, like tcp and chorus: the frame carries the length and the packet ID and
seals or not, and "it has no part in encryption" (version 0.2, 7.4). Version 1 of this driver had a connection of its
own, sealed every message itself and refused an unsealed one; all of that is gone. Key and protocol are independent
(John, 2026-11-21): OpenQASM runs under any key, or under none.

What this protocol does to a message: [THE_WHOLE_MESSAGE_IS_A_DIFFERENCE_V1] (John, 2026-11-21: "All of the control
should diff"). With a circuit down to 11 bytes by the codex (core/openqasm_handler.py), a write of one still put 145
bytes on the connection, nearly all of it the semantic wire's own envelope, the same from one write to the next: the
tuple's name and service, where the request came from and is for, the field order's print. Chorus makes a message's
first 24 bytes a difference against the message before it. This protocol makes the whole message one, up to SPAN
bytes: runs of bytes that are the same as at that place in the last message for the same Role in the same direction
are counted, and only the others cross.

The codex is chosen by the channel: proxy/transport_semantic.py sets codec.qasm when the Role's setting says openqasm.
"""
from core.transport_driver import ConnectionGone

BUILD = "openqasm-driver-v2-20261121b"
WORD = "openqasm"
FRAME_WORD = b"OQ3\x02"
SPAN = 4096


def _varint(z: int, out: bytearray) -> None:
    while z >= 0x80:
        out.append(0x80 | (z & 0x7f)); z >>= 7
    out.append(z)


def pack(state: dict, pid: int, message: bytes) -> bytes:
    frame, was = bytes(message), state.get("was", b"")
    m = min(len(frame), len(was), SPAN)
    out = bytearray()
    _varint(m, out)
    i = 0
    while i < m:
        j = i
        while j < m and frame[j] == was[j]:
            j += 1
        _varint(j - i, out)                                  # this many are as they were
        if j >= m:
            break
        k = j
        while k < m and not (frame[k] == was[k] and frame[k + 1:k + 3] == was[k + 1:k + 3] and k + 3 <= m):
            k += 1                                           # a run of others ends at three in a row that are the same
        _varint(k - j, out)
        out += frame[j:k]
        i = k
    state["was"] = frame[:SPAN]
    return bytes(out) + frame[m:]


def unpack(state: dict, pid: int, data: bytes) -> bytes:
    was, p = state.get("was", b""), 0

    def number():
        nonlocal p
        z, shift = 0, 0
        while True:
            if p >= len(data) or shift > 35:
                raise ConnectionGone("packet %d opened to something that is not OpenQASM's" % pid)
            c = data[p]; p += 1
            z |= (c & 0x7f) << shift
            shift += 7
            if not c & 0x80:
                return z
    m = number()
    if m > min(len(was), SPAN):
        raise ConnectionGone("packet %d is a difference against %d bytes and this end holds %d" % (pid, m, len(was)))
    out = bytearray()
    while len(out) < m:
        same = number()
        out += was[len(out):len(out) + same]
        if len(out) >= m:
            break
        d = number()
        out += data[p:p + d]; p += d
    if len(out) != m:
        raise ConnectionGone("packet %d: its difference does not come to the %d bytes it says" % (pid, m))
    whole = bytes(out) + bytes(data[p:])
    state["was"] = whole[:SPAN]
    return whole


def register() -> None:
    """The word a channel's setting may name ([A_DRIVER_REGISTERS_ITS_WORD_V1]), and the protocol under the frame."""
    from core import channel_frame, homa_transport
    homa_transport.register_driver(WORD, "homa")
    homa_transport.ON_CHORUS.add(WORD)
    channel_frame.register_driver(WORD, FRAME_WORD, pack, unpack)
