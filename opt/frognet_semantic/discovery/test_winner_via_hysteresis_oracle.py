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
"""
test_winner_via_hysteresis_oracle.py - [WINNER_HYSTERESIS_V1] via-level + 50% bar

The Seattle5 loop: 10.120.120.0/24 (Seattle2) is reachable over the SAME dev
(eth0) through three LAN relays at near-equal RTT. The dev-only hysteresis never
engaged (dev never changed), so promote took rank0_lowest_measured_rtt and the
via flipped every merge - .191 -> .130.130.1 -> .130.130.2 -> .191 - each an
`ip route replace` that mutated the /24, latched runAgain, and yanked any flow in
flight, all for single-digit-ms jitter.

Fix: stickiness on the full (via, dev) identity, and the bar to flip is a CLEAR
win (>=50% faster), not 10%. So:
  CASE KEEP  incumbent .191@15ms, challenger .130.130.1@8ms (only ~47% better):
             route is KEPT, /24 NOT mutated -> runAgain not re-armed.  <-- the loop
  CASE FLIP  incumbent .191@15ms, challenger @5ms (>50% better): route DOES move,
             so a genuinely better path is still adopted (we don't freeze a winner).

REGRESSION (old: dev-only check + 0.90 bar): the 8ms challenger flips the via in
CASE KEEP -> /24 mutated -> the production loop.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

DEST = "10.120.120.0/24"
INC  = "10.250.250.191"     # incumbent relay, installed last merge
CHAL = "10.130.130.1"       # challenger relay, same dev (eth0)


def _run(chal_rtt):
    """Seed .191 as the installed winner, offer .191@15 and CHAL@chal_rtt as
    this pass's candidates, run the REAL promote(), return (winner_via, mutated)."""
    k = FakeKernel()
    k.seed(f"{DEST} via {INC} dev eth0 metric 22",   # incumbent installed (src-less, post-fix shape)
           "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
                     self_identity="10.250.250.1", own_subnet="10.250.250.0/24",
                     has_own_uplink=False, uplink_dev="eth1", verify=FakeVerify())
    # rtt|via|dev|onlink|src|kind|host  - both measured, same dev, different via
    disc.CAND[DEST] = [
        f"15|{INC}|eth0|0|10.250.250.1|lan|Seattle2",
        f"{chal_rtt}|{CHAL}|eth0|0|10.250.250.1|lan|Seattle2",
    ]
    disc.promote()
    return routes.winner_via(DEST), (DEST in routes.mutated_slash24)


print("=== CASE KEEP: challenger 8ms vs incumbent 15ms (~47% better, under the bar) ===")
via, mutated = _run(8)
print(f"  winner_via={via}  /24_mutated={mutated}")
check(via == INC, "incumbent relay .191 is KEPT (via did not flip on jitter)")
check(not mutated, "/24 was NOT mutated -> runAgain not re-armed by this dest  <-- loop broken")

print("=== CASE FLIP: challenger 5ms vs incumbent 15ms (>50% better, clear win) ===")
via, mutated = _run(5)
print(f"  winner_via={via}  /24_mutated={mutated}")
check(via == CHAL, "a genuinely better path (>=50%) IS adopted (no frozen winner)")
check(mutated, "/24 mutated on the real improvement (expected, justified change)")

print()
print("ALL WINNER-VIA-HYSTERESIS CHECKS PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
