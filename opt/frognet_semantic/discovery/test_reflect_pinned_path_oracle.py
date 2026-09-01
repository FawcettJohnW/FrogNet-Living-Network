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
test_reflect_pinned_path_oracle.py - [REFLECT_PINNED_PATH_V1]

Folds in John's 2026-06-06 finding: Seattle3's correct downstream candidate to
Seattle2 (via the direct DHCP next-hop) was falsely REFLECT_LOOP-rejected. Echo
rode the pinned /32 to the dest .2 (candidate path) and SUCCEEDED, but reflect
probed the dest .1 - which had no pinned /32 - so it kernel-routed by the bent
installed /24 (via the upstream) and looped. Reflect tested a DIFFERENT path than
echo validated.

Fix: reflect probes the pinned `pip` (.2), the same route echo used. This oracle
drives a REAL walk where echo succeeds and a reflect that LOOPS on the .1 but is
clean on the .2; the candidate must now SURVIVE (proving reflect rides .2).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import FakeEcho, FakeRtt, FakeGetHosts, FakeBroker, HostStore

IDENT = "10.130.130.1"; LEASE = "10.130.130.2"

class PathVerify:
    """[LOOP_DETECT_9009_V1] measure_or_loop -> LOOP only for targets in `loops`.
    We loop the .1 (old bent /24 path) and leave the .2 (pinned candidate path)
    clean, to prove the :9009 loop check rides the pinned .2, not the .1."""
    def __init__(self, loops): self.loops = set(loops)
    def measure_or_loop(self, target, dev):
        return "LOOP" if target in self.loops else 5.0
    def alive(self, dest1, dev): return True

def _walk_seattle2(verify):
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
           # the BENT stale route the bug followed: Seattle2 via upstream Six
           "10.120.120.0/24 via 10.160.160.1 dev wlan1 metric 22")
    routes = Routes(k, clock=lambda: 0.0)
    # Seattle2's .2 answers echo over the downstream candidate path (pinned /32).
    echo = FakeEcho(answers={"10.120.120.2": "Seattle2,10.120.120.1,,"})
    rtt = FakeRtt(table={("wlan0", "10.120.120.2"): [4]})
    disc = Discovery(routes, echo, rtt, FakeGetHosts(children={}), FakeBroker(),
                     HostStore(), local_ips={IDENT, LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT, verify=verify)
    # downstream walk: reach Seattle2 via the direct DHCP next-hop on wlan0
    disc.walk("wlan0", "10.120.120.1", 1, parent_via="10.130.130.47")
    return disc

def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c)
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # .1 loops (old bent path), .2 is clean (pinned candidate path).
    d = _walk_seattle2(PathVerify(loops={"10.120.120.1"}))
    cand = d.CAND.get("10.120.120.0/24", [])
    check(bool(cand),
          "A   candidate SURVIVES: :9009 loop check rides the pinned .2 (clean), not the .1 (looped)")

    # Control: .2 itself loops (candidate path genuinely bends) -> rejected.
    d2 = _walk_seattle2(PathVerify(loops={"10.120.120.2"}))
    check(not d2.CAND.get("10.120.120.0/24", []),
          "B   candidate REJECTED when the pinned .2 path genuinely loops (LOOP_9009)")

    print()
    print("ALL REFLECT-PINNED-PATH ORACLE CHECKPOINTS PASS" if ok
          else "REFLECT-PINNED-PATH PROOF FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
