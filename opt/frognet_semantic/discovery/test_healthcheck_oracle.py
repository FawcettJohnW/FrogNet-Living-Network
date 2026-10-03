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
test_healthcheck_oracle.py - [PINGPONG_HEALTH_V1] + [HEALTH_ON_DOT2_V1]

Proves run_tunnel_health_check gates tunnel liveness on the :9009 ping-pong (NOT the
HTTP frognet_echo.php gate) and runs it on the peer's .2 DISCOVERY plane - never on
the production .1.

[HEALTH_ON_DOT2_V1] John 2026-09-25: "All discovery happens on .2." The probe used
to install <peer>.1/32 dev wgN metric 6 at the start of every merge; that /32 beat
the installed /24, so production traffic to every tunnel peer's .1 rode a probe
route until it was deleted (Seattle5 2026-09-25 14:59:58-59, BABox/HappyDog/
Seattle3B).

Drives the REAL HealthCheck.run with a FakeVerify keyed on the .2 targets:
  wg0  peer .2 pong=None  -> dead reason=no_pong
  wg1  peer .2 pong=LOOP  -> dead reason=loop_9009
  wg2  peer .2 pong=alive -> healthy
and the .1 of every peer is ALIVE in the fixture, so a probe that still targets .1
reports all three healthy and fails the dead-set checks.

PLANE GUARD: every route the health check writes is a peer .2/32, and no .1 address
is ever given a route. Old code writes three .1/32s and fails it.

REGRESSION GUARD: dead reasons are no_pong / loop_9009, never echo_failed /
wrong_peer.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.healthcheck import HealthCheck
from discovery.sources import FakeVerify

ACTIVE = [
    ("wg0", "BABox-10.111.11", "10.111.11.0/24"),       # peer .2 = 10.111.11.2
    ("wg1", "BAMacBook-10.179.179", "10.179.179.0/24"), # peer .2 = 10.179.179.2
    ("wg2", "Seattle5-10.250.250", "10.250.250.0/24"),  # peer .2 = 10.250.250.2
]
ALL_DEVS = ["eth0", "eth1", "frognet0", "wg0", "wg1", "wg2"]
PEER_DOT1 = {"10.111.11.1", "10.179.179.1", "10.250.250.1"}


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

    # Verdicts live on the .2 plane. Every .1 is left alive on purpose: a health
    # check that still probes .1 sees three healthy peers.
    verify = FakeVerify(dead={"10.111.11.2"}, loops={"10.179.179.2"})
    hc = HealthCheck(r, verify, logger=logs.append)

    k.mutate_log.clear()
    dead, filtered = hc.run(ACTIVE, ALL_DEVS)
    blob = "\n".join(logs)

    check(set(dead) == {"wg0", "wg1"}, "dead set = {wg0, wg1}")
    check("reason=no_pong" in (dead.get("wg0") or ""),
          "wg0 (no :9009 answer on .2) -> dead reason=no_pong")
    check("peer_ip=10.111.11.2" in (dead.get("wg0") or ""),
          "wg0 dead reason names the .2 it probed")
    check("reason=loop_9009" in (dead.get("wg1") or ""),
          "wg1 (hairpin on .2) -> dead reason=loop_9009")
    healthy = {l.split("iface=")[1].split()[0]
               for l in logs if l.startswith("TUNNEL_HEALTHY")}
    check(healthy == {"wg2"}, "healthy = {wg2}")
    check("peer_ip=10.250.250.2" in blob, "wg2 proven healthy on 10.250.250.2")
    check(filtered == ["eth0", "eth1", "frognet0", "wg2"],
          "ALL_DEVS after health filter excludes wg0+wg1")
    check("echo_failed" not in blob and "wrong_peer" not in blob,
          "no legacy echo-gate reasons (echo_failed / wrong_peer) - gate is ping-pong")

    touched = {d.split("/")[0] for (_v, d, _m, _rc) in k.mutate_log}
    check(touched and all(t.endswith(".2") for t in touched),
          f"every health route is a peer .2/32 (touched={sorted(touched)})")
    check(not (touched & PEER_DOT1),
          "no peer's production .1 is ever given a probe route")

    print()
    print("ALL HEALTHCHECK ORACLE CHECKPOINTS PASS" if ok
          else "HEALTHCHECK ORACLE PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
