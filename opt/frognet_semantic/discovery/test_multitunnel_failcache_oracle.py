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
test_multitunnel_failcache_oracle.py - guards descend._probe_through's per-merge
fail-cache KEY against the recurring tunnel-collapse regression.

THE BUG (regressed at least twice - see the July-17 NY2 diagnosis):
  Every tunnel avenue is (dev, via="") - wg0, wg1, wg2 all carry via="". If the
  fail-cache keys on (x_ip, via), all tunnels collapse to ONE entry (x_ip, "").
  The first tunnel probed (wg0) fails and _fail() poisons (x_ip, ""); wg1 and wg2
  are then skipped at the cache check BEFORE any probe. A node whose ONLY working
  avenue is a non-first tunnel is therefore never probed on it, gets no CANDIDATE,
  and never installs.

SCENARIO (isolates the bug - pure tunnels, no LAN avenue):
  Seattle5 (10.250.250.1) with three tunnels:
      wg0 -> BABox      10.111.11.1
      wg1 -> BAMacBook  10.179.178.1
      wg2 -> New-York-1 10.102.60.1
  New-York-2 (10.28.28.1) sits BEHIND New-York-1: surfaced via NY1's getHosts,
  and its .2 discovery plane reaches ONLY over wg2 (through NY1). wg0 is probed
  first and cannot reach it.

fail-on-old:  (x_ip, via)        -> wg0 poisons (10.28.28.1, ""); wg2 never probed;
                                    NO 10.28.28.0/24 route. Oracle FAILS.
pass-on-new:  (x_ip, via or dev) -> (10.28.28.1,"wg0") and (10.28.28.1,"wg2") are
                                    distinct; wg2 is probed, reflects OK, installs.
                                    Oracle PASSES.

No :9009 verify backend is wired (verify=None) - the fall-through _prove_dot1 is
inert, so a non-reaching avenue takes the _fail() path exactly as on hardware when
the daemon is dark. That is what makes the collapse observable in the sim.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeReflect, HostStore)
from discovery import descend as _descend


IDENT = "10.250.250.1"   # Seattle5's own identity


def build():
    # Tunnel peers answer at their .2 discovery alias; NY2 answers at its .2 too
    # (it is a real FrogNet node, just reachable only through NY1).
    echo = FakeEcho(answers={
        "10.111.11.2":  "BABox,10.111.11.1,,",
        "10.179.178.2": "BAMacBook,10.179.178.1,,",
        "10.102.60.2":  "New-York-1,10.102.60.1,,",
        "10.28.28.2":   "New-York-2,10.28.28.1,,",
    })

    rtt = FakeRtt(table={
        ("wg0", "10.111.11.2"):  [120], ("wg1", "10.179.178.2"): [115],
        ("wg2", "10.102.60.2"):  [119], ("wg2", "10.28.28.2"):   [140],
    })

    # NY1 vouches its behind-LAN guest NY2. The tunnel peers vouch the wider mesh
    # (kept minimal - only NY2 matters for this oracle).
    gethosts = FakeGetHosts(children={
        "10.102.60.1": [("10.102.60.1", "New-York-1"), ("10.28.28.1", "New-York-2")],
        "10.111.11.1": [("10.111.11.1", "BABox")],
        "10.179.178.1": [("10.179.178.1", "BAMacBook")],
    })

    broker = FakeBroker(by_one={}, iface_channel={
        "wg0": "BABox-10.111.11", "wg1": "BAMacBook-10.179.178",
        "wg2": "New-York-1-10.102.60"})

    k = FakeKernel()
    k.seed(
        "default via 192.168.0.1 dev wlan1 metric 601",
        "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
        "10.111.11.0/24 dev wg0 scope link metric 22",
        "10.179.178.0/24 dev wg1 scope link metric 22",
        "10.102.60.0/24 dev wg2 scope link metric 22",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)

    # reach: each tunnel peer on its own scope-link dev, and NY2 ONLY over wg2.
    # wg0/wg1 do NOT reach NY2 - so wg0 (probed first) fails and, on the buggy key,
    # poisons every tunnel for 10.28.28.1.
    reflect = FakeReflect(kernel=k, reach={
        ("10.111.11.2",  "wg0", ""),
        ("10.179.178.2", "wg1", ""),
        ("10.102.60.2",  "wg2", ""),
        ("10.28.28.2",   "wg2", ""),     # NY2 reachable only through NY1 (wg2)
    })

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"wg0": "10.253.200.110", "wg1": "10.253.200.102",
                                  "wg2": "10.253.200.94"},
                     reflect=reflect, self_identity=IDENT,
                     logger=lambda s: None)
    disc.DEAD_IFACES = set()

    immediate = [
        ("10.111.11.1", "wg0"), ("10.179.178.1", "wg1"), ("10.102.60.1", "wg2"),
    ]
    active_devs = ["eth0", "wg0", "wg1", "wg2"]
    return disc, k, active_devs, immediate


def main():
    disc, k, active_devs, immediate = build()
    _descend.descend(disc, active_devs, immediate)
    disc.promote()

    final = k.route_show()

    def has_route(dest, dev):
        for line in final.replace(";", "\n").splitlines():
            if line.startswith(dest) and f"dev {dev}" in line:
                return True
        return False

    checks = []
    # THE regression check: NY2 reached only via wg2 must survive the fail-cache.
    # This is the line that FAILS on (x_ip, via) and PASSES on (x_ip, via or dev).
    ny2 = has_route("10.28.28.0/24", "wg2")
    checks.append(("New-York-2 10.28.28.0/24 on wg2 (behind NY1, non-first tunnel)", ny2))
    # No regression to the immediate tunnel peers.
    checks.append(("BABox 10.111.11.0/24 on wg0", has_route("10.111.11.0/24", "wg0")))
    checks.append(("New-York-1 10.102.60.0/24 on wg2", has_route("10.102.60.0/24", "wg2")))

    print("=== MULTITUNNEL FAILCACHE ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL MULTITUNNEL-FAILCACHE ORACLE CHECKPOINTS PASS")
        return 0
    print("MULTITUNNEL-FAILCACHE ORACLE FAILED")
    print("--- final table ---")
    print(final)
    return 1


if __name__ == "__main__":
    sys.exit(main())
