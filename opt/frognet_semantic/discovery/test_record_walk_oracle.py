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
"""test_record_walk_oracle - the record-driven pass asks EVERY interface and never
skips a dest that a route exists for.

This is the exact failure the seed-crawl walk kept producing: an interface that
had the route got skipped (parent_via/seg_relay/vouch/fastpath talked it out of
the probe), so the dest was lost. The record walk cannot do that - it probes
every (dest, interface) cell unconditionally and makes a candidate wherever the
kernel answers. Fails on any inference-gated walk; passes on record_walk."""
import os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.record_walk import probe_interfaces

FAILS = []
def check(ok, label):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)


class FakeVerify:
    """Reachability truth table by (dot2, dev). Records every cell it was asked,
    so we can prove EVERY interface got probed."""
    def __init__(self, table):
        self.table = table
        self.asked = set()
    def measure_or_loop(self, dot2, dev):
        self.asked.add((dot2, dev))
        return self.table.get((dot2, dev))   # float | "LOOP" | None


class FakeDisc:
    def __init__(self, verify, own_subnet):
        self.verify = verify
        self.own_subnet = own_subnet
        self.CAND = defaultdict(list)
        self.logs = []
    def dev_src(self, dev):
        return ""
    def log(self, m):
        self.logs.append(m)


# Self is Seattle3 (10.130.130). Interfaces: wlan0 (own LAN), wlan1 (uplink), wg0 (tunnel).
IFACES = ["wlan0", "wlan1", "wg0"]
records = [
    {"dot1": "10.102.60.1",  "name": "New-York-1"},   # remote: reachable ONLY via wlan1 (relay)
    {"dot1": "10.111.11.1",  "name": "BABox"},         # remote: reachable ONLY via wg0 (tunnel)
    {"dot1": "10.250.250.1", "name": "Seattle5"},       # reachable via BOTH wlan1 AND wg0
    {"dot1": "10.130.130.1", "name": "Seattle3"},       # OWN subnet - must be skipped
    {"dot1": "10.28.28.1",   "name": "New-York-2"},     # reachable via NO interface (dead)
]
# reachability truth (dot2, dev) -> rtt / "LOOP" / absent(None)
truth = {
    ("10.102.60.2", "wlan1"): 55.0,     # NY-1 via relay on wlan1
    ("10.111.11.2", "wg0"):   12.0,     # BABox via tunnel
    ("10.250.250.2", "wlan1"): 3.0,     # Seattle5 via wlan1
    ("10.250.250.2", "wg0"):   7.0,     # Seattle5 ALSO via wg0 (two real paths)
    ("10.28.28.2", "wlan1"):  "LOOP",   # NY-2 loops on wlan1
}
vf = FakeVerify(truth)
disc = FakeDisc(vf, own_subnet="10.130.130.0/24")

# via resolver: relay for the wlan1 remote, on-link for tunnels/direct
def via_res(d2, dev):
    if dev == "wlan1" and d2 == "10.102.60.2":
        return "10.250.250.1"
    return ""   # on-link (tunnel / direct)

n = probe_interfaces(disc, records, IFACES, via_resolver=via_res,
                     max_workers=16, logger=disc.log)

CAND = disc.CAND

# 1. EVERY interface was asked for every remote host (4 remote hosts x 3 ifaces = 12 cells)
remote_dot2 = ["10.102.60.2", "10.111.11.2", "10.250.250.2", "10.28.28.2"]
expected_cells = {(d2, dev) for d2 in remote_dot2 for dev in IFACES}
check(vf.asked == expected_cells,
      f"every (dest,interface) cell probed ({len(vf.asked)}/{len(expected_cells)})")

# 2. own subnet never probed
check(("10.130.130.2", "wlan1") not in vf.asked and "10.130.130.0/24" not in CAND,
      "own subnet skipped (directly attached)")

# 3. the remote reachable only via wlan1 IS found (the classic dropped route)
c = CAND["10.102.60.0/24"]
check(len(c) == 1 and c[0] == "55|10.250.250.1|wlan1|0||lan|New-York-1",
      "NY-1 found via wlan1 with relay via (not skipped, not collapsed)")

# 4. tunnels are walked - remote via wg0 is found
c = CAND["10.111.11.0/24"]
check(len(c) == 1 and c[0] == "12||wg0|0||tunnel|BABox",
      "BABox found via wg0 tunnel")

# 5. a host reachable two ways yields TWO distinct candidates (no collapse - the walk-oracle bug)
c = sorted(CAND["10.250.250.0/24"])
check(len(c) == 2
      and "3||wlan1|0||lan|Seattle5" in c
      and "7||wg0|0||tunnel|Seattle5" in c,
      "Seattle5 yields BOTH wlan1 and wg0 candidates (distinct via/dev, no collapse)")

# 6. a loop prunes only that interface, and a truly-dead dest yields NO candidate but was still asked
check("10.28.28.0/24" not in CAND,
      "NY-2 (loop on wlan1, dead elsewhere) produces no candidate")
check(("10.28.28.2", "wlan0") in vf.asked and ("10.28.28.2", "wg0") in vf.asked,
      "dead dest still probed on every interface (skip is per-interface, not per-dest)")

# 7. total candidate count
check(n == 4, f"candidate count ({n}, want 4: NY-1 + BABox + 2xSeattle5)")

if FAILS:
    print(f"RECORD-WALK ORACLE FAILED: {FAILS}")
    sys.exit(1)
print("ALL RECORD-WALK ORACLE CHECKS PASS")
