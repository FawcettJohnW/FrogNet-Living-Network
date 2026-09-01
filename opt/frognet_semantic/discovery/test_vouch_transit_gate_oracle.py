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
test_vouch_transit_gate_oracle.py - [VOUCH_TRANSIT_GATE_V1] regression.

getHosts is the authority for WHICH hosts a relay knows, but a relay knows the
whole propagated mesh, not just what it relays. A vouch must therefore be gated
on whether the relay actually TRANSITS the child's /24 (broker transit map).
This is the discriminator the LAN-vs-tunnel tie-break got wrong:

  - Seattle5 -> Seattle3 via Seattle6 (eth0 LAN): VALID. Seattle6 transits
    10.130.130 (Seattle3 is downstream of it). KEEP.
  - Seattle5 -> Seattle3 via BAMacBook / New-York-1 (wg): LOOP. Those tunnel
    peers reach Seattle3 only back through the chain. They do NOT transit
    10.130.130. SUPPRESS.
  - New-York-1 -> Seattle5 via New-York-2 (eth0 LAN): LOOP. New-York-2 is an
    unregistered LAN leaf -> ABSENT from the transit map -> SUPPRESS.

Harness: the proven VOUCH_ROUTE harness - Seattle3 (identity 10.130.130.1, lease
10.160.160.191 on Seattle6's AP) walks up to Seattle6 (10.160.160.1), which
vouches Seattle5 (echo OK), BAMacBook + New-York-1 (echo FAIL). We vary only the
broker transit map and assert which vouches survive.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)

IDENT = "10.130.130.1"
LEASE = "10.160.160.191"


def _run(node_transit):
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
           "10.160.160.0/24 dev wlan1 proto kernel scope link src 10.160.160.191 metric 600")
    routes = Routes(k, clock=lambda: 0.0)
    echo = FakeEcho(answers={
        "10.160.160.2": "Seattle6,10.160.160.1,10.250.250.1,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })
    rtt = FakeRtt(table={("wlan1", "10.160.160.2"): [23],
                         ("wlan1", "10.250.250.2"): [28]})
    gethosts = FakeGetHosts(children={"10.160.160.1": [
        ("10.250.250.1", "Seattle5"),     # echo OK
        ("10.179.179.1", "BAMacBook"),    # echo FAIL -> vouch
        ("10.102.60.1", "New-York-1"),    # echo FAIL -> vouch
    ]})
    broker = FakeBroker(node_transit=node_transit)
    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={IDENT, "10.130.130.2", LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT)
    disc.walk("wlan1", "10.160.160.1", 1)
    disc.promote()
    return k


def _has(k, dest):
    return bool((k.route_show(dest) or "").strip())


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    # 1) Relay (Seattle6, prefix 10.160.160) transits Seattle3's subnet but NOT
    #    BAMacBook/New-York-1's -> only the transiting vouch survives. (Echo-OK
    #    Seattle5 always survives on its own echo.)
    k = _run({"10.160.160": ["10.160.160.0/24", "10.130.130.0/24",
                             "10.250.250.0/24"]})
    check(not _has(k, "10.179.179.0/24"),
          "1  BAMacBook vouch SUPPRESSED - Seattle6 does not transit 10.179.179")
    check(not _has(k, "10.102.60.0/24"),
          "1  New-York-1 vouch SUPPRESSED - Seattle6 does not transit 10.102.60")
    check(_has(k, "10.250.250.0/24"),
          "1  Seattle5 (echo OK) still routes")

    # 2) Relay transits all three -> all vouches survive (the legit uplink-relay
    #    case: Seattle3's only way out IS Seattle6, which transits everything).
    k = _run({"10.160.160": ["10.160.160.0/24", "10.130.130.0/24",
                             "10.250.250.0/24", "10.179.179.0/24",
                             "10.102.60.0/24"]})
    check(_has(k, "10.179.179.0/24") and _has(k, "10.102.60.0/24"),
          "2  relay transits all -> BAMacBook + New-York-1 vouches KEPT")

    # 3) Relay ABSENT from a populated map (unregistered LAN leaf, the
    #    New-York-2 case) -> every vouch through it suppressed.
    k = _run({"10.250.250": ["10.250.250.0/24"]})   # map has someone else, not 10.160.160
    check(not _has(k, "10.179.179.0/24") and not _has(k, "10.102.60.0/24"),
          "3  relay absent from map -> all vouches through it SUPPRESSED")

    # 4) Empty map -> gate inert (pre-gate behaviour): all vouches survive.
    k = _run({})
    check(_has(k, "10.179.179.0/24") and _has(k, "10.102.60.0/24"),
          "4  empty map -> gate INERT, vouches survive (safe to deploy early)")

    print()
    print("ALL VOUCH-TRANSIT-GATE CHECKPOINTS PASS" if ok
          else "VOUCH-TRANSIT-GATE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
