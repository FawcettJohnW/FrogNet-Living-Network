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
"""frogsim_runagain - [DESCEND_FRONTIER_FROM_KNOWN_V1] proof on the real engine.

The crawl is two levels per pass (children + grandchildren). DEPTH must come from
re-merging: each runAgain pass has to advance the frontier edge two hops deeper. That
only works if the frontier is seeded from the installed /24 routes, not just the immediate
link. This drives NY1's real merge repeatedly against ONE persistent kernel (a runAgain
re-run on a box), with every node frozen knowing ONLY its immediate neighbour (no cross-
node propagation), and asserts NY1 walks NY1-Sea5-Sea6-Sea3-Sea2 to the end and then stops
with runAgain=0. With the frontier seeded from the immediate only, it must stall at Sea3.
"""
import os, sys
sys.path.insert(0, os.environ.get("FROGNET_SEMANTIC_ROOT",
                                  "/home/claude/descend_good/opt/frognet_semantic"))
os.environ.setdefault("FROGNET_ROUTE_ALIVE_STORE", "/tmp/frogsim_runagain.tsv")
os.environ.pop("FROGNET_DESCEND_MAX_LEVELS", None)   # design default = 2 levels
from discovery.sim.system import System, TopologySpec, NodeSpec as N
from discovery.kernel import FakeKernel

CHAIN = ["NY1", "Sea5", "Sea6", "Sea3", "Sea2"]
SUB = {"10.102.60": "NY1", "10.250.250": "Sea5", "10.160.160": "Sea6",
       "10.130.130": "Sea3", "10.120.120": "Sea2"}

def _spec():
    return TopologySpec(nodes=[
        N("NY1", "10.102.60"), N("Sea5", "10.250.250"),
        N("Sea6", "10.160.160", guest_on=("Sea5", "10.250.250.222")),
        N("Sea3", "10.130.130", guest_on=("Sea6", "10.160.160.191")),
        N("Sea2", "10.120.120", guest_on=("Sea3", "10.130.130.47")),
    ], tunnels=[("NY1", "Sea5")])

def _frozen(s):
    # every node knows ONLY its immediate downstream -> no cross-node propagation
    s.known = {"NY1": {"10.102.60"}, "Sea5": {"10.250.250", "10.160.160"},
               "Sea6": {"10.160.160", "10.130.130"}, "Sea3": {"10.130.130", "10.120.120"},
               "Sea2": {"10.120.120"}}

def _runagain_to_fixpoint(frontier_from_routes):
    os.environ["FROGNET_DESCEND_FRONTIER_FROM_ROUTES"] = "1" if frontier_from_routes else "0"
    s = System(_spec()); _frozen(s)
    k = FakeKernel(); prev = None
    for p in range(1, 9):
        out = s.run_node_on_kernel("NY1", k, seed_synthetic=(p == 1))
        routes = {l.split()[0] for l in out["final_table"] if l.split()[0].endswith("/24")}
        if routes == prev:                       # routes_mutated == 0 -> runAgain == 0
            break
        prev = routes
    known = {".".join(r.split(".")[:3]) for r in prev}; known.add("10.102.60")
    return known, p

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

def main():
    print("=== DESCEND FRONTIER / runAgain ORACLE (real engine, frozen neighbours) ===")
    old, _ = _runagain_to_fixpoint(frontier_from_routes=False)
    check(f"OLD (frontier=immediate only) stalls before Sea2 ({sorted(SUB.get(x,x) for x in old)})",
          "10.120.120" not in old)
    new, passes = _runagain_to_fixpoint(frontier_from_routes=True)
    check(f"NEW reaches Sea2 over runAgain re-runs ({sorted(SUB.get(x,x) for x in new)})",
          "10.120.120" in new)
    check("NEW learns the whole chain", {"10.250.250","10.160.160","10.130.130","10.120.120"} <= new)
    check(f"NEW converges in a bounded number of passes ({passes})", passes <= 5)
    print("\n" + ("ALL RUNAGAIN-FRONTIER CHECKS PASS" if not FAILS
                  else f"RUNAGAIN-FRONTIER ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
