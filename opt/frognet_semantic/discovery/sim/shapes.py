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
shapes.py - topology-shape builders over the WG tunnel graph + multi-LAN-over-
Internet. Each returns a TopologySpec the System runs the REAL ported discovery
against. Shapes are tunnel arrangements (real WG pairings the broker would hand
out); intra-LAN uses the proven guest pattern.

NOTE: multi-`.1`-host L2 segments and LAN-broker short-circuit pathways are NOT
modeled here - they need the real LAN-broker contract / a real multi-host-LAN
log to ground without inventing. Shapes ride the tunnel graph, which is real.
"""
from .system import TopologySpec, NodeSpec


def _node(i):
    return NodeSpec(f"N{i}", f"10.{10 + i}.{10 + i}")


def snake(n):
    """Chain: N0-N1-...-N(n-1) (each linked by a tunnel)."""
    nodes = [_node(i) for i in range(n)]
    tunnels = [(f"N{i}", f"N{i+1}") for i in range(n - 1)]
    return TopologySpec(nodes=nodes, tunnels=tunnels)


def ring(n):
    """Circle: snake plus a tunnel closing N(n-1)-N0 (multi-path)."""
    s = snake(n)
    s.tunnels.append((f"N{n-1}", "N0"))
    return s


def star(n):
    """Hub N0 with n leaves tunneled to it."""
    nodes = [_node(0)] + [_node(i) for i in range(1, n + 1)]
    tunnels = [("N0", f"N{i}") for i in range(1, n + 1)]
    return TopologySpec(nodes=nodes, tunnels=tunnels)


def snowflake(branches, leaves_per):
    """Star of stars: center C, `branches` sub-hubs tunneled to C, each sub-hub
    with `leaves_per` leaves tunneled to it."""
    nodes = [NodeSpec("C", "10.1.1")]
    tunnels = []
    k = 0
    for b in range(branches):
        sh = f"S{b}"; k += 1
        nodes.append(NodeSpec(sh, f"10.{20+k}.{20+k}"))
        tunnels.append(("C", sh))
        for l in range(leaves_per):
            k += 1
            ln = f"S{b}L{l}"
            nodes.append(NodeSpec(ln, f"10.{20+k}.{20+k}"))
            tunnels.append((sh, ln))
    return TopologySpec(nodes=nodes, tunnels=tunnels)


def two_lan_over_internet():
    """Two LANs, each a gateway (Internet + StreamingFrog) plus a LAN guest with
    no broker of its own (its routes ride the LAN). Gateways tunneled across the
    Internet (StreamingFrog pairing). Cross-LAN reachability is the test:
    a guest in LAN-1 must reach a guest in LAN-2."""
    nodes = [
        NodeSpec("GW1", "10.1.1"),                                  # LAN-1 gateway
        NodeSpec("A",   "10.2.2", guest_on=("GW1", "10.1.1.230")),  # LAN-1 guest
        NodeSpec("GW2", "10.3.3"),                                  # LAN-2 gateway
        NodeSpec("B",   "10.4.4", guest_on=("GW2", "10.3.3.230")),  # LAN-2 guest
    ]
    tunnels = [("GW1", "GW2")]   # cross-Internet
    return TopologySpec(nodes=nodes, tunnels=tunnels)
