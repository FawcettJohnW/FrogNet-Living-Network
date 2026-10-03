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
test_fixdefault_oracle.py - prove fixDefaultRoute reproduces the oracle's
decision lines (purge keep, discover 1 candidate, compute metric 601, INSTALL
skip already_exact, DECISION) and leaves the eth1 default unchanged.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.fixdefault import FixDefaultRoute

# Oracle fixDefaultRoute decision lines (the non-TRACE, non-stage ones), in order.
ORACLE_FDR = [
    "ENTER default_metric=601 ping_wait=2",
    "forced_override status=none",
    "purge_scan existing_defaults=1",
    "purge_ping_fire via=192.168.1.1 dev=eth1 metric=601",
    "PURGE decision=keep reason=ping_ok via=192.168.1.1 dev=eth1 metric=601",
    "purge_summary total=1 kept=1 purged=0",
    "offlan_candidates count=1 list=192.168.1.1\teth1,",
    "compute_install_metrics n=1 result=601",
    "INSTALL role=primary via=192.168.1.1 dev=eth1 metric=601 onlink=no",
    "INSTALL_skip via=192.168.1.1 dev=eth1 metric=601 reason=already_exact",
    "DECISION action=installed_primary_plus_fallbacks candidates=1",
]


def main():
    ok = True
    logs = []
    k = FakeKernel()
    k.seed("default via 192.168.1.1 dev eth1 metric 601")        # pre-existing default
    k.seed("192.168.1.0/24 dev eth1 proto kernel scope link src 192.168.1.245")

    fdr = FixDefaultRoute(
        k,
        dev_ip4={"eth0": "10.102.60.1", "eth1": "192.168.1.245",
                 "frognet0": "10.254.1.4", "wg0": "10.253.203.98",
                 "wg1": "10.253.203.106", "wg2": "10.253.203.90"},
        up_devs={"eth0", "eth1", "frognet0", "wg0", "wg1", "wg2"},
        connected_prefixes={"eth1": {"192.168.1"}, "eth0": {"10.102.60"}},
        ping=lambda via, dev: (via, dev) == ("192.168.1.1", "eth1"),  # eth1 gw alive
        frognet_interfaces=["eth0", "eth1", "frognet0", "wg0", "wg1", "wg2"],
        local_ips={"10.102.60.1", "10.102.60.2", "192.168.1.245",
                   "10.254.1.4", "10.253.203.90", "10.253.203.98", "10.253.203.106"},
        logger=logs.append,
    )
    fdr.run()

    if logs == ORACLE_FDR:
        print(f"  PASS fixDefaultRoute decision lines ({len(logs)})")
    else:
        ok = False
        print("  FAIL fixDefaultRoute decision lines")
        for i in range(max(len(logs), len(ORACLE_FDR))):
            g = logs[i] if i < len(logs) else "<none>"
            w = ORACLE_FDR[i] if i < len(ORACLE_FDR) else "<none>"
            if g != w:
                print(f"    @{i} got ={g!r}")
                print(f"        want={w!r}")

    default_after = k.show_default()
    if default_after == ["default via 192.168.1.1 dev eth1 metric 601"]:
        print("  PASS default route unchanged")
    else:
        ok = False
        print(f"  FAIL default route: {default_after}")

    print()
    print("ALL FIXDEFAULT ORACLE CHECKPOINTS PASS" if ok else "FIXDEFAULT ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
