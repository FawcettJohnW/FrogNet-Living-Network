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
test_snapshot_struct.py - STRUCTURAL proofs (no oracle artifact in the log):
emit_routes_snapshot route classifier, snapshot payload shape, and the
commit_only no-op contract (table unchanged with empty observations).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.snapshot import classify_routes, build_routes_snapshot, commit_only_noop

FULL_IPR = [
"default via 192.168.1.1 dev eth1 metric 601",
"10.28.28.0/24 via 10.28.28.1 dev eth0 metric 22 onlink",
"10.102.60.0/24 dev eth0 proto kernel scope link src 10.102.60.1",
"10.111.11.0/24 dev wg0 scope link metric 22",
"10.120.120.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
"10.130.130.0/24 dev wg1 scope link src 10.253.203.106 metric 22",
"10.130.130.0/24 dev wg2 scope link src 10.253.203.90 metric 100",
"10.160.160.0/24 dev wg2 scope link src 10.253.203.90 metric 22",
"10.160.160.0/24 dev wg1 scope link src 10.253.203.106 metric 100",
"10.179.179.0/24 dev wg1 scope link metric 22",
"10.250.250.0/24 dev wg2 scope link metric 22",
"10.253.203.88/30 dev wg2 proto kernel scope link src 10.253.203.90",
"10.253.203.96/30 dev wg0 proto kernel scope link src 10.253.203.98",
"10.253.203.104/30 dev wg1 proto kernel scope link src 10.253.203.106",
"10.254.1.0/24 dev frognet0 proto kernel scope link src 10.254.1.4",
"192.168.1.0/24 dev eth1 proto kernel scope link src 192.168.1.245",
]
EXPECT = dict(routes_total=16, defaults=1, frognet_routes=14,
              wg_tunnel_routes=11, local_routes=2, transit_routes=0)

def main():
    ok = True
    s = classify_routes(FULL_IPR)
    if all(s[k] == v for k, v in EXPECT.items()):
        print("  PASS route classifier")
    else:
        ok = False; print(f"  FAIL classifier: {s}")
    snap = build_routes_snapshot(FULL_IPR, now_ts=1780530000, run_id="r",
                                 host="FrogNetHost", domain="New-York-1")
    if snap["ttl_sec"] == 45 and snap["routes"]["kernel_summary"]["routes_total"] == 16:
        print("  PASS snapshot payload shape")
    else:
        ok = False; print("  FAIL snapshot shape")
    k = FakeKernel(); k.seed(*FULL_IPR)
    c = commit_only_noop(k, observations=[])
    if c["table_unchanged"] and c["installs"] == 0 and c["winners"] == 0:
        print("  PASS commit_only no-op (table unchanged)")
    else:
        ok = False; print(f"  FAIL commit no-op: {c}")
    print()
    print("ALL SNAPSHOT STRUCT CHECKS PASS" if ok else "SNAPSHOT STRUCT FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
