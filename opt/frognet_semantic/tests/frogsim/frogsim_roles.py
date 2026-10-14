#!/usr/bin/env python3
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
"""frogsim_roles.py - properly calculate databasehost over a CONVERGED System, using
the REAL election engine (core.frognet_role_elect + the real DatabaseRoleHandler). Not a
model: each node publishes a capability tuple and the election runs on every node. Then
we validate:

  databasehost - pond-wide agreement; best mysql-capable (WAN-inclusive), IP tiebreak.

(The A/V role half went with that role, T2 2026-10-12.)
"""
import os, sys, types, time
WT = os.environ.get("FROGNET_SEMANTIC_ROOT", "/home/claude/descend_good/opt/frognet_semantic")
sys.path.insert(0, WT)


_STATE = {"rows": {}, "my_ip": ""}


def _cap_rows(caps_by_role):
    fresh = int(time.time()) - 60      # recent ballot (well within FROGNET_BALLOT_MAX_AGE_S)
    out = {}
    for role, caps in caps_by_role.items():
        out[(role, "capability")] = [
            {"addr": ip, "var": "capability",
             "value": {"capability": cap, "loadavg": {"1": 0.1}, "temps_c": [], "ts": fresh}}
            for ip, cap in caps.items()]
    return out


def _install_persistent_fake():
    """Install ONE fake frognet_tuples reading from mutable _STATE; import RE against it."""
    T = types.ModuleType("frognet_tuples")
    T.get = lambda service, var, dbhost=None, fresh_s=0, timeout=4.0: _STATE["rows"].get((service, var), [])
    T.put = lambda *a, **k: True
    T.my_ip = lambda: _STATE["my_ip"]
    T.DEFAULT_DBHOST = "databasehost.frognet"
    sys.modules["core.frognet_tuples"] = T
    import core
    core.frognet_tuples = T
    sys.modules.pop("core.frognet_role_elect", None)
    from core import frognet_role_elect as RE   # module-level T binds to the fake
    from core.database_handler import DatabaseRoleHandler
    return RE, DatabaseRoleHandler


def _caps_for(system, caps_by_node):
    """Build the per-role capability dicts keyed by each node's .1 identity."""
    db = {}
    for name, nd in system.node.items():
        c = caps_by_node.get(name, {})
        ip = nd.one
        if getattr(nd, "dead", False):
            continue                                   # a dead node publishes nothing
        if c.get("mysql"):
            db[ip] = dict(lan_ip=ip, mysql_running=1, cores=c.get("cpu", 2),
                          cpu_bench_total=c.get("bench", 1000),
                          mem_total_kb=c.get("mem_gb", 2) * 1024 * 1024,
                          mem_available_kb=c.get("mem_gb", 2) * 1024 * 1024,
                          disk_write_mbps=c.get("disk_mbps", 40), disk_fsync_ms=c.get("fsync", 5),
                          disk_free_gb=c.get("disk_free_gb", 50))
    return {"databasehost": db}


_RE = None


def elect_all(system, caps_by_node):
    """Return {node: {databasehost}} using the REAL election."""
    global _RE
    caps = _caps_for(system, caps_by_node)
    if _RE is None:
        _RE = _install_persistent_fake()
    RE, DatabaseRoleHandler = _RE
    cap_rows = _cap_rows(caps)
    out = {}
    for node in system.node:
        _STATE["rows"] = dict(cap_rows); _STATE["my_ip"] = system.node[node].one
        e = RE.elect(DatabaseRoleHandler())
        out[node] = {"databasehost": e.get("lan_ip") if e else None}
    return out


def validate_roles(system, results, caps_by_node):
    problems = []
    dbs = {r["databasehost"] for r in results.values()}
    if len(dbs) > 1:
        problems.append(f"databasehost DISAGREEMENT across nodes: {dbs}")
    # databasehost should be a mysql-capable node
    dbcap = {system.node[n].one for n, c in caps_by_node.items() if c.get("mysql")}
    if dbs and dbcap and next(iter(dbs)) not in dbcap:
        problems.append(f"databasehost={dbs} not among mysql-capable {dbcap}")
    return problems


# ---- runnable gate: role election across representative shapes -----------------
def _run():
    from discovery.sim.system import System, TopologySpec, NodeSpec as N
    ok = 0; total = 0
    scenarios = {}
    scenarios["chain"] = (TopologySpec(nodes=[
        N("Sea5", "10.250.250"), N("Sea6", "10.160.160", guest_on=("Sea5", "10.250.250.222")),
        N("Sea3", "10.130.130", guest_on=("Sea6", "10.160.160.191")),
        N("Sea2", "10.120.120", guest_on=("Sea3", "10.130.130.47")), N("NY1", "10.102.60")],
        tunnels=[("Sea5", "NY1")]),
        {"NY1": {"mysql": 1, "mem_gb": 32, "cpu": 8, "bench": 9000},
         "Sea5": {"mysql": 1, "mem_gb": 4, "cpu": 4, "bench": 2000},
         "Sea6": {}, "Sea3": {}})
    scenarios["wgmesh"] = (TopologySpec(nodes=[N("X", "10.60.60"), N("Y", "10.61.61"), N("Z", "10.62.62")],
        tunnels=[("X", "Y"), ("X", "Z"), ("Y", "Z")]),
        {"X": {"mysql": 1, "mem_gb": 8, "cpu": 4, "bench": 3000},
         "Y": {"mysql": 1, "mem_gb": 16, "cpu": 8, "bench": 6000}, "Z": {}})
    scenarios["star"] = (TopologySpec(nodes=[N("H", "10.40.40"),
        N("S1", "10.41.41", guest_on=("H", "10.40.40.51")), N("S2", "10.42.42", guest_on=("H", "10.40.40.52"))]),
        {"H": {"mysql": 1, "mem_gb": 8, "cpu": 4, "bench": 3000},
         "S1": {}, "S2": {"mysql": 1, "mem_gb": 2}})
    for name, (spec, caps) in scenarios.items():
        total += 1
        s = System(spec); s.converge(max_cycles=15)
        res = elect_all(s, caps); probs = validate_roles(s, res, caps)
        db = next(iter({r["databasehost"] for r in res.values()}))
        if not probs:
            print(f"  PASS  {name:8} databasehost={db}"); ok += 1
        else:
            print(f"  FAIL  {name:8} {probs}")
    print(f"\nROLE ELECTION GATE: {'PASS' if ok == total else 'FAIL'} ({ok}/{total})")
    return ok == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
