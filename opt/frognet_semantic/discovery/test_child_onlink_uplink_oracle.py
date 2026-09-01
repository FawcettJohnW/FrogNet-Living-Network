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
"""test_child_onlink_uplink_oracle.py - a child routes the upstream mesh through its
parent over an ON-LINK uplink gateway.

Topology (from the Seattle3 node log, build al):
  Seattle3 (identity 10.130.130.1, its own /24 on wlan0) is a CHILD of Seattle5.
  Its uplink is wlan1, where it holds an address on Seattle5's segment
  (10.250.250.30) - so 10.250.250 is on-local-subnet and 10.250.250.1 (Seattle5) is
  a next-hop. BUT the kernel has NO connected route to 10.250.250.0/24 (the observed
  node anomaly: the uplink gateway is reachable only on-link, e.g. via the default).
  So a plain `via 10.250.250.1` install is rejected rc=2 "Nexthop has invalid
  gateway" (strict_gateway=True models this).

  Seattle3 learns the upstream mesh (New-York-1, BABox) from Seattle5's getHosts and
  must route each `<mesh>/24 via 10.250.250.1 dev wlan1`. Without the on-link retry
  every one fails rc=2, the hosts are dropped, and the child (Seattle2, one level
  down) starves. With [ONLINK_RETRY_V1] the install re-asserts on-link and succeeds.

THE GATE: New-York-1 / BABox route via 10.250.250.1 on wlan1.
  FAILS on old routes.py (no retry -> rc=2 -> no route);
  PASSES on the rc=2 -> onlink retry.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeReflect, HostStore)
from discovery import descend as _descend

IDENT = "10.130.130.1"   # Seattle3's identity (its own /24, on wlan0)

MESH = [("10.130.130.1", "Seattle3"), ("10.250.250.1", "Seattle5"),
        ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox")]


def build():
    echo = FakeEcho(answers={
        # Seattle5, the parent, answering at its own segment address on our uplink.
        "10.250.250.1": "Seattle5,10.250.250.1,,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })
    rtt = FakeRtt(table={("wlan1", "10.250.250.1"): [20], ("wlan1", "10.250.250.2"): [20]})
    gethosts = FakeGetHosts(children={
        "10.250.250.1": MESH,   # parent advertises the upstream mesh
    })
    broker = FakeBroker(by_one={}, iface_channel={})

    k = FakeKernel(strict_gateway=True)   # model the real kernel's rc=2 for a
                                          # via whose gateway isn't on a connected /24
    k.seed(
        # our own /24 on wlan0 (connected); NO connected route to 10.250.250.0/24 -
        # the uplink gateway 10.250.250.1 is reachable only on-link.
        "10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
        "default via 10.250.250.1 dev wlan1 onlink metric 601",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)

    # reflect: the mesh nodes are reachable through the parent 10.250.250.1 on wlan1
    # (the probe route, installed on-link by the retry, forwards through Seattle5).
    reflect = FakeReflect(kernel=k, reach={
        ("10.102.60.2", "wlan1", "10.250.250.1"),
        ("10.111.11.2", "wlan1", "10.250.250.1"),
    })

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     # local_ips include our uplink address 10.250.250.30 -> 10.250.250
                     # is on-local-subnet, so 10.250.250.1 is a next-hop.
                     local_ips={"10.130.130.1", "10.130.130.2", "10.250.250.30",
                                "127.0.0.1"},
                     dev_src_map={"wlan0": "10.130.130.1", "wlan1": "10.250.250.30"},
                     reflect=reflect, self_identity=IDENT, logger=lambda s: None)
    disc.DEAD_IFACES = set()
    # immediate: the parent Seattle5 on the uplink wlan1.
    immediate = [("10.250.250.1", "wlan1")]
    _descend.descend(disc, ["wlan0", "wlan1"], immediate)
    disc.promote()
    return k.route_show()


def _has(table, dest, via, dev):
    for line in table.replace(";", "\n").splitlines():
        if line.startswith(dest) and f"dev {dev}" in line:
            if via == "" or f"via {via} " in (line + " "):
                return True
    return False


def main():
    final = build()
    checks = [
        ("New-York-1 10.102.60.0/24 via 10.250.250.1 dev wlan1 (through parent)",
         _has(final, "10.102.60.0/24", "10.250.250.1", "wlan1")),
        ("BABox 10.111.11.0/24 via 10.250.250.1 dev wlan1 (through parent)",
         _has(final, "10.111.11.0/24", "10.250.250.1", "wlan1")),
    ]
    print("=== CHILD ONLINK UPLINK ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL CHILD-ONLINK-UPLINK ORACLE CHECKPOINTS PASS")
        return 0
    print("CHILD-ONLINK-UPLINK ORACLE FAILED")
    print("--- final table ---"); print(final)
    return 1


if __name__ == "__main__":
    sys.exit(main())
