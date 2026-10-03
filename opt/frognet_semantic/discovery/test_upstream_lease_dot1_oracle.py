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
test_upstream_lease_dot1_oracle.py - [UPSTREAM_LEASE_DOT1_V1]

Seattle3 (field, 2026-07-20) holds 10.250.250.191 on wlan1 - a DHCP client on
Seattle5's LAN - and routes through 10.250.250.1, yet Seattle5 never appears in its
host list, so databasehost_control (highest .1) collapses to BAMacBook and the
election splits three ways. Root: the upstream immediate was derived from ARP only,
so the gateway drops out the moment its ARP entry ages (route via it survives under
incumbency-hold; the HOST vanishes).

fail-on-old: with an EMPTY ARP table, the gateway .1 is not an immediate -> Seattle5
             is never crawled or added.
pass-on-new: the .1 of a subnet we hold a NON-.1 lease on is an immediate,
             ARP-independent; our own served .1 is not added as an upstream.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
import discovery.descend as _descend


def main():
    d = Discovery.__new__(Discovery)          # bypass __init__; set only what's used
    d.local_ips = {"10.130.130.1", "10.130.130.2", "10.250.250.191", "127.0.0.1"}
    d.local_subnets = {ip.rsplit(".", 1)[0] for ip in d.local_ips}
    d.DEAD_IFACES = set()

    captured = {}
    orig = _descend.descend
    _descend.descend = lambda disc, active_devs, immediate, **kw: captured.setdefault(
        "immediate", list(immediate))
    try:
        d.descend_downstream(
            active_devs=["eth0", "wlan1"],
            dev_ip={"eth0": "10.130.130.1", "wlan1": "10.250.250.191"},
            leases=[],                        # no downstream clients
            arp_neigh={"eth0": [], "wlan1": []},   # EMPTY ARP - the failing condition
            active_states=[])
    finally:
        _descend.descend = orig

    imm = captured.get("immediate", [])
    gws = {ip for ip, dev in imm}
    checks = [
        ("upstream gateway 10.250.250.1 derived from lease (ARP empty)", ("10.250.250.1", "wlan1") in imm),
        ("our own served .1 (10.130.130.1) NOT added as an upstream", "10.130.130.1" not in gws),
    ]
    print("=== UPSTREAM LEASE-.1 ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL UPSTREAM-LEASE-DOT1 ORACLE CHECKPOINTS PASS"); return 0
    print("UPSTREAM-LEASE-DOT1 ORACLE FAILED"); print("immediate:", imm); return 1


if __name__ == "__main__":
    sys.exit(main())
