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
frognet_monitor/discovery.py — the node snapshot (getSnapshot.php) and API helpers.

All HTTP requests go through the proxy on port 80.
NEVER open sockets to daemon port 9009 directly.
"""

import json
import urllib.request
from typing import Any, List, Tuple

from .trace import trace


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
    call it; the monitor's peer list is the snapshot's members only [ONE_MONITOR_TRUTH_V1].
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


# [ONE_MONITOR_TRUTH_V1] the node's snapshot, getSnapshot.php: build, mode, ponds, hub link, members, elections, and
# where each came from (var/www/html/frognet_node.inc.php says which file per field per mode). The monitor reads this
# and nothing else for those fields; the peer table is its `members`.
SNAPSHOT_URL = "http://127.0.0.1/getSnapshot.php"

# [DEVICE_ROW_V1] ip -> role, ip -> alive, from the snapshot's members (region mode: the daemon's region_hosts.json; a
# "leaf" is a device (a phone, a laptop) reached over the hub tunnel: its session in the region IS its aliveness
# ([ALIVE_IS_THE_SESSION_V1]) and it runs no FrogNet stack, so it is not echo-probed; its row reads <name> DEVICE
# ONLINE|DOWN).
ROLES: dict = {}
ALIVE: dict = {}
SELF_IP: str = ""                                   # the member the snapshot marks self (node.dot1)


def parse_snapshot(text: str):
    """getSnapshot.php's body -> the normalized summary every monitor draws from, or None when it is not a snapshot.
    Pure (no I/O): the shared parser vectors (frognet_monitor_shared/parser_vectors.json, "snapshot") run it, and the
    C++ (parseSnapshot) and C# (Parsers.ParseSnapshot) must give the same answer.

    summary = {"mode", "name", "dot1", "build", "ponds": ["*area" (forward) | "area"], "hub": ["<iface> <age>s" |
               "<iface> no handshake" | "<iface> error"], "members": ["<ip> <name> <role> <alive|down> <source>"
               (+ " self")], "dbhost": <ip or "">, "errors": [str], "raw": the decoded object}"""
    import json as _j
    try:
        d = _j.loads(text)
    except (ValueError, TypeError):
        return None
    def _num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    def _str(x):
        return x if isinstance(x, str) else ""
    if not isinstance(d, dict) or not _num(d.get("snapshot")) or d.get("snapshot") != 1 \
            or d.get("mode") not in ("lan-only", "region"):
        return None
    node = d.get("node") if isinstance(d.get("node"), dict) else {}
    build = d.get("build") if isinstance(d.get("build"), dict) else {}
    ponds = []
    for p in (d.get("ponds") if isinstance(d.get("ponds"), list) else []):
        if isinstance(p, dict):
            ponds.append(("*" if p.get("forward") is True else "") + _str(p.get("area")))
    hub = []
    for h in (d.get("hub") if isinstance(d.get("hub"), list) else []):
        if not isinstance(h, dict):
            continue
        age = h.get("handshake_age_s")
        if _str(h.get("error")):
            hub.append("%s error" % _str(h.get("iface")))
        elif _num(age):
            hub.append("%s %ds" % (_str(h.get("iface")), int(age)))
        else:
            hub.append("%s no handshake" % _str(h.get("iface")))
    members = []
    for m in (d.get("members") if isinstance(d.get("members"), list) else []):
        if not isinstance(m, dict):
            continue
        members.append("%s %s %s %s %s%s" % (_str(m.get("ip")), _str(m.get("name")), _str(m.get("role")),
                                             "alive" if m.get("alive") is True else "down", _str(m.get("source")),
                                             " self" if m.get("self") is True else ""))
    el = d.get("elections") if isinstance(d.get("elections"), dict) else {}
    errs = d.get("errors") if isinstance(d.get("errors"), list) else []
    return {"mode": d["mode"], "name": _str(node.get("name")), "dot1": _str(node.get("dot1")),
            "build": _str(build.get("build")), "ponds": ponds, "hub": hub, "members": members,
            "dbhost": _str(el.get("databasehost")), "errors": [e for e in errs if isinstance(e, str)], "raw": d}


def fetch_snapshot():
    """One read of getSnapshot.php. A failed or malformed read is traced and returns None: the screen says the snapshot
    did not answer, and the next cycle asks again (nothing stands in for it)."""
    global ROLES, ALIVE, SELF_IP
    try:
        req = urllib.request.Request(SNAPSHOT_URL)
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        trace(f"[SNAPSHOT] len={len(raw)}")
    except Exception as e:
        trace(f"[SNAPSHOT] {SNAPSHOT_URL} FAILED: {e}")
        ROLES, ALIVE, SELF_IP = {}, {}, ""
        return None
    snap = parse_snapshot(raw.decode("utf-8", errors="replace"))
    if snap is None:
        trace(f"[SNAPSHOT] not a snapshot: {raw[:200]!r}")
        ROLES, ALIVE, SELF_IP = {}, {}, ""
        return None
    ms = [m for m in snap["raw"].get("members") or [] if isinstance(m, dict) and "ip" in m]
    ROLES = {m["ip"]: m.get("role", "") for m in ms}
    ALIVE = {m["ip"]: m.get("alive") is True for m in ms}
    SELF_IP = snap["dot1"]
    return snap


def hosts_of(snap):
    """[(ip, name), ...] for the peer table: the snapshot's members, in its order. No snapshot -> no rows."""
    if not snap:
        return []
    return [(m["ip"], m.get("name", m["ip"])) for m in snap["raw"].get("members") or [] if isinstance(m, dict) and "ip" in m]
