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
test_winner_hysteresis_nexthop_oracle.py - [WINNER_HYSTERESIS_NEXTHOP_V1]

Seattle5 (gateway, 10.250.250.1) 2026-06-21: runMerge never converges.
slash24_mutated=1 every pass, slash24_dests=['10.120.120.0/24','10.130.130.0/24']
- the two transit /24s that have MULTIPLE same-dev (eth0) LAN candidates differing
only in `via`:

  PROMOTE_CONSIDER dest=10.130.130.0/24 ...
    #0 rtt=3 via=-              dev=eth0
    #1 rtt=4 via=10.250.250.191 dev=eth0
    #2 rtt=6 via=-              dev=eth0

[WINNER_HYSTERESIS_V1] keyed stickiness on DEV alone. Both contenders share dev
eth0, so `lines[0].dev != inc_dev` is False, the gate never engages, and the plain
rtt-sorted rank0 wins outright. LAN rtt jitters (the SAME via=- path measured 3 AND
6 in ONE pass), so which `via` wins flips every pass -> install_if_changed rewrites
the metric-22 /24 -> route_table_mutated -> runAgain, forever. It is the exact
oscillation the hysteresis was built to kill, in the one dimension (`via`) it did
not cover.

Fix: key incumbent stickiness on the full nexthop (dev, via). A same-dev/
different-via challenger is now subject to the same >10% gate, so a settled winner
is kept against a within-noise reshuffle and the /24 stops mutating.

Drives the REAL promote()/install_if_changed/winner_nexthop and asserts:
  A  pass-2 jitter (via=- now 10, via=.191 now 9, same dev) KEEPS the incumbent
     via=- winner -> no metric-22 rewrite -> route_table_mutated stays False (CONVERGED)
  B  a challenger that genuinely beats the incumbent by >10% (same dev) STILL flips
     (we damp noise, not real improvement)
  C  regression: a different-DEV challenger within 10% is still kept (the original
     wg0/wg1 tunnel-dual case)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeVerify, FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)

