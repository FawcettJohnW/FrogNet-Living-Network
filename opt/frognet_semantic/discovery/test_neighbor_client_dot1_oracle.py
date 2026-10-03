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
test_neighbor_client_dot1_oracle.py - [NEIGHBOR_CLIENT_DOT1_V1]

Seattle2 (2026-07-20) converged then propagated to 0 peers:
    peer_scan source=direct_neighbors direct_targets_found=0
It is a CLIENT on Seattle3's 10.130.130.0/24 (holds .66/.47, not .1) and reaches
Seattle3 (10.130.130.1) over the kernel connected route + default route. Neither
is a `/24 via` route, so the neighbor scan never derived 10.130.130.1 and the node
notified nobody - no gossip edge, no downstream merge, no sync.

fail-on-old: local addresses on a client subnet yield no neighbor -> [].
pass-on-new: the subnet .1 we are a client on is derived as a LAN neighbor.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.neighbors import direct_neighbor_targets


def main():
    route_text = "\n".join([
        "default via 10.130.130.1 dev eth1 metric 100",
        "10.130.130.0/24 dev eth1 proto kernel scope link src 10.130.130.66 metric 100",
        "10.130.130.0/24 dev wlan1 proto kernel scope link src 10.130.130.47 metric 600",
        "10.120.120.0/24 dev wlan0 proto kernel scope link src 10.120.120.1",
    ])
    local = {"10.130.130.66", "10.130.130.47", "10.120.120.1", "10.120.120.2", "127.0.0.1"}
    got = direct_neighbor_targets(route_text, [], local)

    checks = [
        ("client-subnet upstream .1 (10.130.130.1) is a neighbor", "10.130.130.1" in got),
        ("own-subnet .1 (10.120.120.1) is NOT self-notified", "10.120.120.1" not in got),
        ("exactly the one upstream neighbor", got == ["10.130.130.1"]),
    ]
    print("=== NEIGHBOR CLIENT-.1 ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL NEIGHBOR-CLIENT-DOT1 ORACLE CHECKPOINTS PASS"); return 0
    print("NEIGHBOR-CLIENT-DOT1 ORACLE FAILED"); print("got:", got); return 1


if __name__ == "__main__":
    sys.exit(main())
