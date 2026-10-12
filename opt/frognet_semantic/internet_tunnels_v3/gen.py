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
"""[GEN_V1] the generation counter: one integer per node, bumped only on an IMPACTFUL change, announced as the
node's `gen` fact. Triggering is then integer comparison, not notifications:

    world_gen  = sum of every alive member's `gen` (as this node sees it)      -> /var/lib/frognet/world_gen

It is the audit trail, not a trigger: no merge runs for a region event [NO_MERGE_ON_REGION_EVENTS_V1] -- the facts
carry what the graph needs and the daemon applies it. The GEN line names the change on the node that made it; the
`REGION: world gen a -> b (<member> n->m)` line on every other node says they saw it.

Impactful (closed list; the name goes in the GEN line):
    routes_changed, default_changed, topology_hosts_changed, member_came, member_went, service_host_changed,
    hub_channel_changed, maps_changed, autoswitch_changed, iptables_changed.
Not impactful: the same facts re-announced, the same hosts re-derived, a capability re-publish, a benchmark.

Files live under /var/lib/frognet, never /etc/sentinels (runMerge wipes that directory at the top of every pass,
which is why its own change detection was blind: the baseline it diffed against was always empty).
"""
import fcntl
import json
import os
import time

GEN_PATH = os.environ.get("FROGNET_GEN", "/var/lib/frognet/gen")
WORLD_PATH = os.environ.get("FROGNET_WORLD_GEN", "/var/lib/frognet/world_gen")
IMPACT = ("routes_changed", "default_changed", "topology_hosts_changed", "member_came", "member_went",
          "service_host_changed", "hub_channel_changed", "maps_changed", "autoswitch_changed", "iptables_changed",
          "first_run")


def _read_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")
    os.replace(tmp, path)


def read() -> int:
    return int(_read_json(GEN_PATH).get("gen", 0))


def bump(why: str, by: str, log=None, announce: bool = True) -> int:
    """gen -> gen+1, under a lock (the daemon, the merge and the elector can all bump). Announced as the `gen` fact
    so every member's daemon sees the world move. Returns the new gen."""
    assert why in IMPACT, why
    os.makedirs(os.path.dirname(GEN_PATH), exist_ok=True)
    with open(GEN_PATH + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cur = _read_json(GEN_PATH)
        old = int(cur.get("gen", 0))
        new = old + 1
        _write_json(GEN_PATH, {"gen": new, "why": why, "by": by, "at": time.time()})
    if log:
        log("GEN: %d -> %d why=%s by=%s" % (old, new, why, by))
    if announce:
        from . import region
        region.write_facts({"gen": new, "gen_why": why})          # the announcement; an error here is an error
    return new


def world(participants) -> dict:
    """{"world": sum, "members": {name: gen}} over the ALIVE participants' facts."""
    members = {}
    for p in participants or []:
        if p.get("alive") and p.get("name"):
            members[p["name"]] = int(((p.get("facts") or {}).get("gen")) or 0)
    return {"world": sum(members.values()), "members": members}


def write_world(w: dict) -> None:
    _write_json(WORLD_PATH, {"world": w["world"], "members": w["members"], "at": time.time()})


def read_world() -> dict:
    return _read_json(WORLD_PATH)
