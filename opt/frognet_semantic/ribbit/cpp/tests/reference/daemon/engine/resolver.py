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
# daemon/engine/resolver.py
#
# Destination resolution for the daemon.
#
# USER INVARIANT (ENFORCED):
#   Run locally ONLY if the destination FQDN/IP resolves to a local interface.
#   Otherwise forward to the real target; no other heuristics.
#
# NO forced control-plane destinations. No path-based overrides.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from daemon.util.hosts import resolve_host
from daemon.util.ip import local_ips


@dataclass
class Destination:
    upstream_port: int     # 8080 if local apache, else 80 to local proxy
    host_header: str       # explicit destination identity
    dest_ip: Optional[str] # resolved IP of host_header (hosts-only)
    is_local: bool


class DestinationResolver:
    def __init__(self) -> None:
        # local_ips() is TTL-cached in util/ip.py - no subprocess per call
        pass

    def _is_local_ip(self, ip: Optional[str]) -> bool:
        return bool(ip) and ip in local_ips()

    def resolve(
        self,
        req_tpl,
        *,
        peer_ip: str,
        origin_ip: str,
        dest_host: str,
    ) -> Destination:
        """
        Rules:
          1) Destination identity MUST be explicit (dest_host from packet).
          2) Resolve that identity (hosts-only).
          3) Execute locally ONLY if resolved IP is one of our local interface IPs.
          4) Otherwise forward to local proxy (port 80), preserving Host identity.
        """
        host = (dest_host or "").strip()
        if not host:
            raise ValueError("semantic packet missing explicit dest_host (fail closed)")

        ip = resolve_host(host)
        is_local = self._is_local_ip(ip)

        if is_local:
            return Destination(upstream_port=8080, host_header=host, dest_ip=ip, is_local=True)

        return Destination(upstream_port=80, host_header=host, dest_ip=ip, is_local=False)
