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

    Pure function: takes the file content as input, no I/O.  frognet_sim.py (tests) and the parser-vector oracle
    call it; discover_hosts() reads getHosts.php only [MONITOR_HOSTS_ONE_SOURCE_V1].
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


# [DEVICE_ROW_V1] ip -> role, from getHosts.php (region mode: the daemon's region_hosts.json, i.e. the region's alive
# members, plus those in their hold-down). A "leaf" is a device (a phone, a laptop) reached over the hub tunnel: its
# session in the region IS its aliveness ([ALIVE_IS_THE_SESSION_V1], getHosts.php "alive") and it runs no FrogNet stack,
# so it is not echo-probed; its row reads <name> DEVICE ONLINE|DOWN.
ROLES: dict = {}
ALIVE: dict = {}                                    # ip -> its member's region session is up (getHosts.php "alive")


def discover_hosts() -> List[Tuple[str, str]]:
    """Return [(ip, name), ...] from getHosts.php, the one source of the peer list. [MONITOR_HOSTS_ONE_SOURCE_V1]
    A failed read is traced and the table is empty until the next cycle asks again (there used to be an /etc/hosts
    read behind it; parse_etc_hosts_content stays for the simulator and the parser-vector oracle)."""
    global ROLES, ALIVE
    try:
        data = api_request("http://127.0.0.1/getHosts.php", timeout=5)
    except Exception as e:
        trace(f"[DISCOVER] getHosts.php failed: {e} - no peer list this cycle")
        ROLES, ALIVE = {}, {}
        return []
    ROLES = {h["ip"]: h.get("role", "") for h in data if "ip" in h}
    ALIVE = {h["ip"]: bool(h.get("alive")) for h in data if "ip" in h}
    return [(h["ip"], h.get("name", h["ip"])) for h in data if "ip" in h]


# [PONDS_ARE_LISTED_V1] every network this machine is in (getPonds.php: read live from each pond's RAM manager).
# The peer table is the forward pond's; draw_ponds shows the rest. A failed read is said, not replaced.
def discover_ponds():
    try:
        return api_request("http://127.0.0.1/getPonds.php", timeout=5)
    except Exception as e:
        trace(f"[DISCOVER] getPonds.php failed: {e}")
        return {"ponds": [], "error": str(e)}

