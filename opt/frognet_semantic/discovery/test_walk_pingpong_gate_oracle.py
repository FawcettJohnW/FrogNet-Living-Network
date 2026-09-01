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
test_walk_pingpong_gate_oracle.py - [PINGPONG_GATE_V1] (walk, ALL kinds)

Proves the walk gates reachability on the :9009 ping-pong for EVERY candidate
kind, not the HTTP echo. echo is identity-only (broker/getHosts/seed name it).
Folds in John's 2026-06-19 point: ping-pong for ALL routes everywhere - a peer
whose proxy is down but whose daemon answers :9009 is reachable, and the walk
(tunnel AND relayed legs alike) must treat it so, or a route that survives the
now-ping-pong health check still FAIL_ECHOs in the walk and reaps.

Unreachable = :9009 dead (None); hairpin = :9009 LOOP; both hold on .1 and .2.

Cases (drive REAL Discovery.walk):
  TUNNEL  A echo down + :9009 alive -> DIRECT candidate (the fix)
          B echo down + :9009 dead  -> none      C echo down + :9009 LOOP -> none
          D echo OK   + :9009 alive -> candidate (happy path)
  RELAYED E echo down + :9009 alive -> DIRECT candidate  (proves ALL kinds, not
            just tunnels - this is the leg that was still echo-gated)
          F echo down + :9009 dead  -> no direct candidate (-> vouch path)

REGRESSION: against the echo-gate code, A and E FAIL_ECHO -> no candidate.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

T_PEER, T_PIP = "10.102.60.1", "10.102.60.2"        # tunnel peer (wg2)
R_DEST, R_PIP = "10.77.77.1", "10.77.77.2"          # relayed dest (eth1 via parent)
R_VIA = "10.50.50.1"


def _tunnel(echo_ans, verify):
    k = FakeKernel()
    k.seed("10.102.60.0/24 dev wg2 scope link metric 22",
           "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    broker = FakeBroker(by_one={T_PEER: ("New-York-1-10.102.60", "10.102.60.0/24", T_PEER)},
                        iface_channel={"wg2": "New-York-1-10.102.60"})
    disc = Discovery(routes, FakeEcho(answers=echo_ans), FakeRtt(table={}),
                     FakeGetHosts(children={}), broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"wg2": "10.253.203.118"},
                     self_identity="10.250.250.1", verify=verify)
    disc.walk("wg2", T_PEER, 1, parent_via="")
    return disc


def _relayed(echo_ans, verify):
    k = FakeKernel()
    k.seed("10.50.50.0/24 dev eth1 proto kernel scope link src 10.50.50.9",
           "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    disc = Discovery(routes, FakeEcho(answers=echo_ans), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"eth1": "10.50.50.9"},
                     self_identity="10.250.250.1", verify=verify)
    disc.walk("eth1", R_DEST, 1, parent_via=R_VIA)
    return disc


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    TDEST, RDEST = "10.102.60.0/24", "10.77.77.0/24"

    check(bool(_tunnel({}, FakeVerify()).CAND.get(TDEST)),
          "A  TUNNEL  echo down + :9009 alive -> DIRECT candidate (the fix)")
    check(not _tunnel({}, FakeVerify(dead={T_PIP})).CAND.get(TDEST),
          "B  TUNNEL  echo down + :9009 dead -> no candidate")
    check(not _tunnel({}, FakeVerify(loops={T_PIP})).CAND.get(TDEST),
          "C  TUNNEL  echo down + :9009 hairpin -> no candidate")
    check(bool(_tunnel({T_PIP: f"New-York-1,{T_PEER},,"}, FakeVerify()).CAND.get(TDEST)),
          "D  TUNNEL  echo OK + :9009 alive -> candidate (happy path)")

    check(bool(_relayed({}, FakeVerify()).CAND.get(RDEST)),
          "E  RELAYED echo down + :9009 alive -> DIRECT candidate (ALL kinds, not just tunnel)")
    check(not _relayed({}, FakeVerify(dead={R_PIP})).CAND.get(RDEST),
          "F  RELAYED echo down + :9009 dead -> no direct candidate (-> vouch path)")

    print()
    print("ALL WALK-PINGPONG-GATE ORACLE CHECKPOINTS PASS" if ok
          else "WALK-PINGPONG-GATE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
