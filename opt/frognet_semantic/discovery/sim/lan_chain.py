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
sim/lan_chain.py - multi-host reproduction of the Seattle LAN-relay chain and the
"deep children don't learn their ancestors" failure, grounded in four real
runMerge logs (2026-06-05, Seattle5/6/3/2).

Topology (each arrow = "is a DHCP guest on the AP of"):

    Seattle2 -> Seattle3 -> Seattle6 -> Seattle5 == wg ==> {NY1, NY2, BAMacBook, BABox*}
    10.120.120  10.130.130  10.160.160  10.250.250        10.102.60 10.28.28 10.179.179 10.111.11*
                                         (gateway)                              (*BABox tunnel dead)

Observed per-node final tables (the scenario to reproduce):
  Seattle6 (1 hop from gw): knows the tunnel remotes (NY1/NY2/BAMacBook).
  Seattle3 (2 hops):        MISSING all tunnel remotes - it WALKS them (Seattle6
                            vouches them) but every probe FAIL_ECHOs.
  Seattle2 (3 hops):        MISSING all tunnel remotes - Seattle3 never learned
                            them, so never vouches them; pure cascade.

Why (grounded in the logs' own lease addresses): a LAN HOP probe is NOT src-pinned
(contrast the tunnel probes, which carry `src 10.253.203.x`). The kernel sources it
from the prober's client lease on its uplink segment:
  Seattle6 sources 10.250.250.221  - on the GATEWAY's /24, which the remotes route
                                      back to (it is their transit) -> echo returns.
  Seattle3 sources 10.160.160.191  - on Seattle6's /24, two hops behind the gateway,
  Seattle2 sources 10.130.130.47   - on Seattle3's /24, three hops behind,
                                      neither of which the remotes can return to
                                      -> echo times out -> host never learned.

So crossing the gateway<->tunnel boundary, only a DIRECT guest of the gateway has a
return-routable source. That is the rule ChainSystem models. It is structural (depth
from gateway), so it is stable across convergence cycles - matching the persistent
failure in the logs.

CAVEAT (flagged, needs a remote-side table to confirm): whether the failure is
exactly "remote lacks a route to the deep /24" vs the wg /30-transit variant cannot
be settled from these four LAN-side logs alone. The reproduction is faithful to the
observed LAN-side behaviour; the precise physics across the boundary awaits an NY1 /
BAMacBook `ip r`. The fix is evaluated against this scenario either way.
"""
from .system import TopologySpec, NodeSpec, System

GATEWAY = "Seattle5"

# tunnel remotes (direct wg peers of the gateway)
REMOTES = {"10.102.60": "NY1", "10.28.28": "NY2", "10.179.179": "BAMacBook"}
DEAD_REMOTE = ("10.111.11", "BABox")


def build_seattle_chain() -> TopologySpec:
    s5 = NodeSpec("Seattle5", "10.250.250")                                   # gateway
    s6 = NodeSpec("Seattle6", "10.160.160", guest_on=("Seattle5", "10.250.250.221"))
    s3 = NodeSpec("Seattle3", "10.130.130", guest_on=("Seattle6", "10.160.160.191"))
    s2 = NodeSpec("Seattle2", "10.120.120", guest_on=("Seattle3", "10.130.130.47"))
    ny1 = NodeSpec("NY1", "10.102.60")
    ny2 = NodeSpec("NY2", "10.28.28")
    bam = NodeSpec("BAMacBook", "10.179.179")
    bab = NodeSpec("BABox", "10.111.11", dead=True)                           # wg0 dead
    return TopologySpec(
        nodes=[s5, s6, s3, s2, ny1, ny2, bam, bab],
        tunnels=[("Seattle5", "NY1"), ("Seattle5", "NY2"),
                 ("Seattle5", "BAMacBook"), ("Seattle5", "BABox")],
    )


class ChainSystem(System):
    """System whose echo models the borrowed-lease return-path failure across the
    gateway<->tunnel boundary."""

    def __init__(self, spec, gateway=GATEWAY):
        self.gateway = gateway
        super().__init__(spec)

    def _is_tunnel_remote(self, name):
        return name in self.tunnels.get(self.gateway, [])

    def _lan_depth(self, name):
        """Hops up the guest chain to the gateway. gateway=0, direct guest=1, ..."""
        d, cur, seen = 0, name, set()
        while cur != self.gateway:
            n = self.node.get(cur)
            if not n or not n.guest_on or cur in seen:
                return None                      # not on the gateway's LAN chain
            seen.add(cur)
            cur = n.guest_on[0]
            d += 1
        return d

    def _echo_carries(self, observer, query_ip):
        owner = self._owner_of(query_ip)
        if owner is None:
            return False
        if owner.name == observer:
            return True
        o_remote = self._is_tunnel_remote(observer)
        t_remote = self._is_tunnel_remote(owner.name)
        if o_remote == t_remote:
            return True                          # same side of the boundary
        # crossing gateway<->tunnel: the LAN-side endpoint's source is its uplink
        # lease; only return-routable if it sits on the gateway's own /24, i.e. the
        # LAN node is the gateway itself (0) or a direct guest of it (1).
        lan = owner.name if o_remote else observer
        depth = self._lan_depth(lan)
        return depth is not None and depth <= 1
