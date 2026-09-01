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
test_loop9009_hairpin_oracle.py - [LOOP_DETECT_9009_V1]

The BABox runMerge infinite loop, distilled. BABox (10.111.11.1) sees a candidate
to Seattle5 (10.250.250) "via 10.179.179.1 dev wlan0" - i.e. out to BAMacBook, a
LAN child. BAMacBook's only path back to Seattle5 is THROUGH BABox's wg1 tunnel,
so that candidate is a hairpin: BABox -> BAMacBook -> BABox -> wg1 -> Seattle5.

The echo to .2 succeeds (the hairpin physically round-trips), so the candidate
looks alive and - on the old fail-open reflect probe - could win on RTT, flip the
metric-22 winner, mutate the table, and re-arm runAgain forever.

With loop detection at the :9009 alive step, the daemon on the hairpin lands on
BABox's OWN daemon (HELLO return_ip is local) and answers RTT_LOOP. measure_or_loop
returns "LOOP" at candidate entry, the candidate is dropped, and - the property
that actually kills the loop - NOTHING is installed for 10.250.250, so there is no
route mutation and no runAgain.

Proven here:
  A  hairpin candidate is dropped (not in CAND), no /24 winner installed -> no mutation
  B  control: identical walk, no loop -> candidate survives (so A isn't a false drop)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

IDENT = "10.111.11.1"; LEASE = "10.111.11.2"          # BABox
SEA5  = "10.250.250"                                   # Seattle5 (the hairpin dest)
CHILD = "10.179.179.1"                                 # BAMacBook (LAN child, the hairpin via)


def _walk_hairpin(verify):
    k = FakeKernel()
    k.seed(
        # BABox LAN
        "10.111.11.0/24 dev eth0 proto kernel scope link src 10.111.11.1",
        # the LAN child segment BABox reaches over wlan0 (BAMacBook)
        "10.179.179.0/24 dev wlan0 proto kernel scope link src 10.179.179.254",
    )
    routes = Routes(k, clock=lambda: 0.0)
    # The hairpin echo SUCCEEDS - that is exactly the trap the old gate fell into.
    echo = FakeEcho(answers={f"{SEA5}.2": f"Seattle5,{SEA5}.1,,"})
    rtt = FakeRtt(table={("wlan0", f"{SEA5}.2"): [140]})   # looks fast - would win on RTT
    disc = Discovery(routes, echo, rtt, FakeGetHosts(children={}), FakeBroker(),
                     HostStore(), local_ips={IDENT, LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT, verify=verify)
    # walk Seattle5 via the LAN child on wlan0 (the hairpin candidate)
    disc.walk("wlan0", f"{SEA5}.1", 1, parent_via=CHILD)
    disc.promote()
    return disc, k


def _winner_line(k):
    return " ".join((k.route_show(f"{SEA5}.0/24") or "").replace(";", " ").split())


def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c)
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # A - hairpin: .2 loops back through us -> dropped at entry, nothing installed.
    d, k = _walk_hairpin(FakeVerify(loops={f"{SEA5}.2"}))
    check(not d.CAND.get(f"{SEA5}.0/24", []),
          "A1  hairpin candidate is DROPPED at entry (not in CAND)")
    check(not _winner_line(k),
          "A2  no /24 winner installed for the hairpin dest -> no mutation -> no runAgain")

    # B - control: same walk, clean path (no loop) -> candidate survives.
    d2, _ = _walk_hairpin(FakeVerify())
    check(bool(d2.CAND.get(f"{SEA5}.0/24", [])),
          "B   control: identical walk with no loop KEEPS the candidate (A is not a false drop)")

    print()
    print("ALL LOOP9009-HAIRPIN ORACLE CHECKPOINTS PASS" if ok
          else "LOOP9009-HAIRPIN PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
