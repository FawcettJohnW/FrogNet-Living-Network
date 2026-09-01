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
loop_scenarios.py - exercise back-vouch / split-horizon loops across topologies.

For each scenario: converge the System, then for EVERY installed /24 route on
EVERY node, reflect-trace it (origin = that node) and classify:
    OK    - reaches the destination
    LOOP  - hairpins back through the origin (a back-vouch loop: the bug)
    DEAD  - no path / times out
A scenario is CLEAN iff no installed route is a LOOP.

This is the oracle the split-horizon fix must satisfy on every topology.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.sim.system import TopologySpec, NodeSpec, System
from discovery.sim import system as S


def _reflect_installed(sysm, origin, dest_net):
    """origin sends a probe for dest_net's .1 along origin's OWN installed route;
    classify OK / LOOP / DEAD exactly like the shipped reflect handler."""
    target = dest_net + ".1"
    o_ips = _node_ips(sysm, origin)
    cur, c, seen = origin, 0, 0
    while seen <= 16:
        seen += 1
        nd = sysm.node[cur]
        if nd.subnet3 == dest_net:               # target local -> reached
            return "OK"
        if c > 0 and _node_ips(sysm, cur) & o_ips:   # origin local & forwarded -> loop
            return "LOOP"
        eg = S.route_egress(sysm.routes[cur], target)
        if not eg:
            return "DEAD"
        dev, _m, via = eg
        if via:
            nxt = sysm._ip_node(via)
        elif dev.startswith("wg"):
            dev_peer = {d: p for p, d in sysm._wg_devs(cur).items()}
            nxt = dev_peer.get(dev)
        else:
            owner = sysm._owner_of(target)
            return "OK" if owner else "DEAD"
        if not nxt or nxt == cur:
            return "OK" if sysm._owner_of(target) else "DEAD"
        c += 1
        cur = nxt
    return "LOOP"


def _node_ips(sysm, name):
    nd = sysm.node[name]
    s = {nd.one}
    if nd.guest_on:
        s.add(nd.guest_on[1])
    if getattr(nd, "shared_lan", None):
        s.add(nd.shared_lan[1])
    return s


def audit(sysm):
    """Return list of (node, dest, via, verdict) for every installed /24, and the
    set of LOOP offenders."""
    rows, loops = [], []
    for name in sysm.node:
        for line in sysm.routes[name]:
            p = line.split()
            dest = p[0]
            if not dest.endswith("/24") or not dest.startswith("10."):
                continue
            net = dest[:-5]
            if net == sysm.node[name].subnet3:      # own connected subnet
                continue
            via = p[p.index("via") + 1] if "via" in p else "-"
            v = _reflect_installed(sysm, name, net)
            rows.append((name, dest, via, v))
            if v == "LOOP":
                loops.append((name, dest, via))
    return rows, loops


# ---- scenarios ------------------------------------------------------------
def chain(depth, with_remote=False):
    """Linear guest chain of `depth` LAN hops to a gateway; optional tunnel remote."""
    subs = ["10.250.250", "10.130.130", "10.160.160", "10.120.120", "10.140.140"]
    names = ["Seattle5", "Seattle3", "Seattle6", "Seattle2", "SeattleX"]
    nodes = [NodeSpec(names[0], subs[0])]                      # gateway
    for i in range(1, depth + 1):
        host = names[i - 1]
        lease = f"{subs[i-1]}.{190 + i}"
        nodes.append(NodeSpec(names[i], subs[i], guest_on=(host, lease)))
    tunnels = []
    if with_remote:
        nodes.append(NodeSpec("NY1", "10.102.60"))
        tunnels.append(("Seattle5", "NY1"))
    return TopologySpec(nodes=nodes, tunnels=tunnels), f"chain(depth={depth}, remote={with_remote})"


def multihomed_remote():
    """Chain to gateway + tunnel remote + a SECOND tunnel remote (fan-out)."""
    g = NodeSpec("Seattle5", "10.250.250")
    s3 = NodeSpec("Seattle3", "10.130.130", guest_on=("Seattle5", "10.250.250.191"))
    s2 = NodeSpec("Seattle2", "10.120.120", guest_on=("Seattle3", "10.130.130.47"))
    ny1 = NodeSpec("NY1", "10.102.60"); ny2 = NodeSpec("NY2", "10.28.28")
    return (TopologySpec(nodes=[g, s3, s2, ny1, ny2],
                         tunnels=[("Seattle5", "NY1"), ("Seattle5", "NY2")]),
            "multihomed_remote (2 tunnel remotes behind gw, 2 LAN hops)")


def SCENARIOS():
    return [chain(2), chain(3), chain(4),
            chain(2, with_remote=True), chain(3, with_remote=True),
            multihomed_remote()]


def run():
    total_loops = 0
    for spec, label in SCENARIOS():
        sysm = System(spec); sysm.converge()
        rows, loops = audit(sysm)
        # also settle the PRODUCTION way (notification propagation, not brute force)
        sysm2 = System(spec); ev, quiesced = sysm2.converge_by_notification()
        _, loops2 = audit(sysm2)
        total_loops += len(loops) + len(loops2)
        nfail = "" if (quiesced and not loops2) else \
                f"  [NOTIF {'no-quiesce' if not quiesced else str(len(loops2))+' loops'}]"
        status = "CLEAN" if not loops else f"{len(loops)} LOOP(S)"
        print(f"[{status:>10}] {label}   (notif: {ev} events, quiesced={quiesced}, "
              f"loops={len(loops2)}){nfail}")
        for n, d, via in loops:
            print(f"              LOOP: {n} routes {d} via {via} (hairpins back to {n})")
    print(f"\nTOTAL back-vouch loops (brute-force + notification) across "
          f"{len(SCENARIOS())} scenarios: {total_loops}")
    return total_loops


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
