"""[GRAPH_V1] FD&F's statement of the network's structure: a graph built from the address table in the region's RAM
(PLAN-RAM-DISCOVERY §1-2, §5). Lives in /opt/fdf/lib/python; FrogNet imports it and applies what it says.

Input: the region's participants (what `participants` returns: name, alive, facts). Each live host's facts.addrs is
its rows: {"iface","ip","value"}. This module is pure: no kernel, no files, no clock. It answers, for one host:

    routes    [(dest/24 or /32, via or "", dev)]   explicit LAN routes; everything else rides the default route
    default   (via, dev) or None                   toward the chosen exit (a host with a live uplink), none if I am one
    hosts     [(ip, name)]                          every .1 row and every leaf /32, for /etc/hosts
    maps      [prefix]                              what I register with the hub: the /24s of hosts whose exit is me
    hub_routes[prefix]                              /24s with no LAN path from me, to be routed dev <wg iface> if I hold a hub link
    conflicts [(ip, [hosts])]                       the same .1 claimed by several live hosts: routed by nobody

Rules (one line each; the oracle in tests/graph_oracle.py holds them to the agreed topologies):
  link     = the same address under two hosts; the side holding the .1 of that /24 is the parent.
  uplink   = an "uplink <type> <rate>" row not marked dead.
  exit(h)  = h if h has a live uplink; else the nearest LAN-reachable host with one; same distance -> higher
             rate; same rate -> lower .1. None if none.
  routes   = for every other host t with a LAN path: t's /24s via the address on my side of the first link.
  maps(h)  = h's own /24s + the /24s of every host whose exit is h (h has a hub link, else nothing).
"""
import ipaddress
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

BUILD_TAG = "fdf-2026-10-16-r21"

OVERLAY_SECOND_OCTETS = ("253", "254")      # 10.253/16 region edges, 10.254 the frognet0 pond range: never a served /24


def _is_dot1(ip: str) -> bool:
    o = ip.split(".")
    return len(o) == 4 and o[0] == "10" and o[1] not in OVERLAY_SECOND_OCTETS and o[3] == "1"


def _slash24(ip: str) -> str:
    return ip.rsplit(".", 1)[0] + ".0/24"


def _parse_uplink(value: str):
    """'uplink eth 1000' / 'uplink wifi 72 dead' -> (type, rate, alive) or None."""
    p = value.split()
    if not p or p[0] != "uplink":
        return None
    typ = p[1] if len(p) > 1 else "?"
    try:
        rate = float(p[2]) if len(p) > 2 else 0.0
    except ValueError:
        rate = 0.0
    return typ, rate, "dead" not in p[3:]


