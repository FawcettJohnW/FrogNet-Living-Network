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
host_float.py -- a bundle app's float detector (vendored; bundles install standalone, so this
is self-contained, no cross-bundle import). A table/codex host can float (the service-host
name re-resolves to a new node). The thin client sees it the same trivial way any node does:
the resolved IP of its connect host changes between ticks. On that delta the app re-syncs
against the new host -- drops its stale watermark so the new host's lower ts is NOT mistaken
for a split, clears any prior 'disconnected', and refreshes. Memory, not messages: nothing is
pushed; the client re-asserts its view against the host that now holds the table.

Tk-free on purpose, so it is unit-provable without a display.
"""
from __future__ import annotations
import socket
from urllib.parse import urlparse


def resolve_base_ip(base: str):
    """IP for the host in an http://host[:port] base, or None if it can't resolve."""
    try:
        host = urlparse(base if "://" in base else "http://" + base).hostname
        if not host:
            return None
        # [HOSTS_ONLY_V1] /etc/hosts, never the resolver. This watches a FrogNet host
        # base (databasehost.frognet and friends) for a float; a resolver answer can
        # differ from the file the rest of the node routes by, so the watch would fire
        # on a change nobody made, or miss a real one.
        try:
            from core.hosts_only import try_resolve
            return try_resolve(host)
        except ImportError:
            pass
        import os
        for line in open(os.environ.get("FROGNET_HOSTS_PATH", "/etc/hosts"),
                         encoding="utf-8", errors="replace"):
            line = line.split("#", 1)[0].split()
            if len(line) >= 2 and host in line[1:]:
                return line[0]
        return None
    except Exception:
        return None


class FloatWatch:
    """Tick this in the app's poll loop. First tick primes the baseline; on a later change of
    the resolved IP it calls on_float() exactly once. Never raises -- must not break the loop."""

    def __init__(self, resolve_ip, on_float, logger=lambda s: None):
        self._resolve = resolve_ip
        self._on_float = on_float
        self._log = logger
        self._last = None

    def tick(self) -> bool:
        try:
            ip = self._resolve()
        except Exception:
            return False
        if not ip or ip == self._last:
            return False
        first = self._last is None
        self._last = ip
        if first:
            return False
        self._log(f"FLOAT table host -> {ip}; re-syncing")
        try:
            self._on_float()
        except Exception:
            pass
        return True
