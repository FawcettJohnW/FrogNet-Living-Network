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
"""frogsim_roles.py - properly calculate databasehost / mediahost / gamehost over a
CONVERGED System, using the REAL election engine (core.frognet_role_elect + the real
DatabaseRoleHandler / SotFMediaHandler / GameRoleHandler). Not a model: each node
publishes a capability tuple, the observer's reach_plane (LAN vs WAN, from its
converged routes) is published, and RE.elect() runs per role. Then we validate:

  databasehost - pond-wide agreement; best mysql-capable (WAN-inclusive), IP tiebreak.
  gamehost     - pond-wide agreement; highest-IP reachable host (no capability gate).
  mediahost    - LAN-scoped; each node elects its best ffmpeg+libvpx host on its OWN
                 attached LAN (a WAN/relayed box is barred).
"""
import os, sys, types, time
WT = os.environ.get("FROGNET_SEMANTIC_ROOT", "/home/claude/descend_good/opt/frognet_semantic")
sys.path.insert(0, WT)
from discovery.sim.fabric import route_egress


def _attached_lan_subnets(system, node):
    """/24s directly attached to this node (connected routes: dev, no via) - but a
    wg (tunnel) segment is WAN, never an attached LAN, so exclude wg devs."""
    wg = set(system._wg_devs(node).values())
    out = set()
    for line in system.routes.get(node, []):
        p = line.split()
        if p and "/" in p[0] and p[0].startswith("10.") and "via" not in p:
            dev = p[p.index("dev") + 1] if "dev" in p else ""
            if dev in wg:
                continue
            out.add(p[0])
    out.add(system.node[node].cidr)
    return sorted(out)


def _wan_subnets(system, node):
    """/24s whose winning egress is a wg (tunnel) dev = the WAN plane (as promote() derives)."""
    wg = set(system._wg_devs(node).values())
    wan = set()
    for line in system.routes.get(node, []):
        p = line.split()
        if p and "/" in p[0] and p[0].startswith("10."):
            net = p[0].split("/")[0]
            if net.startswith("10.253.") or net.startswith("10.254."):
                continue
            eg = route_egress(system.routes[node], net.rsplit(".", 1)[0] + ".1")
            if eg and eg[0] in wg:
                wan.add(p[0])
    return sorted(wan)


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
    sys.modules["frognet_tuples"] = T
    sys.modules["core.frognet_tuples"] = T
    import core
    core.frognet_tuples = T
    bundle = os.path.join(WT, "etc", "frognet_bundles", "communicator")
    if bundle not in sys.path:
        sys.path.insert(0, bundle)
    for m in ("frognet_role_elect", "core.frognet_role_elect"):
        sys.modules.pop(m, None)
    from core import frognet_role_elect as RE   # module-level T binds to the fake
    from core.database_handler import DatabaseRoleHandler
    from core.sotf_handler import SotFMediaHandler
    from core.game_role import GameRoleHandler
    return RE, DatabaseRoleHandler, SotFMediaHandler, GameRoleHandler


def _caps_for(system, caps_by_node):
    """Build the per-role capability dicts keyed by each node's .1 identity."""
    db, media, game = {}, {}, {}
    for name, nd in system.node.items():
        c = caps_by_node.get(name, {})
        ip = nd.one
        if getattr(nd, "dead", False):
            continue                                   # a dead node publishes nothing
        game[ip] = dict(lan_ip=ip, cores=c.get("cpu", 1))   # gamehost: trivial eligibility
        if c.get("mysql"):
            db[ip] = dict(lan_ip=ip, mysql_running=1, cores=c.get("cpu", 2),
                          cpu_bench_total=c.get("bench", 1000),
                          mem_available_kb=c.get("mem_gb", 2) * 1024 * 1024,
                          disk_write_mbps=c.get("disk_mbps", 40), disk_fsync_ms=c.get("fsync", 5),
                          disk_free_gb=c.get("disk_free_gb", 50))
        if c.get("media"):
            media[ip] = dict(lan_ip=ip, ffmpeg=1, libvpx=1, cores=c.get("cpu", 2),
                             cpu_bench_total=c.get("bench", 1000))
    return {"databasehost": db, "mediahost": media, "boardgame": game}


_RE = None


