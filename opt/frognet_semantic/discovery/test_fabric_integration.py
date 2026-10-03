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
test_fabric_integration.py - integrated whole-system check:
  1. build the NY-1 mesh as a Fabric (nodes/channels/vouch/latency)
  2. DERIVE the discovery sources from the fabric (no hand-scripted per-call data)
  3. run the ported discovery for the NY-1 node -> routes into its sim kernel
  4. assert final table + /etc/hosts == oracle (so the fabric SUBSUMES the
     hand-scripted sim/topology.py)
  5. EXERCISE: route packets over the installed table and confirm egress devs
This is the "set up routes, then exercise them" loop over a derived fabric.
"""
import os as _os  # [OFFLINE_TUPLES_GATE_V1] build-oracle floors deterministically:
_os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")  # never read live capability
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.fabric import Fabric, Node, route_egress
from discovery.sim.topology import build_ny1, ENTER_SEED
from discovery.orchestrate import merge
from discovery.test_kernel_oracle import ORACLE_FINAL_IPR_REAPED, _diff
from discovery.test_hosts_oracle import ORACLE_ETC_HOSTS, BRINGUP_PEERS

LOCAL_IPS = {"10.102.60.1", "10.102.60.2", "10.254.1.4",
             "10.253.203.90", "10.253.203.98", "10.253.203.106", "127.0.0.1"}
DEV_SRC = {"wg1": "10.253.203.106", "wg2": "10.253.203.90", "wg0": "10.253.203.98"}


def build_fabric():
    f = Fabric()
    f.nodes = {n.name: n for n in [
        Node("New-York-2", "10.28.28", upstream="10.102.60.230", lan_relay="10.102.60.230"),
        Node("BAMacBook", "10.179.179", upstream="172.16.26.155"),
        Node("Seattle5", "10.250.250", upstream="192.168.0.27", upstream2="192.168.0.21"),
        Node("Seattle3", "10.130.130"),
        Node("Seattle6", "10.160.160", upstream="10.251.251.221"),
        Node("Seattle2", "10.120.120", upstream="10.130.130.47"),
        Node("BABox", "10.111.11", dead=True),
    ]}
    f.channels = {
        "wg0": ("BABox", "10.253.203.98", "BABox-10.111.11"),
        "wg1": ("BAMacBook", "10.253.203.106", "BAMacBook-10.179.179"),
        "wg2": ("Seattle5", "10.253.203.90", "Seattle5-10.250.250"),
    }
    f.vouch = {
        "BAMacBook": ["10.250.250.1", "10.130.130.1", "10.160.160.1"],
        "Seattle5": ["10.179.179.1", "10.130.130.1", "10.120.120.1", "10.160.160.1"],
    }
    f.rtt = {
        ("wg1", "10.179.179"): [194], ("wg1", "10.250.250"): [0],
        ("wg1", "10.130.130"): [252], ("wg1", "10.160.160"): [257],
        ("wg2", "10.250.250"): [147], ("wg2", "10.179.179"): [0],
        ("wg2", "10.130.130"): [312], ("wg2", "10.120.120"): [228],
        ("wg2", "10.160.160"): [236], ("eth0", "10.28.28"): [55, 56, 89],
    }
    return f


def main():
    ok = True
    # reuse node bringup state (seeds) - these are the daemon/kernel inputs a
    # node already has; frogsim would supply them per node.
    _, _, seeds, upstream = build_ny1()

    fab = build_fabric()
    disc, k = fab.discovery_for("New-York-1", enter_seed=ENTER_SEED,
                                local_ips=LOCAL_IPS, dev_src=DEV_SRC,
                                dead_ifaces={"wg0"})
    disc._promote_order = ["10.179.179.0/24", "10.28.28.0/24", "10.160.160.0/24",
                           "10.250.250.0/24", "10.120.120.0/24", "10.130.130.0/24"]

    out = merge(disc, k, seeds, upstream, local=("10.102.60.1", "New-York-1"),
                bringup_peers=BRINGUP_PEERS)

    ok &= _diff("fabric-derived final ip r", out["final_table"], ORACLE_FINAL_IPR_REAPED)
    ok &= _diff("fabric-derived /etc/hosts", out["etc_hosts"], ORACLE_ETC_HOSTS)

    # EXERCISE the installed routes
    cases = [
        ("10.130.130.5", "wg1"),   # Seattle3 winner
        ("10.160.160.5", "wg2"),   # Seattle6 winner
        ("10.250.250.9", "wg2"),   # Seattle5
        ("10.179.179.9", "wg1"),   # BAMacBook
        ("10.28.28.9",  "eth0"),   # New-York-2 (LAN)
        ("10.120.120.9", "wg2"),   # Seattle2
    ]
    ex_ok = True
    for dst, want_dev in cases:
        eg = route_egress(out["final_table"], dst)
        got = eg[0] if eg else None
        if got != want_dev:
            ex_ok = False
            print(f"    EXERCISE FAIL {dst}: egress {got} want {want_dev}")
    if ex_ok:
        print(f"  PASS exercise: {len(cases)} packets egress the expected dev")
    else:
        ok = False

    print()
    print("ALL FABRIC INTEGRATION CHECKPOINTS PASS" if ok else "FABRIC INTEGRATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