class Graph:
    def __init__(self, participants: list):
        self.rows: List[Tuple[str, str, str, str, str, str]] = []  # (host, iface, ip, value, kind, mac)
        self.hosts: Set[str] = set()
        self.roles: Dict[str, str] = {}
        self.hubs: Set[str] = set()
        for p in participants:
            if not p.get("alive"):
                continue
            f = p.get("facts") or {}
            name = p.get("name") or ""
            if not name:
                continue
            self.roles[name] = f.get("role") or ""
            if f.get("role") == "hub":
                self.hubs.add(name)
                continue                                              # the hub is not a routed host
            self.hosts.add(name)
            rows = [r for r in (f.get("addrs") or []) if r.get("ip")]
            reacher = f.get("role") == "participant" and f.get("reach") is True   # [REACH_IS_A_LEAF_HERE_V1] its /32 here
            if not rows and (f.get("role") == "leaf" or reacher) and f.get("addr"):
                # [LEAF_ROW_FROM_ADDR_V1] a leaf with no rows (the phone, a laptop running only fdf-client) is its addr:
                # one leaf row, so every node routes its /32 (hub route, or via its exit) and names it. This was nested
                # under the subnet test below, which a leaf never passes (no subnet): no leaf ever got a row, no node
                # routed one -- the hub held the peer, the nodes sent 10.252.x.y out their default route.
                rows = [{"iface": "wg", "ip": f["addr"].split("/")[0], "value": name, "kind": "leaf", "mac": ""}]
            elif not rows and f.get("subnet"):
                # [SUBNET_FALLBACK_ROW_V1] a member without rows (no fdf-facts yet) still declares the /24 it serves:
                # its .1 counts as an own row, so it is named and routed; it has no links, leases or uplink in the graph.
                dot1 = f["subnet"].split("/")[0].rsplit(".", 1)[0] + ".1"
                rows = [{"iface": "", "ip": dot1, "value": name if name.startswith("FrogNetHost.") else "FrogNetHost." + name, "kind": "own", "mac": ""}]
            for r in rows:
                self.rows.append((name, r.get("iface", ""), r["ip"], r.get("value", ""), r.get("kind", ""), r.get("mac", "")))
        # who holds which .1 (served /24s), and which addresses each host holds
        self.dot1: Dict[str, List[str]] = {}                       # ip -> [hosts] (several = conflict)
        self.served: Dict[str, Set[str]] = {h: set() for h in self.hosts}   # host -> {/24}
        self.held: Dict[str, Dict[str, str]] = {h: {} for h in self.hosts}  # host -> {ip: iface}
        self.names: Dict[str, str] = {}                              # .1 or leaf ip -> name
        self.uplink: Dict[str, Tuple[str, float]] = {}               # host -> (type, rate) if live
        self.hub_link: Dict[str, Tuple[str, str]] = {}               # host -> (wg iface, hub name)
        self.leaf_ip: Dict[str, str] = {}                            # leaf host -> its /32
        self.given: Dict[Tuple[str, str], str] = {}                 # (ip, mac) -> giver host   [ADJACENCY_IS_A_LEASE_PAIR_V1]
        self._uplink_ip: Dict[str, str] = {}
        self.heldl: List[Tuple[str, str, str, str]] = []             # (host, ip, mac, iface)
        for host, iface, ip, value, kind, mac in self.rows:
            up = _parse_uplink(value) if kind in ("uplink", "") else None
            if up:
                if up[2] and (host not in self.uplink or up[1] > self.uplink[host][1]):
                    self.uplink[host] = (up[0], up[1])
                    self._uplink_ip[host] = ip
                continue
            if kind == "hub" or value in self.hubs:
                self.hub_link[host] = (iface, value)
                if self.roles.get(host) == "leaf":
                    self.leaf_ip[host] = ip
                    self.names[ip] = host
                continue
            if kind == "leaf":
                self.leaf_ip[host] = ip
                self.names[ip] = value or host
                continue
            if not ip.startswith("10."):
                continue
            if kind == "given":
                self.given[(ip, mac.lower())] = host
                continue                                               # a given lease is not an address the giver holds
            self.held[host][ip] = iface
            if kind == "held":
                self.heldl.append((host, ip, mac.lower(), iface))
            if kind == "own" and _is_dot1(ip):
                self.dot1.setdefault(ip, [])
                if host not in self.dot1[ip]:
                    self.dot1[ip].append(host)
                self.served[host].add(_slash24(ip))
                self.names.setdefault(ip, value or host)
        self.conflicts: List[Tuple[str, List[str]]] = sorted((ip, sorted(hs)) for ip, hs in self.dot1.items() if len(hs) > 1)
        bad = {ip for ip, _ in self.conflicts}
        for host in self.hosts:                                    # a conflicted /24 is served by nobody
            self.served[host] = {s for s in self.served[host] if s.split("/")[0].rsplit(".", 1)[0] + ".1" not in bad}
        # candidate links: a held lease whose giver wrote the same (ip, mac) and serves that /24
        self.links: List[Tuple[str, str, str, str]] = []          # (child, parent, child_addr, parent_dot1)
        cand: Dict[str, List[Tuple[str, str, str]]] = {h: [] for h in self.hosts}   # host -> [(peer, my_addr, peer_addr)]
        for child, ip, mac, iface in self.heldl:
            parent = self.given.get((ip, mac))
            net_dot1 = ip.rsplit(".", 1)[0] + ".1"
            if not parent or parent == child or _slash24(ip) not in self.served.get(parent, set()):
                continue
            self.links.append((child, parent, ip, net_dot1))
            cand[child].append((parent, ip, net_dot1))
            cand[parent].append((child, net_dot1, ip))
        # [UPLINK_IS_NOT_A_PARENT_V1] a default route out of a lease a region member gave me is not my uplink: that
        # member is my parent and its exit is mine. (Seattle7 behind Seattle5: an uplink only while Seattle5 is absent.)
        for child, parent, child_addr, _d1 in self.links:
            if self._uplink_ip.get(child) == child_addr:
                self.uplink.pop(child, None)
        # [FOREST_V1] one upward link per host: the one toward its ranked exit (else the lowest parent .1); the
        # routing graph has exactly those edges, so every path is unique and there are no cycles.
        self.parent: Dict[str, Tuple[str, str, str]] = {}
        self._cand = cand
        self.adj: Dict[str, List[Tuple[str, str, str]]] = {h: [] for h in self.hosts}
        self._dist_cache = {}; self._first_hop = {}
        for h in self.hosts:
            ups = [c for c in cand[h] if any(l[0] == h and l[1] == c[0] for l in self.links)]
            if not ups:
                continue
            ex = self._exit_over(cand, h)
            pick = None
            if ex and ex != h:
                first = self._first_over(cand, h, ex)
                pick = next((c for c in ups if c[0] == first[0]), None)
            if pick is None:
                pick = min(ups, key=lambda c: tuple(int(x) for x in c[2].split(".")))
            self.parent[h] = pick
        for child, (parent, my_addr, peer_addr) in self.parent.items():
            self.adj[child].append((parent, my_addr, peer_addr))
            self.adj[parent].append((child, peer_addr, my_addr))
        self._dist_cache: Dict[str, Dict[str, int]] = {}
        self._first_hop: Dict[str, Dict[str, Tuple[str, str, str]]] = {}

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _walk(adj, src):
        dist = {src: 0}
        first = {}
        q = deque([src])
        while q:
            h = q.popleft()
            for peer, my_addr, peer_addr in sorted(adj.get(h, [])):
                if peer in dist:
                    continue
                dist[peer] = dist[h] + 1
                first[peer] = first[h] if h != src else (peer, my_addr, peer_addr)
                q.append(peer)
        return dist, first

    def _exit_over(self, adj, h):
        if h in self.uplink:
            return h
        dist, _ = self._walk(adj, h)
        # [EXIT_RANK_V1] John's rule: by speed first (eth 1000 beats wifi 72), then the lower .1. Not by hops.
        cands = [(-self.uplink[t][1], self._dot1_of(t), t) for t in dist if t in self.uplink]
        return min(cands)[2] if cands else None

    def _first_over(self, adj, h, t):
        _, first = self._walk(adj, h)
        return first.get(t)

    def _bfs(self, src: str):
        if src in self._dist_cache:
            return
        self._dist_cache[src], self._first_hop[src] = self._walk(self.adj, src)

    def distance(self, a: str, b: str) -> Optional[int]:
        self._bfs(a)
        return self._dist_cache[a].get(b)

    def exit_of(self, h: str) -> Optional[str]:
        """The host whose uplink carries h's traffic (and whose hub link and mappings carry h). Over the forest."""
        return self._exit_over(self.adj, h)

    def _dot1_of(self, h: str) -> Tuple[int, ...]:
        subs = sorted(self.served.get(h, set()))
        if not subs:
            return (999, 999, 999, 999)
        return tuple(int(x) for x in subs[0].split("/")[0].split("."))

    # ------------------------------------------------------------------ one host's answers
    def plan(self, me: str) -> dict:
        self._bfs(me)
        reach = self._dist_cache[me]
        first = self._first_hop[me]
        my_nets = set(self.served.get(me, set())) | {_slash24(ip) for ip in self.held.get(me, {})}
        routes: List[Tuple[str, str, str]] = []
        for t, d in sorted(reach.items(), key=lambda kv: (kv[1], kv[0])):
            if t == me:
                continue
            nxt, my_addr, peer_addr = first[t]
            dev = self.held[me].get(my_addr, "")
            for net in sorted(self.served.get(t, set())):
                if net in my_nets:
                    continue                                          # on my wire already: the kernel's connected route
                routes.append((net, peer_addr, dev))              # via the next hop's address on my wire
        # default: toward my exit
        default = None
        ex = self.exit_of(me)
        lan_nets = set(my_nets)
        for t in reach:
            lan_nets |= self.served.get(t, set())
        # my hub link is the active one whenever I hold one (OWN_TUNNEL_CARRIES_OWN_V1); remote /24s are then hub
        # routes on it, and only a tunnel-less child reaches the world through its exit's LAN address.
        own_link_active = bool(me in self.hub_link)                 # [OWN_TUNNEL_CARRIES_OWN_V1]
        if ex and ex != me:
            nxt, my_addr, peer_addr = first[ex]
            default = (peer_addr, self.held[me].get(my_addr, ""))
            # [CHILD_REMOTE_ROUTES_V1] a child reaches every /24 outside its LAN through its exit: say so per /24, so
            # the kernel table (and HOSTS_GATE) carry it explicitly rather than leaning on whatever default exists.
            # Not when my own hub link is the active one: then those /24s are hub routes (below).
            dev = self.held[me].get(my_addr, "")
            if not own_link_active:
                for net in sorted({n for h in self.hosts for n in self.served.get(h, set()) if n not in lan_nets}
                                  | {ip + "/32" for ip in self.leaf_ip.values()}):
                    routes.append((net, peer_addr, dev))
        # hosts lines: every .1 (unconflicted) and every leaf
        hosts = sorted((ip, name) for ip, name in self.names.items()
                       if (ip in self.dot1 and len(self.dot1[ip]) == 1) or ip in self.leaf_ip.values())
        # what I register, if I hold a hub link: my own /24s, plus those of every TUNNEL-LESS host whose exit is me.
        # [OWN_TUNNEL_CARRIES_OWN_V1] a host with a hub link carries its own /24 on it, child or not. The kernel's
        # connected route for the 10.253/16 edge net already sends that host's replies out its own tunnel; routing its
        # /24 behind its exit at the hub while the host answers on its own link is an asymmetric path WireGuard drops
        # (source not in the peer's allowed-ips). One link, both directions, decided by one row: the host's hub row.
        # The exit carries only the children that have no tunnel (y behind z in the netns test; DHCP guests).
        maps: List[str] = []
        if me in self.hub_link:
            maps.extend(sorted(self.served.get(me, set())))
            for h in sorted(self.hosts):
                if h != me and self.exit_of(h) == me and h not in self.hub_link:
                    maps.extend(sorted(self.served.get(h, set())))
        # /24s with no LAN path: the hub's, if I hold a hub link
        # (HUB_LINK_ONLY_AT_THE_EXIT_V1 withdrawn 2026-10-09 15:20 for OWN_TUNNEL_CARRIES_OWN_V1, see above)
        hub_routes = sorted({n for h in self.hosts for n in self.served.get(h, set()) if n not in lan_nets}
                            | {ip + "/32" for ip in self.leaf_ip.values()}) if own_link_active else []
        return {"routes": routes, "default": default, "hosts": hosts, "maps": sorted(set(maps)),
                "own": sorted(self.served.get(me, set())),
                "hub_routes": hub_routes, "conflicts": self.conflicts, "exit": ex,
                "hub_iface": self.hub_link.get(me, ("", ""))[0], "hub_active": own_link_active}

    def summary(self) -> str:
        return "hosts=%d links=%d uplinks=%s hubs=%s conflicts=%s" % (
            len(self.hosts), len(self.links), sorted(self.uplink), sorted(self.hubs), self.conflicts)
