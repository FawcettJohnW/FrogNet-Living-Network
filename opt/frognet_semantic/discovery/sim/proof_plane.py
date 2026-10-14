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
proof_plane.py - the standard proof run for replacing bash discovery: declared
topologies (snake/ring/star/snowflake), multi-LAN-over-Internet, and change-
driven churn + mobility (remove/add, move-LAN, re-IP, rename). Every scenario
builds the topology, runs the REAL ported discovery to convergence, and
validates full host set + all-pairs reachability.

Boundary: discovery + proxy route-planning are real code; the tunnel graph (which
node has a channel to which) is modeled as the INPUT, not executed. Multi-`.1`-host
L2 segments are not modeled (need real source).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.sim.system import System, TopologySpec, NodeSpec
from discovery.sim import shapes
from discovery.sim.fabric import route_egress

FAILS = []

def check(name, problems):
    ok = not problems
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    for p in problems[:4]:
        print(f"           - {p}")
    if not ok:
        FAILS.append(name)

def cv(label, spec, cap=16):
    s = System(spec); c = s.converge(cap); probs = s.validate()
    if c >= cap:
        probs = probs + [f"did not converge within {cap} cycles"]
    check(f"{label} ({len(spec.nodes)}n,{c}cyc)", probs)
    return s

def main():
    print("=== SHAPES (real ported discovery over the tunnel graph) ===")
    cv("snake(5)", shapes.snake(5)); cv("snake(8)", shapes.snake(8))
    cv("ring(6)", shapes.ring(6));   cv("ring(9)", shapes.ring(9))
    cv("star(6)", shapes.star(6))
    cv("snowflake(3x3)", shapes.snowflake(3, 3))
    cv("snowflake(4x2)", shapes.snowflake(4, 2))

    print("\n=== MULTI-LAN OVER INTERNET (LAN-to-LAN via gateways) ===")
    s = cv("two LANs via StreamingFrog gateways", shapes.two_lan_over_internet())
    check("cross-LAN: A(LAN-1) reaches B(LAN-2)",
          [] if s._reaches("A", "10.4.4.1") else ["A cannot reach B across the Internet"])

    print("\n=== CHURN + MOBILITY (discovery is change-driven) ===")
    # remove/add on a star
    s = System(shapes.star(5)); s.converge()
    check("star baseline", s.validate())
    s.remove_node("N3"); s.converge()
    check("remove N3 -> gone everywhere",
          [] if all("10.13.13" not in s.known[n] for n in s.known) else ["N3 lingering"])
    check("survivors still converged", s.validate())
    s.add_node(NodeSpec("N3", "10.13.13"), tunnels=[("N0", "N3")]); s.converge()
    check("re-add N3 -> rediscovered", s.validate())

    # node moves LANs (disappears from one, reappears on another)
    s = System(shapes.two_lan_over_internet()); s.converge()
    s.move_node("A", new_guest_on=("GW2", "10.3.3.231")); s.converge()
    check("move A from LAN-1 to LAN-2 (reachable in new LAN + cross-Internet)",
          [] if (s._reaches("B", "10.2.2.1") and s._reaches("GW1", "10.2.2.1"))
          else ["A not reachable after move"])
    check("validate after move", s.validate())

    # node changes IP (re-IP its served /24)
    s = System(shapes.star(4)); s.converge()
    s.reip_node("N2", "10.200.200"); s.converge()
    check("re-IP N2: old /24 gone + new /24 known everywhere",
          ([] if all("10.12.12" not in s.known[n] for n in s.known)
           and all("10.200.200" in s.known[n] for n in s.known)
           else ["re-IP not fully propagated"]))
    check("validate after re-IP", s.validate())

    # node changes name (Pond/identity churn at the name level)
    s = System(shapes.star(4)); s.converge()
    s.rename_node("N1", "N1b"); s.converge()
    check("rename N1->N1b: no reachability regression", s.validate())

    print("\n=== ADMIN-CHANNEL LOOP + DUPLICATE-IP PROBE ===")
    from discovery.sim import loopcheck
    loopcheck.FAILS = []
    loopcheck.loop_scenario()
    loopcheck.valid_long_path_scenario()
    loopcheck.dup_collision_scenario()
    loopcheck.dup_unique_scenario()
    loopcheck.emitter_wiring_scenario()
    FAILS.extend(loopcheck.FAILS)

    # [NO_LIFECYCLE_AT_INSTALL_V1] The E2E lifecycle scenarios are NOT part of
    # the proof plane, because the proof plane is the installer's pre-activation
    # gate.  The lifecycle harness drives the REAL merge, which reads the REAL
    # box -- /etc/hosts, /etc/sentinels, /etc/frognet.  Mid-install that state is
    # the PREVIOUS install's: neither the old network nor the new one.  The
    # harness then validates a 3-node fabric against next hops belonging to a
    # network being replaced, and fails on the box's history instead of on the
    # code.  Verified: the identical scenario passes on a machine with no prior
    # FrogNet and fails on one that has one.
    #
    # Run them deliberately, pre-build, on a clean tree:
    #     cd /opt/frognet_semantic && PYTHONPATH=. python3 -m discovery.sim.lifecycle
    #
    # Everything above this point is fabric-only and stays in the gate.
    print("\n=== END-TO-END LIFECYCLE ===")
    print("  [SKIP] lifecycle scenarios (development gate; not valid mid-install)")

    print("\n" + ("ALL PROOF-PLANE SCENARIOS PASS" if not FAILS
                  else f"PROOF PLANE FAILED: {FAILS}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
