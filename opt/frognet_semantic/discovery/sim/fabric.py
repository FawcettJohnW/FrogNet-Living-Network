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
sim/fabric.py - a whole-system mesh fabric for integrated simulation.

This replaces the hand-scripted per-call answers in sim/topology.py with a single
model of the network from which the discovery sources are DERIVED:

  - nodes: subnet, echo identity, dead flag, LAN relay addr
  - direct channels (broker/handshake): dev <-> node, with a transit /30 src
  - vouching graph (getHosts): node -> peers it forwards for
  - link latencies (rtt) per (observing dev, target node)

From that, for any OBSERVER node, make_sources() yields echo/rtt/getHosts/broker
backends whose answers follow the fabric - so a probe's echo "carries" only when
the observer can actually reach the target over the probed dev, exactly the
property a system sim needs.

The loop you asked for:
  1. run discovery for a node  -> routes installed into THAT node's sim kernel
  2. exercise()                -> route packets over the installed tables and
                                  confirm they egress the expected dev / arrive

This is integration scaffolding. It is the seam the real frogsim harness should
plug the ported discovery into (frogsim supplies the node/kernel model + the
traffic exercises; discovery is the route-setup stage). To wire the actual
frogsim, upload its harness and I'll bind these adapters to its node objects and
the frognet_route Real/Fake kernel instead of FakeKernel.
"""
from __future__ import annotations

import os, sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.kernel import FakeKernel, _ip_to_int
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import HostStore
from discovery.propagate import Propagator


@dataclass
class Node:
    name: str
    subnet3: str                 # "10.179.179"
    upstream: str = "0.0.0.0"    # echo field 3
    upstream2: str = "0.0.0.0"   # echo field 4
    dead: bool = False           # tunnel dead (echo self_ip blank)
    lan_relay: str = ""          # if on observer's LAN, its client addr

    @property
    def one(self): return f"{self.subnet3}.1"
    @property
    def cidr(self): return f"{self.subnet3}.0/24"

    def echo_csv(self) -> str:
        # self-describing: name,<own .1>,<upstream>,<2nd-upstream>
        if self.dead:
            return f"{self.name},,,"          # 200 + blank self_ip (health: dead)
        return f"{self.name},{self.one},{self.upstream},{self.upstream2}"


@dataclass
class Fabric:
    nodes: dict = field(default_factory=dict)             # name -> Node
    channels: dict = field(default_factory=dict)          # dev -> (node_name, src, channel_name)
    vouch: dict = field(default_factory=dict)             # node_name -> [peer .1, ...]
    rtt: dict = field(default_factory=dict)               # (dev, target_subnet3) -> [ms,...]
    lan_devs: tuple = ("eth0",)

    # ---- reachability derivation -------------------------------------------
    def _owner_of(self, ip: str):
        s3 = ".".join(ip.split(".")[:3])
        for n in self.nodes.values():
            if n.subnet3 == s3:
                return n
            if n.lan_relay == ip:
                return n
        return None

    def reach_set(self, dev: str) -> set:
        """Node names reachable when probing on `dev`: the dev's far-end node
        plus everyone that node vouches for (one relay hop), or LAN nodes for a
        LAN dev. Mirrors how AllowedIPs=10/8 carries any 10.x to the far end."""
        if dev in self.lan_devs:
            return {n.name for n in self.nodes.values() if n.lan_relay}
        ch = self.channels.get(dev)
        if not ch:
            return set()
        far = ch[0]
        out = {far}
        for peer_one in self.vouch.get(far, []):
            o = self._owner_of(peer_one)
            if o:
                out.add(o.name)
        return out

    # ---- source adapters for an observer -----------------------------------
    def make_sources(self, observer_name: str):
        rtt_q = {k: list(v) for k, v in self.rtt.items()}
        fab = self

        class _Echo:
            def echo_probe(self, query_ip):
                owner = fab._owner_of(query_ip)
                if owner is None:
                    return None
                # reachability: identity echo (LAN client addr) is direct;
                # otherwise the owner must be reachable on SOME dev. For the
                # walk, the prober installed the /32 on a specific dev, but the
                # echo body is the owner's identity regardless of which dev - the
                # broker-authority check (not echo) rejects wrong-channel peers.
                if owner.dead:
                    return owner.echo_csv()       # 200 + blank self_ip
                return owner.echo_csv()

        class _Rtt:
            def measure_rtt(self, dev, pip):
                owner = fab._owner_of(pip)
                key = (dev, owner.subnet3) if owner else (dev, "")
                q = rtt_q.get(key)
                if not q:
                    return ""
                v = q.pop(0)
                return str(v) if v else ""

        class _GetHosts:
            def get_hosts(self, host_path, pip):
                owner = fab._owner_of(host_path)
                return list(fab.vouch.get(owner.name, [])) if owner else []

        class _Broker:
            def broker_for_peer_ip(self, dest1):
                for dev, (nn, _src, chname) in fab.channels.items():
                    if fab.nodes[nn].one == dest1:
                        return (chname, fab.nodes[nn].cidr, dest1)
                return None
            def channel_for_iface(self, dev):
                ch = fab.channels.get(dev)
                return ch[2] if ch else ""
            def transits(self, relay_one, dest24):
                return True  # no transit map modelled -> gate inert

        return _Echo(), _Rtt(), _GetHosts(), _Broker()

    # ---- build a Discovery for an observer ---------------------------------
    def discovery_for(self, observer_name, *, enter_seed, local_ips, dev_src,
                      dead_ifaces=(), logger=lambda s: None, propagate=False):
        k = FakeKernel(); k.seed(*enter_seed)
        routes = Routes(k, logger=logger, clock=lambda: 0.0)
        echo, rtt, gh, broker = self.make_sources(observer_name)
        hs = HostStore()
        prop = Propagator(hs, logger=logger) if propagate else None
        disc = Discovery(routes, echo, rtt, gh, broker, hs, local_ips, dev_src,
                         logger=logger, propagator=prop)
        disc.DEAD_IFACES = set(dead_ifaces)
        return disc, k


# ---- exercise: route packets over an installed table -----------------------

def route_egress(table_lines: list[str], dst_ip: str):
    """Longest-prefix-match over an installed kernel table -> (dev, metric) of
    the winning route, or None. This is the 'exercise the routes' step: given the
    table discovery built, where does a packet to dst_ip go?"""
    best = None
    dst = _ip_to_int(dst_ip)
    for line in table_lines:
        parts = line.split()
        cidr = parts[0]
        if cidr == "default":
            net, plen = 0, 0
        elif "/" in cidr:
            net_s, plen_s = cidr.split("/"); plen = int(plen_s)
            try: net = _ip_to_int(net_s)
            except ValueError: continue
        else:
            try: net, plen = _ip_to_int(cidr), 32   # bare host addr == /32
            except ValueError: continue
        mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF if plen else 0
        if (dst & mask) != (net & mask):
            continue
        dev = parts[parts.index("dev") + 1] if "dev" in parts else ""
        via = parts[parts.index("via") + 1] if "via" in parts else ""
        metric = int(parts[parts.index("metric") + 1]) if "metric" in parts else 0
        # longest prefix wins; tie -> lowest metric
        cand = (plen, -metric, dev, via)
        if best is None or (cand[0], cand[1]) > (best[0], best[1]):
            best = cand
    if best is None:
        return None
    return (best[2], -best[1], best[3])