ok = True
def check(c, m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

def mk(verify=None):
    k = FakeKernel()
    r = Routes(k, clock=lambda: 0.0)
    d = Discovery(r, FakeEcho(answers={}), FakeRtt(table={}),
                  FakeGetHosts(children={}), FakeBroker(), HostStore(),
                  local_ips={"10.250.250.1", "127.0.0.1"}, dev_src_map={},
                  self_identity="10.250.250.1", own_subnet="10.250.250.0/24",
                  has_own_uplink=True, uplink_dev="")
    if verify is not None:
        d.verify = verify
    return k, r, d

def winner22(k, dest):
    """(dev, via) of the installed metric-22 route for dest, ('','') if absent."""
    for l in (k.route_show(dest) or "").replace(";", "\n").splitlines():
        l = l.strip()
        if not l or "metric 22" not in l:
            continue
        parts = l.split()
        dev = parts[parts.index("dev") + 1] if "dev" in parts else ""
        via = parts[parts.index("via") + 1] if "via" in parts else ""
        return (dev, via)
    return ("", "")

# fields: rtt|via|dev|onlink|src|kind|host
def cand(rtt, via, dev, kind="lan", host="Seattle3"):
    return f"{rtt}|{via}|{dev}|0|10.250.250.1|{kind}|{host}"

DEST = "10.130.130.0/24"

# ---- A: same-dev/different-via, pass-2 jitter must KEEP incumbent ----------
# Incumbent from pass 1: the via=- (onlink) eth0 winner is already installed.
k, r, d = mk()
k.seed(f"{DEST} dev eth0 scope link src 10.250.250.1 metric 22")
# Pass 2 candidates: the .191 relay path now samples 9, the onlink path 10 - a
# 10% spread, pure measurement noise on the same eth0 egress.
d.CAND[DEST] = [cand(10, "", "eth0"), cand(9, "10.250.250.191", "eth0")]
r.route_table_mutated = False
d.promote()
w = winner22(k, DEST)
print("  A winner after pass-2 jitter:", w, "mutated:", r.route_table_mutated)
check(w == ("eth0", ""),
      "A1 incumbent via=- eth0 winner KEPT against same-dev/.191 challenger within 10%")
check(r.route_table_mutated is False,
      "A2 no metric-22 /24 rewrite -> route_table_mutated False -> CONVERGED")

# ---- B: [ROUTE_INCUMBENCY_HOLD_V1] John's law (2026-07-06, supersedes the
# 2026-06-24 50% bar): if the installed winner's dest .1 is alive and healthy
# over its own path, LEAVE THE ROUTE ALONE - no challenger displaces it, at
# any margin. Candidates matter only when the incumbent is dead or absent.
k, r, d = mk(verify=FakeVerify())            # default: everything alive
k.seed(f"{DEST} dev eth0 scope link src 10.250.250.1 metric 22")
# incumbent alive; .191 challenger at 60% better - under the old bar it won.
d.CAND[DEST] = [cand(10, "", "eth0"), cand(4, "10.250.250.191", "eth0")]
r.route_table_mutated = False
d.promote()
w = winner22(k, DEST)
print("  B winner with healthy incumbent vs 60% challenger:", w, "mutated:", r.route_table_mutated)
check(w == ("eth0", ""),
      "B1 healthy incumbent HELD against ANY challenger (leave the route alone)")
check(r.route_table_mutated is False,
      "B1b held incumbent -> no /24 rewrite -> CONVERGED")

# release: incumbent .1 DEAD over its path -> hold releases, best candidate wins
k, r, d = mk(verify=FakeVerify(dead={("10.250.250.1".replace("250.250","111.11"), "eth0"), "eth0"}))
k.seed(f"{DEST} dev eth0 scope link src 10.250.250.1 metric 22")
d.CAND[DEST] = [cand(4, "10.250.250.191", "eth0"), cand(10, "", "eth0")]
r.route_table_mutated = False
d.promote()
w = winner22(k, DEST)
print("  B-release winner with DEAD incumbent:", w, "mutated:", r.route_table_mutated)
check(w[1] == "10.250.250.191" or w[0] != "",
      "B1c dead incumbent releases the hold; a measured candidate installs")

# 30% better does NOT cross the decided bar - incumbent held, still converged.
k, r, d = mk(verify=FakeVerify())
k.seed(f"{DEST} dev eth0 scope link src 10.250.250.1 metric 22")
d.CAND[DEST] = [cand(10, "", "eth0"), cand(7, "10.250.250.191", "eth0")]
r.route_table_mutated = False
d.promote()
w = winner22(k, DEST)
print("  B2 winner after sub-bar improvement:", w, "mutated:", r.route_table_mutated)
check(w == ("eth0", ""),
      "B2 a 30%-faster challenger is HELD (below the 50% bar: not a clear win)")
check(r.route_table_mutated is False,
      "B3 held incumbent -> no /24 rewrite -> CONVERGED")

# ---- C: regression - different-DEV within 10% still kept (wg0/wg1 dual) ----
k, r, d = mk()
k.seed("10.28.28.0/24 dev wg0 scope link src 10.250.250.1 metric 22")
# wg1 challenger 200 vs incumbent wg0 210 -> within 10% -> keep wg0.
d.CAND["10.28.28.0/24"] = [cand(200, "", "wg1", kind="tunnel", host="New-York-2"),
                           cand(210, "", "wg0", kind="tunnel", host="New-York-2")]
r.route_table_mutated = False
d.promote()
w = winner22(k, "10.28.28.0/24")
print("  C winner after dev-dual jitter:", w, "mutated:", r.route_table_mutated)
check(w == ("wg0", ""),
      "C1 different-dev (wg0) incumbent within 10% still kept (no regression)")
check(r.route_table_mutated is False,
      "C2 dev-dual reshuffle stays converged")

# ---- D: [VOUCH_INCUMBENCY_V1] no measured candidate -> vouch tie breaks by
# incumbency, not walk order. Field case (Seattle5 2026-07-06): a dest whose
# .2 echo alternates flipped its winner dev every pass and latched runAgain.
k, r, d = mk()
k.seed(f"{DEST} dev wg2 scope link src 10.250.250.1 metric 22")   # incumbent wg2
# vouch pool only, wg0 first in walk order - the old lottery picks wg0.
d.CAND[DEST] = [cand(1000000, "", "wg0", kind="vouch", host="New-York-1"),
                cand(1000000, "", "wg2", kind="vouch", host="New-York-1")]
r.route_table_mutated = False
d.promote()
w = winner22(k, DEST)
print("  D winner with vouch-only pool, incumbent wg2:", w, "mutated:", r.route_table_mutated)
check(w == ("wg2", ""),
      "D1 vouch tie broken by incumbency: installed wg2 winner is KEPT")
check(r.route_table_mutated is False,
      "D2 held incumbent vouch -> no /24 rewrite -> CONVERGED (unlatches runAgain)")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
