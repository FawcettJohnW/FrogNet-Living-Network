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
import os
import subprocess
import time
import threading

_CACHE_TTL = float(os.environ.get("FROGNET_LOCAL_IP_CACHE_TTL", "30.0"))

_cache_lock = threading.RLock()
_cached_ips = None
_cached_at = 0.0


def _discover_local_ips():
    ips = {"127.0.0.1"}
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True)
        for line in out.splitlines():
            if "inet" in line:
                ip = line.split()[3].split("/")[0]
                ips.add(ip)
    except Exception:
        pass
    return ips


def local_ips():
    global _cached_ips, _cached_at
    now = time.monotonic()
    if _cached_ips is not None and now - _cached_at < _CACHE_TTL:
        return _cached_ips
    with _cache_lock:
        if _cached_ips is not None and now - _cached_at < _CACHE_TTL:
            return _cached_ips
        _cached_ips = _discover_local_ips()
        _cached_at = time.monotonic()
        return _cached_ips
