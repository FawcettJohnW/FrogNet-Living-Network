#!/opt/frognet_semantic/venv/bin/python3
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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
daemon/engine/packet.py

Semantic packet helpers:
  - decode_header(packet) -> (version, flags, opcode, nfields)
  - encode_error_reply(status, msg) -> bytes
  - extract_origin_dest_and_normalize_packet(packet, peer_ip) -> (origin_ip, dest_host, normalized_packet)

Trailer format MUST match proxy/origin.py:
  [origin_bytes][dest_host_bytes][orig_len][host_len][ver][MAGIC]
  MAGIC = 0xFA 0xCE at end.
"""

from __future__ import annotations

import struct
from typing import Tuple

from core.codec import SemanticCodec

SEM_HDR_V1_LEN = 8

_MAGIC = b"\xFA\xCE"
_TRAILER_VER = 1


def decode_header(packet: bytes) -> Tuple[int, int, int, int]:
    if not packet or len(packet) < SEM_HDR_V1_LEN:
        raise ValueError("packet too short for header")
    return struct.unpack("<BBIH", packet[:SEM_HDR_V1_LEN])


def encode_error_reply(status: int, msg: str) -> bytes:
    return SemanticCodec().encode_error_reply(int(status), str(msg))


def extract_origin_dest_and_normalize_packet(*, packet: bytes, peer_ip: str) -> Tuple[str, str, bytes]:
    """
    Returns:
      origin_ip: derived from trailer, else peer_ip
      dest_host: derived from trailer, else "" (explicit host missing)
      normalized: original packet with trailer stripped (codec-safe)
    """
    if not packet:
        return (peer_ip, "", packet)

    # Default fallback (fail-closed behavior is handled higher up)
    origin_ip = peer_ip
    dest_host = ""
    normalized = packet

    # Need at least header + trailer footer
    if len(packet) < SEM_HDR_V1_LEN + 2 + 3:
        return (origin_ip, dest_host, normalized)

    if packet[-2:] != _MAGIC:
        return (origin_ip, dest_host, normalized)

    ver = packet[-3]
    host_len = packet[-4]
    orig_len = packet[-5]

    if ver != _TRAILER_VER:
        # Unknown trailer version: treat as absent
        return (origin_ip, dest_host, normalized)

    trailer_total = 2 + 3 + orig_len + host_len
    if trailer_total > len(packet):
        return (origin_ip, dest_host, normalized)

    start = len(packet) - trailer_total
    ob = packet[start : start + orig_len]
    hb = packet[start + orig_len : start + orig_len + host_len]
    normalized = packet[:start]

    try:
        if ob:
            origin_ip = ob.decode("utf-8", "replace").strip() or origin_ip
    except Exception:
        pass

    try:
        if hb:
            dest_host = hb.decode("utf-8", "replace").strip()
    except Exception:
        dest_host = ""

    return (origin_ip, dest_host, normalized)
