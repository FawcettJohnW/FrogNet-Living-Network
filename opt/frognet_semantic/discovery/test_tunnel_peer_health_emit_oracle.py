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
test_tunnel_peer_health_emit_oracle.py - [TUNNEL_PEER_HEALTH_EMIT_V1]

THE OBSERVED BUG (Seattle5 2026-07-18 runMerge): the three tunnel peers were all
TUNNEL_HEALTHY (healthcheck's SRCLESS :9009 ping-pong on <peer>.1 answered over
each wg), yet descend emitted none of them and reap deleted their /24s. Cause: the
immediate tunnel peer is emitted ONLY if a measure to its .2 DISCOVERY plane
answers - and on real hardware that .2 plane is silent over the tunnel. The .1 the
healthcheck already proved was never consulted, so a health-proven peer was
discarded.

THE RULE: a tunnel that passed the healthcheck HAS a reachable peer .1. When the
.2 discovery plane is silent, descend must fall back to the SAME srcless :9009 the
healthcheck used (identity-src false-negatives here - the peer cannot return to our
.1 over the tunnel) and emit the peer as a measured immediate. It must NOT require
the .2 plane to independently re-prove a peer the healthcheck already proved.

SCENARIO: Seattle5 with one tunnel wg2 -> New-York-1 (10.102.60.1). NY1's .2
(10.102.60.2) is SILENT - no echo, no reflect, no measured rtt (exactly the
hardware condition). NY1 is ONLY an immediate (nothing vouches it via getHosts, so
there is no seed path to rescue it). A :9009 verify backend answers on 10.102.60.1
over wg2 (the healthcheck's proven srcless pong).

fail-on-old:  no .1 fallback -> v is None -> NY1 not emitted -> NO 10.102.60.0/24.
pass-on-new:  srcless :9009 on .1 pongs -> emit measured immediate -> route on wg2.

NOTE ON SCOPE: the sim's verify backend is src-blind, so this oracle proves the
CONTROL FLOW (silent .2 -> fall back to the .1 health probe -> route). The srcless
route on that fallback is lifted verbatim from healthcheck._probe (the mechanism
already proven to carry over tunnels on hardware), not invented here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)
from discovery import descend as _descend


IDENT = "10.250.250.1"


def build():
    # NY1's .2 discovery plane is SILENT: no echo answer for 10.102.60.2, and no
    # measured rtt to it. This is the exact hardware condition.
    echo = FakeEcho(answers={})
    rtt = FakeRtt(table={})              # no (wg2, 10.102.60.2) -> _measure(.2)=None
    gethosts = FakeGetHosts(children={})  # nothing vouches NY1: immediate-only
    broker = FakeBroker(by_one={}, iface_channel={"wg2": "New-York-1-10.102.60"})

    k = FakeKernel()
    k.seed(
        "default via 192.168.0.1 dev wlan1 metric 601",
        "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
        "10.102.60.0/24 dev wg2 scope link metric 22",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)

    # :9009 verify: NY1's .1 pongs over wg2 (healthcheck's proven srcless signal).
    # dead is empty -> measure_or_loop(10.102.60.1, wg2) returns a float.
    verify = FakeVerify()

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"wg2": "10.253.200.94"},
                     self_identity=IDENT, verify=verify,
                     logger=lambda s: None)
    disc.DEAD_IFACES = set()

    immediate = [("10.102.60.1", "wg2")]   # the tunnel peer, silent on .2
    active_devs = ["eth0", "wg2"]
    return disc, k, active_devs, immediate


def main():
    disc, k, active_devs, immediate = build()
    _descend.descend(disc, active_devs, immediate)
    # THE proof: the health-proven peer produced a CANDIDATE. Without the emit it is
    # empty, so the peer is "not a winner this pass" and reap deletes even its
    # bring-up /24 (exactly REAP_STALE 10.102.60.0/24 in the live log). With the emit
    # it is a verified winner and survives.
    emitted = bool(disc.CAND.get("10.102.60.0/24"))
    disc.promote()
    final = k.route_show()

    # corroboration: promote installed the descend-produced route (metric 5 .2),
    # which is present ONLY when the peer was emitted (the seeded /24 is not proof).
    installed = any(line.startswith("10.102.60.2") and "dev wg2" in line
                    for line in final.replace(";", "\n").splitlines())

    checks = [
        ("New-York-1 emitted as a candidate from the .1 health signal", emitted),
        ("promote installed the descend-produced wg2 route", installed),
    ]
    print("=== TUNNEL PEER HEALTH-EMIT ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL TUNNEL-PEER-HEALTH-EMIT ORACLE CHECKPOINTS PASS")
        return 0
    print("TUNNEL-PEER-HEALTH-EMIT ORACLE FAILED")
    print("--- final table ---")
    print(final)
    return 1


if __name__ == "__main__":
    sys.exit(main())
