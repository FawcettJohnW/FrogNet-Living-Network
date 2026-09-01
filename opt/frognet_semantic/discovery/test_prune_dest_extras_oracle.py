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
test_prune_dest_extras_oracle.py - [PRUNE_DEST_EXTRAS_V1]

Folds in John's 2026-06-11 Seattle5 forever-loop. The live table held /24s that
were BOTH local and tunnel (10.102.60/10.111.11 via .221 eth0 AND dev wgN) plus
duplicate same-dev/different-metric copies (10.28.28 wg2@22 and wg2@102). Those
are corpses: reap_unverified_winners keeps the whole dest because it IS a winner,
and install_if_changed only rewrites the metrics promote installs this pass - so a
LAN copy left under a tunnel winner, or a higher-metric rank from a pass that saw
more devs, survives untouched. A later pass then trips route_table_mutated on the
moved metric and the merge never converges.

The fix: after promote installs a dest's winner + fallbacks, prune any route for
that dest at a metric NOT installed this pass. Deletes are flag_mutation=False
(removing a non-winner copy cannot move the winner set), so a pass whose only
change is cleaning corpses stays converged.

Drives the REAL promote()/prune and asserts:
  A  a LAN corpse under a tunnel winner is deleted -> dest is one plane
  B  a higher-metric leftover (rank-3 corpse) under a multi-tunnel dest is deleted
  C  a settled multi-tunnel dest re-promoted is CLEAN (no /24 mutation) -> converges
  D  the prune itself does NOT flip route_table_mutated (no runAgain livelock)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

def mk():
    k = FakeKernel()
    r = Routes(k, clock=lambda: 0.0)
    d = Discovery(r, FakeEcho(answers={}), FakeRtt(table={}),
                  FakeGetHosts(children={}), FakeBroker(), HostStore(),
                  local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
                  self_identity="10.250.250.1", own_subnet="10.250.250.0/24",
                  has_own_uplink=True, uplink_dev="")
    return k, r, d

def routes_for(k, dest):
    return [l.strip() for l in (k.route_show(dest) or "").replace(";", "\n").splitlines() if l.strip()]

# ---- A: LAN corpse under a single tunnel winner is pruned -----------------
k, r, d = mk()
k.seed("10.111.11.0/24 dev wg0 scope link src 10.250.250.1 metric 22",        # winner already
       "10.111.11.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 100",  # LAN corpse
       "10.111.11.0/24 dev wg2 scope link src 10.250.250.1 metric 101")       # leftover tunnel
d.CAND["10.111.11.0/24"] = ["155||wg0|0|10.250.250.1|tunnel|BABox"]   # one measured winner: wg0
d.promote()
after = routes_for(k, "10.111.11.0/24")
print("  10.111.11 after:", after)
check(any("metric 22" in l and "wg0" in l for l in after),
      "A1 the wg0 winner (metric 22) survives")
check(not any("eth0" in l for l in after),
      "A2 the LAN corpse (via .221 eth0 metric 100) is PRUNED - no longer both local and wg")
check(not any("metric 101" in l for l in after),
      "A3 the stale wg2 metric-101 leftover is PRUNED")

# ---- B + C: multi-tunnel dest, higher-metric corpse, then idempotent ------
k, r, d = mk()
k.seed("10.28.28.0/24 dev wg2 scope link src 10.250.250.1 metric 102")  # rank-3 corpse, prior pass
d.CAND["10.28.28.0/24"] = [                       # NY-2 reachable over 3 tunnels (relay)
    "158||wg2|0|10.250.250.1|tunnel|New-York-2",
    "168||wg0|0|10.250.250.1|tunnel|New-York-2",
    "169||wg1|0|10.250.250.1|tunnel|New-York-2",
]
d.promote()
after = routes_for(k, "10.28.28.0/24")
metrics = sorted(int(l.split("metric")[1].split()[0]) for l in after if "metric" in l)
print("  10.28.28 after pass1:", after)
check(metrics == [22, 100, 101],
      "B exactly winner@22 + fallbacks@100/101 remain; the metric-102 corpse is PRUNED")

# pass 2: same candidates, table already canonical -> must NOT mutate /24
r.route_table_mutated = False
r.mutated_slash24.clear()
d.promote()
print("  10.28.28 after pass2:", routes_for(k, "10.28.28.0/24"))
check(r.route_table_mutated is False,
      "C a settled multi-tunnel dest re-promoted is CLEAN (slash24_mutated=0) - CONVERGES")
check(not r.mutated_slash24,
      "C2 no /24 dest recorded as mutated on the settled pass")

# ---- D: the prune alone never flips route_table_mutated -------------------
k, r, d = mk()
k.seed("10.102.60.0/24 dev wg2 scope link src 10.250.250.1 metric 22",            # winner already
       "10.102.60.0/24 via 10.250.250.221 dev eth0 src 10.250.250.1 metric 100")  # LAN corpse only
d.CAND["10.102.60.0/24"] = ["163||wg2|0|10.250.250.1|tunnel|New-York-1"]  # same winner, no fallback
r.route_table_mutated = False
r.mutated_slash24.clear()
d.promote()
after = routes_for(k, "10.102.60.0/24")
print("  10.102.60 after:", after)
check(not any("eth0" in l for l in after), "D1 LAN corpse pruned")
check(r.route_table_mutated is False,
      "D2 a pass whose ONLY change is pruning a corpse stays CLEAN (no runAgain livelock)")

print("\n[ PASS ] test_prune_dest_extras_oracle - ALL CHECKPOINTS PASS" if ok
      else "\n[ FAIL ] test_prune_dest_extras_oracle")
sys.exit(0 if ok else 1)
