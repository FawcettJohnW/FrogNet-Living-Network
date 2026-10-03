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
"""test_descend_oracle - descend crawl proof, driven by the REAL sim topology
(build_ny1), not hand-built fakes. Asserts the behaviours descend exists for:
children AND grandchildren are found via getHosts, every peer is NAMED into
/etc/hosts (including the seg-relay NY-2 and the tunnel children), and routes are
installed. This replaces the earlier fake-based oracle whose direct-probe model
no longer matches descend."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.topology import build_ny1
from discovery.runmerge import run_merge
from discovery.test_hosts_oracle import BRINGUP_PEERS

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

disc, k, seeds, upstream = build_ny1()
out = run_merge(disc, k, seeds, upstream, local=("10.102.60.1", "New-York-1"),
                bringup_peers=BRINGUP_PEERS, depth=0, concurrent_attempt=True,
                logger=lambda s: None)
hosts = "\n".join(out["etc_hosts"])
table = "\n".join(out["final_table"])

# children found + named
for name, ip in [("Seattle5","10.250.250.1"), ("BAMacBook","10.179.179.1"),
                 ("Seattle2","10.120.120.1"), ("Seattle6","10.160.160.1"),
                 ("Seattle3","10.130.130.1")]:
    check(f"{ip} FrogNetHost.{name}" in hosts, f"child {name} named in /etc/hosts")

# grandchild / seg-relay NY-2 found + named + routed via the seg-relay
check("10.28.28.1 FrogNetHost.New-York-2" in hosts, "seg-relay New-York-2 named")
check("10.28.28.0/24 via 10.102.60.230 dev eth0" in table,
      "New-York-2 routed via seg-relay 10.102.60.230")

# tunnel children routed on their winning tunnel
check("10.250.250.0/24 dev wg2" in table, "Seattle5 routed via wg2 (winning tunnel)")
check("10.179.179.0/24 dev wg1" in table, "BAMacBook routed via wg1")

if FAILS:
    print(f"DESCEND ORACLE FAILED: {FAILS}")
    sys.exit(1)
print("ALL DESCEND ORACLE CHECKS PASS")
