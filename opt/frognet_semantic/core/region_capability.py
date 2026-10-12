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
"""[ELECTION_FROM_FACTS_V1] the service elections' input in region mode: every member's capability is a FACT in the
region (facts.capability, written by its daemon from frognet_capability_probe.sh), and the election reads the alive
members' facts -- not capability tuples in a store that may live on the very host that just died.

What was: each node advertised <role>/capability tuples to the control store (databasehost_control = highest .1) and
the election read them back from there. The databasehost IS the highest .1, so when it died the control moved, the
advertisers kept writing to the dead name, the new control's store had nothing or stale rows, and every election
ABSTAINed ("the store did not answer ... the standing line is kept") -- Seattle5 2026-10-09 19:00: both roles ABSTAIN,
databasehost.frognet left pointing at a dead host. The switchover was not automatic because its input was not.

Here: `set_snapshot(participants)` is called by the elector with the region's alive members (the daemon's view at
this event); `rows(role)` then answers in the shape the readers expect from the store (value/addr/ts_env/name/age_s),
so gather_candidates, _capability_index and _live_roles work unchanged over the region. Region mode always sets the
snapshot; LAN-only (no region) never does and reads the store as before. That is the mode, not a fallback.
"""
from __future__ import annotations
import threading
from typing import Dict, List, Optional

_snap = threading.local()


def _dot1_of(facts: dict) -> str:
    for r in facts.get("addrs") or []:
        ip = r.get("ip", "")
        if r.get("kind") == "own" and ip.endswith(".1"):
            return ip
    sub = facts.get("subnet") or ""
    return sub.rsplit(".", 1)[0] + ".1" if sub.endswith(".0/24") else ""


def set_snapshot(participants: Optional[list]) -> int:
    """participants: the region's list (name, alive, facts). None clears (LAN-only). Returns how many rows."""
    if participants is None:
        _snap.rows = None
        return 0
    rows: List[Dict] = []
    for p in participants:
        f = p.get("facts") or {}
        if not p.get("alive") or f.get("role") != "node":
            continue
        cap = f.get("capability")
        ip = _dot1_of(f)
        if not isinstance(cap, dict) or not ip:
            continue
        cap = dict(cap)
        cap["lan_ip"] = ip                              # the .1 the region knows, not whatever the probe guessed
        rows.append({"value": {"capability": cap, "loadavg": cap.get("loadavg") or {}, "temps_c": cap.get("temps_c") or []},
                     "addr": ip, "ts_env": float(f.get("capability_ts") or 0), "age_s": 0,
                     "name": "region:%s" % p.get("name", ip), "member": p.get("name", "")})
    _snap.rows = rows
    return len(rows)


def active() -> bool:
    return getattr(_snap, "rows", None) is not None


def rows(role: str) -> Optional[List[Dict]]:
    """The capability rows for a role, from the snapshot; None when no snapshot is set (LAN-only)."""
    r = getattr(_snap, "rows", None)
    return None if r is None else [dict(x, name="%s:%s" % (x["name"], role)) for x in r]
