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
multi_merge_thrash_check.py - does the via fix actually stop the loop across merges?

The single-step oracle proves one pass keeps the incumbent. This drives the REAL
promote() across a SEQUENCE of merges, carrying the installed winner forward as the
next merge's incumbent (exactly as the kernel route does on the box), with the same
jittery near-equal LAN relays the Seattle5 log showed - a different relay measures
lowest almost every merge. It then checks the thing that actually matters for
convergence: after the first install, does the winner via stop changing and does the
/24 stop landing in mutated_slash24 (which is what re-arms runAgain)?

  STABLE  => slash24_mutated=0 from merge 2 on => runAgain not re-armed by this dest
            => computed_wan/reach_plane stop moving => the databasehost election runs
               a BOUNDED number of times and settles (deterministic given stable input,
               proven separately in databasehost_convergence_check.py).
  THRASH  => via flips, /24 mutates every merge, runAgain never clears (the bug).

Run on the fixed tree it must report STABLE. Set FROGNET_WINNER_HYSTERESIS=0.90 to
see the threshold half of the regression (the dev-only half needs the old code).
"""
import os, sys
from collections import defaultdict
_ROOT = os.path.abspath(__file__)
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                      # .../opt/frognet_semantic
sys.path.insert(0, _ROOT)
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

DEST = "10.120.120.0/24"
RELAYS = ["10.250.250.191", "10.130.130.1", "10.130.130.2"]
# Per-merge measured RTTs for (relay0, relay1, relay2). Jittery and overlapping,
# like the log: the lowest relay changes merge to merge, but none is ~2x better
# than another, so no change is a real >=50% win.
MERGE_RTTS = [
    (15, 34, 40),   # merge 0: relay0 lowest -> initial winner
    (12,  8, 20),   # merge 1: relay1 dips (jitter)
    (10, 14,  6),   # merge 2: relay2 dips
    ( 9, 11,  7),   # merge 3: relay2 again
    ( 8, 13, 12),   # merge 4: relay0 back
    (11,  9, 10),   # merge 5: relay1 dips
]


def run():
    k = FakeKernel()
    k.seed("10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
                     self_identity="10.250.250.1", own_subnet="10.250.250.0/24",
                     has_own_uplink=False, uplink_dev="eth1", verify=FakeVerify())

    thr = os.environ.get("FROGNET_WINNER_HYSTERESIS", "0.50")
    print(f"=== {len(MERGE_RTTS)} successive merges, jittery relays, "
          f"FROGNET_WINNER_HYSTERESIS={thr} ===")
    history = []   # (merge_idx, winner_via, mutated)
    for i, rtts in enumerate(MERGE_RTTS):
        # fresh candidate set this merge; reset the per-pass mutation flags
        disc.CAND = defaultdict(list)
        disc.CAND[DEST] = [f"{rtt}|{relay}|eth0|0|10.250.250.1|lan|Seattle2"
                           for relay, rtt in zip(RELAYS, rtts)]
        disc.r.route_table_mutated = False
        disc.r.mutated_slash24 = set()
        disc.promote()
        via = routes.winner_via(DEST)
        mutated = DEST in disc.r.mutated_slash24
        history.append((i, via, mutated))
        print(f"  merge {i}: rtts={rtts}  winner_via={via:<15} "
              f"slash24_mutated={int(mutated)}{'  <- re-arms runAgain' if mutated else ''}")

    post = history[1:]                                   # after the initial install
    flips = sum(1 for (i, v, _), (_, pv, _) in zip(post, history) if v != pv)
    rearms = sum(1 for _, _, m in post if m)
    print()
    print(f"  after the initial install: via flips={flips}  runAgain re-arms={rearms}")
    stable = (flips == 0 and rearms == 0)
    print()
    if stable:
        print("RESULT: STABLE - the winner settles, the /24 stops mutating, runAgain "
              "clears. The merge chain converges, so the databasehost election runs a "
              "bounded number of times over a fixed candidate set and (with the "
              "EVAL_ISOLATE guard, no crash) returns ONE deterministic answer.")
    else:
        print(f"RESULT: THRASH - {flips} via flips / {rearms} re-arms after the first "
              "merge. The /24 keeps mutating, runAgain never clears, the chain never "
              "converges, and the databasehost is re-elected every pass.")
    return 0 if stable else 1


if __name__ == "__main__":
    sys.exit(run())
