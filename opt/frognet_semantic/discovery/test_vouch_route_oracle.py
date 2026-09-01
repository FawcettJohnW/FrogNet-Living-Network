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
test_vouch_route_oracle.py - [VOUCH_ROUTE_V1] regression.

The upstream's getHosts is the AUTHORITY for what to route. When an upstream
vouches a host, the node must establish a route to it THROUGH the upstream, even
if a direct echo to that host fails - a deep node cannot echo a far host over its
borrowed uplink lease (the return path breaks), but the upstream forwards it.

Reproduces Seattle3 (identity 10.130.130.1; client lease 10.160.160.191 on
Seattle6's AP, wlan1) reaching upstream Seattle6 (10.160.160.1), which vouches
three hosts: Seattle5 (Seattle3 CAN echo it) and BAMacBook + New-York-1 (Seattle3
CANNOT echo them - FAIL_ECHO on the borrowed lease). Asserts:

  A. 10.179.179.0/24 (echo FAILS, vouched) installs at metric 22 via 10.160.160.1
     - the route exists on the authority of the vouch alone.            [the fix]
  B. 10.102.60.0/24 (echo FAILS, vouched) likewise installs via 10.160.160.1.
  C. BAMacBook and New-York-1 are recorded as hosts (named) despite no echo.
  D. 10.250.250.0/24 (echo SUCCEEDS) installs at metric 22 and gets NO redundant
     metric-100 vouch fallback - vouch only wins when nothing better exists.
  E. with a FakeVerify marking the echo-failers' .1 as dead, the vouch routes
     are DROPPED (kind=vouch is NO LONGER exempt; an unreachable :9009 alive
     means honest absence, never a black-hole route). [FROGNET_ALIVE_9009_V1]
     fail for the same return-path reason the echo did).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

IDENT = "10.130.130.1"            # Seattle3 identity (.1 on its own AP, wlan0)
LEASE = "10.160.160.191"          # Seattle3's client lease on Seattle6's AP (wlan1)


def _run(verify=None):
    k = FakeKernel()
    k.seed("10.130.130.0/24 dev wlan0 proto kernel scope link src 10.130.130.1",
           "10.160.160.0/24 dev wlan1 proto kernel scope link src 10.160.160.191 metric 600")
    routes = Routes(k, clock=lambda: 0.0)
    # echo answers ONLY for the uplink parent and the one reachable host; the deep
    # remotes (BAMacBook .2, New-York-1 .2) are unanswered -> FAIL_ECHO.
    echo = FakeEcho(answers={
        "10.160.160.2": "Seattle6,10.160.160.1,10.250.250.1,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })
    rtt = FakeRtt(table={("wlan1", "10.160.160.2"): [23],
                         ("wlan1", "10.250.250.2"): [28]})
    # Seattle6 vouches three hosts, WITH names (getHosts surfaces name now)
    gethosts = FakeGetHosts(children={"10.160.160.1": [
        ("10.250.250.1", "Seattle5"),       # reachable: echo succeeds
        ("10.179.179.1", "BAMacBook"),      # echo FAILS, vouched
        ("10.102.60.1", "New-York-1"),      # echo FAILS, vouched
    ]})
    hs = HostStore()
    disc = Discovery(routes, echo, rtt, gethosts, FakeBroker(), hs,
                     local_ips={IDENT, "10.130.130.2", LEASE, "127.0.0.1"},
                     dev_src_map={}, self_identity=IDENT, verify=verify)
    disc.walk("wlan1", "10.160.160.1", 1)
    disc.promote()
    return k, hs


def _line_for(k, dest):
    show = k.route_show(dest)
    return " ".join(x for x in (show or "").replace(";", " ").split())


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    k, hs = _run()
    bamac = _line_for(k, "10.179.179.0/24")
    ny1 = _line_for(k, "10.102.60.0/24")
    s5 = _line_for(k, "10.250.250.0/24")
    print(f"    BAMacBook (echo FAIL): {bamac}")
    print(f"    New-York-1 (echo FAIL): {ny1}")
    print(f"    Seattle5  (echo OK)  : {s5}")
    print()

    check("via 10.160.160.1" in bamac and "metric 22" in bamac,
          "A   10.179.179.0/24 routed via upstream at metric 22 despite FAIL_ECHO")
    check("via 10.160.160.1" in ny1 and "metric 22" in ny1,
          "B   10.102.60.0/24 routed via upstream at metric 22 despite FAIL_ECHO")
    check("metric 22" in s5,
          "D   10.250.250.0/24 (echo OK) is the metric-22 winner")
    check("metric 100" not in s5,
          "D   ...and carries NO redundant metric-100 vouch fallback")

    # C: hosts recorded by name even though echo failed
    names = " ".join(sorted(hs_names(hs)))
    print(f"    hosts recorded: {names}")
    check("BAMacBook" in names and "New-York-1" in names,
          "C   BAMacBook + New-York-1 recorded as named hosts (no echo needed)")

    # E: [ALIVE_GATE_VOUCH_V1 + PINGPONG_GATE_V1] the echo-failers are deep hosts
    # unreachable directly, so their :9009 fails on BOTH .1 and .2 - the same
    # broken return path that fails their echo. The walk therefore forms NO direct
    # candidate (the .2 gate is dead), leaving only the vouch; the vouch then needs
    # the dest .1 to PONG over its route, which it does not, so it installs NOTHING
    # (honest absence, never a black-hole LAN fallback). This is the rule that
    # stops the New-York-2/BABox loop. Measured (echo-OK) winners are unchanged.
    kv, _ = _run(verify=FakeVerify(
        dead={"10.179.179.1", "10.179.179.2", "10.102.60.1", "10.102.60.2"}))
    bamac_v = _line_for(kv, "10.179.179.0/24")
    ny1_v = _line_for(kv, "10.102.60.0/24")
    check(not bamac_v,
          "E   vouch route to 10.179.179 DROPPED on a dead :9009 (alive-gated)")
    check(not ny1_v,
          "E   vouch route to 10.102.60 DROPPED on a dead :9009 (alive-gated)")

    print()
    print("ALL VOUCH-ROUTE ORACLE CHECKPOINTS PASS" if ok else "VOUCH-ROUTE PROOF FAILED")
    return 0 if ok else 1


def hs_names(hs):
    # HostStore records names; surface them however the store exposes them.
    for attr in ("added", "entries", "hosts"):
        v = getattr(hs, attr, None)
        if v is None:
            continue
        v = v() if callable(v) else v
        out = set()
        for item in v:
            if isinstance(item, (tuple, list)) and item:
                out.add(item[0])
            elif isinstance(item, str):
                out.add(item)
        if out:
            return out
    return set()


if __name__ == "__main__":
    sys.exit(main())
