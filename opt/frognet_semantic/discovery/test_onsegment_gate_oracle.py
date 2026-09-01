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
test_onsegment_gate_oracle.py - [PINGPONG_GATE_V1]

Proves the on-segment leg gates on the :9009 ping-pong (the SAME probe every
other route kind uses), NOT the HTTP echo. Folds in John's 2026-06-19 finding:
the eth0 descent used echo_probe, so a live on-segment FrogNet node whose proxy
answers only on its .1 identity (while its daemon answers :9009 on every address
it holds) was stamped NOT_FROGNET, and on-segment paths were never loop-checked.

Drives the REAL Discovery.walk over fake edges. Four on-segment neighbors:

  .50  pong=alive, echo=identity   -> passes gate, acquires identity, proceeds
  .60  pong=REFUSED (definitive)   -> NOT_FROGNET reason=on_segment_client_no_pong
  .70  pong=LOOP  (path hairpins)  -> LOOP_9009 on-segment (echo gate never caught this)
  .80  pong=alive, echo=None       -> ALIVE_NO_IDENTITY (NOT poisoned as NOT_FROGNET)
  .90  pong=None  (timeout)        -> NOT_ALIVE reason=no_pong_timeout, NEVER marked
                                      ([NF_CONTRACT_V1]: a slow real node is not poisoned)

REGRESSION GUARD: the legacy reason string `on_segment_client_no_echo` must NOT
appear. Reverting to the echo gate re-emits it on .60/.80 and lets .70's looping
path through (no LOOP_9009) - every one of those flips an assertion below.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeVerify, HostStore)
from discovery.sim.topology import FakeKernel


def build():
    logs = []
    echo = FakeEcho(answers={
        # on-segment FrogNet node: identity .1 = 10.144.144.1 (field 2)
        "10.250.250.50": "NodeX,10.144.144.1,,",
        # .80 deliberately absent -> pong alive but no echo identity
    })
    rtt = FakeRtt(table={})
    gethosts = FakeGetHosts(children={})
    broker = FakeBroker(by_one={}, iface_channel={})
    verify = FakeVerify(refused={"10.250.250.60"}, dead={"10.250.250.90"},
                        loops={"10.250.250.70"})

    local_ips = {"10.250.250.1", "10.250.250.2", "127.0.0.1"}
    dev_src_map = {"eth0": "10.250.250.1"}

    k = FakeKernel()
    routes = Routes(k, logger=logs.append, clock=lambda: 0.0)
    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips, dev_src_map, logger=logs.append, verify=verify)
    return disc, logs


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    disc, logs = build()
    for ip in ("10.250.250.50", "10.250.250.60",
               "10.250.250.70", "10.250.250.80", "10.250.250.90"):
        disc.walk("eth0", ip, depth=1, parent_via="")
    blob = "\n".join(logs)

    check("decision=NOT_FROGNET reason=on_segment_client_no_pong" in blob
          and "ip=10.250.250.60" in blob,
          ".60 no-pong -> NOT_FROGNET reason=on_segment_client_no_pong")

    check("ip=10.250.250.90 decision=NOT_ALIVE" in blob
          and "reason=no_pong_timeout" in blob,
          ".90 timeout -> NOT_ALIVE (probed, not proceeded)")
    check("ip=10.250.250.90 decision=NOT_FROGNET" not in blob,
          ".90 timeout NEVER marked NOT_FROGNET ([NF_CONTRACT_V1])")

    check("ip=10.250.250.70 decision=LOOP_9009" in blob
          and "reason=on_segment_path_loops_through_us" in blob,
          ".70 looping path -> LOOP_9009 on-segment (echo gate never caught loops)")

    check("ip=10.250.250.80 decision=ALIVE_NO_IDENTITY" in blob,
          ".80 pong-ok/echo-none -> ALIVE_NO_IDENTITY (not poisoned NOT_FROGNET)")

    # .50 cleared the gate AND acquired identity -> walk continued onto its hosted
    # net (probe target = hosted .1's .2 = 10.144.144.2), seg_relay set to .50.
    check("ip=10.250.250.50 depth=1 kind=lan probe=10.144.144.2" in blob
          and "seg_relay=10.250.250.50" in blob,
          ".50 pong+identity -> passed gate, walked hosted net (seg_relay set)")
    check("ip=10.250.250.50" in blob
          and "ip=10.250.250.50 decision=NOT_FROGNET" not in blob
          and "ip=10.250.250.50 decision=LOOP_9009" not in blob,
          ".50 not rejected by the gate")

    # Regression guard: the legacy echo-gate reason must be gone everywhere.
    check("on_segment_client_no_echo" not in blob,
          "legacy echo-gate reason on_segment_client_no_echo absent (gate is ping-pong)")

    print()
    print("ALL ONSEGMENT-GATE ORACLE CHECKPOINTS PASS" if ok
          else "ONSEGMENT-GATE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
