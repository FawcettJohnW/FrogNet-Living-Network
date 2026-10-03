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
# daemon/util/hosts.py
#
# Hosts-first resolution with NO DNS fallback for control-plane destinations.
# Specifically, databasehost.frognet MUST resolve from /etc/hosts only.

from __future__ import annotations

import os
import socket
import time
import threading
from typing import Dict, Optional

_HOSTS_PATH = "/etc/hosts"
_CACHE_TTL = float(os.environ.get("FROGNET_HOSTS_CACHE_TTL", "5.0"))

_cache_lock = threading.RLock()
_cached_map: Dict[str, str] = {}
_cached_at: float = 0.0


class HostsMapUnreadable(RuntimeError):
    """/etc/hosts could not be read, so no name can be resolved."""


def _read_hosts_map() -> Dict[str, str]:
    """[NO_FALLBACK_V1] Raise if /etc/hosts cannot be read or has no entries.

    `except Exception: pass` returned {} for a read failure, which
    _get_hosts_map() then declined to cache (it caches only truthy maps), so
    every resolve_host() call re-read the unreadable file and every caller got
    None - the same value as "that name is not in the file". The daemon has no
    DNS behind this by design, so an unreadable file is a total resolution
    outage and needs to say so once, not present as a name-by-name miss.

    Names are lowered on the way in. DNS names are case-insensitive (RFC 4343)
    and /etc/hosts is written as `FrogNetHost.BABox`, so a lookup for
    `frognethost.babox` missed. netutil.py lowers both sides; this copy lowered
    neither. Both sides are lowered here - lowering only one breaks the other
    case.
    """
    m: Dict[str, str] = {}
    try:
        with open(_HOSTS_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "#" in s:
                    s = s.split("#", 1)[0].strip()
                parts = s.split()
                if len(parts) < 2:
                    continue
                ip = parts[0].strip()
                for name in parts[1:]:
                    m[name.strip().lower()] = ip
    except OSError as e:
        raise HostsMapUnreadable(
            f"{_HOSTS_PATH}: {type(e).__name__} errno={e.errno} ({e.strerror}) "
            f"- no name can be resolved") from e
    if not m:
        raise HostsMapUnreadable(
            f"{_HOSTS_PATH}: read OK but contains no host entries "
            f"- no name can be resolved")
    return m


def _get_hosts_map() -> Dict[str, str]:
    global _cached_map, _cached_at
    now = time.monotonic()
    if now - _cached_at < _CACHE_TTL and _cached_map:
        return _cached_map
    with _cache_lock:
        # Re-check after acquiring lock
        if now - _cached_at < _CACHE_TTL and _cached_map:
            return _cached_map
        _cached_map = _read_hosts_map()
        _cached_at = time.monotonic()
        return _cached_map

def _is_v4(s: str) -> bool:
    """[NO_FALLBACK_V1] inet_aton raises OSError for a malformed address and
    that is the answer. Narrowed from `except Exception`, which also reported a
    caller passing a non-string as "that is a hostname" and sent it to the
    hosts map, where it missed and came back as an unresolvable name.
    """
    try:
        socket.inet_aton((s or "").strip())
        return True
    except OSError:
        return False

def resolve_host(name: str) -> Optional[str]:
    """
    Resolve a FrogNet control-plane name to IPv4.
    Rules:
      - If literal IPv4, return as-is.
      - /etc/hosts ONLY (cached with TTL). No DNS, for any name: a resolver
        answer can differ from the file the kernel and every other component
        route by.
      - Lookup is lowered to match the map (RFC 4343).

    Raises HostsMapUnreadable if the file itself cannot be read. None means the
    name is genuinely absent from the file - the two are different facts.
    """
    n = (name or "").strip()
    if not n:
        return None
    if _is_v4(n):
        return n

    return _get_hosts_map().get(n.lower())
