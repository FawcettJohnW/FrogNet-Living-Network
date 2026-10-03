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
test_attached_onlink_oracle.py - [ATTACHED_ONLINK_SRC_V1] regression.

The next-hop-only rule: a route whose `via` falls inside its own destination /24
is a violation - that destination is a segment the node is directly ON (its uplink
DHCP lease), so it is on-link, not reached through a next-hop.

Reproduces Seattle3 reaching its uplink parent Seattle6 (Seattle3 holds the
10.160.160.191 lease on Seattle6's AP; identity is 10.130.130.1). Asserts:

  A. 10.160.160.0/24 installs ON-LINK with src=identity (no via)        [the fix]
  B. it is NOT `via 10.160.160.1` (via inside its own dest)       [the regression]
  C. a genuine remote one hop further (10.250.250 via Seattle6) is UNCHANGED -
     still `via 10.160.160.1` - proving the fix is surgical, attached subnets only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import FakeEcho, FakeRtt, FakeGetHosts, FakeBroker, HostStore

IDENT = "10.130.130.1"            # Seattle3 identity (.1 on its own AP, wlan0)
LEASE = "10.160.160.191"          # Seattle3's client lease on Seattle6's AP (wlan1)


def _run():
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
           "10.160.160.0/24 dev wlan1 proto kernel scope link src 10.160.160.191 metric 600")
    routes = Routes(k, clock=lambda: 0.0)
    echo = FakeEcho(answers={
        "10.160.160.2": "Seattle6,10.160.160.1,10.250.250.1,",   # uplink parent
        "10.250.250.2": "Seattle5,10.250.250.1,,",               # one hop beyond
    })
    rtt = FakeRtt(table={("wlan1", "10.160.160.2"): [23], ("wlan1", "10.250.250.2"): [28]})
    gethosts = FakeGetHosts(children={"10.160.160.1": ["10.250.250.1"]})  # S6 vouches S5
    broker = FakeBroker()
    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={IDENT, "10.130.130.2", LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT)
    disc.walk("wlan1", "10.160.160.1", 1)     # ARP-seeded uplink parent .1
    disc.promote()
    return k


def _line_for(k, dest):
    show = k.route_show(dest)
    return " ".join(x for x in (show or "").replace(";", " ").split())


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    k = _run()
    parent = _line_for(k, "10.160.160.0/24")
    remote = _line_for(k, "10.250.250.0/24")
    print(f"    parent (attached) : {parent}")
    print(f"    remote (one beyond): {remote}")
    print()

    check("via" not in parent.split(),
          "A/B 10.160.160.0/24 is on-link - NO via (was the via=dest's-own-.1 violation)")
    check("via 10.160.160.1" not in parent,
          "B   the offending `via 10.160.160.1` form is gone")
    check(f"src {IDENT}" in parent,
          f"A   10.160.160.0/24 carries src={IDENT} (identity, not the .191 lease)")
    check("via 10.160.160.1" in remote,
          "C   genuine remote 10.250.250.0/24 still `via 10.160.160.1` (fix is surgical)")

    print()
    print("ALL ATTACHED-ONLINK ORACLE CHECKPOINTS PASS" if ok
          else "ATTACHED-ONLINK PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
