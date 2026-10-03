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
sim/system.py - topology-driven, event-driven whole-system simulator that runs
the REAL ported discovery against a declared network and validates behaviour.

You declare a TopologySpec (nodes, their served /24s, LAN guest relations, and
tunnels). The System builds each node's per-merge inputs from that spec, then
runs the ACTUAL ported merge() per node, cycle by cycle, with getHosts returning
each peer's *currently discovered* host set - so horizons grow transitively to a
fixpoint, exactly as repeated runMerge + propagation do on the real fleet.

Then it validates: every (live) node converged to the full host set, each route
has the right shape (winner /24 via the correct dev), and all-pairs reachability
holds over the installed tables. Events (remove_node / add_node) re-trigger
convergence and re-validate - discovery is change-driven, so churn is the point.

"Real code" = the faithful Python port of the bash, in-process. It is validated
against the oracle logs; port-vs-bash equivalence still needs the on-box
differential. The proof plane gives behavioural coverage, not bash-equivalence.
"""
from __future__ import annotations

import os, sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from discovery.kernel import FakeKernel
from discovery.routes import Routes
from discovery.discovery import Discovery
from discovery.sources import HostStore
from discovery.orchestrate import merge
from discovery.sim.fabric import route_egress


@dataclass
class NodeSpec:
    name: str
    subnet3: str                       # "10.10.10" -> serves 10.10.10.0/24, is .1
    guest_on: tuple | None = None      # (host_name, client_addr) if on another's LAN
    shared_lan: tuple | None = None    # (seg3, my_addr) shared peering LAN membership
    dead: bool = False                 # this node's endpoints answer echo dead
    upstream: str = "0.0.0.0"
    upstream2: str = "0.0.0.0"
    guid: str = ""                     # FrogNet GUID - network-unique box identity

    @property
    def one(self): return f"{self.subnet3}.1"
    @property
    def cidr(self): return f"{self.subnet3}.0/24"

    def echo_csv(self) -> str:
        if self.dead:
            return f"{self.name},,,"
        return f"{self.name},{self.one},{self.upstream},{self.upstream2}"


@dataclass
class TopologySpec:
    nodes: list                        # [NodeSpec]
    tunnels: list = field(default_factory=list)   # [(nameA, nameB)] undirected wg


# latency model: lower wins. direct beats relayed; lan beats wg.
_RTT = {("lan", "direct"): 20, ("lan", "relay"): 40,
        ("wg", "direct"): 50, ("wg", "relay"): 90}






def install_offline_tuples():
    """[SIM_OFFLINE_TUPLES_V1] The sim's merges call the REAL orchestrate.merge ->
    select_database_host, which reads capability from core.frognet_tuples over the
    network at `dbhost`. In a sim `dbhost` is a FAKE node IP (e.g. 10.18.18.1). On a
    box that is NOT wired into the mesh (this container) that IP has no route and the
    read fails instantly; on a real FrogNet box the fake IP is routable-but-dead, so
    every read BLOCKS the full ~4s DB timeout -- and a sim does dozens of converges x
    ~27 reads each, so the pre-activation gate never finishes. The sim expects NO
    capability (the degraded highest-IP floor) anyway, so install a no-network stub
    that returns [] instantly. Idempotent. Entry points that install their own
    capability fake (service_election_check, dbhost_barrier_run) simply don't call
    this and set their own module."""
    import sys as _sys, types as _types
    cur = _sys.modules.get("core.frognet_tuples")
    if cur is not None and getattr(cur, "_sim_offline", False):
        return
    T = _types.ModuleType("core.frognet_tuples")
    T._sim_offline = True
    T.DEFAULT_DBHOST = "databasehost_control.frognet"
    T.get = lambda *a, **k: []
    T.get_all = lambda *a, **k: []
    T._values_raw = lambda *a, **k: []
    T.put = lambda *a, **k: True
    T.my_ip = lambda: "10.0.0.1"
    T.role_scope = lambda role: f"SD:capability.host:10.0.0.1:{role}"
    _sys.modules["core.frognet_tuples"] = T
    _sys.modules["frognet_tuples"] = T
    try:
        import core as _core
        _core.frognet_tuples = T
    except Exception:
        pass


class System:
    def __init__(self, spec: TopologySpec):
        install_offline_tuples()   # sim merges must not do real network capability reads
        self._load(spec)

    def _load(self, spec: TopologySpec):
        self.spec = spec
        self.node = {n.name: n for n in spec.nodes}
        for n in spec.nodes:
            if not n.guid:
                n.guid = f"guid-{n.name}"
        self.tunnels = {}
        for a, b in spec.tunnels:
            self.tunnels.setdefault(a, []).append(b)
            self.tunnels.setdefault(b, []).append(a)
        # LAN: host_name -> [(guest_name, client_addr)]
        self.lan_guests = {}
        for n in spec.nodes:
            if n.guest_on:
                host, addr = n.guest_on
                self.lan_guests.setdefault(host, []).append((n.name, addr))
                # seg-relay self-description: echo advertises the client addr as
                # the relay (matches oracle New-York-2 echo field 3 = client addr)
                if n.upstream in ("", "0.0.0.0"):
                    n.upstream = addr
        # shared peering LAN: seg3 -> [(node_name, addr)] (all members on one /24)
        self.shared_lan_members = {}
        for n in spec.nodes:
            if n.shared_lan:
                seg3, addr = n.shared_lan
                self.shared_lan_members.setdefault(seg3, []).append((n.name, addr))
        # dynamic discovered set: subnet3s each node knows (own to start)
        self.known = {n.name: {n.subnet3} for n in spec.nodes}
        self.routes = {n.name: [] for n in spec.nodes}
        self._build_adj()

    def _build_adj(self):
        """Adjacency with edge costs: tunnels=50 (wg), LAN=20. Used to model
        measured RTT as true path cost so the nearest path wins (no loops)."""
        adj = {n.name: {} for n in self.spec.nodes}
        for a, b in self.spec.tunnels:
            adj[a][b] = min(adj[a].get(b, 99), 50)
            adj[b][a] = min(adj[b].get(a, 99), 50)
        # shared-LAN adjacency: every pair of members is a direct LAN neighbour
        for seg3, members in getattr(self, "shared_lan_members", {}).items():
            for a2 in members:
                for b2 in members:
                    if a2[0] != b2[0]:
                        adj[a2[0]][b2[0]] = 20
        # LAN adjacency: guest<->host, and guests sharing a host
        for host, guests in self.lan_guests.items():
            gnames = [g for (g, _a) in guests]
            for g in gnames:
                adj[g][host] = 20; adj[host][g] = 20
            for i in range(len(gnames)):
                for j in range(i + 1, len(gnames)):
                    adj[gnames[i]][gnames[j]] = 20
                    adj[gnames[j]][gnames[i]] = 20
        self.adj = adj

    def _dist(self, src, dst):
        """Dijkstra shortest path cost over adj; inf if unreachable."""
        import heapq
        if src == dst:
            return 0
        seen = set(); pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                return d
            if u in seen:
                continue
            seen.add(u)
            for v, w in self.adj.get(u, {}).items():
                if v not in seen:
                    heapq.heappush(pq, (d + w, v))
        return 10 ** 9

    # ---- topology helpers --------------------------------------------------
    def _owner_of(self, ip: str):
        # exact identity matches first: a node's .1, then a guest's client addr.
        for n in self.node.values():
            if n.one == ip:
                return n
        for n in self.node.values():
            if n.guest_on and n.guest_on[1] == ip:
                return n
        for n in self.node.values():
            if n.shared_lan and n.shared_lan[1] == ip:
                return n
        # fall back to served-subnet match (e.g. the .2 admin alias)
        s3 = ".".join(ip.split(".")[:3])
        for n in self.node.values():
            if n.subnet3 == s3:
                return n
        return None

    def _wg_devs(self, name):
        # deterministic wgN per tunnel peer
        return {peer: f"wg{i}" for i, peer in enumerate(sorted(self.tunnels.get(name, [])))}

    def _echo_carries(self, observer, query_ip):
        """Whether observer's echo probe to query_ip gets a reply (return path
        exists). Base model: always. Overridden by sim/lan_chain.py."""
        return True

    # ---- per-observer sources (dynamic getHosts) ---------------------------
    def _sources(self, observer):
        sysref = self
        wgmap = self._wg_devs(observer)            # peer -> wg dev
        dev_peer = {dev: peer for peer, dev in wgmap.items()}
        direct_tunnel_ones = {sysref.node[p].one for p in self.tunnels.get(observer, [])}

        class _Echo:
            def echo_probe(self, query_ip):
                owner = sysref._owner_of(query_ip)
                if owner is None:
                    return None
                # return-path gate: a probe's echo only comes back if the far end
                # can route to the prober's source. Default True (no constraint);
                # subclasses model the constraint (see sim/lan_chain.py).
                if not sysref._echo_carries(observer, query_ip):
                    return None
                return owner.echo_csv()                       # owner.dead -> blank self_ip

        class _Rtt:
            def measure_rtt(self, dev, pip):
                owner = sysref._owner_of(pip)
                if not owner:
                    return ""
                if dev.startswith("wg"):
                    far = dev_peer.get(dev)
                    if far is None:
                        return ""
                    cost = 50 + sysref._dist(far, owner.name)   # hop onto tunnel + path beyond
                else:
                    cost = sysref._dist(observer, owner.name) or 20
                return str(int(cost))

        class _GetHosts:
            def get_hosts(self, host_path, pip):
                owner = sysref._owner_of(host_path)
                if not owner:
                    return []
                return [(sysref.node[n2].one, n2)
                        for n2 in [x for x in sysref.node]
                        if sysref.node[n2].subnet3 in sysref.known[owner.name]
                        and sysref.node[n2].subnet3 != owner.subnet3]

        class _Broker:
            def broker_for_peer_ip(self, dest1):
                # only DIRECT tunnel peers are uplink-parents (authority)
                if dest1 in direct_tunnel_ones:
                    o = sysref._owner_of(dest1)
                    peer = next(p for p in sysref.tunnels[observer]
                                if sysref.node[p].one == dest1)
                    return (f"{peer}-{o.subnet3}", o.cidr, dest1)
                return None
            def channel_for_iface(self, dev):
                p = dev_peer.get(dev)
                return f"{p}-{sysref.node[p].subnet3}" if p else ""
            def transits(self, relay_one, dest24):
                return True  # no transit map modelled -> gate inert

        return _Echo(), _Rtt(), _GetHosts(), _Broker()

    # ---- per-observer merge inputs ----------------------------------------
    def _inputs(self, observer):
        n = self.node[observer]
        wgmap = self._wg_devs(observer)
        devs = ["eth0"] + [wgmap[p] for p in sorted(self.tunnels.get(observer, []))]
        dev_ip = {"eth0": n.one}
        dev_src = {}
        # enter_seed: own connected /24 + bringup route per direct tunnel peer
        enter = [f"{n.cidr} dev eth0 proto kernel scope link src {n.one}"]
        active_states, wg_kernel_nets, dead = [], {}, set()
        for peer in sorted(self.tunnels.get(observer, [])):
            dev = wgmap[peer]; po = self.node[peer]
            enter.append(f"{po.cidr} via {po.one} dev {dev} metric 22 onlink")
            active_states.append((dev, [po.cidr]))
            wg_kernel_nets[dev] = [po.cidr]
            if po.dead:
                dead.add(dev)
        # arp seeds: LAN guests on my served /24
        arp = {"eth0": [addr for (_g, addr) in self.lan_guests.get(observer, [])]}
        # if I am a guest on someone's LAN, I have an UPLINK interface (wlan1, a second
        # radio - wlan0 is my hostapd AP projecting my own SSID). The uplink carries a
        # DHCP lease on the host's /24 AND a DEFAULT ROUTE via the host's .1 (dnsmasq
        # option 3) - that default is the UPSTREAM direction the directional next-hop
        # keys on. Mirrors reality: Sea2 = wlan0(hostapd AP 10.120.120.1) +
        # wlan1(client lease 10.130.130.47, default via 10.130.130.1).
        if n.guest_on:
            host, client_addr = n.guest_on
            ho = self.node[host]
            devs.append("wlan1")
            dev_ip["wlan1"] = client_addr
            enter.append(f"{ho.cidr} dev wlan1 proto kernel scope link src {client_addr}")
            enter.append(f"default via {ho.one} dev wlan1")
            arp.setdefault("wlan1", []).append(ho.one)
        # shared peering LAN: an interface (eth2) on the common /24, plus every OTHER
        # member's addr as an arp neighbour (they discover each other directly).
        if n.shared_lan:
            seg3, addr = n.shared_lan
            devs.append("eth2"); dev_ip["eth2"] = addr
            enter.append(f"{seg3}.0/24 dev eth2 proto kernel scope link src {addr}")
            peers = [a for (nm, a) in self.shared_lan_members.get(seg3, []) if a != addr]
            if peers:
                arp.setdefault("eth2", []).extend(peers)
        seeds = dict(active_devs=devs, dev_ip=dev_ip, leases=[], disk_cache=[],
                     active_states=active_states, wg_kernel_nets=wg_kernel_nets,
                     arp_neigh=arp)
        local_ips = {n.one, f"{n.subnet3}.2", "127.0.0.1"}
        if n.guest_on:
            local_ips.add(n.guest_on[1])   # my uplink lease IS my local IP -> parent .1 is on-subnet
        if n.shared_lan:
            local_ips.add(n.shared_lan[1]) # my shared-LAN addr IS a local IP
        bringup = [(p, self.node[p].one) for p in sorted(self.tunnels.get(observer, []))]
        return enter, seeds, dev_src, local_ips, bringup, dead

    def _merge_one(self, observer, logger=lambda s: None):
        enter, seeds, dev_src, local_ips, bringup, dead = self._inputs(observer)
        k = FakeKernel(); k.seed(*enter)
        routes = Routes(k, logger=logger, clock=lambda: 0.0)
        echo, rtt, gh, broker = self._sources(observer)
        disc = Discovery(routes, echo, rtt, gh, broker, HostStore(),
                         local_ips, dev_src, logger=logger,
                         self_identity=self.node[observer].one,
                         own_subnet=self.node[observer].cidr)
        disc.DEAD_IFACES = dead
        n = self.node[observer]
        out = merge(disc, k, seeds, {}, local=(n.one, observer), bringup_peers=bringup)
        return out

    # ---- churn events (discovery is change-driven) -------------------------
    def remove_node(self, name):
        self.spec.nodes = [n for n in self.spec.nodes if n.name != name]
        self.spec.tunnels = [(a, b) for (a, b) in self.spec.tunnels
                             if name not in (a, b)]
        self._load(self.spec)          # reset discovered state; churn re-triggers merges

    def add_node(self, nodespec, tunnels=()):
        self.spec.nodes.append(nodespec)
        self.spec.tunnels.extend(list(tunnels))
        self._load(self.spec)

    def reip_node(self, name, new_subnet3):
        for n in self.spec.nodes:
            if n.name == name:
                n.subnet3 = new_subnet3
                if n.upstream not in ("", "0.0.0.0") and n.guest_on:
                    pass
        self._load(self.spec)

    def rename_node(self, old, new):
        for n in self.spec.nodes:
            if n.name == old:
                n.name = new
        self.spec.tunnels = [(new if a == old else a, new if b == old else b)
                             for (a, b) in self.spec.tunnels]
        for n in self.spec.nodes:
            if n.guest_on and n.guest_on[0] == old:
                n.guest_on = (new, n.guest_on[1])
        self._load(self.spec)

    def move_node(self, name, new_guest_on=None, new_tunnels=None):
        """Node leaves its current LAN/tunnels and reappears elsewhere."""
        self.spec.tunnels = [(a, b) for (a, b) in self.spec.tunnels
                             if name not in (a, b)]
        for n in self.spec.nodes:
            if n.name == name:
                n.guest_on = new_guest_on
                n.upstream = "0.0.0.0"   # re-derive seg-relay on reload
        if new_tunnels:
            self.spec.tunnels.extend(list(new_tunnels))
        self._load(self.spec)

    # ---- convergence -------------------------------------------------------
    def run_node_on_kernel(self, observer, kernel, seed_synthetic=True,
                           logger=lambda s: None):
        """Run ONE node's real merge against a provided kernel backend.
        seed_synthetic=True seeds a FakeKernel with the derived bringup table
        (pure sim). For a ShadowKernel/RealKernel on a real box, pass
        seed_synthetic=False - it reads the box's actual `ip route` table."""
        from discovery.routes import Routes
        from discovery.discovery import Discovery
        from discovery.sources import HostStore
        from discovery.orchestrate import merge
        enter, seeds, dev_src, local_ips, bringup, dead = self._inputs(observer)
        if seed_synthetic and hasattr(kernel, "seed"):
            kernel.seed(*enter)
        routes = Routes(kernel, logger=logger, clock=lambda: 0.0)
        echo, rtt, gh, broker = self._sources(observer)
        disc = Discovery(routes, echo, rtt, gh, broker, HostStore(),
                         local_ips, dev_src, logger=logger,
                         self_identity=self.node[observer].one,
                         own_subnet=self.node[observer].cidr)
        disc.DEAD_IFACES = dead
        n = self.node[observer]
        return merge(disc, kernel, seeds, {}, local=(n.one, observer),
                     bringup_peers=bringup)

    def converge(self, max_cycles=8):
        for cycle in range(1, max_cycles + 1):
            changed = False
            for name in list(self.node):
                out = self._merge_one(name)
                self.routes[name] = out["final_table"]
                got = {".".join(line.split()[0].split(".")[:3])
                       for line in out["final_table"]
                       if line.split()[0].endswith("/24")}
                got.add(self.node[name].subnet3)
                if got != self.known[name]:
                    self.known[name] = got
                    changed = True
            if not changed:
                return cycle
        return max_cycles          # exhausted without quiescing

    def converge_by_notification(self, max_events=6000):
        """Converge the way the boxes actually do: merge a node; if it CHANGED,
        fire a notification to its derived direct neighbors (discovery.neighbors),
        who merge next, propagating onward until the queue drains - 'merges
        continue until a run completes with no routing changes.' Returns
        (events, quiesced). Brute-force converge() re-runs everyone every cycle and
        hides whether propagation actually reaches every affected node; this does
        not. Use it to prove a change (loop fix, election, route) settles under the
        real notification mechanism, not just an idealized sweep."""
        from collections import deque
        from discovery.neighbors import direct_neighbor_targets
        q = deque(self.node); events = 0
        while q and events < max_events:
            events += 1
            name = q.popleft()
            out = self._merge_one(name)
            self.routes[name] = out["final_table"]
            got = {".".join(l.split()[0].split(".")[:3])
                   for l in out["final_table"] if l.split()[0].endswith("/24")}
            got.add(self.node[name].subnet3)
            if got != self.known.get(name):
                self.known[name] = got
                nd = self.node[name]
                lips = {nd.one, f"{nd.subnet3}.2", "127.0.0.1"}
                if nd.guest_on:
                    lips.add(nd.guest_on[1])
                if getattr(nd, "shared_lan", None):
                    lips.add(nd.shared_lan[1])
                rt = "\n".join(self.routes[name])
                for tip in direct_neighbor_targets(rt, [], lips):
                    owner = self._owner_of(tip)
                    if owner is None:
                        nn = self._ip_node(tip)
                        owner = self.node.get(nn) if nn else None
                    nm = getattr(owner, "name", None)
                    if nm and nm != name:
                        q.append(nm)
        return events, len(q) == 0
        return max_cycles

    # ---- validation --------------------------------------------------------
    def validate(self):
        problems = []
        live = {n.name for n in self.spec.nodes if not n.dead}
        all_s3 = {self.node[x].subnet3 for x in live}
        for name in live:
            missing = all_s3 - self.known[name] - {self.node[name].subnet3}
            if missing:
                problems.append(f"{name}: missing host subnets {sorted(missing)}")
        # all-pairs reachability over installed tables (multi-hop trace)
        for a in live:
            for b in live:
                if a == b:
                    continue
                if not self._reaches(a, self.node[b].one):
                    problems.append(f"{a} cannot reach {b} ({self.node[b].one})")
        return problems

    def _ip_node(self, ip):
        """Resolve a next-hop IP to the node that owns it: an identity .1 -> the
        serving node; a guest's uplink lease -> that guest."""
        for nm, nd in self.node.items():
            if ip == nd.one:
                return nm
            if nd.guest_on and ip == nd.guest_on[1]:
                return nm
        # fall back to subnet ownership (a plain host on a served /24)
        o = self._owner_of(ip)
        return o.name if o else None

    def _reaches(self, src, dst_ip, max_hops=12):
        cur = src; seen = set()
        for _ in range(max_hops):
            n = self.node[cur]
            if n.subnet3 == ".".join(dst_ip.split(".")[:3]):
                return True                       # dst is on my served net -> arrived
            eg = route_egress(self.routes[cur], dst_ip)
            if not eg:
                return False
            dev, _metric, via = eg
            if via:
                nxt = self._ip_node(via)          # follow via to the owning node
            elif dev.startswith("wg"):
                dev_peer = {d: p for p, d in self._wg_devs(cur).items()}
                nxt = dev_peer.get(dev)
            else:
                # connected route (no via): dst sits on this connected subnet
                owner = self._owner_of(dst_ip)
                return owner is not None
            if not nxt or nxt == cur or nxt in seen:
                return bool(nxt) and nxt != cur
            seen.add(cur); cur = nxt
        return False

    # ---- admin-channel reflect probe (matches shipped REFLECT_PROBE_V1) ------
    # Mirrors proxy/proxy_main.py FrogNetReflectHandler on /reflect?o=&c=:
    #   target is one of my IPs            -> REFLECT_OK   (200, reached dest)
    #   o is one of my IPs AND c > 0       -> REFLECT_LOOP (508, reflection)
    #   c >= MAX_HOPS                       -> REFLECT_MAXHOPS (508, safety net)
    #   else                               -> c++ , forward toward target
    # SHIPPED probe carries only o (origin IP) and c (counter) - NO GUID.
    # The trace also returns the GUID at the reflect point so the (PROPOSED, not
    # shipped) duplicate-IP extension can distinguish self vs impostor.
    def _reflect_trace(self, origin, start, target, max_hops=32):
        o = self.node[origin].one
        cur, c, seen = start, 1, 0
        while seen <= max_hops + 4:
            seen += 1
            B = self.node[cur]
            owner = self._owner_of(target)
            if owner is not None and owner.name == cur:          # target local -> 200
                return ("REFLECT_OK", cur, c, B.guid)
            if B.one == o and c > 0:                              # o local & c>0 -> 508
                return ("REFLECT_LOOP", cur, c, B.guid)
            if c >= max_hops:                                     # safety net -> 508
                return ("REFLECT_MAXHOPS", cur, c, B.guid)
            eg = route_egress(self.routes[cur], target)
            if not eg:
                return ("TIMEOUT", cur, c, B.guid)
            c += 1
            dev = eg[0]
            if dev == "eth0":
                owner = self._owner_of(target)
                if owner is not None and owner.name != cur:
                    cur = owner.name; continue
                return ("TIMEOUT", cur, c, B.guid)
            dev_peer = {d: p for p, d in self._wg_devs(cur).items()}
            nxt = dev_peer.get(dev)
            if not nxt or nxt == cur:
                return ("TIMEOUT", cur, c, B.guid)
            cur = nxt
        return ("REFLECT_MAXHOPS", cur, c, self.node[cur].guid)

    def loop_probe(self, origin, target, via):
        """SHIPPED loop/path-validity test (o+c only): origin sends a probe for
        `target` via first-hop `via`. REFLECT_LOOP/MAXHOPS -> LOOP (reject path);
        REFLECT_OK -> DELIVERED (valid, however far); TIMEOUT -> unreachable."""
        tag = self._reflect_trace(origin, via, target)[0]
        if tag in ("REFLECT_LOOP", "REFLECT_MAXHOPS"):
            return "LOOP"
        if tag == "REFLECT_OK":
            return "DELIVERED"
        return tag  # TIMEOUT

    def dup_probe(self, origin):
        """PROPOSED duplicate-IP test (NOT in shipped REFLECT_PROBE_V1 - requires
        adding the /etc/fnid GUID to the probe). Origin probes its OWN 10. on
        every tunnel peer; at the reflect point the box compares GUIDs:
        guid != mine -> COLLISION (another holder); guid == mine / dead-end ->
        UNIQUE. Without the GUID the shipped o/c scheme returns 508 in BOTH
        cases and cannot tell them apart - which is the whole point of the
        extension."""
        target = self.node[origin].one
        og = self.node[origin].guid
        for peer in sorted(self.tunnels.get(origin, [])):
            tag, _at, _c, guid = self._reflect_trace(origin, peer, target)
            if tag in ("REFLECT_LOOP", "REFLECT_OK") and guid != og:
                return "COLLISION"
        return "UNIQUE"
if __name__ == "__main__":
    # Tunnel chain: A - C - D. D is two tunnels from A (cross-tunnel, the
    # SeattleThree->BABox case). Only transitive getHosts can find it.
    spec = TopologySpec(
        nodes=[NodeSpec("A", "10.10.10"),
               NodeSpec("C", "10.30.30"),
               NodeSpec("D", "10.40.40")],
        tunnels=[("A", "C"), ("C", "D")],
    )
    sysm = System(spec)
    cyc = sysm.converge()
    print(f"converged in {cyc} cycles")
    for name in sysm.node:
        print(f"  {name} knows: {sorted(sysm.known[name])}")
    probs = sysm.validate()
    print("\nVALIDATION:", "PASS" if not probs else "FAIL")
    for p in probs:
        print("   -", p)
