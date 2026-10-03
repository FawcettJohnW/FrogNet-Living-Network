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
proxy/origin.py

Exports required by proxy code:
  - is_control_plane_path(path): bool
  - inject_origin_into_semantic_request(packet, origin_ip, dest_host): bytes

Packet extension format (TRAILER at end of packet):
  [origin_bytes][dest_host_bytes][orig_len][host_len][ver][MAGIC0][MAGIC1]

Where:
  - orig_len, host_len, ver are 1 byte each
  - MAGIC is 2 bytes (0xFA, 0xCE)
  - ver currently 1
  - lengths are byte counts of the UTF-8 encoded origin and dest_host strings

Back-compat:
  - If no trailer magic, metadata is absent (daemon must fall back).
"""

from __future__ import annotations

from typing import Optional

_MAGIC = b"\xFA\xCE"
_TRAILER_VER = 1

# Control-plane endpoints (FrogNet control plane paths)
_CONTROL_PATH_PREFIXES = (
    "/api.php",
    "/api_semantic.php",
    "/getHosts.php",
    "/createFrogNet.php",
    "/frognet_echo.php",
    "/getDefaultRoute.php",
)


def is_control_plane_path(path: str) -> bool:
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    return any(p.startswith(x) for x in _CONTROL_PATH_PREFIXES)


def inject_origin_into_semantic_request(packet: bytes, origin_ip: str, dest_host: str) -> bytes:
    """
    Append origin + explicit destination host identity to a semantic packet.

    origin_ip:
      - the client/origin identity (typically ctx['client_ip'])

    dest_host:
      - the explicit Host identity of the request (ctx['target_host'] or ctx['target_ip'])
      - MUST be explicit; daemon must not guess.

    This does NOT modify codec header/body. It appends a trailer.
    """
    if not isinstance(packet, (bytes, bytearray)):
        raise TypeError("packet must be bytes")

    o = (origin_ip or "").strip()
    h = (dest_host or "").strip()

    ob = o.encode("utf-8", "replace")
    hb = h.encode("utf-8", "replace")

    if len(ob) > 255:
        ob = ob[:255]
    if len(hb) > 255:
        hb = hb[:255]

    trailer = (
        ob
        + hb
        + bytes([len(ob), len(hb), _TRAILER_VER])
        + _MAGIC
    )
    return bytes(packet) + trailer
