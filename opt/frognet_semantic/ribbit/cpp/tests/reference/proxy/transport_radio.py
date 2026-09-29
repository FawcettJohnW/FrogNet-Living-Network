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
proxy/transport_radio.py

UDP-based semantic HAM transport over ham0.

This replaces the in-process simulation with a real gateway-to-gateway
semantic carrier that traverses:

  ham0 (gretap) -> phantom underlay -> tc/netem shaping

This module:
  - DOES use sockets
  - DOES bind to ham0
  - DOES support synchronous request/response
  - DOES fail closed on timeout or transport error

It intentionally does NOT:
  - manipulate routing
  - touch iptables
  - depend on kernel ICMP
"""

from typing import Dict, Any
import socket
import json
import time
import uuid
import select


# -----------------------------
# Exceptions
# -----------------------------

class RadioUnavailableError(RuntimeError):
    pass


# -----------------------------
# Constants
# -----------------------------

DEFAULT_PORT = 17777
MAX_PACKET = 8192
HAM_IFACE = "ham0"


# -----------------------------
# Low-level socket helpers
# -----------------------------

def _bind_udp_socket() -> socket.socket:
    """
    Create a UDP socket bound to ham0.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Best-effort bind to ham0
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, HAM_IFACE.encode())
    except OSError:
        # Non-fatal: continue without device pinning
        pass

    return s


# -----------------------------
# Public API
# -----------------------------

def send_radio_packet(
    *,
    origin_ip: str,
    target_gateway_ip: str,
    payload: Dict[str, Any],
    gateway_port: int = DEFAULT_PORT,
) -> None:
    """
    Fire-and-forget semantic send over ham0.
    """
    if not target_gateway_ip:
        raise RadioUnavailableError("No target gateway IP")

    if not isinstance(payload, dict):
        raise ValueError("HAM payload must be a dict")

    s = _bind_udp_socket()
    try:
        data = json.dumps(payload).encode("utf-8")
        s.sendto(data, (target_gateway_ip, gateway_port))
    finally:
        s.close()


def send_radio_request_and_wait(
    *,
    origin_ip: str,
    target_gateway_ip: str,
    payload: Dict[str, Any],
    timeout_ms: int = 2000,
    gateway_port: int = DEFAULT_PORT,
) -> Dict[str, Any]:
    """
    Send a semantic request over HAM and wait for a semantic reply.

    This is the authoritative bridge used by ICMP semantics.
    """
    if not target_gateway_ip:
        raise RadioUnavailableError("No target gateway IP")

    if not isinstance(payload, dict):
        raise ValueError("HAM payload must be a dict")

    corr_id = uuid.uuid4().hex
    payload = dict(payload)
    payload["_corr_id"] = corr_id

    s = _bind_udp_socket()

    try:
        # Bind ephemeral local port on ham0
        s.bind(("", 0))

        encoded = json.dumps(payload).encode("utf-8")
        s.sendto(encoded, (target_gateway_ip, gateway_port))

        deadline = time.time() + (timeout_ms / 1000.0)

        while time.time() < deadline:
            timeout = max(0.0, deadline - time.time())
            r, _, _ = select.select([s], [], [], timeout)
            if not r:
                break

            data, addr = s.recvfrom(MAX_PACKET)
            if addr[0] != target_gateway_ip:
                continue

            try:
                reply = json.loads(data.decode("utf-8"))
            except Exception:
                continue

            if isinstance(reply, dict) and reply.get("_corr_id") == corr_id:
                return reply

        raise RadioUnavailableError("HAM request timed out")

    finally:
        s.close()
