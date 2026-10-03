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
test_reflect_vouch_gate_oracle.py - [REFLECT_VOUCH_GATE_V1]

Folds in John's 2026-06-06 finding: the circular-route detector "isn't working"
because vouches NEVER reach the walk's reflect probe - they're added straight to
CAND. So a bent vouch (New-York-2 vouching Seattle5 back through us) installed as a
black-hole LAN route, and zero [REFLECT] lines ever appeared. The transit-map gate
that was supposed to catch this is inert (fail-open when node_transit.json absent).

The fix reflect-probes each vouched dest's .1 at the point of the vouch. This
oracle drives the REAL walk+promote with a reflect that returns LOOP for one
vouched dest and not the other, and asserts: LOOP -> vouch skipped (no route);
no-LOOP -> vouch installs as before.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import FakeEcho, FakeRtt, FakeGetHosts, FakeBroker, HostStore

IDENT = "10.130.130.1"; LEASE = "10.130.130.2"

class LoopVerify:
    """[LOOP_DETECT_9009_V1] measure_or_loop -> 'LOOP' for a bent path (targets in
    `loops`); None for an unreachable deep host (targets in `dead` - its direct
    :9009 fails for the same broken-return-path reason its echo does); else an rtt
    (directly reachable). alive() True so promote installs a reachable vouch.
    [PINGPONG_GATE_V1] A bent/dead condition holds for BOTH the .1 and the .2 of
    the dest: the walk now :9009-gates the .2 candidate, the vouch path the .1."""
    def __init__(self, loops=frozenset(), dead=frozenset()):
        self.loops = set(loops); self.dead = set(dead)
    def measure_or_loop(self, target, dev):
        if target in self.loops:
            return "LOOP"
        if target in self.dead:
            return None
        return 5.0
    def alive(self, dest1, dev): return True

def _run(verify):
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
           "10.160.160.0/24 dev wlan1 proto kernel scope link src 10.160.160.191 metric 600")
    routes = Routes(k, clock=lambda: 0.0)
    echo = FakeEcho(answers={"10.160.160.2": "Seattle6,10.160.160.1,10.250.250.1,"})
    rtt = FakeRtt(table={("wlan1", "10.160.160.2"): [23]})
    # Seattle6 vouches two deep hosts (echo fails -> vouched): NY-1 and BAMacBook.
    gethosts = FakeGetHosts(children={"10.160.160.1": [
        ("10.102.60.1", "New-York-1"),   # we'll mark this one a LOOP
        ("10.179.179.1", "BAMacBook"),   # this one clean
    ]})
    disc = Discovery(routes, echo, rtt, gethosts, FakeBroker(), HostStore(),
                     local_ips={IDENT, LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT, verify=verify, reflect=None)
    disc.walk("wlan1", "10.160.160.1", 1)
    disc.promote()
    return k

def _line(k, dest):
    return " ".join((k.route_show(dest) or "").replace(";", " ").split())

def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c)
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # NY-1's path is bent (loops on .1 AND .2); BAMacBook is a deep host -
    # directly unreachable on :9009 (.1 AND .2 dead, like its echo) but NOT bent,
    # so it is served by the vouch (promote's alive() is True for it).
    k = _run(LoopVerify(loops={"10.102.60.1", "10.102.60.2"},
                        dead={"10.179.179.1", "10.179.179.2"}))
    check(not _line(k, "10.102.60.0/24"),
          "A   bent vouch (LOOP_9009) is SKIPPED - no black-hole route installed")
    check(bool(_line(k, "10.179.179.0/24")),
          "B   clean vouch (no loop) still installs")

    # Control: no reflect wired -> both vouches install (inert, no regression).
    k2 = _run(None)
    check(bool(_line(k2, "10.102.60.0/24")) and bool(_line(k2, "10.179.179.0/24")),
          "C   verify=None -> inert, both vouches install (back-compat)")

    print()
    print("ALL REFLECT-VOUCH-GATE ORACLE CHECKPOINTS PASS" if ok
          else "REFLECT-VOUCH-GATE PROOF FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
