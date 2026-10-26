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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_bed.py - [S4_BED_V1] TIER 4 bed for the IS-IS / shared-RAM comparison (day-claims, Day's IS-IS question).

One physical graph, built in real-kernel network namespaces, shared by both arms:

  hub       one node, the hub. Point-to-point layer-2 link hl<i> to each egress node i.
  egress i  serves 10.20.i.0/24 on its LAN (eth0, 10.20.i.1); hub link hl0 (172.30.i.2/30, hub side 172.30.i.1).
  child k   serves 10.20.k.0/24 on lan0 (10.20.k.1); holds a lease on its egress's LAN on eth1 (10.20.e.(19+n)).

A LAN is a bridge on the machine its egress lives on; a child lives with its egress. A hub link is a veth pair when
both ends are on one machine, and a VXLAN tunnel (layer 2 over UDP, its own VNI) when the egress is on another
machine: then the link crosses the real network between the machines. Nothing here routes: the arms do.

Plan:   plan(S, E, machines) -> {"nodes": {name: {...}}, "links": [...], "placement": {name: machine}}
        machines = [{"name", "ip"}]; the hub and egress e1 on machines[0]; egress i on machines[(i-1) % len].
Build:  Bed(plan, me).build() on each machine builds that machine's namespaces, LANs and its ends of the hub links.
        Bed.teardown() removes everything it made. Namespace names: q-<node> (never s-*, the old bed's).

The oracle: walk() follows each node's kernel table from every node to every served /24 (the verification plan, the only
place a route is followed) and shortest() gives the hops the physical graph allows; a LAN is one broadcast segment, so
children of one egress are one hop apart.
"""
import json
import os
import subprocess
import sys

NS = "q-"


def sh(*argv, check=True, ns=None, quiet=False):
    cmd = (["ip", "netns", "exec", NS + ns] if ns else []) + [str(a) for a in argv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r


def plan(S, E, machines, ring=False, mtu=1500):
    """ring: [S4_RING_V1] also a point-to-point link between neighbouring egress nodes (e1-e2, e2-e3, ..., eE-e1;
    one link when E = 2), so every egress has a second path to the hub and a cut hub link can be routed around.
    The hub also holds ram0 = 198.51.100.1/32 (the region's RAM manager, for the layered run; a passive IS-IS circuit)."""
    assert 1 <= E < S <= 250 and machines
    nodes, links, placement, seats = {}, [], {}, {}
    nodes["hub"] = {"role": "hub", "ifaces": {"ram0": "198.51.100.1/32"}}
    placement["hub"] = machines[0]["name"]
    for i in range(1, E + 1):
        n = f"e{i}"
        nodes[n] = {"role": "egress", "served": f"10.20.{i}.0/24",
                    "ifaces": {"eth0": f"10.20.{i}.1/24", "hl0": f"172.30.{i}.2/30"}}
        nodes["hub"]["ifaces"][f"hl{i}"] = f"172.30.{i}.1/30"
        placement[n] = machines[(i - 1) % len(machines)]["name"]
        seats[i] = 0
        links.append({"kind": "hub", "a": ["hub", f"hl{i}"], "b": [n, "hl0"], "vni": 4000 + i})
    if ring and E >= 2:
        pairs = [(1, 2)] if E == 2 else [(i, i % E + 1) for i in range(1, E + 1)]
        for j, (x, y) in enumerate(pairs):
            nodes[f"e{x}"]["ifaces"][f"rn{y}"] = f"172.31.{j}.1/30"
            nodes[f"e{y}"]["ifaces"][f"rn{x}"] = f"172.31.{j}.2/30"
            links.append({"kind": "ring", "a": [f"e{x}", f"rn{y}"], "b": [f"e{y}", f"rn{x}"], "vni": 5000 + j})
    for k in range(E + 1, S + 1):
        e = (k - E - 1) % E + 1
        seats[e] += 1
        n = f"c{k}"
        nodes[n] = {"role": "child", "egress": f"e{e}", "served": f"10.20.{k}.0/24",
                    "ifaces": {"lan0": f"10.20.{k}.1/24", "eth1": f"10.20.{e}.{19 + seats[e]}/24"}}
        placement[n] = placement[f"e{e}"]
    return {"S": S, "E": E, "ring": ring, "mtu": mtu, "machines": machines, "nodes": nodes, "links": links, "placement": placement}


def adjacency(p):
    """The physical graph: hub - egress (hub link); egress - each of its children and the children among themselves
    (one LAN, one broadcast segment: every member is one hop from every other)."""
    adj = {n: set() for n in p["nodes"]}
    lans = {}
    for n, d in p["nodes"].items():
        if d["role"] == "egress":
            adj[n].add("hub"); adj["hub"].add(n); lans.setdefault(n, [n])
    for n, d in p["nodes"].items():
        if d["role"] == "child":
            lans[d["egress"]].append(n)
    for members in lans.values():
        for a in members:
            adj[a].update(m for m in members if m != a)
    for l in p["links"]:
        if l["kind"] == "ring":
            x, y = l["a"][0], l["b"][0]
            adj[x].add(y); adj[y].add(x)
    return adj


def shortest(p):
    """{src: {dest /24: hops}} over the physical graph (BFS)."""
    from collections import deque
    adj = adjacency(p)
    served = {d["served"]: n for n, d in p["nodes"].items() if d.get("served")}
    out = {}
    for src in p["nodes"]:
        dist = {src: 0}; q = deque([src])
        while q:
            x = q.popleft()
            for y in adj[x]:
                if y not in dist:
                    dist[y] = dist[x] + 1; q.append(y)
        out[src] = {net: dist[dst] for net, dst in served.items() if dst != src}
    return out


def owners(p):
    """address -> node, for every address a node holds (the walk turns a next-hop address into a node)."""
    o = {}
    for n, d in p["nodes"].items():
        for cidr in d["ifaces"].values():
            o[cidr.split("/")[0]] = n
        if d["role"] == "egress":                             # its overlay edge (the RAM arm's tunnels; FrogNet's rule)
            a, b = d["served"].split(".")[1:3]
            o[f"10.253.{a}.{b}"] = n
    o["10.253.253.254"] = "hub"
    return o


def walk(p, tables, src, net, own=None, dead=frozenset()):
    """[S4_VERIFY_WALK_V1] the one place a route is followed: from src toward net over the kernel tables every node
    reported ({node: {dest: (via, dev)}}), hop by hop. -> (path, verdict)."""
    own = own or owners(p)
    served = {d["served"]: n for n, d in p["nodes"].items() if d.get("served")}
    path, at = [src], src
    while True:
        if served.get(net) == at:
            return path, "ok"
        if len(path) > 12:
            return path, "LOOP"
        r = tables.get(at, {}).get(net)
        dflt = False
        if not r:
            r = tables.get(at, {}).get("default")
            dflt = True
        if not r:
            return path, "NO ROUTE"
        via, dev = r
        if (at, dev) in dead:
            # [S4_DEAD_LINK_V1] the table still sends it over a silenced link: the packet is lost there
            return path, f"DEAD LINK {at}:{dev}"
        if dflt and p["nodes"][at]["role"] != "child":
            # [S4_UNDERLAY_IS_NOT_A_PATH_V1] an egress's default is its Internet uplink: it routes no 10/8 (the hub's
            # machine drops 10/8 from the underlay, as the Internet does). Only a child's default (its exit's LAN) counts
            return path, "NO ROUTE"
        if dev.startswith("wg") and not via:
            nxt = "hub"                                       # an overlay route: the tunnel's far end is the hub
        else:
            # a connected route (no via): the destination's own server is on that link
            nxt = own.get(via) if via else served.get(net)
        if not nxt:
            return path, f"NO HOLDER OF {via}"
        path.append(nxt)
        at = nxt


def addr_of(p, node, peer):
    """The address on `peer`'s side of the link between node and peer (the next hop as an address)."""
    nd, pd = p["nodes"][node], p["nodes"][peer]
    if nd["role"] == "child" and peer == nd["egress"]:
        return pd["ifaces"]["eth0"].split("/")[0]
    if pd["role"] == "child" and node == pd["egress"]:
        return pd["ifaces"]["eth1"].split("/")[0]
    if node == "hub":
        return pd["ifaces"]["hl0"].split("/")[0]
    if peer == "hub":
        i = int(node[1:])
        return pd["ifaces"][f"hl{i}"].split("/")[0]
    raise ValueError(f"{node} and {peer} are not adjacent")


class Bed:
    def __init__(self, p, me):
        self.p, self.me = p, me
        self.mine = [n for n, m in p["placement"].items() if m == me]
        self.ip = {m["name"]: m["ip"] for m in p["machines"]}
        self.made_links = []
        self.silenced = set()                 # (node, dev) silenced now: the walk treats a hop over one as lost

    def _veth(self, a, ai, b, bi):
        t1, t2 = f"qv{len(self.made_links)}a", f"qv{len(self.made_links)}b"
        sh("ip", "link", "del", t1, check=False)
        sh("ip", "link", "add", t1, "type", "veth", "peer", "name", t2)
        for t, n, i in ((t1, a, ai), (t2, b, bi)):
            sh("ip", "link", "set", t, "netns", NS + n)
            sh("ip", "link", "set", t, "name", i, ns=n)
        self.made_links.append(t1)

    def build(self):
        p = self.p
        # [S4_CLEANS_FIRST_V1] a run that crashed before teardown leaves q-* namespaces -- and the VXLAN devices moved
        # into them -- holding their VNIs, so a rebuild collides with its own leftovers ("VNI already exists"). Delete
        # every q-* namespace on this machine (not just this plan's), and any qx* VXLAN still dangling in root, first.
        for ns in sh("ip", "-o", "netns", "list", check=False).stdout.splitlines():
            ns = (ns.split() or [""])[0]
            if ns.startswith(NS):
                sh("ip", "netns", "del", ns, check=False)
        for ln in sh("ip", "-o", "link", "show", "type", "vxlan", check=False).stdout.splitlines():
            name = ln.split(":", 2)[1].strip().split("@")[0] if ":" in ln else ""
            if name.startswith("qx"):
                sh("ip", "link", "del", name, check=False)
        for n in self.mine:
            sh("ip", "netns", "del", NS + n, check=False)
            sh("ip", "netns", "add", NS + n)
            sh("ip", "link", "set", "lo", "up", ns=n)
            sh("sysctl", "-q", "-w", "net.ipv4.ip_forward=1", ns=n)
            sh("sysctl", "-q", "-w", "net.ipv4.conf.all.rp_filter=0", ns=n)
        # LANs: a bridge per egress on this machine, in the egress's own namespace (eth0 is the bridge itself)
        for n in self.mine:
            d = p["nodes"][n]
            if d["role"] == "egress":
                sh("ip", "link", "add", "eth0", "type", "bridge", ns=n)
            if d["role"] == "hub":
                sh("ip", "link", "add", "ram0", "type", "veth", "peer", "name", "ram0p", ns=n)
                sh("ip", "link", "set", "ram0p", "up", ns=n)
            if d["role"] == "child":
                # lan0: a veth whose far end stays in the node (the served /24 needs a link that is up)
                sh("ip", "link", "add", "lan0", "type", "veth", "peer", "name", "lan0p", ns=n)
                sh("ip", "link", "set", "lan0p", "up", ns=n)
        for n in self.mine:
            d = p["nodes"][n]
            if d["role"] == "child":
                e = d["egress"]
                k = int(n[1:])
                pe = f"lp{k}"
                # child eth1 <-> a port on its egress's bridge
                t1, t2 = f"qc{k}a", f"qc{k}b"
                sh("ip", "link", "del", t1, check=False)
                sh("ip", "link", "add", t1, "type", "veth", "peer", "name", t2)
                sh("ip", "link", "set", t1, "netns", NS + n)
                sh("ip", "link", "set", t1, "name", "eth1", ns=n)
                sh("ip", "link", "set", t2, "netns", NS + e)
                sh("ip", "link", "set", t2, "name", pe, ns=e)
                sh("ip", "link", "set", pe, "master", "eth0", ns=e)
                sh("ip", "link", "set", pe, "up", ns=e)
        # hub links
        for l in p["links"]:
            (a, ai), (b, bi) = l["a"], l["b"]
            ma, mb = p["placement"][a], p["placement"][b]
            if ma == self.me and mb == self.me:
                self._veth(a, ai, b, bi)
            elif self.me in (ma, mb):
                n, i, other = (a, ai, mb) if ma == self.me else (b, bi, ma)
                # [S4_VXLAN_V1] created in the root namespace (its UDP socket stays there, on the real network), then
                # moved into the node: a layer-2 link across the machines
                t = f"qx{l['vni']}"
                sh("ip", "link", "del", t, check=False)
                sh("ip", "link", "add", t, "type", "vxlan", "id", l["vni"], "remote", self.ip[other],
                   "local", self.ip[self.me], "dstport", 4789 + 0, "nolearning")
                sh("ip", "link", "set", t, "netns", NS + n)
                sh("ip", "link", "set", t, "name", i, ns=n)
                sh("bridge", "fdb", "append", "00:00:00:00:00:00", "dev", i, "dst", self.ip[other], ns=n, check=False)
        for n in self.mine:
            for i, cidr in p["nodes"][n]["ifaces"].items():
                sh("ip", "addr", "add", cidr, "dev", i, ns=n)
                if i.startswith(("hl", "rn")):
                    # [S4_LINK_MTU_V1] one MTU on every hub and ring link, local or not: a VXLAN link across the machines
                    # carries 50 bytes of its own inside whatever path joins them (a WireGuard overlay: 1420)
                    sh("ip", "link", "set", i, "mtu", p.get("mtu", 1500), ns=n)
                sh("ip", "link", "set", i, "up", ns=n)
        return self

    def teardown(self):
        """[S4_TEARDOWN_LEAVES_NOTHING_V1] every process in the bed's namespaces ends (TERM, then KILL after 3 s for any
        that did not): a namespace deleted under a live daemon leaves the daemon running with no network at all."""
        import time as _t
        pids = []
        for n in self.mine:
            pids += sh("ip", "netns", "pids", NS + n, check=False).stdout.split()
        for pid in pids:
            subprocess.run(["kill", pid], capture_output=True)
        t_end = _t.time() + 3
        while _t.time() < t_end and any(os.path.exists(f"/proc/{pid}") for pid in pids):
            _t.sleep(0.1)
        for pid in pids:
            if os.path.exists(f"/proc/{pid}"):
                subprocess.run(["kill", "-9", pid], capture_output=True)
        for n in self.mine:
            sh("ip", "netns", "del", NS + n, check=False)

    def routes(self, n):
        """{dest: (via, dev)} for the bed's /24s and the default route in n's kernel table (any protocol)."""
        out = {}
        for ln in sh("ip", "-4", "route", "show", ns=n).stdout.splitlines():
            q = ln.split()
            if q and ((q[0].endswith("/24") and q[0].startswith("10.20.")) or q[0] == "default"):
                via = q[q.index("via") + 1] if "via" in q else ""
                dev = q[q.index("dev") + 1] if "dev" in q else ""
                out.setdefault(q[0], (via, dev))            # the first listed is the one the kernel uses (lowest metric)
        return out

    def silence(self, n, dev, on=True):
        """[S4_SILENT_CUT_V1] the link stays up but carries nothing: an nftables netdev egress drop on n's end of dev
        (the far end gets the same call). This is the failure a WAN link has (no carrier change, nothing arrives), so
        the arm must find it itself; a carrier drop would let the kernel flush the routes before the arm does anything.
        Egress, not ingress: a routing daemon's packet socket sees frames before an ingress hook could drop them."""
        t = f"s4cut_{dev}"
        (self.silenced.add if on else self.silenced.discard)((n, dev))
        if on:
            sh("nft", "add", "table", "netdev", t, ns=n)
            sh("nft", "add", "chain", "netdev", t, "out", f"{{ type filter hook egress device {dev} priority 0 ; }}", ns=n)
            sh("nft", "add", "rule", "netdev", t, "out", "drop", ns=n)
        else:
            sh("nft", "delete", "table", "netdev", t, ns=n)

    def tables(self):
        return {n: self.routes(n) for n in self.mine}


class Watcher:
    """[S4_HELD_ROUTES_V1] one `ip -ts monitor route` per node: the kernel says when a table changed; the waiter re-reads
    only those tables. No polling: a wait sleeps on the condition until a monitor line arrives.
    [S4_NEXTHOP_EVENTS_V1] route events alone miss changes: zebra installs routes through nexthop objects (nhid), and
    when a nexthop object or its link goes the kernel drops the routes using it WITHOUT a route event. So nexthop and
    link events mark the table changed too."""
    def __init__(self, bed):
        import threading
        self.bed, self.cv, self.dirty, self.last = bed, threading.Condition(), set(bed.mine), {}
        self.procs, self.tables = [], {}          # every node's table as last read: kept across waits
        for n in bed.mine:
            pr = subprocess.Popen(["ip", "netns", "exec", NS + n, "ip", "monitor", "route", "nexthop", "link"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
            self.procs.append(pr)
            threading.Thread(target=self._read, args=(n, pr), daemon=True).start()

    def _read(self, n, pr):
        import time as _t
        for _ in pr.stdout:
            with self.cv:
                self.dirty.add(n)
                self.last[n] = _t.time()
                self.cv.notify_all()

    def wait_until(self, test, timeout_s):
        """test(tables) over every node's table, re-run after each burst of changes. -> (ok, seconds, tables)."""
        import time as _t
        t_end = _t.time() + timeout_s
        tables = self.tables
        while True:
            with self.cv:
                d, self.dirty = self.dirty, set()
            for n in d:
                tables[n] = self.bed.routes(n)
            if test(tables):
                return True, tables
            with self.cv:
                while not self.dirty:
                    left = t_end - _t.time()
                    if left <= 0:
                        return False, tables
                    self.cv.wait(left)
            _t.sleep(0.02)                                   # let a burst of route changes land before re-reading

    def close(self):
        for pr in self.procs:
            pr.kill()


def wire_bytes(bed):
    """(tx bytes, tx packets) summed over every interface of every node here: with no application traffic on the bed,
    this is the arm's control traffic (each packet counted once, by its sender)."""
    b = pk = 0
    for n in bed.mine:
        r = sh("sh", "-c", "for d in /sys/class/net/*; do i=${d##*/}; case $i in lo|lan0p|ram0p|lp*) continue;; esac; "
               "echo $(cat $d/statistics/tx_bytes) $(cat $d/statistics/tx_packets); done", ns=n, check=False)
        for ln in r.stdout.splitlines():
            x, y = ln.split()
            b += int(x); pk += int(y)
    return b, pk


if __name__ == "__main__":
    p = plan(int(sys.argv[1]) if len(sys.argv) > 1 else 10, int(sys.argv[2]) if len(sys.argv) > 2 else 2,
             [{"name": "local", "ip": "127.0.0.1"}])
    print(json.dumps({k: p[k] for k in ("S", "E", "placement")}, indent=1)[:2000])
