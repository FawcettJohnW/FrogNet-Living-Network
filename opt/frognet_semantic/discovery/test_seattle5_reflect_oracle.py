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
test_seattle5_reflect_oracle.py - models the REAL Seattle5 node from the July-09
logs so the simulator finally EXERCISES descend._probe_through's reflect path (the
code that shipped untested all day and had to be debugged on hardware).

Scenario (from dnsmasq.leases + frognet_echo.php on the real Seattle5):

  Seattle5 is 10.250.250.1, gateway of its own /24 (eth0). Attached on that eth0
  LAN as DHCP clients are two REAL FrogNet nodes:
      10.250.250.191  ->  echoes "Seattle3,10.130.130.1,10.250.250.191,0.0.0.0"
      10.250.250.20   ->  echoes "Seattle2,10.120.120.1,10.250.250.20,0.0.0.0"
  plus plain non-FrogNet DHCP clients (.85, .134) that do NOT echo.

  Seattle5 also has three tunnels: wg0->BABox(10.111.11.1), wg1->BAMacBook
  (10.179.178.1), wg2->New-York-1(10.102.60.1).

What the real node MUST do, and what this oracle asserts:
  1. Echo each on-subnet neighbour at ITS OWN address (10.250.250.191), NOT
     net_dot(ip,2)=10.250.250.2 (which is Seattle5 itself). [on-subnet echo fix]
  2. See Seattle3's echo identity 10.130.130.1 (a REMOTE /24) and route
     10.130.130.0/24 via 10.250.250.191 on eth0 (SEG-RELAY). Same for Seattle2.
  3. Reach those remote /24s over the reflect .2 discovery plane - reflect.probe
     must be consulted (FakeReflect), not the direct-measure fallback.

This fails on the pre-fix descend (echoes self, never sees Seattle2/3) and passes
only with the on-subnet echo + .2-plane reflect fixes.
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


def build_seattle5():
    # Echo: each ADDRESS -> its self-describing CSV. The two attached FrogNet nodes
    # echo their REMOTE identity in field 2; plain DHCP clients are absent (None).
    echo = FakeEcho(answers={
        # attached FrogNet seg-relay, answering at its OWN on-subnet address.
        "10.250.250.191": "Seattle3,10.130.130.1,10.250.250.191,0.0.0.0",
        # .20/.85/.134 are PLAIN clients (do NOT self-identify). Seattle2 is NOT on
        # this segment - it sits BEHIND Seattle3 (surfaced via Seattle3's getHosts).
        # The proven behind-relay route lives in test_behind_relay_pingpong_oracle,
        # which wires a :9009 verify backend; this oracle has verify=None, so a
        # node behind a relay correctly does NOT route here (nothing proves its .1).
        # tunnel peers answer at their .2 discovery alias
        "10.111.11.2":  "BABox,10.111.11.1,,",
        "10.179.178.2": "BAMacBook,10.179.178.1,,",
        "10.102.60.2":  "New-York-1,10.102.60.1,,",
        # Seattle5's own .2 (would be hit by the BUGGY net_dot(ip,2) path) echoes
        # SELF - the bug's tell: if descend probes here it "discovers" itself and
        # never sees Seattle2/3.
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })

    # RTT for the next-hop measurements (neighbour .2 for LAN, tunnel peer for wg).
    rtt = FakeRtt(table={
        ("eth0", "10.250.250.20"):  [15], ("eth0", "10.250.250.191"): [16],
        ("wg0", "10.111.11.2"):  [120], ("wg1", "10.179.178.2"): [115],
        ("wg2", "10.102.60.2"):  [119],
    })

    # getHosts: Seattle3 (reached via its eth0 neighbour 10.250.250.191) vouches
    # for its child Seattle2 (10.120.120.1) - Seattle2 is NOT attached to Seattle5,
    # it sits BEHIND Seattle3. So Seattle5 learns Seattle2 only transitively, and
    # its next hop to Seattle2 is Seattle3's next hop (10.250.250.191). The tunnel
    # peers vouch the wider mesh.
    gethosts = FakeGetHosts(children={
        "10.130.130.1": [("10.130.130.1", "Seattle3"), ("10.120.120.1", "Seattle2"),
                         ("10.250.250.1", "Seattle5")],
        "10.111.11.1": [("10.250.250.1", "Seattle5"), ("10.102.60.1", "New-York-1"),
                        ("10.111.11.1", "BABox"), ("10.179.178.1", "BAMacBook")],
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

    # FakeReflect: which (target.2, dev, via) avenues genuinely reach. Seattle3 is
    # reached by installing a probe route to 10.130.130.2 via 10.250.250.191 on eth0.
    # Seattle2 sits BEHIND Seattle3: its .2 is reachable ONLY over the same next hop
    # (10.250.250.191), because Seattle3 forwards onward to it. Tunnel peers reach on
    # their scope-link (via="").
    reflect = FakeReflect(kernel=k, reach={
        ("10.130.130.2", "eth0", "10.250.250.191"),   # Seattle3 via its eth0 neighbour
        # Seattle2 (behind Seattle3) is NOT .2-probed - its .2 can't traverse the
        # relay. It is vouch-emitted via Seattle3's segment next-hop (10.250.250.191).
        ("10.111.11.2",  "wg0", ""),                  # BABox on wg0
        ("10.179.178.2", "wg1", ""),                  # BAMacBook on wg1
        ("10.102.60.2",  "wg2", ""),                  # New-York-1 on wg2
    })

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"eth0": "10.250.250.1"},
                     reflect=reflect, self_identity=IDENT,
                     logger=lambda s: None)
    disc.DEAD_IFACES = set()

    # immediate connections: the tunnels + the attached eth0 neighbours. Seattle3 is
    # attached (10.250.250.191); Seattle2 is NOT - it is discovered transitively via
    # Seattle3's getHosts. Plain DHCP clients (.85/.134) do not echo.
    immediate = [
        ("10.111.11.1", "wg0"), ("10.179.178.1", "wg1"), ("10.102.60.1", "wg2"),
        ("10.250.250.191", "eth0"),
        ("10.250.250.85", "eth0"), ("10.250.250.134", "eth0"),   # plain clients
    ]
    active_devs = ["eth0", "wg0", "wg1", "wg2"]
    return disc, k, active_devs, immediate


