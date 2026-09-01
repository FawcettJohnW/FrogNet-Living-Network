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
test_reflect_promote_vouch_oracle.py - [REFLECT_VOUCH_GATE_V2]

The live bug (2026-06-28): a vouched (relayed) subnet was dropped
vouch_not_alive_no_install because promote() gated it on verify.alive(dest1,dev)
- a RAW :9009 TCP connect that only succeeds on-segment (only the proxy speaks
:9009). For a dest reachable solely THROUGH a relay, alive() always returns None,
so every relayed node dropped regardless of real reachability.

The reflect chain is the verifier that traverses relays (proxy /reflect?o&c,
c+1 per hop). This oracle seeds ONE vouched candidate (the sentinel form promote
consumes) and drives the REAL promote():

  A  reflect=OK, :9009 dead  -> INSTALLS   (the live fix; old code DROPS)
  B  reflect=LOOP            -> dropped     (bent path, no black hole)
  C  reflect=None (wired)    -> dropped     (not reached through chain)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import FakeEcho, FakeRtt, FakeGetHosts, FakeBroker, HostStore

IDENT = "10.120.120.1"

class DeadVerify:
    """:9009 is dead for a relayed dest (the real on-segment-only limitation)."""
    def measure_or_loop(self, target, dev): return None
    def alive(self, dest1, dev): return False

class Reflect:
    def __init__(self, verdict): self.v = verdict
    def probe(self, o, target, counter=0): return self.v

def _promote_vouch(reflect):
    k = FakeKernel()
    k.seed("10.120.120.0/24 dev wlan0 proto kernel scope link src 10.120.120.1")
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={IDENT, "127.0.0.1"}, dev_src_map={},
                     self_identity=IDENT, verify=DeadVerify(), reflect=reflect)
    # seed exactly what the walk would append for a relayed vouch:
    # rtt(sentinel) | via | dev | onlink | src | kind | host
    disc.CAND["10.250.250.0/24"].append(
        f"1000000|10.130.130.1|wlan1|0|{IDENT}|vouch|Seattle5")
    disc.promote()
    return " ".join((k.route_show("10.250.250.0/24") or "").replace(";", " ").split())

def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    check(bool(_promote_vouch(Reflect("OK"))),
          "A  reflect=OK + dead :9009 -> vouch INSTALLS (was vouch_not_alive drop)")
    check(not _promote_vouch(Reflect("LOOP")),
          "B  reflect=LOOP -> dropped, no black-hole route")
    check(not _promote_vouch(Reflect(None)),
          "C  reflect=None (wired) -> dropped, not reached through chain")

    print()
    print("ALL REFLECT-PROMOTE-VOUCH CHECKPOINTS PASS" if ok
          else "REFLECT-PROMOTE-VOUCH PROOF FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
