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
"""frogsim_topologies.py - REAL-engine convergence gate across topology classes.

Each topology is run through the ACTUAL discovery engine (System drives real
merge/promote/install per machine, cycles to a stable fixpoint), then validated:
every node sees every subnet AND all-pairs reachable over the installed routes.
This is real convergence, not a model. Run:  python3 frogsim_topologies.py
(needs FROGNET_SEMANTIC_ROOT on the worktree; sets FROGNET_ROUTE_ALIVE_STORE).
"""
import os, sys
os.environ.setdefault("FROGNET_ROUTE_ALIVE_STORE", "/tmp/frogsim_topos.tsv")
WT = os.environ.get("FROGNET_SEMANTIC_ROOT",
                    "/home/claude/descend_good/opt/frognet_semantic")
sys.path.insert(0, WT)
from discovery.sim.system import System, TopologySpec, NodeSpec as N


def g(nm, s3, host, lease):
    return N(nm, s3, guest_on=(host, lease))


def topologies():
    T = {}
    T["2node_LAN"] = TopologySpec([N("A", "10.10.10"), g("B", "10.11.11", "A", "10.10.10.50")])
    T["3chain"] = TopologySpec([N("G", "10.20.20"), g("M", "10.21.21", "G", "10.20.20.50"),
                                g("L", "10.22.22", "M", "10.21.21.50")])
    T["5chain"] = TopologySpec([N("N1", "10.30.30"), g("N2", "10.31.31", "N1", "10.30.30.50"),
                                g("N3", "10.32.32", "N2", "10.31.31.50"), g("N4", "10.33.33", "N3", "10.32.32.50"),
                                g("N5", "10.34.34", "N4", "10.33.33.50")])
    T["6chain"] = TopologySpec([N("D1", "10.150.10"), g("D2", "10.150.20", "D1", "10.150.10.50"),
                                g("D3", "10.150.30", "D2", "10.150.20.50"), g("D4", "10.150.40", "D3", "10.150.30.50"),
                                g("D5", "10.150.50", "D4", "10.150.40.50"), g("D6", "10.150.60", "D5", "10.150.50.50")])
    T["4star"] = TopologySpec([N("H", "10.40.40"), g("S1", "10.41.41", "H", "10.40.40.51"),
                               g("S2", "10.42.42", "H", "10.40.40.52"), g("S3", "10.43.43", "H", "10.40.40.53")])
    T["snowflake"] = TopologySpec([N("R", "10.50.50"), g("A", "10.51.51", "R", "10.50.50.51"),
                                   g("B", "10.52.52", "R", "10.50.50.52"), g("A1", "10.53.53", "A", "10.51.51.61"),
                                   g("B1", "10.54.54", "B", "10.52.52.61")])
    T["4ring_wg"] = TopologySpec([N("R1", "10.110.10"), N("R2", "10.110.20"), N("R3", "10.110.30"), N("R4", "10.110.40")],
                                 tunnels=[("R1", "R2"), ("R2", "R3"), ("R3", "R4"), ("R4", "R1")])
    T["3site_wgmesh"] = TopologySpec([N("X", "10.60.60"), N("Y", "10.61.61"), N("Z", "10.62.62")],
                                     tunnels=[("X", "Y"), ("X", "Z"), ("Y", "Z")])
    T["4gw_mesh"] = TopologySpec([N("W", "10.120.10"), N("Xg", "10.120.20"), N("Yg", "10.120.30"), N("Zg", "10.120.40")],
                                 tunnels=[("W", "Xg"), ("W", "Yg"), ("W", "Zg"), ("Xg", "Yg"), ("Xg", "Zg"), ("Yg", "Zg")])
    T["hub_spoke_wg"] = TopologySpec([N("HX", "10.70.70"), N("HY", "10.71.71"), N("HZ", "10.72.72")],
                                     tunnels=[("HX", "HY"), ("HX", "HZ")])
    T["mixed_lan_wg"] = TopologySpec([N("GW", "10.80.80"), g("LC", "10.81.81", "GW", "10.80.80.50"), N("RM", "10.82.82")],
                                     tunnels=[("GW", "RM")])
    T["asym_lan_chain"] = TopologySpec([N("GA", "10.90.90"), g("A2", "10.91.91", "GA", "10.90.90.50"),
                                        N("GB", "10.92.92"), g("B2", "10.93.93", "GB", "10.92.92.50"),
                                        g("B3", "10.94.94", "B2", "10.93.93.50")], tunnels=[("GA", "GB")])
    T["asym_star_chain"] = TopologySpec([N("SH", "10.100.100"), g("SS1", "10.101.101", "SH", "10.100.100.51"),
                                         g("SS2", "10.102.102", "SH", "10.100.100.52"),
                                         N("CH", "10.103.103"), g("CM", "10.104.104", "CH", "10.103.103.50")],
                                        tunnels=[("SH", "CH")])
    T["wlan0_ap"] = TopologySpec([N("AP", "10.130.10"), g("C1", "10.131.10", "AP", "10.130.10.50"),
                                  g("C2", "10.132.10", "AP", "10.130.10.51")])
    T["ham_peered"] = TopologySpec([N("HA", "10.140.10"), g("HAc", "10.141.10", "HA", "10.140.10.50"),
                                    N("HB", "10.142.10"), g("HBc", "10.143.10", "HB", "10.142.10.50")],
                                   tunnels=[("HA", "HB")])
    T["oddball"] = TopologySpec([N("O1", "10.7.199"), g("O2", "10.253.8", "O1", "10.7.199.99"), N("O3", "10.99.1")],
                                tunnels=[("O1", "O3")])
    # shared peering LAN (.2 reserved as admin alias, per FrogNet convention)
    T["lan_triangle"] = TopologySpec([N("TX", "10.60.0", shared_lan=("10.60.99", "10.60.99.1")),
                                      N("TY", "10.61.0", shared_lan=("10.60.99", "10.60.99.3")),
                                      N("TZ", "10.62.0", shared_lan=("10.60.99", "10.60.99.4"))])
    return T


def seattle_chain():
    from discovery.sim.lan_chain import build_seattle_chain
    return build_seattle_chain()


def run():
    cases = dict(topologies()); cases["seattle_chain"] = seattle_chain()
    ok = 0
    for name, spec in cases.items():
        s = System(spec); cyc = s.converge(max_cycles=25); probs = s.validate()
        nn = len(spec.nodes)
        if not probs:
            print(f"  PASS  {name:16} {nn:2}n {cyc:2}cyc - all see all + all-pairs reachable"); ok += 1
        else:
            print(f"  FAIL  {name:16} {len(probs)} problems e.g. {probs[0]}")
    total = len(cases)
    print(f"\nTOPOLOGY CONVERGENCE GATE: {'PASS' if ok == total else 'FAIL'} "
          f"({ok}/{total} real-engine convergences)")
    return ok == total


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
