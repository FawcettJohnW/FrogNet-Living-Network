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
core/hosts_only.py

[HOSTS_ONLY_V1] Strict /etc/hosts resolution.  No DNS, no env vars, no
socket.gethostbyname, no socket.getaddrinfo.  /etc/hosts is the ONLY
source of name->IP mapping in FrogNet.

If a name is not in /etc/hosts, resolve() raises RuntimeError loudly
rather than silently falling through to a network resolver.  This is
deliberate: the alternative is non-deterministic routing.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

_HOSTS_PATH = "/etc/hosts"
_CACHE_TTL_SEC = float(os.environ.get("FROGNET_HOSTS_CACHE_TTL", "5.0"))

_lock = threading.RLock()
_cached_map: Dict[str, str] = {}
_cached_at: float = 0.0


def _is_ipv4_literal(s: str) -> bool:
    if not s:
        return False
    parts = s.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except (TypeError, ValueError):
        return False


def _read_hosts_map() -> Dict[str, str]:
    m: Dict[str, str] = {}
    try:
        with open(_HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                ip = parts[0]
                # IPv4 only - IPv6 entries have colons
                if ":" in ip:
                    continue
                for name in parts[1:]:
                    m[name] = ip
    except Exception as e:
        raise RuntimeError(
            f"[HOSTS_ONLY_V1] cannot read {_HOSTS_PATH}: {e!r}"
        )
    return m


def _hosts_map() -> Dict[str, str]:
    """Cached read of /etc/hosts.  TTL bounded so a build-time rewrite
    becomes visible without a process restart."""
    global _cached_map, _cached_at
    now = time.monotonic()
    if _cached_map and (now - _cached_at) < _CACHE_TTL_SEC:
        return _cached_map
    with _lock:
        if _cached_map and (now - _cached_at) < _CACHE_TTL_SEC:
            return _cached_map
        _cached_map = _read_hosts_map()
        _cached_at = time.monotonic()
        return _cached_map


def resolve(name: str) -> str:
    """[HOSTS_ONLY_V1] Resolve `name` to IPv4 via /etc/hosts.

    - If `name` is already an IPv4 literal, returns it unchanged.
    - Otherwise consults /etc/hosts (cached).
    - Raises RuntimeError if not found.  No DNS fallback.
    """
    if not name:
        raise RuntimeError("[HOSTS_ONLY_V1] resolve(): empty name")
    name = name.strip()
    if _is_ipv4_literal(name):
        return name
    m = _hosts_map()
    ip = m.get(name)
    if ip is None:
        raise RuntimeError(
            f"[HOSTS_ONLY_V1] {name!r} not in {_HOSTS_PATH}; "
            f"refusing to fall through to DNS."
        )
    return ip


def try_resolve(name: str) -> Optional[str]:
    """Same as resolve() but returns None on miss instead of raising.

    Use only at sites that genuinely need the Optional contract for
    backwards compatibility (e.g. legacy callers that already test for
    None).  Prefer resolve()."""
    try:
        return resolve(name)
    except RuntimeError:
        return None
