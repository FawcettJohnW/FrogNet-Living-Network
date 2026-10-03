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
test_mapinterfaces_oracle.py - prove mapInterfaces classify() against the
setup_lillypad_v4 trace (oracle): the NY-1 box at setup time classifies to
FROGNET_INTERFACES='eth0 eth1 frognet0', FROGNET_DEVS=eth0, DHCP=eth0,
UPSTREAM_SEEDS=10.102.60.1, UPSTREAM_DEVIPS=eth1=192.168.1.245, TRANSIT=''.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.mapinterfaces import Iface, classify

ORACLE = {
    "FROGNET_INTERFACES": "eth0 eth1 frognet0",
    "FROGNET_TRANSIT_DEVS": "",
    "FROGNET_FROGNET_DEVS": "eth0",
    "FROGNET_DHCP_DEVS": "eth0",
    "FROGNET_UPSTREAM_SEEDS": "10.102.60.1",
    "FROGNET_UPSTREAM_DEVIPS": "eth1=192.168.1.245",
}


def main():
    ifaces = [
        Iface("eth0", "up", ["10.102.60.1/24"]),
        Iface("eth1", "up", ["192.168.1.245/24"]),
        Iface("frognet0", "unknown", []),
        Iface("wlan0", "down", []),
    ]
    got = classify(ifaces, leases_nonempty=True)  # sim_underlay empty
    ok = got == ORACLE
    if ok:
        print("  PASS mapInterfaces classification (NY-1 setup oracle)")
    else:
        print("  FAIL mapInterfaces")
        for k in ORACLE:
            if got.get(k) != ORACLE[k]:
                print(f"    {k}: got={got.get(k)!r} want={ORACLE[k]!r}")
    print()
    print("ALL MAPINTERFACES ORACLE CHECKPOINTS PASS" if ok else "MAPINTERFACES PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
