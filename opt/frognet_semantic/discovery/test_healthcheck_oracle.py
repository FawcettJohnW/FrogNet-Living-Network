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
test_healthcheck_oracle.py - [PINGPONG_HEALTH_V1]

Proves run_tunnel_health_check gates tunnel liveness on the :9009 ping-pong, NOT
the HTTP frognet_echo.php gate. Folds in John's 2026-06-19 finding: the echo gate
false-negatived healthy tunnels (curl code=000 -> all three TUNNEL_DEAD ->
everything reaped) and never caught a hairpin.

Drives the REAL HealthCheck.run with a FakeVerify (the ping-pong backend):
  wg0  peer pong=None  -> dead reason=no_pong
  wg1  peer pong=LOOP  -> dead reason=loop_9009 (echo gate NEVER caught a loop here)
  wg2  peer pong=alive -> healthy

REGRESSION GUARD: dead reasons must be no_pong / loop_9009, never echo_failed /
wrong_peer. Reverting to the echo gate changes both the reasons AND the
constructor contract (health_echo vs verify), so old code fails this oracle.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.healthcheck import HealthCheck
from discovery.sources import FakeVerify

ACTIVE = [
    ("wg0", "BABox-10.111.11", "10.111.11.0/24"),       # peer .1 = 10.111.11.1
    ("wg1", "BAMacBook-10.179.179", "10.179.179.0/24"), # peer .1 = 10.179.179.1
    ("wg2", "Seattle5-10.250.250", "10.250.250.0/24"),  # peer .1 = 10.250.250.1
]
ALL_DEVS = ["eth0", "eth1", "frognet0", "wg0", "wg1", "wg2"]


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    logs = []
    k = FakeKernel()
    k.seed("10.111.11.0/24 dev wg0 scope link metric 22",
           "10.179.179.0/24 dev wg1 scope link metric 22",
           "10.250.250.0/24 dev wg2 scope link metric 22")
    r = Routes(k, logger=lambda s: None, clock=lambda: 0.0)

    # wg0 peer never answers :9009 (no_pong); wg1 peer's path hairpins (LOOP);
    # wg2 peer pongs (alive -> 5.0). FakeVerify keys on target/dev/(target,dev).
    verify = FakeVerify(dead={"10.111.11.1"}, loops={"10.179.179.1"})
    hc = HealthCheck(r, verify, logger=logs.append)

    dead, filtered = hc.run(ACTIVE, ALL_DEVS)
    blob = "\n".join(logs)

    check(set(dead) == {"wg0", "wg1"}, "dead set = {wg0, wg1}")
    check("reason=no_pong" in (dead.get("wg0") or ""),
          "wg0 (no :9009 answer) -> dead reason=no_pong")
    check("reason=loop_9009" in (dead.get("wg1") or ""),
          "wg1 (hairpin) -> dead reason=loop_9009 (echo gate never caught loops)")
    healthy = {l.split("iface=")[1].split()[0]
               for l in logs if l.startswith("TUNNEL_HEALTHY")}
    check(healthy == {"wg2"}, "healthy = {wg2}")
    check(filtered == ["eth0", "eth1", "frognet0", "wg2"],
          "ALL_DEVS after health filter excludes wg0+wg1")
    check("echo_failed" not in blob and "wrong_peer" not in blob,
          "no legacy echo-gate reasons (echo_failed / wrong_peer) - gate is ping-pong")

    print()
    print("ALL HEALTHCHECK ORACLE CHECKPOINTS PASS" if ok
          else "HEALTHCHECK ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