def elect_all(system, caps_by_node):
    """Return {node: {databasehost, mediahost, gamehost}} using the REAL election."""
    global _RE
    caps = _caps_for(system, caps_by_node)
    if _RE is None:
        _RE = _install_persistent_fake()
    RE, DatabaseRoleHandler, SotFMediaHandler, GameRoleHandler = _RE
    cap_rows = _cap_rows(caps)
    out = {}
    for node in system.node:
        attached = _attached_lan_subnets(system, node)
        my_ip = system.node[node].one
        rows = dict(cap_rows)
        rows[("discovery", "reach_plane")] = [
            {"addr": my_ip, "var": "reach_plane",
             "value": {"wan_subnets": _wan_subnets(system, node), "lan_subnets": attached,
                       "self_ip": my_ip, "ts": int(time.time())}}]
        _STATE["rows"] = rows; _STATE["my_ip"] = my_ip
        def _elect(handler):
            # pass the attached LAN set so the mediahost LAN test is correct
            # (LAN = directly-attached segment, not the wg-plane fallback).
            hosts, lan = RE.gather_candidates(handler, lan_subnets=attached)
            if not hosts and not lan:
                return None
            e = handler.evaluate(hosts, lan)
            return e.get("lan_ip") if e else None
        out[node] = {
            "databasehost": _elect(DatabaseRoleHandler()),
            "mediahost":    _elect(SotFMediaHandler()),
            "gamehost":     _elect(GameRoleHandler()),
        }
    return out


def validate_roles(system, results, caps_by_node):
    problems = []
    dbs = {r["databasehost"] for r in results.values()}
    games = {r["gamehost"] for r in results.values()}
    if len(dbs) > 1:
        problems.append(f"databasehost DISAGREEMENT across nodes: {dbs}")
    if len(games) > 1:
        problems.append(f"gamehost DISAGREEMENT across nodes: {games}")
    # gamehost should be the highest-IP reachable host (no gate)
    from discovery.sim.fabric import _ip_to_int
    all_ones = [nd.one for nd in system.node.values() if not getattr(nd, "dead", False)]
    exp_game = max(all_ones, key=_ip_to_int)
    if games and next(iter(games)) != exp_game:
        problems.append(f"gamehost={games} but highest-IP reachable is {exp_game}")
    # databasehost should be a mysql-capable node
    dbcap = {system.node[n].one for n, c in caps_by_node.items() if c.get("mysql")}
    if dbs and dbcap and next(iter(dbs)) not in dbcap:
        problems.append(f"databasehost={dbs} not among mysql-capable {dbcap}")
    # mediahost must be media-capable and on the electing node's own LAN (never WAN)
    for node, r in results.items():
        m = r["mediahost"]
        if m is None:
            continue
        if m not in {system.node[n].one for n, c in caps_by_node.items() if c.get("media")}:
            problems.append(f"{node}: mediahost {m} not media-capable")
        wan = _wan_subnets(system, node)
        if _slash24(m) in wan:
            problems.append(f"{node}: mediahost {m} is on the WAN plane (must be LAN)")
    return problems


def _slash24(ip):
    return ip.rsplit(".", 1)[0] + ".0/24" if ip else ""


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
        {"NY1": {"mysql": 1, "media": 1, "mem_gb": 32, "cpu": 8, "bench": 9000},
         "Sea5": {"mysql": 1, "media": 1, "mem_gb": 4, "cpu": 4, "bench": 2000},
         "Sea6": {"media": 1}, "Sea3": {"media": 1}})
    scenarios["wgmesh"] = (TopologySpec(nodes=[N("X", "10.60.60"), N("Y", "10.61.61"), N("Z", "10.62.62")],
        tunnels=[("X", "Y"), ("X", "Z"), ("Y", "Z")]),
        {"X": {"mysql": 1, "media": 1, "mem_gb": 8, "cpu": 4, "bench": 3000},
         "Y": {"mysql": 1, "media": 1, "mem_gb": 16, "cpu": 8, "bench": 6000}, "Z": {"media": 1}})
    scenarios["star"] = (TopologySpec(nodes=[N("H", "10.40.40"),
        N("S1", "10.41.41", guest_on=("H", "10.40.40.51")), N("S2", "10.42.42", guest_on=("H", "10.40.40.52"))]),
        {"H": {"mysql": 1, "media": 1, "mem_gb": 8, "cpu": 4, "bench": 3000},
         "S1": {"media": 1}, "S2": {"mysql": 1, "media": 1, "mem_gb": 2}})
    for name, (spec, caps) in scenarios.items():
        total += 1
        s = System(spec); s.converge(max_cycles=15)
        res = elect_all(s, caps); probs = validate_roles(s, res, caps)
        db = next(iter({r["databasehost"] for r in res.values()}))
        gm = next(iter({r["gamehost"] for r in res.values()}))
        if not probs:
            print(f"  PASS  {name:8} databasehost={db} gamehost={gm} mediahost=LAN-local/node"); ok += 1
        else:
            print(f"  FAIL  {name:8} {probs}")
    print(f"\nROLE ELECTION GATE: {'PASS' if ok == total else 'FAIL'} ({ok}/{total})")
    return ok == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
