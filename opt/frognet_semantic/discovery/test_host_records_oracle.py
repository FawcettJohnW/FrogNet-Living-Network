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
"""test_host_records_oracle - /etc/hosts built from the DB record set is the FULL
set, with NO route gate. This is the behavior HOSTS_GATE breaks: it drops a host
whose /24 isn't in the routing table. A record-sourced file names every
self-asserted host, routed or not - reachability is the walk's separate plane.

Fails on the old route-coupled naming; passes on host_records.hosts_lines."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discovery.host_records as hr
from discovery.host_records import hosts_lines, read_all

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)


# Seattle5 has an installed /24 route; New-York-1 does NOT (reached via default).
# HOSTS_GATE(no_route_to_subnet) would drop New-York-1. The record set must not.
recs = [
    {"dot1": "10.250.250.1", "name": "Seattle5",    "subnet": "10.250.250.0/24"},
    {"dot1": "10.102.60.1",  "name": "New-York-1",  "subnet": "10.102.60.0/24"},
]
lines = hosts_lines(recs)
blob = "\n".join(lines)

check("10.250.250.1 Seattle5 FrogNetHost.Seattle5" in blob,
      "routed host named")
check("10.102.60.1 New-York-1 FrogNetHost.New-York-1" in blob,
      "UNROUTED host still named (HOSTS_GATE would drop it) - the core fix")
check("10.102.60.2 FrogNetAdmin.New-York-1" in blob,
      "unrouted host admin (.2) line present too")
check(len(lines) == 4, f"full set, no gate ({len(lines)} lines, want 4)")
check(lines[0].startswith("10.102.60.1"),
      "deterministic sorted-by-ip order (byte-stable file, no churn)")

# read_all keeps the freshest self-record per node (a stale duplicate is superseded).
class _FakeT:
    @staticmethod
    def get_all(service, dbhost=None, fresh_s=0, timeout=0):
        return [
            {"value": {"dot1": "10.102.60.1", "name": "STALE", "ts": 100},
             "addr": "10.102.60.1"},
            {"value": {"dot1": "10.102.60.1", "name": "New-York-1", "ts": 200},
             "addr": "10.102.60.1"},
        ]

_saved = hr._T
hr._T = _FakeT
try:
    got = read_all()
finally:
    hr._T = _saved
check(len(got) == 1 and got[0]["name"] == "New-York-1",
      "read_all keeps freshest self-record per node")

# A record missing name or dot1 is skipped, not crashed on.
check(hosts_lines([{"dot1": "", "name": "X"}]) == [], "empty dot1 skipped")

if FAILS:
    print(f"HOST-RECORDS ORACLE FAILED: {FAILS}")
    sys.exit(1)
print("ALL HOST-RECORDS ORACLE CHECKS PASS")
