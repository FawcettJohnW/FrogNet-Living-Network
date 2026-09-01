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
test_direct_neighbors_oracle.py - [NEIGHBOR_SCOPE_V1] regression.

Proves the gossip target set is exactly the directly-attached neighbors (LAN
route next-hops on physical devs + WG tunnel-peer .1s), using the real route
tables captured from NY1, Seattle5, and the 10.120.120 leaf.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.neighbors import direct_neighbor_targets


# Real NY1 table (WAN uplink eth1; NY2 downstream on eth0; 3 tunnels).
NY1 = """default via 192.168.1.1 dev eth1 metric 601
10.28.28.0/24 via 10.102.60.230 dev eth0 src 10.102.60.1 metric 22
10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1
10.111.11.0/24 via 10.102.60.230 dev eth0 src 10.102.60.1 metric 22
10.130.130.0/24 via 10.102.60.230 dev eth0 src 10.102.60.1 metric 22
10.130.130.0/24 dev wg2 scope link src 10.102.60.1 metric 100
10.179.179.0/24 dev wg1 scope link src 10.102.60.1 metric 22
10.250.250.0/24 via 10.102.60.230 dev eth0 src 10.102.60.1 metric 22
10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90
10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4
192.168.1.0/24 dev eth1 proto kernel scope link src 192.168.1.245"""
NY1_CHANS = ["Seattle5-10.250.250", "BAMacBook-10.179.179", "BABox-10.111.11"]
NY1_LOCAL = {"10.102.60.1", "127.0.0.1"}

# 10.120.120 LAN leaf - uplinks to Seattle3 (10.130.130.1) over wlan1, no tunnels.
LEAF = """default via 10.130.130.1 dev wlan1 proto dhcp src 10.130.130.47 metric 600
10.28.28.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22
10.102.60.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22
10.111.11.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22
10.120.120.0/24 dev wlan0 proto kernel scope link src 10.120.120.1
10.130.130.0/24 dev wlan1 scope link src 10.120.120.1 metric 22
10.130.130.0/24 dev wlan1 proto kernel scope link src 10.130.130.47 metric 600
10.160.160.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22
10.179.179.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22
10.250.250.0/24 via 10.130.130.1 dev wlan1 src 10.120.120.1 metric 22"""
LEAF_LOCAL = {"10.120.120.1", "10.130.130.47", "127.0.0.1"}

# Seattle5 - serves 10.250.250 on eth0; Seattle6 downstream at .221; 2 tunnels.
S5 = """default via 192.168.0.1 dev wlan1 metric 600
10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1
10.160.160.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 22
10.130.130.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 22
10.120.120.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 22
10.179.179.0/24 dev wg1 scope link src 10.250.250.1 metric 22
10.102.60.0/24 dev wg2 scope link src 10.250.250.1 metric 22"""
S5_CHANS = ["BAMacBook-10.179.179", "New-York-1-10.102.60"]
S5_LOCAL = {"10.250.250.1", "127.0.0.1"}


