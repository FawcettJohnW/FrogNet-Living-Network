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
"""
live_failures.py - failure modes through the REAL planner+committer.

M1 (live_engine) validated steady-state reachability. This validates the other
half of the goal: what the installed planner+committer do when the network
BREAKS. For each scenario it converges a baseline, kills a node or link, then
re-converges through the real engine and asserts the right post-failure state:

  - reroute:   survivors still reach each other when an alternate path exists.
  - black hole: the dead node's /24 is gone everywhere (no phantom route to it).
  - partition: when the kill splits the graph, within-partition pairs still
               reach and cross-partition pairs are HONESTLY unreachable (no
               false reach, no loop that pretends to deliver).

All reachability is judged on the REAL committed tables (live_engine mirrors
them into the trace model). Fresh re-converge models steady state after the
failure - the property that matters for "will it still work in production."
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from frognet_log import get_logger
import live_engine as LE
import frognet_sim as H

log = get_logger("simulation.live_failures")
FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


def _subnet24(ip):
    return ip.rsplit(".", 1)[0] + ".0/24"


def kill_node(topo, name):
    """Remove a node and every segment membership referencing it. Returns the
    dead node's (frognet_ip, subnet24) so callers can assert it's unreachable."""
    H.assign_identities(topo)
    dead = topo.nodes[name]
    dead_ip, dead_net = dead.frognet_ip, _subnet24(dead.frognet_ip)
    del topo.nodes[name]
    new_segs = []
    for seg in topo.segments:
        members = [(n, i) for (n, i) in seg if n != name]
        if len(members) >= 2:               # a segment needs >=2 members to matter
            new_segs.append(members)
    topo.segments = new_segs
    return dead_ip, dead_net


def drop_link(topo, a, b):
    """Remove the tunnel segment between nodes a and b (a ring-direction cut)."""
    topo.segments = [seg for seg in topo.segments
                     if not ({n for n, _i in seg} == {a, b})]


def _delivers(topo, src, dst_ip, dst_name):
    return H.trace_packet(topo, src, dst_ip)[-1] == dst_name


def _no_phantom_to(topo, dead_net):
    """No survivor should hold a /24 route to the dead node's subnet."""
    for n in topo.nodes.values():
        for r in n.routes:
            if r.dest == dead_net:
                return False, n.name
    return True, ""


def scenario_reroute_ring():
    print("ring(4): kill one node -> survivors reroute the other way")
    t = H.topo_ring_4()
    LE.converge_real(t)
    check("baseline ring all-pairs reach", not H.verify_reachability(t)[2])
    dead_ip, dead_net = kill_node(t, "B")
    LE.converge_real(t)
    ok, total, fails = H.verify_reachability(t)
    check("after kill B: 3 survivors still all reach (rerouted)", not fails, str(fails[:2]))
    nop, who = _no_phantom_to(t, dead_net)
    check("after kill B: no phantom route to B's /24 (black hole)", nop, f"held by {who}")


def scenario_reroute_broker_pond():
    print("broker pond (real broker mesh): kill a member -> mesh reroutes")
    import broker_topology as BT
    try:
        BT.load_broker()
    except Exception as e:
        print(f"  [SKIP] broker unavailable: {e}")
        return
    t, edges = BT.build_topology(
        [("M1", "10.71.1"), ("M2", "10.71.2"), ("M3", "10.71.3"), ("M4", "10.71.4")],
        label="failure pond")
    LE.converge_real(t)
    check("baseline broker pond all-pairs reach", not H.verify_reachability(t)[2])
    dead_ip, dead_net = kill_node(t, "M3")
    LE.converge_real(t)
    fails = H.verify_reachability(t)[2]
    check("after kill M3: survivors still all reach (full-mesh reroute)", not fails, str(fails[:2]))
    nop, who = _no_phantom_to(t, dead_net)
    check("after kill M3: no phantom route to M3's /24", nop, f"held by {who}")


def scenario_partition_chain():
    print("chain(5): kill the middle node -> honest partition")
    t = H.topo_chain_5()
    LE.converge_real(t)
    check("baseline chain all-pairs reach", not H.verify_reachability(t)[2])
    # chain is A-B-C-D-E; kill C -> {A,B} | {D,E}
    names = list(t.nodes)
    mid = names[len(names) // 2]
    left, right = names[:names.index(mid)], names[names.index(mid) + 1:]
    a_ips = {n: t.nodes[n].frognet_ip for n in t.nodes}
    dead_ip, dead_net = kill_node(t, mid)
    LE.converge_real(t)
    # within-partition reach
    within_ok = True
    if len(left) >= 2:
        within_ok &= _delivers(t, left[0], a_ips[left[-1]], left[-1])
    if len(right) >= 2:
        within_ok &= _delivers(t, right[0], a_ips[right[-1]], right[-1])
    check(f"within-partition pairs still reach (kill {mid})", within_ok)
    # cross-partition must NOT reach (honest unreachability, no false reach)
    cross_false = _delivers(t, left[0], a_ips[right[0]], right[0])
    check("cross-partition pair is honestly UNREACHABLE (no false reach)", not cross_false,
          f"{left[0]} falsely reached {right[0]}")
    nop, who = _no_phantom_to(t, dead_net)
    check(f"no phantom route to killed {mid}'s /24", nop, f"held by {who}")


def main():
    print("=== FAILURE MODES through REAL planner+committer ===")
    FAILS.clear()
    scenario_reroute_ring()
    scenario_reroute_broker_pond()
    scenario_partition_chain()
    print()
    if FAILS:
        print(f"FAILURE-MODE TESTS FAILED: {FAILS}")
        return 1
    print("ALL FAILURE-MODE TESTS PASS (reroute + black-hole + honest partition)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
