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
proxy/icmp_semantic.py

Gateway-terminated ICMP semantics over HAM.

This module does NOT do iptables/NFQUEUE or raw sockets.
It defines the message contract and provides helpers to:
  - build a semantic ping request to send over HAM
  - interpret a ping reply/error
  - enforce fail-closed behavior

Radio payload format (dict):
  {
    "type": "icmp_ping",
    "dst_ip": "<target>",
    "ident": <int>,
    "seq": <int>,
    "ttl": <int>,
    "timeout_ms": <int>,
  }

Reply format:
  {
    "type": "icmp_pong",
    "ok": true/false,
    "rtt_ms": <float|null>,
    "reason": "<string>",
    "ident": <int>,
    "seq": <int>,
  }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class IcmpPingRequest:
    dst_ip: str
    ident: int
    seq: int
    ttl: int = 64
    timeout_ms: int = 2000

    def to_radio_payload(self) -> Dict[str, Any]:
        if not self.dst_ip:
            raise ValueError("dst_ip required")
        return {
            "type": "icmp_ping",
            "dst_ip": str(self.dst_ip),
            "ident": int(self.ident) & 0xFFFF,
            "seq": int(self.seq) & 0xFFFF,
            "ttl": int(self.ttl) & 0xFF,
            "timeout_ms": int(self.timeout_ms),
        }


@dataclass(frozen=True)
class IcmpPingReply:
    ok: bool
    ident: int
    seq: int
    rtt_ms: Optional[float] = None
    reason: str = ""

    @staticmethod
    def from_radio_payload(p: Dict[str, Any]) -> "IcmpPingReply":
        if not isinstance(p, dict):
            raise ValueError("payload must be dict")
        if p.get("type") != "icmp_pong":
            raise ValueError("not an icmp_pong payload")

        ok = bool(p.get("ok", False))
        ident = int(p.get("ident", 0)) & 0xFFFF
        seq = int(p.get("seq", 0)) & 0xFFFF

        rtt = p.get("rtt_ms", None)
        rtt_ms = float(rtt) if rtt is not None else None
        reason = str(p.get("reason", "") or "")

        return IcmpPingReply(ok=ok, ident=ident, seq=seq, rtt_ms=rtt_ms, reason=reason)


def build_icmp_ping_payload(dst_ip: str, ident: int, seq: int, *, ttl: int = 64, timeout_ms: int = 2000) -> Dict[str, Any]:
    return IcmpPingRequest(dst_ip=dst_ip, ident=ident, seq=seq, ttl=ttl, timeout_ms=timeout_ms).to_radio_payload()
