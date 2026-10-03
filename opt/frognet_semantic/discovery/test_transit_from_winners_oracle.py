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
test_transit_from_winners_oracle.py - [TRANSIT_FROM_WINNERS_V1]

Folds in John's 2026-06-06 rule: transit is derived from the winners promote()
installs, not a route scan or DHCP-lease probe. A winning /24 on a SERVED dev
entered this node from below, so it is transit. A GATEWAY (own non-10.x WAN
uplink) therefore transits its ENTIRE downstream LAN subtree - "if this is a
gateway, everything LAN is routed." A LAN-child excludes its mesh-ward uplink
dev (the path UP) and advertises only the children below it.

Drives the REAL promote() with the exact Seattle5 (gateway) and SeattleThree
(LAN-child) candidate sets from the live tables and pins computed_transit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

def _disc(local_ip, own_subnet, has_uplink, uplink_dev=""):
    k = FakeKernel()
    routes = Routes(k, clock=lambda: 0.0)
    d = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                  FakeGetHosts(children={}), FakeBroker(), HostStore(),
                  local_ips={local_ip, "127.0.0.1"}, dev_src_map={},
                  self_identity=local_ip, own_subnet=own_subnet,
                  has_own_uplink=has_uplink, uplink_dev=uplink_dev)
    return d, k

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

# Winner candidates are single rank-0 lines: rtt|via|dev|onlink|src|kind|host.
# Measured (rtt!=sentinel) so they install as winners.
def w(via, dev, host, kind="lan"):
    return [f"10|{via}|{dev}|0||{kind}|{host}"]

# ---- Seattle5 GATEWAY: LAN subtree via eth0, mesh-ward via wg, own 250 ----
d5, k5 = _disc("10.250.250.1", "10.250.250.0/24", has_uplink=True)
d5.CAND["10.28.28.0/24"]   = w("10.250.250.221","eth0","NY-2")
d5.CAND["10.111.11.0/24"]  = w("10.250.250.221","eth0","BABox")
d5.CAND["10.120.120.0/24"] = w("10.250.250.221","eth0","Two")
d5.CAND["10.130.130.0/24"] = w("10.250.250.221","eth0","Three")
d5.CAND["10.160.160.0/24"] = w("10.250.250.221","eth0","Six")
d5.CAND["10.102.60.0/24"]  = w("","wg2","NY-1","tunnel")
d5.CAND["10.179.179.0/24"] = w("","wg1","BAMac","tunnel")
d5.promote()
t5 = d5.computed_transit
print("Seattle5 (gateway) computed_transit =", t5)
check(set(["10.120.120.0/24","10.130.130.0/24","10.160.160.0/24"]) <= set(t5),
      "A gateway routes the ENTIRE LAN subtree (120/130/160)")
check("10.28.28.0/24" in t5 and "10.111.11.0/24" in t5,
      "B gateway includes every other LAN /24 reached via served eth0")
check("10.102.60.0/24" not in t5 and "10.179.179.0/24" not in t5,
      "C gateway excludes mesh-ward tunnel devs (wg1/wg2)")
check("10.250.250.0/24" not in t5, "D gateway excludes its own subnet")

# ---- SeattleThree LAN-CHILD: downstream child via wlan0, mesh-ward via wlan1 ----
d3, k3 = _disc("10.130.130.1", "10.130.130.0/24", has_uplink=False, uplink_dev="wlan1")
d3.CAND["10.120.120.0/24"] = w("10.130.130.50","wlan0","Two")     # downstream child
d3.CAND["10.160.160.0/24"] = w("10.130.130.1","wlan1","Six")      # mesh-ward (uplink)
d3.CAND["10.250.250.0/24"] = w("10.130.130.1","wlan1","Five")     # mesh-ward (uplink)
d3.promote()
t3 = d3.computed_transit
print("SeattleThree (LAN-child) computed_transit =", t3)
check(t3 == ["10.120.120.0/24"],
      "E LAN-child advertises only its downstream child (120), not the path up")

print("\n[ PASS ] test_transit_from_winners_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_transit_from_winners_oracle")
sys.exit(0 if ok else 1)