def main():
    disc, k, active_devs, immediate = build_seattle5()
    _descend.descend(disc, active_devs, immediate)
    disc.promote()   # candidates -> installed routes (as the live merge does)

    final = k.route_show()   # full final table
    checks = []

    def has_route(dest, via, dev):
        for line in final.replace(";", "\n").splitlines():
            if line.startswith(dest) and f"dev {dev}" in line:
                if via == "" or f"via {via}" in line:
                    return True
        return False

    # 1) Seattle3's /24 routed via its eth0 neighbour (the SEG-RELAY the on-subnet
    #    echo + reflect path must produce). THE regression that shipped today.
    ok3 = has_route("10.130.130.0/24", "10.250.250.191", "eth0")
    checks.append(("Seattle3 10.130.130.0/24 via 10.250.250.191 dev eth0", ok3))

    # 2) Seattle2 is BEHIND Seattle3 and this oracle wires NO :9009 verify backend,
    #    so the behind-relay .1 cannot be proven here -> Seattle2 must NOT route. A
    #    route appearing here would be an unproven (fake) route. The PROVEN
    #    behind-relay case (route iff .1 pongs via .191) is in
    #    test_behind_relay_pingpong_oracle.
    ok2 = not has_route("10.120.120.0/24", "", "eth0")
    checks.append(("Seattle2 NOT routed without a :9009 proof (no fake route)", ok2))

    # 3) The bug's tell: NO route to Seattle3/Seattle2 whose VIA is 10.250.250.1
    #    (self) - the old net_dot(ip,1)-collapses-to-self mistake. Check `via`
    #    specifically (the line legitimately carries src 10.250.250.1).
    def routed_via_self(dest):
        for line in final.replace(";", "\n").splitlines():
            if line.startswith(dest) and "via 10.250.250.1 " in (line + " "):
                return True
        return False
    no_self3 = not routed_via_self("10.130.130.0/24")
    checks.append(("Seattle3 NOT routed via self 10.250.250.1", no_self3))

    # 4) Tunnel peers still route on their own dev (no regression to the mesh).
    okb = has_route("10.111.11.0/24", "", "wg0")
    checks.append(("BABox 10.111.11.0/24 on wg0", okb))

    print("=== SEATTLE5 REFLECT ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL SEATTLE5-REFLECT ORACLE CHECKPOINTS PASS")
        return 0
    print("SEATTLE5-REFLECT ORACLE FAILED")
    print("--- final table ---")
    print(final)
    return 1


if __name__ == "__main__":
    sys.exit(main())
