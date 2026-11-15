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
s_topo_tier.py - [S_TOPO_TIER_V1] TIER 3, the rest of it: every topology the simulator knows, discovered the FD&F way
-- from memory -- over slow links, through events, with the complete verification plan after every step.

Tier 3 was one shape (S nodes, E egress points, one level of children, one event). This runs:

  baselines   the 20 topologies whose behaviour was recorded on HARDWARE (baselines/*.json, frognet_sim's builders),
              each converted to the facts fdf-facts would write on those machines (a segment's .1 holder gives the
              lease, every other member holds one), every member a real fdf-client session on a real fdf-ram. The
              oracle is the hardware's: every node reached every served /24 there, so every pair must be reached here.
              A pair reached over a longer path than the segments allow is reported (FD&F routes over a forest).
  generated   multi-level trees (egress -> child -> grandchild -> ...), meshes in which nodes hold leases from two
              parents with different uplinks (the exit is chosen by speed, not hops: [EXIT_RANK_V1]), and a mixed
              forest. The generator states, independently of the planner, which exit each node must use and therefore
              the exact path of every pair; the verification requires that path, hop for hop.
  events      on the generated topologies, each followed by the complete verification against the new expectation:
                link-down     a lease between a node and its fast parent goes; the node must fall back to the other
                egress-lost   an egress's session ends; its dual-homed children re-home, its single-homed subtree has
                              no exit (reachable inside its LAN only -- a path out is a phantom)
                egress-back   the egress rejoins; everything is as before
                child-moves   a child's lease moves from one egress's LAN to another's
                churn         rounds of leaf members leaving and rejoining
                ram-link-cut  one member's link to the RAM host goes dark past the session keepalive: it is gone,
                              then the link returns and it is back
  slow links  every member's session to the RAM host runs over a shaped link (slowlink.py: lan, wifi, 4g, geo, halow,
              jammed, lossy variants), and the time for the memory to hold each step is measured per profile.

Routes are followed in one place only: the verification plan, every node to every served /24, written whole to
<out>/verify-<topology>-<step>.tsv.

usage (from opt/frognet_semantic):
  python3 simulation/s_topo_tier.py --bin DIR [--only baselines|generated|NAME ...] [--links mixed|lan|PROFILE]
"""
import argparse
import ipaddress
import json
import os
import random
import socket
import statistics as st
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import s_scale_tier as S3                          # noqa: E402  the Region (real fdf-ram, real fdf-client per member)
import slowlink as SL                              # noqa: E402

API = S3.API


def mac(*k):
    import zlib
    b = "|".join(k).encode()
    h = (zlib.crc32(b) << 8 | (zlib.adler32(b) & 255)) & 0xFFFFFFFFFF
    return "02:%02x:%02x:%02x:%02x:%02x" % ((h >> 32) & 255, (h >> 24) & 255, (h >> 16) & 255, (h >> 8) & 255, h & 255)


# ================================================================== the network, as the generator states it
class Net:
    """Nodes with the /24s they serve, LAN segments (a parent serving the segment's /24 and the members holding
    leases on it), uplinks, hub links. Facts are derived from it; so is the expectation."""

    def __init__(self, name):
        self.name = name
        self.nodes = {}          # name -> {"served": [dot1...], "uplink": (type, rate) | None, "hub": bool}
        self.segs = []           # [{"parent": n, "dot1": ip, "iface_p": i, "members": [(child, iface, ip, mac)]}]
        self.dests = None        # the /24s verified as destinations (default: every served /24)
        self.expect_paths = False    # the generator states every path exactly
        self.down = set()        # members whose session is ended (egress-lost, churn)
        self.site_of = {}        # baselines that span sites: member -> site ([WAN_IS_THE_HUB_V1])

    def node(self, n, served=(), uplink=None, hub=False):
        self.nodes[n] = {"served": list(served), "uplink": uplink, "hub": hub}
        return self

    def lease(self, parent, child, parent_dot1, iface_p="eth0", iface_c="eth1", host=None):
        seg = next((s for s in self.segs if s["dot1"] == parent_dot1), None)
        if seg is None:
            seg = {"parent": parent, "dot1": parent_dot1, "iface_p": iface_p, "members": []}
            self.segs.append(seg)
        host = host or 20 + len(seg["members"])
        ip = parent_dot1.rsplit(".", 1)[0] + f".{host}"
        seg["members"].append((child, iface_c, ip, mac(self.name, parent_dot1, child)))
        return self

    def unlease(self, parent_dot1, child):
        for s in self.segs:
            if s["dot1"] == parent_dot1:
                s["members"] = [m for m in s["members"] if m[0] != child]

    def facts(self, n):
        d = self.nodes[n]
        addrs = []
        for i, dot1 in enumerate(d["served"]):
            ifc = next((s["iface_p"] for s in self.segs if s["dot1"] == dot1 and s["parent"] == n), f"lan{i}")
            addrs.append({"iface": ifc, "ip": dot1, "value": f"FrogNetHost.{n}", "kind": "own", "mac": mac(self.name, n, dot1)})
        for s in self.segs:
            for child, ifc, ip, m in s["members"]:
                if s["parent"] == n:
                    addrs.append({"iface": s["iface_p"], "ip": ip, "value": child, "kind": "given", "mac": m})
                if child == n:
                    addrs.append({"iface": ifc, "ip": ip, "value": s["parent"], "kind": "held", "mac": m})
        if d["uplink"]:
            typ, rate = d["uplink"]
            k = sum(1 for _ in self.nodes) + list(self.nodes).index(n)
            addrs.append({"iface": "wan0", "ip": f"198.18.{k // 250}.{k % 250 + 1}", "value": f"uplink {typ} {rate:g}", "kind": "uplink", "mac": mac(self.name, n, "wan")})
        if d["hub"]:
            k = list(self.nodes).index(n)
            addrs.append({"iface": "wg0", "ip": f"10.253.{k // 250}.{k % 250 + 1}", "value": "", "kind": "hub", "mac": ""})
        addrs.sort(key=lambda r: (r["kind"], r["iface"], r["ip"]))
        sub = d["served"][0].rsplit(".", 1)[0] + ".0/24" if d["served"] else ""
        return {"name": n, "guid": f"g-{self.name}-{n}", "role": "node", "subnet": sub, "pond": "", "addrs": addrs}

    def served_by(self):
        out = {}
        for n, d in self.nodes.items():
            if n in self.down:
                continue
            for dot1 in d["served"]:
                out[dot1.rsplit(".", 1)[0] + ".0/24"] = n
        if self.dests is not None:
            out = {k: v for k, v in out.items() if k in self.dests}
        return out

    # ---------------------------------------------------------------- the expectation, from the network itself
    def _adj(self):
        """The LAN: parent <-> each member of its segment (live members only)."""
        adj = {n: set() for n in self.nodes if n not in self.down}
        for s in self.segs:
            p = s["parent"]
            if p in self.down:
                continue
            for child, _i, _ip, _m in s["members"]:
                if child in self.down:
                    continue
                adj[p].add(child)
                adj[child].add(p)
        return adj

    @staticmethod
    def _bfs(adj, src):
        dist, prev = {src: 0}, {src: None}
        q = deque([src])
        while q:
            h = q.popleft()
            for t in sorted(adj[h]):
                if t not in dist:
                    dist[t] = dist[h] + 1
                    prev[t] = h
                    q.append(t)
        return dist, prev

    def dot1_key(self, n):
        s = sorted(self.nodes[n]["served"], key=lambda ip: tuple(int(x) for x in ip.split(".")))
        return tuple(int(x) for x in s[0].split(".")) if s else (999,) * 4

    def exit_of(self, n, adj):
        """John's rule: the LAN-reachable live uplink with the highest rate, then the lower .1. Not by hops."""
        dist, _ = self._bfs(adj, n)
        c = [(-self.nodes[t]["uplink"][1], self.dot1_key(t), t) for t in dist if self.nodes[t]["uplink"]]
        return min(c)[2] if c else None

    def wire(self, n):
        """The /24s n holds a lease on (live parents only): the kernel's connected routes on n."""
        return {s["dot1"].rsplit(".", 1)[0] + ".0/24" for s in self.segs
                if s["parent"] not in self.down and any(m[0] == n for m in s["members"])}

    def expected_path(self, src, net, adj, exits, holders):
        """The path the network calls for, hop by hop: a /24 on my wire is one hop (the connected route); one in my
        forest component is the forest path; otherwise up to my exit, over the hub to the member that maps it, and on
        from there by the same rules. None: no path should exist."""
        owner = self.served_by().get(net)
        if owner is None:
            return None
        path, at, crossed = [src], src, False
        while at != owner:
            if len(path) > 40:
                return None
            if net in self.wire(at):
                path.append(owner)
                break
            comp = self._lanpath(adj, at, owner)
            if comp is not None:
                path.append(comp[1])
                at = comp[1]
                continue
            if crossed:
                return None
            hubber = at if self.nodes[at]["hub"] else exits.get(at)
            if not hubber or not self.nodes[hubber]["hub"]:
                return None
            if hubber != at:
                up = self._lanpath(adj, at, hubber)
                if up is None:
                    return None
                path.append(up[1])
                at = up[1]
                continue
            holder = holders.get(net)
            if holder is None:
                return None
            path += ["hub", holder]
            at, crossed = holder, True
        return path

    def _lanpath(self, adj, a, b):
        dist, prev = self._bfs(adj, a)
        if b not in dist:
            return None
        path, at = [], b
        while at is not None:
            path.append(at)
            at = prev[at]
        return list(reversed(path))

    def holders(self, adj, exits):
        """Who maps each /24 at the hub: a hub-linked owner maps its own; a hub-less owner is mapped by its exit if
        that exit holds a hub link ([OWN_TUNNEL_CARRIES_OWN_V1])."""
        out = {}
        for net, owner in self.served_by().items():
            if self.nodes[owner]["hub"]:
                out[net] = owner
            else:
                ex = exits.get(owner)
                if ex and self.nodes[ex]["hub"]:
                    out[net] = ex
        return out

    def forest(self):
        """[FOREST_V1], as the rule reads: each host keeps ONE upward link -- the one toward its exit (the first hop
        of its shortest LAN path there), else the one to the lowest parent .1. The routing graph is those links."""
        full = self._adj()
        ups = {n: [] for n in full}
        for s in self.segs:
            p = s["parent"]
            if p in self.down:
                continue
            for child, *_ in s["members"]:
                if child not in self.down:
                    ups[child].append((tuple(int(x) for x in s["dot1"].split(".")), p))
        adj = {n: set() for n in full}
        self.parent_of = {}
        for h in full:
            if not ups[h]:
                continue
            ex = self.exit_of(h, full)
            pick = None
            if ex and ex != h:
                path = self._lanpath(full, h, ex)
                if path and len(path) > 1 and any(p == path[1] for _d, p in ups[h]):
                    pick = path[1]
            if pick is None:
                pick = min(ups[h])[1]
            self.parent_of[h] = pick
            adj[h].add(pick)
            adj[pick].add(h)
        # [NO_STRANDED_MEMBER_V1], as fdf_graph states it: a tree with no exit and no hub link is joined to a
        # neighbouring tree over its lowest-.1 link (then the names); trees with a way out are left as chosen.
        self.joined = []
        while True:
            comp, seen = {}, set()
            for h in sorted(adj):
                if h not in seen:
                    d, _ = self._bfs(adj, h)
                    for m in d:
                        comp[m] = h
                    seen |= set(d)
            out = {comp[m] for m in adj if self.nodes[m]["uplink"] or self.nodes[m]["hub"]}
            best = None
            for s in self.segs:
                p = s["parent"]
                if p in self.down:
                    continue
                for child, *_ in s["members"]:
                    if child in self.down or comp[child] == comp[p] or (comp[child] in out and comp[p] in out):
                        continue
                    k = (tuple(int(x) for x in s["dot1"].split(".")), child, p)
                    if best is None or k < best:
                        best = k
            if best is None:
                break
            _d, child, p = best
            adj[child].add(p)
            adj[p].add(child)
            self.joined.append((child, p))
        return adj

    def expectation(self):
        adj = self.forest()
        exits = {n: self.exit_of(n, adj) for n in adj}
        holders = self.holders(adj, exits)
        exp = {}
        for src in adj:
            for net in self.served_by():
                exp[(src, net)] = self.expected_path(src, net, adj, exits, holders)
        return exp, exits

    def shortest_lan_hops(self, src, owner):
        adj = self._adj()
        dist, _ = self._bfs(adj, src)
        return dist.get(owner)


# ================================================================== the 20 hardware topologies, as facts
# [WAN_IS_THE_HUB_V1] the sites of the hardware topologies that span the Internet, as each builder's docstring states
# them. Those were recorded when site gateways tunnelled to each other. In CroakOS, nodes at different sites have no
# tunnel to each other: each gateway is a region member with an uplink and a link to the region's hub, and traffic
# between sites goes member -> hub -> member. So a segment between two sites is not a lease here; its endpoints
# are given the hub link and the uplink, and its /30 is gone. Every pair must still be reached, as on the hardware,
# and every pair between sites must cross the hub. Topologies not listed have no Internet links and are unchanged.
SITES = {
    "topo_asym_chain_wg_star": {"A": ["Ca1", "Ca2", "Ca3"], "B": ["HubB", "Bs1", "Bs2", "Bs3"]},
    "topo_asym_lan2_wg_lan3": {"A": ["A1", "A2"], "B": ["B1", "B2", "B3"]},
    "topo_asym_ring_wg_lan": {"A": ["Ra", "Rb", "Rc", "Rd"], "B": ["Lb1", "Lb2", "Lb3"]},
    "topo_asym_three_sites": {"A": ["A_w1", "A_gw"], "B": ["B_end1", "B_gw", "B_end2"], "C": ["C_hub", "C_s1", "C_s2"]},
    "topo_dual_wg_transit_bridge": {"G1": ["G1_gw", "G1_w1"], "G2": ["G2_gw", "G2_t"], "BR": ["BR_gw", "BR_w1"],
                                    "G3": ["G3_hub", "G3_s1", "G3_s2"], "G4": ["G4_gw"]},
    "topo_multilan_shared_wg_transit": {"A": ["A_gw", "A_w1", "A_w2"], "B": ["B_gw", "B_m", "B_t"],
                                        "C": ["C_hub", "C_s1", "C_s2", "C_s3"], "D": ["D_gw"]},
    "topo_pond_full_mesh": {"Seattle": ["Seattle"], "NewYork": ["NewYork"], "Austin": ["Austin"]},
    "topo_pond_hub_spoke": {"Seattle": ["Seattle"], "NewYork": ["NewYork"], "Austin": ["Austin"]},
    "topo_pond_with_workers": {"Seattle": ["Seattle", "Seattle-W1"], "NewYork": ["NewYork", "NewYork-W1"],
                               "Austin": ["Austin", "Austin-W1"]},
}


# [NO_STRANDED_MEMBER_V1] regression: this baseline as it failed before (its site links kept as leases, no hub), where
# B_end1 gave B_gw its only lease and B_gw's one upward link went to A_gw. Every pair must be reached.
AS_LEASES = ("topo_asym_three_sites",)


def from_baseline(key, as_hub=True):
    """frognet_sim's builder for a hardware-recorded topology, as the facts fdf-facts writes on those machines: on each
    L2 segment the member holding the segment's .1 gives the leases, every other member holds one. A node's served /24
    is its first interface's (frognet_sim.assign_identities); the destinations verified are those /24s. A segment
    between two sites ([WAN_IS_THE_HUB_V1]) is the region hub instead."""
    import frognet_sim as H
    topo = getattr(H, key)()
    H.assign_identities(topo)
    net = Net(key)
    notes = []
    site_of = {m: s for s, ms in SITES.get(key, {}).items() for m in ms} if as_hub else {}
    if not as_hub:
        net.name = key + "@as-leases"
    if site_of and set(site_of) != set(topo.nodes):
        raise RuntimeError(f"{key}: SITES names {sorted(set(site_of) ^ set(topo.nodes))} that the builder does not match")
    wan, gws = [], set()
    for seg in topo.segments:
        if site_of and len({site_of[n] for n, _i in seg}) > 1:
            wan.append(seg)
            gws |= {n for n, _i in seg}
    wan_ips = {topo.nodes[n].ifaces[i].ip for seg in wan for n, i in seg}
    for n, node in topo.nodes.items():
        dot1s = [i.ip for i in node.ifaces.values() if i.ip.endswith(".1") and i.ip.startswith("10.") and i.ip not in wan_ips]
        if n in gws:
            net.node(n, served=dot1s, uplink=("eth", 1000), hub=True)
        else:
            net.node(n, served=dot1s)
    net.site_of = site_of
    if wan:
        notes.append(f"{len(wan)} link(s) between sites carried by the region hub; gateways {sorted(gws)}")
    dests = set()
    for n, node in topo.nodes.items():
        if node.frognet_ip and node.frognet_ip.endswith(".1"):
            dests.add(node.frognet_ip.rsplit(".", 1)[0] + ".0/24")
    for seg in topo.segments:
        if seg in wan:
            continue
        mem = [(n, i, topo.nodes[n].ifaces[i].ip) for n, i in seg]
        par = [m for m in mem if m[2].endswith(".1")]
        if not par:
            notes.append(f"segment {[m[0] for m in mem]} has no .1 holder: no lease, so no FD&F link")
            continue
        p, ip_iface, dot1 = par[0]
        seg_rec = {"parent": p, "dot1": dot1, "iface_p": ip_iface, "members": []}
        for c, ci, ip in mem:
            if c == p:
                continue
            seg_rec["members"].append((c, ci, ip, mac(key, dot1, c)))
        # a /30 transit: FD&F counts the parent's .1 as a served /24, which must not collide with another .1 in it
        net.segs.append(seg_rec)
    net.dests = dests
    return net, notes


# ================================================================== generated topologies
def gen_tree(depth=3, fan=3, roots=2):
    """Egress roots (hub link, eth uplink) -> children -> grandchildren -> ... Every node serves its /24."""
    net = Net(f"tree-d{depth}-f{fan}-r{roots}")
    k = [0]

    def nxt():
        k[0] += 1
        return f"10.{30 + k[0] // 250}.{k[0] % 250}.1"
    level = []
    for r in range(1, roots + 1):
        n = f"r{r}"
        net.node(n, served=[nxt()], uplink=("eth", 1000), hub=True)
        level.append(n)
    for d in range(1, depth + 1):
        new = []
        for p in level:
            for j in range(1, fan + 1):
                n = f"{p}.{j}"
                net.node(n, served=[nxt()])
                net.lease(p, n, net.nodes[p]["served"][0])
                new.append(n)
        level = new
    net.expect_paths = True
    return net


def gen_mesh():
    """Path choice. Two egresses: f (eth 1000) and s (wifi 72). Dual-homed nodes hold leases from both sides; the exit
    is the faster uplink even when it is more hops away. A transit chain under f, a chain under s, and cross links:
      f --- a1 --- a2 --- a3          s --- b1 --- b2
             \\__________ x1 ____________/      (x1 holds leases from a1 and b1: its exit is f, via a1, 2 hops)
                          x2: leases from a3 (3 hops to f) and s (1 hop to s): exit f, by speed
      plus y1 holding a lease from b2 only: exit s; and z1, leases from s and b1: exit s, via s directly"""
    net = Net("mesh-speed-not-hops")
    ips = {}

    def add(n, **kw):
        ips[n] = f"10.40.{len(ips) + 1}.1"
        net.node(n, served=[ips[n]], **kw)
    add("f", uplink=("eth", 1000), hub=True)
    add("s", uplink=("wifi", 72), hub=True)
    for n in ("a1", "a2", "a3", "b1", "b2", "x1", "x2", "y1", "z1"):
        add(n)
    net.lease("f", "a1", ips["f"]).lease("a1", "a2", ips["a1"]).lease("a2", "a3", ips["a2"])
    net.lease("s", "b1", ips["s"]).lease("b1", "b2", ips["b1"])
    net.lease("a1", "x1", ips["a1"]).lease("b1", "x1", ips["b1"])
    net.lease("a3", "x2", ips["a3"]).lease("s", "x2", ips["s"])
    net.lease("b2", "y1", ips["b2"])
    net.lease("s", "z1", ips["s"]).lease("b1", "z1", ips["b1"])
    net.expect_paths = True
    return net


def gen_forest(E=4, per=6, seed=7):
    """A mixed forest: E egresses (rates differ), each with a two-level subtree; a third of the children dual-homed
    to a second egress's LAN. The expectation decides each one's exit by the rule."""
    rng = random.Random(seed)
    net = Net(f"forest-E{E}-p{per}")
    rates = [("eth", 1000), ("eth", 100), ("wifi", 72), ("cell", 20), ("halow", 2)]
    k = [0]

    def ip():
        k[0] += 1
        return f"10.{50 + k[0] // 250}.{k[0] % 250}.1"
    for e in range(1, E + 1):
        net.node(f"e{e}", served=[ip()], uplink=rates[(e - 1) % len(rates)], hub=True)
    for e in range(1, E + 1):
        for j in range(1, per + 1):
            c = f"e{e}c{j}"
            net.node(c, served=[ip()])
            net.lease(f"e{e}", c, net.nodes[f"e{e}"]["served"][0])
            if j % 3 == 0:
                o = rng.choice([x for x in range(1, E + 1) if x != e])
                net.lease(f"e{o}", c, net.nodes[f"e{o}"]["served"][0])
            g = f"{c}g"
            net.node(g, served=[ip()])
            net.lease(c, g, net.nodes[c]["served"][0])
    net.expect_paths = True
    return net


# ================================================================== the memory, with each member on its own link
class Region(S3.Region):
    def __init__(self, bindir, port, work, links):
        super().__init__(bindir, port, work)
        self.links = links            # member -> SL.Link or None
        self.relays = {}

    def join(self, name, facts):
        self.write_facts(name, facts)
        d = os.path.join(self.work, "var", name)
        os.makedirs(d, exist_ok=True)
        where = self.where
        L = self.links.get(name)
        if L is not None:
            r = self.relays.get(name)
            if r is None:
                r = SL.Relay(("127.0.0.1", self.port), L, seed=sum(map(ord, name)), name=name)
                self.relays[name] = r
            where = r.where
        env = {k: v for k, v in os.environ.items() if k not in ("RINA_AREA", "RINA_SITE_KEY", "FDF_REACH", "FDF_NAME")}
        env.update(RINA_RAM=where, FDF_FACTS=os.path.join(self.work, "facts", f"{name}.json"), FDF_VAR=d)
        self.clients[name] = subprocess.Popen([os.path.join(self.bindir, "fdf-client")], env=env,
                                              stdout=open(os.path.join(d, "client.log"), "a"), stderr=subprocess.STDOUT)

    def leave(self, name):
        p = self.clients.pop(name, None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    def close(self):
        super().close()
        for r in self.relays.values():
            r.close()


# ================================================================== one step: memory, plans, maps, verification
def settle(reg, net, Graph, alive_names, timeout, step):
    """Wait until the memory holds every live member's facts as written; plan; publish each member's maps; wait until
    the memory holds them; return (participants, plans, timings)."""
    t0 = S3.now_ms()
    want = {n: {r["ip"] for r in net.facts(n)["addrs"]} for n in alive_names}

    def facts_in(P):
        live = {p["name"]: p for p in P if p.get("alive")}
        if any(n in live for n in net.down):
            return False
        for n, ips in want.items():
            p = live.get(n)
            if p is None or {r.get("ip") for r in (p.get("facts") or {}).get("addrs") or []} != ips:
                return False
        return "hubA" in live
    try:
        parts, _ = reg.wait_until(facts_in, timeout, f"{step}: every live member's facts")
    except RuntimeError:
        P = reg.call({"op": "participants"})[0]["participants"]
        live = {p["name"]: p for p in P if p.get("alive")}
        miss = [n for n in want if n not in live]
        stale = [n for n in want if n in live and {r.get("ip") for r in (live[n].get("facts") or {}).get("addrs") or []} != want[n]]
        lk = lambda n: reg.links.get(n).label() if reg.links.get(n) else "loopback"
        raise RuntimeError(f"{step}: after {timeout:g} s the memory lacks "
                           + "; ".join([f"{n} (not alive; link {lk(n)})" for n in miss[:5]] + [f"{n} (old facts; link {lk(n)})" for n in stale[:5]]))
    t_mem = S3.now_ms() - t0
    g = Graph(parts)
    plans = {n: g.plan(n) for n in alive_names}
    rl = "hubA:1:50"
    wmaps = {}
    for n in alive_names:
        f = net.facts(n)
        maps = [{"eid": p, "rlocs": rl} for p in plans[n]["maps"]]
        if maps:
            f["maps"] = maps
        wmaps[n] = sorted(m["eid"] for m in maps)
        reg.write_facts(n, f)

    def maps_in(P):
        live = {p["name"]: p for p in P if p.get("alive")}
        return all(sorted(m["eid"] for m in (live.get(n, {}).get("facts") or {}).get("maps") or []) == wmaps[n] for n in alive_names)
    parts, size = reg.wait_until(maps_in, timeout, f"{step}: every member's maps")
    # the plans verified are the plans of the memory as it is now: one more pass if anything moved meanwhile
    for _ in range(3):
        g = Graph(parts)
        plans = {n: g.plan(n) for n in alive_names}
        if all(sorted(plans[n]["maps"]) == wmaps[n] for n in alive_names):
            break
        for n in alive_names:
            f = net.facts(n)
            maps = [{"eid": p, "rlocs": rl} for p in plans[n]["maps"]]
            if maps:
                f["maps"] = maps
            wmaps[n] = sorted(m["eid"] for m in maps)
            reg.write_facts(n, f)
        parts, size = reg.wait_until(maps_in, timeout, f"{step}: every member's maps (again)")
    return parts, plans, {"memory_ms": round(t_mem), "maps_ms": round(S3.now_ms() - t0 - t_mem), "read_bytes": size}


def walk(parts, plans, src, net_):
    """Follow src's plan toward net_, hop by hop, over the plans and the hub's table (the members' maps)."""
    owner_of_ip = {}
    for p in parts:
        if not p.get("alive"):
            continue
        for r in (p.get("facts") or {}).get("addrs") or []:
            if r.get("kind") in ("own", "held"):
                owner_of_ip.setdefault(r["ip"], p["name"])
    hub_table = {}
    for p in parts:
        if p.get("alive"):
            for m in (p.get("facts") or {}).get("maps") or []:
                hub_table.setdefault(m["eid"], []).append(p["name"])
    wire = {}                        # member -> the /24s it holds a lease on: the kernel's connected routes
    for p in parts:
        if p.get("alive"):
            wire[p["name"]] = {r["ip"].rsplit(".", 1)[0] + ".0/24" for r in (p.get("facts") or {}).get("addrs") or []
                               if r.get("kind") == "held" and r.get("ip", "").startswith("10.")}
    path, at = [src], src
    while True:
        if at not in plans:
            return path, f"NOT A LIVE MEMBER {at}"
        if net_ in set(plans[at]["own"]):
            return path, "ok"
        if len(path) > 40:
            return path, "LOOP"
        pl, nxt = plans[at], None
        if net_ in wire.get(at, ()):     # on my wire: the /24's .1 is there (the planner adds no route for it)
            nxt = owner_of_ip.get(net_.split("/")[0].rsplit(".", 1)[0] + ".1")
            if nxt is None:
                return path, f"NO HOLDER OF {net_}'s .1 ON THE WIRE"
            path.append(nxt)
            at = nxt
            continue
        for dest, via, dev in pl["routes"]:
            if dest == net_:
                nxt = owner_of_ip.get(via)
                if nxt is None:
                    return path, f"NO HOLDER OF {via}"
                break
        if nxt is None and pl.get("hub_active") and net_ in pl["hub_routes"]:
            h = hub_table.get(net_, [])
            if len(h) != 1:
                return path, f"HUB HAS {len(h)} MAPPINGS"
            path.append("hub")
            nxt = h[0]
        if nxt is None and pl.get("default"):
            nxt = owner_of_ip.get(pl["default"][0])
            if nxt is None:
                return path, f"NO HOLDER OF DEFAULT {pl['default'][0]}"
        if nxt is None:
            return path, "NO ROUTE"
        if nxt in path and nxt != "hub":
            path.append(nxt)
            return path, "LOOP"
        path.append(nxt)
        at = nxt


def verify(parts, plans, net, out_tsv, hardware_all_reach=False):
    """Every live node to every served /24. Generated topologies: the path must be exactly the expected one, and a
    pair with no expected path must not be reached. Baselines: every pair reached (hardware did); a path longer than
    the segments allow is counted, not failed."""
    exp, exits = net.expectation() if (net.expect_paths or net.site_of) else ({}, {})
    bad, rows, longer, unreached_ok = [], 0, 0, 0
    with open(out_tsv, "w") as out:
        out.write("src\tdst\tpath\thops\texpected\tverdict\n")
        for src in sorted(plans):
            for net_, owner in sorted(net.served_by().items()):
                path, verdict = walk(parts, plans, src, net_)
                want = None
                if net.expect_paths:
                    want = exp.get((src, net_))
                    if want is None:
                        verdict = "ok (unreachable, as expected)" if verdict != "ok" else "PHANTOM PATH"
                        if verdict.startswith("ok"):
                            unreached_ok += 1
                    elif verdict == "ok" and path != want:
                        verdict = "WRONG PATH"
                elif verdict == "ok":
                    sh = net.shortest_lan_hops(src, owner)
                    if net.site_of:
                        # [WAN_IS_THE_HUB_V1] between sites the path crosses the hub; inside a site it never does,
                        # and it is the path the forest calls for, hop for hop
                        want = exp.get((src, net_)) if exp else None
                        cross = net.site_of[src] != net.site_of[owner]
                        if cross != ("hub" in path):
                            verdict = "CROSSES THE HUB INSIDE A SITE" if not cross else "BETWEEN SITES WITHOUT THE HUB"
                        elif want is not None and path != want:
                            verdict = "WRONG PATH"
                    elif sh is not None and len(path) - 1 > sh:
                        longer += 1
                        verdict = "ok (longer than the segments allow)"
                rows += 1
                out.write(f"{src}\t{net_}\t{'>'.join(path)}\t{len(path)-1}\t{'>'.join(want) if want else '-'}\t{verdict}\n")
                if not verdict.startswith("ok"):
                    bad.append(f"{src} -> {net_} ({owner}): {verdict}: {'>'.join(path)}" + (f"  expected {'>'.join(want)}" if want else ""))
    return {"pairs": rows, "bad": bad, "longer": longer, "unreachable_as_expected": unreached_ok}


# ================================================================== a topology's run: cold start, then its events
def link_for(n, mode, rng):
    if mode == "lan":
        return None
    if mode != "mixed":
        return SL.profile(mode)
    return SL.profile(rng.choice(MIXED))


MIXED = ["wan-100ms", "wifi", "4g", "4g-lossy", "halow", "halow-lossy", "geo", "wifi-lossy"]


def run_net(net, events, a, Graph, outdir, notes=()):
    work = tempfile.mkdtemp(prefix=f"st-{net.name[:20]}-", dir=a.work)
    os.makedirs(os.path.join(work, "facts"))
    rng = random.Random(sum(map(ord, net.name)))
    links = {n: link_for(n, a.links, rng) for n in net.nodes}
    reg = Region(a.bin, a.port, work, links)
    hub = {"name": "hubA", "guid": "g-hubA", "role": "hub", "pubkey": "sim", "endpoint": "198.18.255.1:51820"}
    steps, ok = [], True
    try:
        reg.join("hubA", hub)
        for n in net.nodes:
            reg.join(n, net.facts(n))

        def step(label):
            nonlocal ok
            alive = [n for n in net.nodes if n not in net.down]
            parts, plans, tm = settle(reg, net, Graph, alive, a.timeout, label)
            v = verify(parts, plans, net, os.path.join(outdir, f"verify-{net.name}-{label}.tsv"))
            good = not v["bad"]
            ok &= good
            by_profile = {}
            for n in alive:
                L = links.get(n)
                by_profile.setdefault(L.label() if L else "loopback", 0)
                by_profile[L.label() if L else "loopback"] += 1
            steps.append({"step": label, **tm, **{k: v[k] for k in ("pairs", "longer", "unreachable_as_expected")},
                          "bad": v["bad"][:10], "nbad": len(v["bad"])})
            extra = []
            if v["longer"]:
                extra.append(f"{v['longer']} longer than the segments allow")
            if v["unreachable_as_expected"]:
                extra.append(f"{v['unreachable_as_expected']} unreachable as expected")
            print(f"    [{'PASS' if good else 'FAIL'}] {label:22s} memory {tm['memory_ms']:6d} ms  maps {tm['maps_ms']:6d} ms  "
                  f"verified {v['pairs'] - len(v['bad'])}/{v['pairs']}" + (f"  ({'; '.join(extra)})" if extra else ""), flush=True)
            for b in v["bad"][:6]:
                print(f"           {b}")
            return parts, plans

        reg._step = step
        step("cold")
        for ev in events:
            ev(net, reg, rng)
            step(ev.__name__.replace("ev_", "").replace("_", "-"))
        dead = [n for n, p in reg.clients.items() if p.poll() is not None]
        if dead:
            ok = False
            print(f"    [FAIL] clients ended on their own: {dead[:6]}")
    except RuntimeError as x:
        ok = False
        print(f"    [FAIL] {x}")
        steps.append({"step": "error", "error": str(x)})
    finally:
        reg.close()
    prof = {}
    for n, L in links.items():
        prof.setdefault(L.label() if L else "loopback", []).append(n)
    return {"topology": net.name, "nodes": len(net.nodes), "ok": ok, "steps": steps, "notes": list(notes),
            "links": {k: len(v) for k, v in prof.items()}}


# ---------------------------------------------------------------- events (each mutates the network and the memory)
def _rewrite(reg, net, names):
    for n in names:
        if n not in net.down:
            reg.write_facts(n, net.facts(n))


def ev_link_down(net, reg, rng):
    """The lease between the first dual-homed node and its expected (fast) parent goes."""
    exp, exits = net.expectation()
    adj = net._adj()
    for s in net.segs:
        for child, *_ in s["members"]:
            ups = [x for x in net.segs if any(m[0] == child for m in x["members"])]
            if len(ups) >= 2:
                fast = exp_first_hop(net, child, exits, adj)
                seg = next(x for x in ups if x["parent"] == fast)
                net.unlease(seg["dot1"], child)
                _rewrite(reg, net, [child, seg["parent"]])
                print(f"    .. link-down: {child} loses its lease from {seg['parent']}")
                return
    for s in net.segs:                       # no dual-homed node (a tree): a subtree loses its parent
        if s["members"] and net.nodes[s["parent"]]["uplink"] is None:
            child = s["members"][0][0]
            net.unlease(s["dot1"], child)
            _rewrite(reg, net, [child, s["parent"]])
            print(f"    .. link-down: {child} (single-homed) loses its lease from {s['parent']}: its subtree is cut off")
            return
    raise RuntimeError("link-down: nothing to cut")


def exp_first_hop(net, n, exits, adj):
    net.forest()
    return net.parent_of.get(n)


def _egress(net):
    eg = [n for n, d in net.nodes.items() if d["uplink"] and d["hub"] and n not in net.down]
    return max(eg, key=lambda n: net.nodes[n]["uplink"][1])


def ev_egress_lost(net, reg, rng):
    e = _egress(net)
    net.down.add(e)
    net._lost = e
    reg.leave(e)
    print(f"    .. egress-lost: {e}'s session ends")


def ev_egress_back(net, reg, rng):
    e = net._lost
    net.down.discard(e)
    reg.join(e, net.facts(e))
    print(f"    .. egress-back: {e} rejoins")


def ev_child_moves(net, reg, rng):
    """A single-homed child of one egress moves its lease to another egress's LAN."""
    eg = [n for n, d in net.nodes.items() if d["uplink"] and d["hub"] and n not in net.down]
    for s in net.segs:
        if s["parent"] in eg:
            for child, *_ in list(s["members"]):
                homes = [x for x in net.segs if any(m[0] == child for m in x["members"])]
                if len(homes) == 1:
                    other = next(e for e in eg if e != s["parent"])
                    net.unlease(s["dot1"], child)
                    net.lease(other, child, net.nodes[other]["served"][0], host=200)
                    _rewrite(reg, net, [child, s["parent"], other])
                    print(f"    .. child-moves: {child} from {s['parent']}'s LAN to {other}'s")
                    return
    raise RuntimeError("child-moves: no single-homed child of an egress")


def ev_churn(net, reg, rng, rounds=3):
    leaves = [n for n in net.nodes if not any(s["parent"] == n and s["members"] for s in net.segs)
              and not net.nodes[n]["uplink"] and n not in net.down]
    for r in range(rounds):
        out = rng.sample(leaves, min(3, len(leaves)))
        for n in out:
            net.down.add(n)
            reg.leave(n)
        alive = [n for n in net.nodes if n not in net.down]
        reg._step(f"churn-{r+1}-out")
        for n in out:
            net.down.discard(n)
            reg.join(n, net.facts(n))
        alive = [n for n in net.nodes if n not in net.down]
        reg._step(f"churn-{r+1}-back")
    print(f"    .. churn: {rounds} rounds of 3 leaves leaving and rejoining")


def ev_ram_link_cut(net, reg, rng):
    """One member's link to the RAM host goes dark past the session keepalive; it is gone; the link returns."""
    cands = [n for n in reg.relays if n not in net.down and not net.nodes[n]["uplink"]]
    if not cands:
        n = next(x for x in net.nodes if not net.nodes[x]["uplink"] and x not in net.down)
        print(f"    .. ram-link-cut: no member on a shaped link; ending {n}'s session instead")
        reg.leave(n)
        net.down.add(n)
    else:
        n = cands[0]
        reg.relays[n].cut()
        net.down.add(n)
        print(f"    .. ram-link-cut: {n}'s link to the RAM host is dark")
    alive = [x for x in net.nodes if x not in net.down]
    reg._step("ram-link-dark")
    net.down.discard(n)
    if n in reg.relays:
        reg.relays[n].restore()
        reg.leave(n)                       # a client whose session ended reconnects on its own, or is restarted here
    reg.join(n, net.facts(n))
    print(f"    .. ram-link-cut: {n}'s link is back")


GEN_EVENTS = [ev_link_down, ev_egress_lost, ev_egress_back, ev_child_moves, ev_churn, ev_ram_link_cut]


def bearers(a, Graph, outdir, hold_s=120):
    """[SLOW_LINKS_V1] Which bearers a member's session to the RAM host can be held over: a 52-member forest with one
    grandchild on each profile, the rest on loopback. Each profile's member must join, stay alive through hold_s
    (with the forest changing under it: one leaf leaves and rejoins every 20 s), and hold the current facts and plan
    at the end. Measured: sessions it took (1 = never lost), bytes it received, the participants read's size."""
    net = gen_forest(4, 6)
    names = sorted(n for n in net.nodes if n.endswith("g"))
    profs = sorted(SL.PROFILES)
    pick = dict(zip(names, profs))
    work = tempfile.mkdtemp(prefix="st-bearers-", dir=a.work)
    os.makedirs(os.path.join(work, "facts"))
    links = {n: (SL.profile(pick[n]) if n in pick else None) for n in net.nodes}
    reg = Region(a.bin, a.port, work, links)
    rows = []
    try:
        reg.join("hubA", {"name": "hubA", "guid": "g-hubA", "role": "hub", "pubkey": "sim", "endpoint": "198.18.255.1:51820"})
        for n in net.nodes:
            reg.join(n, net.facts(n))
        t0 = time.monotonic()
        churners = [n for n in net.nodes if n.endswith("g") and n not in pick][:3]
        k = 0
        while time.monotonic() - t0 < hold_s:
            time.sleep(20)
            c = churners[k % len(churners)] if churners else None
            if c:
                reg.leave(c); time.sleep(2); reg.join(c, net.facts(c))
            k += 1
        P, size = reg.call({"op": "participants"})
        live = {p["name"] for p in P["participants"] if p.get("alive")}
        for n in names:
            if n not in pick:
                continue
            log = open(os.path.join(work, "var", n, "client.log")).read()
            joins, ended = log.count(" joined the region "), log.count("ending this session")
            r = reg.relays.get(n)
            rows.append({"profile": pick[n], "link": links[n].label(), "member": n, "alive_at_end": n in live,
                         "sessions": joins, "sessions_ended": ended,
                         "received_bytes": (r.stats.get("down_bytes", 0) if r else 0), "participants_read_bytes": size})
    finally:
        reg.close()
    held = [r for r in rows if r["alive_at_end"] and r["sessions_ended"] == 0]
    print(f"  bearers: one member per profile in a 52-member forest, held {hold_s} s while the forest changes "
          f"(participants read {rows[0]['participants_read_bytes'] if rows else 0} bytes)")
    for r in sorted(rows, key=lambda r: SL.PROFILES[r["profile"]].rate_Bps):
        ok = r["alive_at_end"] and r["sessions_ended"] == 0
        print(f"    [{'HELD' if ok else 'LOST'}] {r['profile']:12s} {r['link']:44s} sessions {r['sessions']}, ended {r['sessions_ended']}, "
              f"alive at the end: {'yes' if r['alive_at_end'] else 'NO'}, received {r['received_bytes']/1024:.0f} KB")
    json.dump(rows, open(os.path.join(outdir, "bearers.json"), "w"), indent=2)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--fdfpy", default=next((d for d in (os.environ.get("FDF_PY", ""),
                                                        os.path.join(_PARENT, "../fdf/src/fdf-source/fdf/lib/python"),
                                                        "/opt/fdf/lib/python")
                                              if d and os.path.isfile(os.path.join(d, "fdf_graph.py"))), ""))
    ap.add_argument("--only", nargs="*", default=["baselines", "generated"])
    ap.add_argument("--links", default="mixed", help="mixed (a profile per member), lan (loopback), or one profile")
    ap.add_argument("--out", default=".")
    ap.add_argument("--work", default=None)
    ap.add_argument("--port", type=int, default=51989)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--hold", type=float, default=120, help="how long the bearer members are held")
    a = ap.parse_args()
    a.bin = os.path.abspath(a.bin)
    for b in ("fdf-ram", "fdf-client", "fdf-events"):
        if not os.access(os.path.join(a.bin, b), os.X_OK):
            print(f"S TOPO: FAIL - {b} not in --bin {a.bin}")
            return 1
    if not a.fdfpy:
        print("S TOPO: FAIL - no fdf_graph.py")
        return 1
    sys.path.insert(0, os.path.abspath(a.fdfpy))
    import fdf_graph
    try:
        if os.geteuid() == 0 and int(open("/proc/sys/fs/inotify/max_user_instances").read()) < 2048:
            open("/proc/sys/fs/inotify/max_user_instances", "w").write("2048")
    except OSError:
        pass
    Region._graph = fdf_graph.Graph
    Region._timeout = a.timeout
    os.makedirs(a.out, exist_ok=True)
    print(f"TIER 3 TOPOLOGIES - FD&F discovery from memory (real fdf-ram, a real fdf-client per member, fdf_graph "
          f"{fdf_graph.BUILD_TAG}); member links: {a.links}; routes followed only in the verification plan")
    import glob
    jobs = []
    want = set(a.only)
    if "baselines" in want or any(x.startswith("topo_") for x in want):
        for f in sorted(glob.glob(os.path.join(_HERE, "baselines", "*.json"))):
            key = os.path.basename(f)[:-5]
            if "baselines" in want or key in want:
                jobs.append(("baseline", key))
                if key in AS_LEASES:
                    jobs.append(("baseline-leases", key))
    gens = {"tree": lambda: gen_tree(3, 3, 2), "mesh": gen_mesh, "forest": lambda: gen_forest(4, 6)}
    for g in gens:
        if "generated" in want or g in want:
            jobs.append(("generated", g))
    results, nbad = [], 0
    for kind, key in jobs:
        if kind.startswith("baseline"):
            net, notes = from_baseline(key, as_hub=(kind == "baseline"))
            print(f"  {net.name}: {len(net.nodes)} nodes, {len(net.served_by())} served /24s (hardware: every pair reached)")
            for n_ in notes:
                print(f"    note: {n_}")
            r = run_net(net, [], a, fdf_graph.Graph, a.out, notes)
        else:
            net = gens[key]()
            print(f"  {net.name}: {len(net.nodes)} nodes, {len(net.served_by())} served /24s (every path stated)")
            r = run_net(net, GEN_EVENTS, a, fdf_graph.Graph, a.out)
        results.append(r)
        nbad += 0 if r["ok"] else 1
    brs = bearers(a, fdf_graph.Graph, a.out, a.hold) if ("bearers" in want or "generated" in want) else []
    # the gate on bearers: every profile at least HaLow's (600 kb/s) holds its session; slower ones are measured
    for r in brs:
        if SL.PROFILES[r["profile"]].rate_Bps >= SL.PROFILES["halow"].rate_Bps and not (r["alive_at_end"] and r["sessions_ended"] == 0):
            nbad += 1
            print(f"  [FAIL] a member on {r['profile']} did not hold its session")
    json.dump(results, open(os.path.join(a.out, "s-topo.json"), "w"), indent=2)
    print(f"S TOPO: {'PASS' if not nbad else 'FAIL'} ({len(results)} topologies, {nbad} failing); verification plans in {os.path.abspath(a.out)}")
    return 1 if nbad else 0


if __name__ == "__main__":
    sys.exit(main())