def main():
    ok = True

    def check(got, want, label):
        nonlocal ok
        cond = got == want
        print(("  PASS " if cond else "  FAIL ") + label)
        if not cond:
            print(f"        got={got}\n        want={want}")
        ok = ok and cond

    # NY1: NY2 LAN next-hop + 3 tunnel peers; relayed Seattle subnets fall out.
    check(direct_neighbor_targets(NY1, NY1_CHANS, NY1_LOCAL),
          sorted({"10.102.60.230", "10.250.250.1", "10.179.179.1", "10.111.11.1"}),
          "NY1 -> NY2(LAN) + 3 tunnel peers (no relayed dests)")

    # Leaf: only its single uplink neighbor.
    check(direct_neighbor_targets(LEAF, [], LEAF_LOCAL),
          ["10.130.130.1"],
          "leaf -> single uplink neighbor only")

    # Seattle5: Seattle6 downstream LAN neighbor + 2 tunnel peers.
    check(direct_neighbor_targets(S5, S5_CHANS, S5_LOCAL),
          sorted({"10.250.250.221", "10.179.179.1", "10.102.60.1"}),
          "Seattle5 -> Seattle6(LAN) + 2 tunnel peers")

    # [NEIGHBOR_FROGNET_ADDR_V1] With discovery's seg_relay map, a downstream
    # child's DHCP next-hop is translated to its FrogNet .1; tunnel peers and
    # upstream .1 next-hops are unaffected.
    check(direct_neighbor_targets(S5, S5_CHANS, S5_LOCAL,
                                  via_identity={"10.250.250.221": "10.160.160.1"}),
          sorted({"10.160.160.1", "10.179.179.1", "10.102.60.1"}),
          "Seattle5 -> Seattle6 FrogNet .1 (not DHCP .221) + tunnels")

    # [NEIGHBOR_TUNNEL_ROUTE_GATE_V1] A dead BABox tunnel (channel present, but
    # 10.111.11.0/24 reaped - crashed/disconnected/renamed) is dropped; the live
    # tunnels and the translated LAN neighbor remain.
    check(direct_neighbor_targets(S5, S5_CHANS + ["BABox-10.111.11"], S5_LOCAL,
                                  via_identity={"10.250.250.221": "10.160.160.1"}),
          sorted({"10.160.160.1", "10.179.179.1", "10.102.60.1"}),
          "Seattle5 -> dead BABox tunnel (no /24 route) dropped; healthy kept")

    # NY1: NY2's DHCP lease 10.102.60.230 is the next-hop for NY2's own subnet AND
    # the subnets it relays; all collapse to the one neighbor NY2 = 10.28.28.1.
    check(direct_neighbor_targets(NY1, NY1_CHANS, NY1_LOCAL,
                                  via_identity={"10.102.60.230": "10.28.28.1"}),
          sorted({"10.28.28.1", "10.250.250.1", "10.179.179.1", "10.111.11.1"}),
          "NY1 -> NY2 FrogNet .1 (not DHCP .230); tunnel peers unchanged")

    # An upstream next-hop is already a .1 identity -> no map entry, unchanged.
    check(direct_neighbor_targets(LEAF, [], LEAF_LOCAL, via_identity={}),
          ["10.130.130.1"],
          "leaf upstream .1 identity unchanged (no DHCP translation needed)")

    # local IPs are never targeted.
    check(direct_neighbor_targets(NY1, NY1_CHANS, NY1_LOCAL | {"10.102.60.230"}),
          sorted({"10.250.250.1", "10.179.179.1", "10.111.11.1"}),
          "local-address next-hop is filtered out")

    # [NEIGHBOR_FROGNET_ADDR_V1] end-to-end: the REAL walk must populate
    # neighbor_via_map from the seg_relay echo, and the scan must translate.
    from discovery.sim.topology import build_ny1
    from discovery.orchestrate import merge as _merge
    _disc, _k, _seeds, _up = build_ny1()
    _out = _merge(_disc, _k, _seeds, _up, local=("10.102.60.1", "New-York-1"),
                  bringup_peers=[], logger=lambda s: None)
    check(dict(_disc.neighbor_via_map), {"10.102.60.230": "10.28.28.1"},
          "real walk captures NY2 DHCP lease -> FrogNet .1 (seg_relay echo)")
    _rt = "\n".join(_out["final_table"])
    check(direct_neighbor_targets(_rt, ["BABox-10.111.11"],
                                  {"10.102.60.1", "127.0.0.1"},
                                  via_identity=_disc.neighbor_via_map),
          ["10.28.28.1"],
          "end-to-end: NY2 at FrogNet .1; dead BABox tunnel (no /24) dropped")

    print()
    print("ALL DIRECT-NEIGHBOR CHECKPOINTS PASS" if ok
          else "DIRECT-NEIGHBOR PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
