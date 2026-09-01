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
"""[TRANSIT_FROM_ROUTES_V1] oracle for discover_transit_subnets' route parser.
Run: python3 test_transit_from_routes.py
Self-contained: extracts _transit_from_routes from config.py source so it
does not depend on the module's runtime imports.
"""
import os, re

def _load():
    src = open(os.path.join(os.path.dirname(__file__), "config.py")).read()
    m = re.search(r"def _transit_from_routes.*?\n    return sorted\(out\)\n",
                  src, re.S)
    ns = {}
    exec(m.group(0), ns)
    return ns["_transit_from_routes"]

def main():
    f = _load()
    # New-York-1 — real `ip r` (2026-06-05 runMerge transcript)
    ny1 = ("default via 192.168.1.1 dev eth1 metric 601\n"
           "10.28.28.0/24 via 10.102.60.230 dev eth0 src 10.102.60.1 metric 22\n"
           "10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1\n"
           "10.111.11.0/24 dev wg2 scope link src 10.102.60.1 metric 22\n"
           "10.120.120.0/24 dev wg2 scope link src 10.102.60.1 metric 22\n"
           "10.130.130.0/24 dev wg2 scope link src 10.102.60.1 metric 22\n"
           "10.160.160.0/24 dev wg2 scope link src 10.102.60.1 metric 22\n"
           "10.179.179.0/24 dev wg1 scope link metric 22\n"
           "10.250.250.0/24 dev wg2 scope link metric 22\n"
           "10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90\n"
           "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4\n"
           "192.168.1.0/24 dev eth1 proto kernel scope link src 192.168.1.245")
    assert f(ny1, "10.102.60.0/24") == ["10.28.28.0/24"]
    # Downstream-hub (Seattle5-like): LAN client serves 10.160.160; rest tunnels
    s5 = ("10.160.160.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 22\n"
          "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1\n"
          "10.102.60.0/24 dev wg0 scope link metric 22\n"
          "10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.9")
    assert f(s5, "10.250.250.0/24") == ["10.160.160.0/24"]
    # Leaf: own + tunnels only -> no transit
    leaf = ("10.179.179.0/24 dev eth0 proto kernel scope link src 10.179.179.1\n"
            "10.111.11.0/24 dev wg0 scope link metric 22")
    assert f(leaf, "10.179.179.0/24") == []
    # WAN-gw (non-10) and /30 excluded; multi-downstream sorted+deduped
    multi = ("10.28.28.0/24 via 10.102.60.230 dev eth0 metric 22\n"
             "10.99.99.0/24 via 10.102.60.231 dev eth0 metric 22\n"
             "10.5.5.0/24 via 192.168.1.9 dev eth1 metric 22\n"
             "10.253.203.88/30 via 10.102.60.230 dev eth0")
    assert f(multi, "10.102.60.0/24") == ["10.28.28.0/24", "10.99.99.0/24"]
    print("[ PASS ] test_transit_from_routes — ALL TRANSIT-ROUTE ORACLES PASS")

if __name__ == "__main__":
    main()
