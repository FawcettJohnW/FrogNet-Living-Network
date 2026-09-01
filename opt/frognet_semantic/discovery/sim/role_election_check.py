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
sim/role_election_check.py - prove the capability role election consumes the
loopback detector's verdict THROUGH the reach_plane tuple (no re-probe), and that
databasehost elects WAN-inclusive while mediahost stays LAN-only.

Ties to the SAME loop topology the shipped loopcheck uses: O has a tunnel to Rmt;
L is a LAN child of O. From O, Rmt is reached over wg (WAN, trips loopback); L and
O's own /24 are local (LAN). We derive the WAN /24 set from O's converged routes
exactly as discovery.promote() does (wg-egress winners -> computed_wan), publish it
to the reach_plane tuple, then run the REAL election and assert the split.
"""
from __future__ import annotations
import os, sys, types, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.sim.system import System, TopologySpec, NodeSpec
from discovery.sim.fabric import route_egress

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

# ---- derive computed_wan from a node's converged routes (mirrors promote()) -------
def wan_subnets_for(s, node):
    wg_devs = set(s._wg_devs(node).values())         # the wg (tunnel) devs on this node
    dests = set()
    for line in s.routes.get(node, []):
        parts = line.split()
        if parts and "/" in parts[0] and parts[0].startswith("10."):
            dests.add(parts[0])
    wan = set()
    for dest in dests:                               # classify by the WINNER route (promote())
        net = dest.split("/")[0]
        if net.startswith("10.253.") or net.startswith("10.254."):
            continue
        eg = route_egress(s.routes[node], net.rsplit(".", 1)[0] + ".1")
        if eg and eg[0] in wg_devs:                  # winning egress is a tunnel dev -> WAN
            wan.add(dest)
    return sorted(wan)

# ---- stub frognet_tuples: a fake transient with capability + reach_plane rows -----
def install_fake_tuples(my_ip, caps_by_role, reach_wan):
    T = types.ModuleType("frognet_tuples")
    rows = {("discovery", "reach_plane"): [
                {"addr": my_ip, "var": "reach_plane",
                 "value": {"wan_subnets": reach_wan, "self_ip": my_ip,
                           "ts": int(time.time())}}]}
    for role, caps in caps_by_role.items():
        rr = []
        for ip, cap in caps.items():
            rr.append({"addr": ip, "var": "capability",
                       "value": {"capability": cap,
                                 "loadavg": {"1": 0.1}, "temps_c": []}})
        rows[(role, "capability")] = rr
    T.my_ip = lambda: my_ip
    T.get = lambda service, var, dbhost=None, fresh_s=0, timeout=4.0: rows.get((service, var), [])
    T.put = lambda *a, **k: True
    T.DEFAULT_DBHOST = "databasehost.frognet"
    sys.modules["frognet_tuples"] = T

def scenario():
    spec = TopologySpec(
        nodes=[NodeSpec("O", "10.250.250"),
               NodeSpec("L", "10.160.160", guest_on=("O", "10.250.250.221")),
               NodeSpec("Rmt", "10.102.60")],
        tunnels=[("O", "Rmt")],
    )
    s = System(spec); s.converge()
    probs = []

    wan = wan_subnets_for(s, "O")
    if "10.102.60.0/24" not in wan:
        probs.append(f"setup: Rmt /24 should be WAN from O, got wan={wan}")
    if "10.160.160.0/24" in wan or "10.250.250.0/24" in wan:
        probs.append(f"setup: L/own must NOT be WAN, got wan={wan}")
    check("[PLANE] computed_wan from O's routes = the wg-reached /24 (Rmt only)", probs)

    # Rmt is the BEAFIEST box: best for BOTH roles on merit. The only thing keeping it
    # out of media is the LAN/WAN split.
    db_caps = {
        "10.250.250.1": dict(lan_ip="10.250.250.1", mysql_running=1, mem_available_kb=2*1024*1024,
                             cores=2, cpu_bench_total=1500, disk_write_mbps=40, disk_fsync_ms=5),
        "10.160.160.1": dict(lan_ip="10.160.160.1", mysql_running=1, mem_available_kb=1*1024*1024,
                             cores=2, cpu_bench_total=1200, disk_write_mbps=30, disk_fsync_ms=8),
        "10.102.60.1":  dict(lan_ip="10.102.60.1",  mysql_running=1, mem_available_kb=32*1024*1024,
                             cores=8, cpu_bench_total=9000, disk_write_mbps=800, disk_fsync_ms=1),
    }
    media_caps = {
        "10.250.250.1": dict(lan_ip="10.250.250.1", ffmpeg=1, libvpx=1, cores=2, cpu_bench_total=1500),
        "10.160.160.1": dict(lan_ip="10.160.160.1", ffmpeg=1, libvpx=1, cores=2, cpu_bench_total=1200),
        "10.102.60.1":  dict(lan_ip="10.102.60.1",  ffmpeg=1, libvpx=1, cores=8, cpu_bench_total=9000),
    }
    install_fake_tuples("10.250.250.1", {"databasehost": db_caps, "mediahost": media_caps}, wan)

    # import the REAL election + handlers AFTER stubbing tuples
    _root = os.path.abspath(__file__)
    for _ in range(5):
        _root = os.path.dirname(_root)              # .../work
    sys.path.insert(0, os.path.join(_root, "etc", "frognet_bundles", "communicator"))
    # core handlers live under opt/frognet_semantic/core as a package
    import importlib
    for m in ("frognet_role_elect",):
        if m in sys.modules: del sys.modules[m]
    import frognet_role_elect as RE
    from core.database_handler import DatabaseRoleHandler
    from core.sotf_handler import SotFMediaHandler

    probs = []
    h_hosts, h_lan = RE.gather_candidates(DatabaseRoleHandler())
    hip = sorted(c["lan_ip"] for c in h_hosts); lip = sorted(c["lan_ip"] for c in h_lan)
    if hip != ["10.102.60.1", "10.160.160.1", "10.250.250.1"]:
        probs.append(f"hosts_list (DB) should be all 3, got {hip}")
    if lip != ["10.160.160.1", "10.250.250.1"]:
        probs.append(f"lan_list (media) should exclude WAN Rmt, got {lip}")
    check("[GATHER] DB sees all 3 (WAN-incl); media sees LAN-only 2 (Rmt excluded)", probs)

    probs = []
    db = RE.elect(DatabaseRoleHandler())
    me = RE.elect(SotFMediaHandler())
    if not (db and db["lan_ip"] == "10.102.60.1"):
        probs.append(f"DB should elect the beefy WAN box Rmt, got {db and db.get('lan_ip')}")
    if not (me and me["lan_ip"] in ("10.250.250.1", "10.160.160.1")):
        probs.append(f"media must elect a LAN box, never WAN Rmt, got {me and me.get('lan_ip')}")
    if me and me["lan_ip"] == "10.102.60.1":
        probs.append("media elected the WAN box - LAN/WAN split FAILED")
    check("[ELECT] databasehost=WAN Rmt (best overall); mediahost=LAN (Rmt barred)", probs)

def main():
    print("=== ROLE ELECTION over the reach_plane tuple (loopback verdict, no re-probe) ===")
    scenario()
    print("\n" + ("ALL ROLE-ELECTION CHECKS PASS" if not FAILS
                  else f"ROLE-ELECTION CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
