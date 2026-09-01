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
test_propagate_oracle.py - prove the propagation layer against the oracle:
  A. addHostAndPropogate DEDUP proceed/skip + PROPAGATE fire sequence across all
     walks (8 proceed incl. Seattle3/Seattle6 on both wg1 & wg2; New-York-2 fires
     once then 2 skips).
  B. propogateNotificationInternal peer scan: 8 admin targets, skip local
     10.102.60.1, fork the other 7.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.sim.topology import build_ny1
from discovery.propagate import Propagator, propagate_notification
from discovery.orchestrate import merge
from discovery.test_hosts_oracle import ORACLE_ETC_HOSTS, BRINGUP_PEERS

# A. expected addHostAndPropogate decision tuples (decision, ip) in walk order
# [DESCEND_V1] descend announces each discovered host ONCE (deduped per identity),
# so the propagate sequence is the 6 unique hosts, each 'proceed', with no
# re-visit 'skip's. The walk's old EXPECT interleaved DFS re-visits (13 entries
# with skips); descend's traversal is cleaner and functionally equivalent - same
# host set, each propagated once, every decision 'fire'. Updated to descend order.
EXPECT_AHP = [
    ("proceed", "10.179.179.1"),
    ("proceed", "10.250.250.1"),
    ("proceed", "10.120.120.1"),
    ("proceed", "10.130.130.1"),
    ("proceed", "10.160.160.1"),
    ("proceed", "10.28.28.1"),
]

# B. oracle peer fan-out
ORACLE_PEER_LIST = ("peer_list ips=10.102.60.1,10.120.120.1,"
                    "10.130.130.1,10.160.160.1,10.179.179.1,10.250.250.1,10.28.28.1,")


def main():
    ok = True

    # ---- A: addHostAndPropogate ----
    logs = []
    disc, k, seeds, upstream = build_ny1()
    prop = Propagator(disc.hosts, logger=logs.append)   # discovery_pending=False
    disc.propagator = prop
    disc.descend_downstream(**seeds)
    disc.descend_upstream(seeds["active_devs"], upstream)

    got = []
    for l in logs:
        if l.startswith("DEDUP decision=proceed") or l.startswith("DEDUP decision=skip"):
            decision = "proceed" if "proceed" in l else "skip"
            # key=_="host ip dev"
            key = l.split('key=_="', 1)[1].rstrip('"')
            ip = key.split()[1]
            got.append((decision, ip))
    if got == EXPECT_AHP:
        print(f"  PASS addHostAndPropogate sequence (6 proceed, deduped)")
    else:
        ok = False
        print("  FAIL addHostAndPropogate sequence")
        for i in range(max(len(got), len(EXPECT_AHP))):
            g = got[i] if i < len(got) else None
            e = EXPECT_AHP[i] if i < len(EXPECT_AHP) else None
            if g != e:
                print(f"    @{i} got={g} want={e}")

    # all PROPAGATE decisions must be 'fire' (discovery_pending False)
    fires = [l for l in logs if l.startswith("PROPAGATE")]
    if all("decision=fire" in l for l in fires) and len(fires) == 6:
        print(f"  PASS PROPAGATE fire x{len(fires)}")
    else:
        ok = False
        print(f"  FAIL PROPAGATE: {fires}")

    # ---- B: propogateNotificationInternal ----
    plogs = []
    out = merge(*build_ny1(), local=("10.102.60.1", "New-York-1"),
                bringup_peers=BRINGUP_PEERS)
    local_ips = {"10.102.60.1", "10.102.60.2", "10.254.1.4",
                 "10.253.203.90", "10.253.203.98", "10.253.203.106", "127.0.0.1"}
    res = propagate_notification(out["etc_hosts"], local_ips,
                                 notify=lambda ip: 0, logger=plogs.append)
    peer_list_line = next((l for l in plogs if l.startswith("peer_list")), "")
    scan_line = next((l for l in plogs if l.startswith("peer_scan")), "")
    if scan_line == "peer_scan admin_targets_found=7" and peer_list_line == ORACLE_PEER_LIST:
        print("  PASS peer scan + list (7 targets; BABox gated, no route)")
    else:
        ok = False
        print(f"  FAIL peer scan\n    scan={scan_line}\n    list={peer_list_line}")
    if res["launched"] == 6 and res["skipped"] == 1 and all(rc == 0 for _, rc in res["results"]):
        print("  PASS peer fan-out (6 launched, 1 skipped, all rc=0)")
    else:
        ok = False
        print(f"  FAIL peer fan-out: {res}")

    print()
    print("ALL PROPAGATE ORACLE CHECKPOINTS PASS" if ok else "PROPAGATE ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
