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
frognet_monitor/discovery.py — Host discovery and API helpers.

All HTTP requests go through the proxy on port 80.
NEVER open sockets to daemon port 9009 directly.
"""

import json
import urllib.request
from typing import Any, List, Tuple

from .trace import trace
from .identity import LOCAL_IP


def api_request(url: str, timeout: float = 5) -> Any:
    """Make an API request through the proxy.

    Unwraps the standard API envelope:
        {"ok":true, "rows":[...]}
        {"ok":true, "row":{...}}
    Returns the rows list, row dict, or raw body.
    """
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            body = json.loads(raw)
        trace(f"[API] url={url} status=OK len={len(raw)} "
              f"body={raw[:200].decode('utf-8', errors='replace')}")
    except Exception as e:
        trace(f"[API] url={url} FAILED: {e}")
        raise
    # Unwrap envelope
    if isinstance(body, dict):
        if "rows" in body:
            return body["rows"]
        if "row" in body:
            return body["row"]
    return body


def parse_etc_hosts_content(content: str) -> List[Tuple[str, str]]:
    """Parse the content of an `/etc/hosts` file and return
    [(ip, name), ...] for entries that look like FrogNet hosts.

    Filters: 10.0.0.0/8 only; excludes 10.253.0.0/16 (WG transit) and
    10.254.0.0/16 (chorus virtual); requires a `FrogNetHost.<name>`
    alias on the line (the name returned is the part after
    `FrogNetHost.`).

    Pure function: takes the file content as input, no I/O.  Both
    discover_hosts() (production) and frognet_sim.py (tests) call this
    with the same parser.
    """
    hosts: List[Tuple[str, str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if (len(parts) >= 2
                and parts[0].startswith("10.")
                and not parts[0].startswith("10.253.")
                and not parts[0].startswith("10.254.")):
            for p in parts[1:]:
                if "FrogNetHost." in p:
                    name = p.split("FrogNetHost.")[-1]
                    hosts.append((parts[0], name))
                    break
    return hosts


def discover_hosts() -> List[Tuple[str, str]]:
    """Return [(ip, name), ...] from getHosts.php or /etc/hosts fallback."""
    try:
        data = api_request("http://127.0.0.1/getHosts.php", timeout=5)
        return [(h["ip"], h.get("name", h["ip"])) for h in data if "ip" in h]
    except Exception as e:
        trace(f"[DISCOVER] getHosts.php failed: {e}, falling back to /etc/hosts")

    try:
        with open("/etc/hosts") as f:
            return parse_etc_hosts_content(f.read())
    except OSError as e:
        trace(f"[DISCOVER] /etc/hosts fallback failed: {e}")
        return []
