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
test_prune_kernel_route_oracle.py - [PRUNE_KERNEL_GUARD_V1]

Seattle2 (2026-07-20) is a DHCP client on Seattle3's 10.130.130.0/24 (eth1 .66,
wlan1 .47), default route via 10.130.130.1. Its winner install failed rc=2
(src=10.130.130.1 is not local), then prune_dest_extras DELETED the kernel's own
connected routes for that /24 and the box dropped off the LAN.

Cause: prune identified kernel connected routes by "no explicit metric," but
NetworkManager assigns EXPLICIT metrics to connected routes (100 eth / 600 wifi),
so the metric-None heuristic misses them and prune eats them.

fail-on-old: the proto-kernel routes (metric 100/600) are pruned -> node loses LAN.
pass-on-new: proto-kernel routes survive; genuine FrogNet corpses still pruned.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.routes import Routes
from discovery.kernel import FakeKernel


def main():
    k = FakeKernel()
    k.seed(
        "10.130.130.0/24 dev eth1 proto kernel scope link src 10.130.130.66 metric 100",
        "10.130.130.0/24 dev wlan1 proto kernel scope link src 10.130.130.47 metric 600",
        "10.130.130.0/24 via 10.130.130.1 dev eth1 metric 101",  # real corpse
    )
    Routes(k, logger=lambda s: None, clock=lambda: 0.0).prune_dest_extras(
        "10.130.130.0/24", keep_metrics={22})
    tbl = k.route_show("10.130.130.0/24").replace(";", "\n")

    checks = [
        ("kernel connected route (eth1, metric 100) survives prune", "metric 100" in tbl),
        ("kernel connected route (wlan1, metric 600) survives prune", "metric 600" in tbl),
        ("genuine FrogNet corpse (metric 101) still pruned", "metric 101" not in tbl),
    ]
    print("=== PRUNE KERNEL-ROUTE GUARD ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL PRUNE-KERNEL-GUARD ORACLE CHECKPOINTS PASS"); return 0
    print("PRUNE-KERNEL-GUARD ORACLE FAILED"); print(tbl); return 1


if __name__ == "__main__":
    sys.exit(main())
