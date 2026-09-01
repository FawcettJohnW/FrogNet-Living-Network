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
test_tunnel_kernel_immediate_oracle.py - a merge must REBUILD tunnel reachability
from the kernel, never ASSUME the tunnel daemon's cached state file is present.

Field case, Seattle5 2026-07-23. wg0 and wg1 both healthy (live handshakes,
allowed-ips 10.0.0.0/8, hundreds of MB transferred) and the kernel FIB holds

    10.111.11.0/24  dev wg0  scope link  metric 22
    10.102.60.0/24  dev wg1  scope link  metric 22

but /var/lib/frognet-tunnel/active/ was EMPTY, so live._active_states() returned []
and descend_downstream built its tunnel immediates from that empty list alone --
wg_kernel_nets was accepted as a parameter and silently discarded. Result: zero wg
probes in the whole merge, no CANDIDATE for any remote /24, wan=[], uplink_dev=-,
then reap_unverified_winners deleted the daemon's own scope-link /24s as "not a
winner this pass" and HOSTS_GATE dropped 12 hosts. Every node behind the tunnels
vanished while the tunnels themselves were perfectly healthy.

Fail-on-old / pass-on-new: with active_states=[] the OLD code returns no immediates;
the fix sources them from the kernel FIB, which is re-read every merge.
"""
import os as _os
import sys

_os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")

from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               HostStore)

PROBS = []


def check(ok, label):
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        PROBS.append(label)


def _disc(local="10.250.250.1"):
    routes = Routes(FakeKernel(), clock=lambda: 0.0)
    return Discovery(routes, FakeEcho(answers={}), FakeRtt(table={}),
                     FakeGetHosts(children={}), FakeBroker(), HostStore(),
                     local_ips={local, "127.0.0.1"},
                     dev_src_map={}, self_identity=local, verify=None)


def main():
    # ---- the exact Seattle5 shape: daemon state GONE, kernel FIB intact --------
    d = _disc()
    kernel = {"wg0": ["10.111.11.0/24"], "wg1": ["10.102.60.0/24"]}
    got = set(d.tunnel_immediates(active_states=[], wg_kernel_nets=kernel))
    check(("10.111.11.1", "wg0") in got,
          "daemon state absent: wg0 peer .1 recovered from kernel FIB")
    check(("10.102.60.1", "wg1") in got,
          "daemon state absent: wg1 peer .1 recovered from kernel FIB")

    # ---- daemon state present and AGREEING must not duplicate -----------------
    d = _disc()
    got = d.tunnel_immediates(active_states=[("wg0", ["10.111.11.0/24"])],
                              wg_kernel_nets={"wg0": ["10.111.11.0/24"]})
    check(got.count(("10.111.11.1", "wg0")) == 1,
          "kernel + daemon agreeing yields ONE immediate, not a duplicate")

    # ---- union: daemon knows a subnet the kernel has not installed yet --------
    d = _disc()
    got = set(d.tunnel_immediates(active_states=[("wg1", ["10.28.28.0/24"])],
                                  wg_kernel_nets={"wg0": ["10.111.11.0/24"]}))
    check(got == {("10.111.11.1", "wg0"), ("10.28.28.1", "wg1")},
          "union of kernel FIB and daemon state (neither source is dropped)")

    # ---- a DEAD iface stays excluded no matter which source names it ----------
    d = _disc()
    d.DEAD_IFACES = {"wg0"}
    got = set(d.tunnel_immediates(active_states=[("wg0", ["10.111.11.0/24"])],
                                  wg_kernel_nets={"wg0": ["10.111.11.0/24"],
                                                  "wg1": ["10.102.60.0/24"]}))
    check(got == {("10.102.60.1", "wg1")},
          "DEAD_IFACES still wins over the kernel FIB (dead wg0 excluded)")

    # ---- transit/chorus carry no identity and must never seed an immediate ----
    d = _disc()
    got = d.tunnel_immediates(active_states=[],
                              wg_kernel_nets={"wg0": ["10.253.200.28/30",
                                                      "10.254.1.0/24"]})
    check(got == [], "10.253/10.254 transit+chorus never become immediates")

    # ---- our own subnet is not an immediate -----------------------------------
    d = _disc(local="10.250.250.1")
    got = d.tunnel_immediates(active_states=[],
                              wg_kernel_nets={"wg0": ["10.250.250.0/24"]})
    check(got == [], "our own .1 is never emitted as a tunnel immediate")

    print()
    if PROBS:
        print("TUNNEL-KERNEL-IMMEDIATE ORACLE FAILED")
        return 1
    print("ALL TUNNEL-KERNEL-IMMEDIATE CHECKPOINTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
