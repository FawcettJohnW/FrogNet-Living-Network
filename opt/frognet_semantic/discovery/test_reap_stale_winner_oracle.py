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
test_reap_stale_winner_oracle.py - [REAP_STALE_WINNERS_V1]

Folds in John's 2026-06-06 NY-2 loop: the reflect detector correctly logged
VOUCH_SKIP reason=reflect_loop for 10.130.130/120/160/111 (installed nothing),
yet stale `via 10.102.60.1 dev eth1 metric 22` copies from a prior merge
survived in the table - a ping-pong loop NY-1<->NY-2. Discovery only ADDED
winners; nothing removed a route it no longer vouches.

The fix: snapshot vs winners. A /24 that was CONSIDERED this pass (a candidate)
but did NOT win is reaped. This oracle seeds a stale looped /24, drives the real
promote() where that dest is a reflect-loop candidate (so it wins nothing), and
asserts the stale route is DELETED. A /24 the pass never considered is untouched.
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

k = FakeKernel()
# Pre-existing stale state from a prior merge. Under the canonical rule, a full
# discovery this pass re-establishes ONLY the winners; everything else is stale.
k.seed("10.130.130.0/24 via 10.102.60.1 dev eth1 metric 22",   # stale loop route
       "10.99.99.0/24 via 10.102.60.1 dev eth1 metric 22",     # also stale (not won)
       "10.250.250.0/24 dev wg2 scope link metric 22",         # will be re-won this pass
       "10.28.28.0/24 dev eth0 proto kernel scope link src 10.28.28.1")  # own connected
routes = Routes(k, clock=lambda: 0.0)
disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                 FakeGetHosts(children={}), FakeBroker(), HostStore(),
                 local_ips={"10.28.28.1", "127.0.0.1"}, dev_src_map={},
                 self_identity="10.28.28.1", own_subnet="10.28.28.0/24",
                 has_own_uplink=False, uplink_dev="eth1")

# This pass discovers ONE winner: 10.250.250 over wg2 (measured). 10.130.130 is
# a reflect-loop candidate that wins nothing; 10.99.99 isn't discovered at all.
# Canonical rule: only 10.250.250 survives; both others are reaped.
disc.CAND["10.250.250.0/24"] = ["140||wg2|0|10.28.28.1|tunnel|Seattle5"]
disc.CAND["10.130.130.0/24"] = []   # considered, no surviving candidate
disc.promote()

after_loop = k.route_show("10.130.130.0/24")
after_won  = k.route_show("10.250.250.0/24")
after_undisc = k.route_show("10.99.99.0/24")
after_own  = k.route_show("10.28.28.0/24")
print("  10.130.130 after:", after_loop or "ABSENT")
print("  10.250.250 after:", after_won or "ABSENT")
print("  10.99.99   after:", after_undisc or "ABSENT")
print("  10.28.28   after:", after_own or "ABSENT")

check(not after_loop,
      "A reflect-loop /24 that won nothing is REAPED")
check("10.250.250.0/24" in (after_won or ""),
      "B the discovered winner survives")
check(not after_undisc,
      "C a /24 not discovered this pass is REAPED (no carryover, canonical)")
check("proto kernel" in (after_own or ""),
      "D own connected /24 is never reaped")

print("\n[ PASS ] test_reap_stale_winner_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_reap_stale_winner_oracle")
sys.exit(0 if ok else 1)
