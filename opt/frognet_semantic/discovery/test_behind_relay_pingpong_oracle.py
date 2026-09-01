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
"""test_behind_relay_pingpong_oracle.py - prove-then-route for a node BEHIND a seg-relay.

Node-faithful Seattle5 (from seattle5.log, build ak):
  - eth0 segment 10.250.250.0/24; Seattle5 is .1.
  - 10.250.250.191 is Seattle3 (a seg-relay: echoes remote identity 10.130.130.1).
  - .20/.85/.134 are PLAIN clients (do NOT self-identify as a FrogNet node).
  - Seattle2 (10.120.120.1) is NOT on the segment - it sits BEHIND Seattle3 and is
    surfaced ONLY via Seattle3's getHosts. Its .2 discovery alias is NOT forwarded
    across Seattle3, so reflect on 10.120.120.2 can never succeed (matches the node:
    getHosts 10.120.120.2 times out, rows=0). Its PRODUCTION .1 IS reachable through
    Seattle3 (John's proven manual `ip r r 10.120.120.0/24 via 10.250.250.191 && ping`).
  - Three tunnels: wg0->BABox, wg1->BAMacBook, wg2->New-York-1.

THE GATE (prove-then-route, no vouch):
  A node reached through a relay is routed ONLY if its .1 answers the :9009 ping-pong
  THROUGH that relay's segment next-hop. This oracle runs discovery twice:

    alive: 10.120.120.1 pongs via 10.250.250.191  -> route 10.120.120.0/24 via .191 INSTALLS
    dead:  10.120.120.1 does NOT pong via .191     -> NO route installs (clean failure)

  This FAILS on the old vouch code (which installs 10.120.120.0/24 via .191 on the bare
  getHosts vouch regardless of .1 liveness -> a fake route in the `dead` case) and PASSES
  on prove-then-route (route present iff proven; absent otherwise).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeReflect, FakeVerify, HostStore)
from discovery import descend as _descend

IDENT = "10.250.250.1"

MESH5 = [("10.250.250.1", "Seattle5"), ("10.130.130.1", "Seattle3"),
         ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox"),
         ("10.179.178.1", "BAMacBook")]
S3LIST = [("10.250.250.1", "Seattle5"), ("10.130.130.1", "Seattle3"),
          ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox"),
          ("10.120.120.1", "Seattle2"), ("10.179.178.1", "BAMacBook")]


def build(seattle2_dot1_pongs_via_relay):
    echo = FakeEcho(answers={
        "10.250.250.191": "Seattle3,10.130.130.1,10.250.250.191,0.0.0.0",  # seg-relay
        "10.111.11.2":  "BABox,10.111.11.1,,",
        "10.179.178.2": "BAMacBook,10.179.178.1,,",
        "10.102.60.2":  "New-York-1,10.102.60.1,,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",   # our own .2 (buggy self-probe tell)
        # .20/.85/.134 absent -> plain clients, not FrogNet identities
    })
    rtt = FakeRtt(table={
        ("eth0", "10.250.250.191"): [16],
        ("wg0", "10.111.11.2"): [120], ("wg1", "10.179.178.2"): [115],
        ("wg2", "10.102.60.2"): [119],
    })
    gethosts = FakeGetHosts(children={
        "10.250.250.1": MESH5,     # on-segment neighbours collapse to our own list
        "10.130.130.1": S3LIST,    # Seattle3 surfaces its child Seattle2
        "10.111.11.1": MESH5, "10.179.178.1": MESH5, "10.102.60.1": MESH5,
    })
    broker = FakeBroker(by_one={}, iface_channel={
        "wg0": "BABox-10.111.11", "wg1": "BAMacBook-10.179.178", "wg2": "New-York-1-10.102.60"})
    k = FakeKernel()
    k.seed(
        "default via 192.168.0.1 dev wlan1 metric 601",
        "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
        "10.111.11.0/24 dev wg0 scope link metric 22",
        "10.179.178.0/24 dev wg1 scope link metric 22",
        "10.102.60.0/24 dev wg2 scope link metric 22",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
    # reflect (.2 plane): Seattle3 reflects via .191; Seattle2's .2 NEVER (relay won't
    # forward the .2 plane); tunnel peers reflect on scope-link.
    reflect = FakeReflect(kernel=k, reach={
        ("10.130.130.2", "eth0", "10.250.250.191"),
        ("10.111.11.2", "wg0", ""), ("10.179.178.2", "wg1", ""), ("10.102.60.2", "wg2", ""),
    })
    # :9009 ping-pong (.1 plane, per-avenue): Seattle3's .1 pongs via .191; Seattle2's .1
    # pongs via .191 ONLY in the `alive` variant.
    vreach = {("10.130.130.1", "eth0", "10.250.250.191")}
    if seattle2_dot1_pongs_via_relay:
        vreach.add(("10.120.120.1", "eth0", "10.250.250.191"))
    verify = FakeVerify(kernel=k, reach=vreach)

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"eth0": "10.250.250.1"},
                     reflect=reflect, self_identity=IDENT, verify=verify,
                     logger=lambda s: None)
    disc.DEAD_IFACES = set()
    immediate = [
        ("10.111.11.1", "wg0"), ("10.179.178.1", "wg1"), ("10.102.60.1", "wg2"),
        ("10.250.250.191", "eth0"),
        ("10.250.250.20", "eth0"), ("10.250.250.85", "eth0"), ("10.250.250.134", "eth0"),
    ]
    _descend.descend(disc, ["eth0", "wg0", "wg1", "wg2"], immediate)
    disc.promote()
    return k.route_show()


def _has(table, dest, via):
    for line in table.replace(";", "\n").splitlines():
        if line.startswith(dest) and "dev eth0" in line:
            if via == "" or f"via {via} " in (line + " "):
                return True
    return False


def _routed_via_self(table, dest):
    for line in table.replace(";", "\n").splitlines():
        if line.startswith(dest) and "via 10.250.250.1 " in (line + " "):
            return True
    return False


def main():
    alive = build(seattle2_dot1_pongs_via_relay=True)
    dead = build(seattle2_dot1_pongs_via_relay=False)
    checks = []

    # Seattle3 (the on-segment relay) routes via .191 in both cases.
    checks.append(("Seattle3 10.130.130.0/24 via .191 (alive case)",
                   _has(alive, "10.130.130.0/24", "10.250.250.191")))
    checks.append(("Seattle3 10.130.130.0/24 via .191 (dead case)",
                   _has(dead, "10.130.130.0/24", "10.250.250.191")))

    # PROVEN: Seattle2's .1 pongs via .191 -> route installs via the relay next-hop.
    checks.append(("Seattle2 10.120.120.0/24 via .191 when .1 PONGs (proven route)",
                   _has(alive, "10.120.120.0/24", "10.250.250.191")))

    # ANTI-FAKE-ROUTE (the gate that fails on the old vouch code): .1 does NOT pong ->
    # NO route to Seattle2 at all. Old code installs it anyway on the bare vouch.
    checks.append(("Seattle2 NOT routed when .1 does NOT pong (no fake route)",
                   not _has(dead, "10.120.120.0/24", "")))

    # never a route via self.
    checks.append(("Seattle2 NOT routed via self 10.250.250.1",
                   not _routed_via_self(alive, "10.120.120.0/24")))
    checks.append(("Seattle3 NOT routed via self 10.250.250.1",
                   not _routed_via_self(alive, "10.130.130.0/24")))

    print("=== BEHIND-RELAY PING-PONG ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL BEHIND-RELAY PINGPONG ORACLE CHECKPOINTS PASS")
        return 0
    print("BEHIND-RELAY PINGPONG ORACLE FAILED")
    print("--- alive table ---"); print(alive)
    print("--- dead table ---"); print(dead)
    return 1


if __name__ == "__main__":
    sys.exit(main())
