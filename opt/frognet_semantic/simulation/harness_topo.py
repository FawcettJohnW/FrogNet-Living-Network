#!/usr/bin/env python3
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
harness_topo.py - compile a netdef.yaml network into the topology model the
REAL-KERNEL tier consumes, so a topology you write runs over netns.

WHY THIS EXISTS
---------------
There are two topology models in the tree and they are not the same object:

  discovery.sim.system.TopologySpec   NodeSpec(name, subnet3, guest_on, ...)
                                      drives the discovery merge / convergence
  frognet_sim.Topology                FrogNode + Iface + L2 segments
                                      drives live_engine.converge_real, i.e.
                                      the REAL planner + committer, and under
                                      FROGNET_SIM_BACKEND=real, a real kernel

`netdef.py` compiles YAML into the first. TIER 4 iterates a hard-coded
`live_engine._BUILDERS` list of twenty function names resolved with
`getattr(frognet_sim, name)` -- all built-ins. So before this module, a topology
written in YAML could be converged and elected over, but could NOT be run over a
real kernel. This closes that.

THE MAPPING
-----------
  network            -> FrogNode with its served /24 on eth0 (wlan0 if it
                        projects an SSID), at <subnet3>.1/24
  host / guest_on    -> an extra iface on the PARENT's served subnet at the
                        lease address, plus an L2 segment joining the two.
                        The guest keeps its own served /24, because a guest is
                        still a FrogNetHost.
  tunnel_to          -> a /30 transit segment between the two gateways, one
                        iface per side. Named wgN so interface classification
                        sees it as a tunnel rather than a LAN.

L2 segments are what `Topology.link` models, and it REQUIRES every member of a
segment to already share a subnet -- it raises otherwise. That constraint is the
reason the guest lease address has to come from the parent's served /24 and not
from anywhere else.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SEM = os.environ.get("FROGNET_SEM_ROOT", "/opt/frognet_semantic")
for _p in (SEM, os.path.join(SEM, "simulation")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# [IMPORT_NETDEF_BEFORE_THE_HARNESS] frognet_sim's bootstrap puts
# FROGNET_BIN_ROOT (/usr/local/bin) on sys.path, and that directory contains a
# top-level discovery.py which SHADOWS the discovery package netdef needs:
#
#   ImportError: attempted relative import with no known parent package
#     /usr/local/bin/discovery.py line 12: from .trace import trace
#
# So netdef is imported first, and frognet_sim lazily, after.
def _import_netdef():
    """Import netdef with the discovery PACKAGE visible.

    frognet_sim's bootstrap puts FROGNET_BIN_ROOT (/usr/local/bin) on sys.path,
    and that directory holds a top-level discovery.py which SHADOWS the
    discovery package netdef needs:

        ImportError: attempted relative import with no known parent package
          /usr/local/bin/discovery.py:12  from .trace import trace

    live_engine imports frognet_sim long before it reaches us, so simply
    importing early is not enough -- the shadowing entry is already there.
    Hoist the semantic root ahead of any bin directory for the duration of the
    import, then put sys.path back exactly as it was.
    """
    import importlib
    saved = list(sys.path)
    try:
        sys.path[:] = ([SEM] +
                       [p for p in sys.path
                        if os.path.basename(p.rstrip("/")) != "bin"])
        for mod in ("discovery", "discovery.sim", "discovery.sim.system"):
            sys.modules.pop(mod, None)
        return importlib.import_module("netdef")
    finally:
        sys.path[:] = saved


netdef = _import_netdef()

_H = None


def _harness():
    global _H
    if _H is None:
        import frognet_sim as H
        _H = H
    return _H


def _transit_pool(i: int) -> str:
    """/30 transit subnets for tunnels. 10.253.x is the cross-NAT transit plane
    and is excluded from node identity everywhere, which is exactly what a
    tunnel leg should be."""
    block = i * 4
    return f"10.253.{200 + (block // 256)}.{block % 256}/30"


def to_harness(compiled, label: Optional[str] = None):
    """netdef.CompiledNet -> frognet_sim.Topology.

    [A_HOST_IS_A_FROGNET_HOST] Every node serves its own /24. There is no
    station/router distinction -- an earlier version invented one to make a
    topology converge, which was solving the wrong problem.

    A parent/child relationship is therefore a ROUTER-TO-ROUTER LINK, and the
    harness models that as a DEDICATED TRANSIT SUBNET, exactly as the built-in
    chain does:

        topo_chain_3:  A eth0 10.1.1.1/24 (served) + wg0 10.200.1.1/30 (link)
                       B eth0 10.2.2.1/24 (served) + wg0 10.200.1.2/30 (link)

    NOT by putting the child's address inside the parent's served /24. That is
    a DHCP-client shape (topo_solo_wlan0_ap), correct for a station on an AP and
    wrong for two FrogNet hosts -- with it, nothing carries the downstream
    prefix and every peer reports "<no route to ...>". That was the whole reason
    chains would not converge.
    """
    H = _harness()
    spec_by = {n.name: n for n in compiled.spec.nodes}
    t = H.Topology(label or "netdef topology")

    # 1. one FrogNode per network, each SERVING its own /24
    served_if: Dict[str, str] = {}
    for m in compiled.machines:
        ifname = m.served_iface.name if m.served_iface.kind == "ap" else "eth0"
        node = H.FrogNode(m.name)
        node.add_iface(ifname, f"{m.lan_ip}/24")
        t.add(node)
        served_if[m.name] = ifname

    # 2. one /30 per link, from a single allocator so parent-child links and
    #    tunnels can never overlap.
    link_n = [0]

    def _link(a: str, b: str, kind: str):
        net = ipaddress.ip_network(_transit_pool(link_n[0])); link_n[0] += 1
        hosts = list(net.hosts())
        pfx = "wg" if kind == "tunnel" else "eth"
        names = []
        for side, addr in ((a, hosts[0]), (b, hosts[1])):
            k = 0
            while f"{pfx}{k}" in t.nodes[side].ifaces:
                k += 1
            nm = f"{pfx}{k}"
            # A distinct name per link. Reusing eth0 would silently OVERWRITE
            # the served iface in the ifaces OrderedDict and the node would end
            # up serving its neighbour's subnet.
            t.nodes[side].add_iface(nm, f"{addr}/{net.prefixlen}")
            names.append(nm)
        t.link((a, names[0]), (b, names[1]))

    for m in compiled.machines:
        s_ = spec_by.get(m.name)
        if not (s_ and s_.guest_on):
            continue
        parent = s_.guest_on[0]
        if parent in t.nodes and parent != m.name:
            _link(parent, m.name, "lan")

    for (a, b) in compiled.spec.tunnels:
        if a in t.nodes and b in t.nodes:
            _link(a, b, "tunnel")

    return t


def builders_from_dir(path: str) -> Dict[str, callable]:
    """Every *.yaml in `path` becomes a zero-arg builder, named for the file.

    Returned rather than registered, so the caller decides where they go --
    live_engine resolves builders with getattr(frognet_sim, name).
    """
    out = {}
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        full = os.path.join(path, fn)
        name = "topo_" + os.path.splitext(fn)[0].replace("-", "_")

        def _mk(_full=full, _label=fn):
            def build():
                return to_harness(netdef.load(_full), label=f"netdef:{_label}")
            return build
        out[name] = _mk()
    return out


def register(path: str) -> List[str]:
    """Attach YAML builders to frognet_sim so getattr() finds them. Returns the
    names, for appending to live_engine._BUILDERS."""
    H = _harness()
    names = []
    for name, fn in builders_from_dir(path).items():
        setattr(H, name, fn)
        names.append(name)
    return names
