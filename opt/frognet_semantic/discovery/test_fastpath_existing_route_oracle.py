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
test_fastpath_existing_route_oracle.py - [PINGPONG_FASTPATH_V1]

John's 2026-06-19 rule: if discovery sees an existing route whose :9009 is still
up and responsive, treat it as valid and go straight to the 2nd/3rd-level
investigation (getHosts) - no probe-install/echo gyrations. Only for DISCOVERED
dests; unconfirmed routes are still reaped; a LOOP/dead existing route does NOT
fast-path (it falls through to the full walk and is re-derived or reaped).

Drives REAL Discovery.walk over a tunnel dest whose /24 winner is ALREADY
installed (metric 22, dev wg2), with the HTTP echo DOWN:

  A  existing winner + :9009 alive ->
       - dest is a CANDIDATE (kept), even with echo down
       - ZERO probe_install calls (the "no gyration" property)
       - getHosts STILL ran (2nd/3rd-level investigation proceeds)
  B  existing winner + :9009 LOOP -> NOT fast-pathed (falls through; dest dropped)
  C  existing winner + :9009 dead -> NOT fast-pathed (falls through; dest dropped)

REGRESSION: the echo-gate code has no fast-path - it probe_installs (gyration) and,
echo being down, FAIL_ECHOs -> no candidate. So A fails on baseline on BOTH the
candidate check and probe_install==0.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore, FakeVerify)

PEER, PIP = "10.102.60.1", "10.102.60.2"
DEST = "10.102.60.0/24"


def _walk(verify):
    k = FakeKernel()
    # the /24 winner is ALREADY installed on wg2 at metric 22 (a prior merge)
    k.seed(f"{DEST} dev wg2 scope link metric 22",
           "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1")
    routes = Routes(k, clock=lambda: 0.0)
    broker = FakeBroker(by_one={PEER: ("New-York-1-10.102.60", DEST, PEER)},
                        iface_channel={"wg2": "New-York-1-10.102.60"})
    # echo DOWN everywhere; getHosts surfaces one deep child (2nd level)
    gethosts = FakeGetHosts(children={PEER: [("10.88.88.1", "DeepHost")]})
    disc = Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}), gethosts,
                     broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"wg2": "10.253.203.118"},
                     self_identity="10.250.250.1", verify=verify)
    n = {"installs": [], "get_hosts": 0}
    opi, ogh = disc.r.probe_install, disc.gethosts.get_hosts
    disc.r.probe_install = lambda pip, *a, **k: (n["installs"].append(pip), opi(pip, *a, **k))[1]
    disc.gethosts.get_hosts = lambda *a, **k: (n.__setitem__("get_hosts", n["get_hosts"] + 1), ogh(*a, **k))[1]
    disc.walk("wg2", PEER, 1, parent_via="")
    return disc, n


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    # A: existing winner + :9009 alive -> kept, no gyration, getHosts ran.
    a, na = _walk(FakeVerify())
    check(bool(a.CAND.get(DEST)),
          "A1 existing winner + :9009 alive -> dest kept as CANDIDATE (echo down)")
    check(PIP not in na["installs"],
          f"A2 dest .2 never probe-installed (no gyration); installs={na['installs']}")
    check(na["get_hosts"] >= 1,
          "A3 getHosts STILL ran -> 2nd/3rd-level investigation proceeds")

    # B: existing winner but :9009 LOOPs -> not fast-pathed; falls through; dropped.
    b, nb = _walk(FakeVerify(loops={PIP}))
    check(not b.CAND.get(DEST),
          "B  existing winner + :9009 LOOP -> NOT fast-pathed, dest dropped")

    # C: existing winner but :9009 dead -> not fast-pathed; falls through; dropped.
    c, _ = _walk(FakeVerify(dead={PIP}))
    check(not c.CAND.get(DEST),
          "C  existing winner + :9009 dead -> NOT fast-pathed, dest dropped")

    print()
    print("ALL FASTPATH ORACLE CHECKPOINTS PASS" if ok else "FASTPATH PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
