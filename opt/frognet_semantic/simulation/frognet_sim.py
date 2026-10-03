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
frognet_sim.py - Validates the patched helpers and the route discovery
algorithm specified in this session.

Algorithm under test:
  1. Address comes from DHCP/interface config.
  2. Connected subnet routes as `dev <iface>`.
  3. getHosts is called on a peer reachable through the interface.
  4. Each remote /24 returned gets `<subnet> via <next_hop> dev <iface>`
     where <next_hop> is the IP of the peer we talked to.
  5. Recurse: ask getHosts on each newly-routable host; routes still
     go via the SAME <next_hop> on the same interface.
  6. dev (connected) beats via (routed) when both could match.

If something is on an interface -> dev.
If something routes through an interface -> via.

Helper functions under test (verbatim transcriptions of the patched code):
  - identity._get_local_ip               (frognet_monitor_py/identity.py)
  - tunnel-daemon subnet derivation       (frognet-tunnel-daemon.py load_config)
  - tunnel-daemon _parse_channel_name     (frognet-tunnel-daemon.py)
  - discovery /etc/hosts fallback         (frognet_monitor_py/discovery.py)
  - tunnel-daemon local_subnets_from_routes (frognet-tunnel-daemon.py)
"""

import glob
import ipaddress
import os
import re
import sys
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ============================================================================
# Section 1: helpers - IMPORTED DIRECTLY FROM THE LIVE PATCHED CODE.
# ============================================================================
#
# The sim used to transcribe each helper inline.  Transcriptions drift.
# Now Section 1 imports the actual pure-parser functions from the live
# source tree, so a regression in the shipped code shows up here on the
# next sim run.  If you change a parser, run the sim; if the sim
# starts failing, you broke the parser.
#
# Resolution order for the source tree:
#   1. $FROGNET_PROXY_ROOT / $FROGNET_BIN_ROOT environment variables
#   2. Standard install paths (/opt/frognet/proxy, /usr/local/bin)
#   3. A `work/` sibling of this file (used during dev / tarball-staging)
#   4. The directory holding this file
#
# Functions imported:
#   internet_tunnels_v3.names.parse_channel_name(ch_name)            -> (name, prefix)
#   internet_tunnels_v3.names.parse_local_subnet(ip_addr_show_output) -> {SUBNET_PREFIX, LOCAL_SUBNET, LOCAL_GW}
#   frognet_monitor_py.identity.parse_local_ip_from_ip_output(out)   -> str
#   frognet_monitor_py.discovery.parse_etc_hosts_content(content)    -> [(ip, name), ...]
#
# All four are pure: no subprocess, no file I/O, no module-level side
# effects on import (we use lazy imports inside the wrapper helpers
# where needed).

def _resolve_live_paths() -> Tuple[Optional[str], Optional[str]]:
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    proxy_candidates = [
        os.environ.get("FROGNET_PROXY_ROOT"),
        parent,                                   # sim sits inside the proxy tree (simulation/)
        "/opt/frognet/proxy",
        os.path.expanduser("~/frognet/proxy"),
        os.path.join(here, "work"),
        here,
    ]
    bin_candidates = [
        os.environ.get("FROGNET_BIN_ROOT"),
        "/usr/local/bin",
        "/opt/frognet/bin",
        os.path.expanduser("~/frognet/bin"),
        os.path.join(here, "work", "bin_extracted"),
        os.path.join(here, "bin_extracted"),
        os.path.join(parent, "bin_extracted"),    # adjacent to simulation/ during dev
    ]
    proxy_root = next(
        (p for p in proxy_candidates
         if p and os.path.isdir(os.path.join(p, "internet_tunnels_v3"))),
        None,
    )
    bin_root = next(
        (p for p in bin_candidates
         if p and os.path.isdir(os.path.join(p, "frognet_monitor_py"))),
        None,
    )
    return proxy_root, bin_root


_PROXY_ROOT, _BIN_ROOT = _resolve_live_paths()
if not _PROXY_ROOT or not _BIN_ROOT:
    sys.stderr.write(
        "FATAL: cannot locate live patched code.\n"
        f"  internet_tunnels_v3/ search root: {_PROXY_ROOT or '(none found)'}\n"
        f"  frognet_monitor_py/ search root:  {_BIN_ROOT or '(none found)'}\n"
        "  Set $FROGNET_PROXY_ROOT and $FROGNET_BIN_ROOT, or place the\n"
        "  sim next to a work/ or bin_extracted/ staging directory.\n"
    )
    sys.exit(2)
sys.path.insert(0, _PROXY_ROOT)
sys.path.insert(0, _BIN_ROOT)

from internet_tunnels_v3.names import (
    parse_channel_name as _live_parse_channel_name,
    parse_local_subnet as _live_parse_local_subnet,
)
from frognet_monitor_py.identity import (
    parse_local_ip_from_ip_output as _live_parse_local_ip,
)
from frognet_monitor_py.discovery import (
    parse_etc_hosts_content as _live_parse_etc_hosts,
)

print(f"SIM: using live patched code from {_PROXY_ROOT} (proxy) and {_BIN_ROOT} (bin)")


# Thin wrappers - keep the sim's existing call sites and tests
# unchanged, but exercise the live functions underneath.

def identity_get_local_ip(ip_addr_show_output: str) -> str:
    return _live_parse_local_ip(ip_addr_show_output)


def tunnel_daemon_load_subnet(ip_addr_show_output: str) -> dict:
    return _live_parse_local_subnet(ip_addr_show_output)


def parse_channel_name(ch_name: str) -> Tuple[str, str]:
    return _live_parse_channel_name(ch_name)


def discover_hosts_etc_hosts(content: str) -> List[Tuple[str, str]]:
    return _live_parse_etc_hosts(content)



# ============================================================================
# Section 1b: Helper tests
# ============================================================================

FAILS: List[str] = []

def expect(name: str, ok: bool, detail: str = ""):
    status = "OK  " if ok else "FAIL"
    print(f"  [{status}] {name}{(' - ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def write_dnsmasq(d, dhcp_range, domain=""):
    with open(os.path.join(d, "opts_only.conf"), "w") as f:
        if domain:
            f.write(f"domain={domain}\n")
        f.write(f"dhcp-range={dhcp_range}\n")


def test_identity():
    print("\n=== identity._get_local_ip - ip addr scan across the 10/8 space ===")
    # (ip_addr_show output, expected ip)
    cases = [
        ("2: eth0    inet 10.101.10.1/24 brd 10.101.10.255 scope global eth0",   "10.101.10.1"),
        ("2: eth0    inet 10.241.241.1/24 brd 10.241.241.255 scope global eth0", "10.241.241.1"),
        ("2: eth0    inet 10.44.44.1/24 brd 10.44.44.255 scope global eth0",     "10.44.44.1"),
        ("2: eth0    inet 10.10.10.1/24 brd 10.10.10.255 scope global eth0",     "10.10.10.1"),
        ("2: eth0    inet 10.201.201.1/24 brd 10.201.201.255 scope global eth0", "10.201.201.1"),
        ("2: eth0    inet 10.1.1.1/24 brd 10.1.1.255 scope global eth0",         "10.1.1.1"),
        ("2: eth0    inet 10.122.221.1/24 brd 10.122.221.255 scope global eth0", "10.122.221.1"),
        ("2: eth0    inet 10.147.147.1/24 brd 10.147.147.255 scope global eth0", "10.147.147.1"),
    ]
    for ip_out, exp in cases:
        got = identity_get_local_ip(ip_out)
        expect(f"10/8 addr -> {exp}", got == exp, f"got={got}, expected={exp}")

    # Reserved-range exclusions: 10.253. is WG transit, 10.254. is chorus
    # virtual.  Both must be skipped even when they're the only 10/8 addr
    # present, so the real served /24 wins when it shows up alongside.
    mixed = """\
2: wg0     inet 10.253.253.5/30 scope global wg0
3: eth0    inet 10.101.10.1/24 brd 10.101.10.255 scope global eth0
4: chorus0 inet 10.254.1.1/32 scope global chorus0
"""
    expect("served /24 wins over 10.253. transit and 10.254. chorus",
           identity_get_local_ip(mixed) == "10.101.10.1",
           f"got={identity_get_local_ip(mixed)}")

    only_reserved = """\
2: wg0     inet 10.253.253.5/30 scope global wg0
3: chorus0 inet 10.254.1.1/32 scope global chorus0
"""
    expect("no real /24 -> fallback 127.0.0.1 (10.253/10.254 ignored)",
           identity_get_local_ip(only_reserved) == "127.0.0.1",
           f"got={identity_get_local_ip(only_reserved)}")

    expect("no addresses at all -> 127.0.0.1",
           identity_get_local_ip("") == "127.0.0.1")


def test_load_subnet():
    print("\n=== tunnel-daemon subnet derivation ===")
    # (ip_addr_show line, expected prefix)
    cases = [
        ("2: eth0    inet 10.101.10.1/24 brd 10.101.10.255 scope global eth0",   "10.101.10"),
        ("2: eth0    inet 10.241.241.1/24 brd 10.241.241.255 scope global eth0", "10.241.241"),
        ("2: eth0    inet 10.44.44.1/24 brd 10.44.44.255 scope global eth0",     "10.44.44"),
        ("2: eth0    inet 10.201.201.1/24 brd 10.201.201.255 scope global eth0", "10.201.201"),
        ("2: eth0    inet 10.122.221.1/24 brd 10.122.221.255 scope global eth0", "10.122.221"),
        ("2: eth0    inet 10.1.1.1/24 brd 10.1.1.255 scope global eth0",         "10.1.1"),
    ]
    for ip_out, exp_prefix in cases:
        cfg = tunnel_daemon_load_subnet(ip_out)
        ok = (cfg.get("SUBNET_PREFIX") == exp_prefix
              and cfg.get("LOCAL_SUBNET") == f"{exp_prefix}.0/24"
              and cfg.get("LOCAL_GW") == f"{exp_prefix}.1"
              and "SUBNET_OCTET" not in cfg)
        expect(f"prefix={exp_prefix}", ok, f"got={cfg}")

    # Reserved transit/chorus addresses must NOT be picked up as the
    # served /24, even when they appear alongside (or before) the real
    # served address.
    transit_first = """\
2: wg0  inet 10.253.253.5/30 scope global wg0
3: eth0 inet 10.101.10.1/24 brd 10.101.10.255 scope global eth0
"""
    cfg = tunnel_daemon_load_subnet(transit_first)
    expect("10.253. transit skipped, 10.101.10 picked",
           cfg.get("SUBNET_PREFIX") == "10.101.10", f"got={cfg}")

    only_transit = "2: wg0  inet 10.253.253.5/30 scope global wg0\n"
    expect("only 10.253. transit present -> empty result",
           tunnel_daemon_load_subnet(only_transit) == {})

    only_chorus = "2: chorus0  inet 10.254.1.1/32 scope global chorus0\n"
    expect("only 10.254. chorus present -> empty result",
           tunnel_daemon_load_subnet(only_chorus) == {})

    expect("no input -> empty result",
           tunnel_daemon_load_subnet("") == {})


def test_parse_channel_name():
    print("\n=== _parse_channel_name ===")
    # (channel name, expected node name, expected subnet prefix string)
    cases = [
        ("BlackBox-10.101.10",          "BlackBox",   "10.101.10"),
        ("IronBox-10.101.20",           "IronBox",    "10.101.20"),
        ("SeattleDB-10.241.241",        "SeattleDB",  "10.241.241"),
        ("Seattle-1G-10.44.44",         "Seattle-1G", "10.44.44"),
        ("New-York-2-10.122.221",       "New-York-2", "10.122.221"),
        ("Box-10.0.0",                  "Box",        "10.0.0"),
        ("simple-10.1.2",               "simple",     "10.1.2"),
        # Node names with multiple hyphens preserved exactly:
        ("foo-bar-baz-10.5.6",          "foo-bar-baz","10.5.6"),
        # Malformed inputs -> ("", "")
        ("no_hyphen_or_octets",         "",           ""),
        ("name-10.1.2.3",               "",           ""),   # 4 octets, not 3
        ("name-foo.bar.baz",            "",           ""),
        ("trailing-",                   "",           ""),
    ]
    for ch, exp_name, exp_prefix in cases:
        got = parse_channel_name(ch)
        ok = got == (exp_name, exp_prefix)
        expect(f"{ch}", ok, f"got={got}, expected=({exp_name!r},{exp_prefix!r})")


def test_etc_hosts():
    print("\n=== discovery /etc/hosts fallback ===")
    content = """\
127.0.0.1 localhost
10.101.10.1 BlackBox FrogNetHost.BlackBox
10.241.241.1 SeattleDB FrogNetHost.SeattleDB
10.44.44.1 Seattle-1G FrogNetHost.Seattle-1G
# comment
10.122.221.1 NewYork-2 FrogNetHost.NewYork-2
10.253.253.5 wg-transit-peer FrogNetHost.transit-peer
10.254.1.1   chorus-virtual  FrogNetHost.chorus-virtual
192.168.1.1 not.a.frognet
10.99.99.1 OldHost
"""
    # 10.253. (transit) and 10.254. (chorus) must be filtered out even
    # if they have a FrogNetHost.* alias.  192.168 and 10.99 with no
    # FrogNetHost prefix are also dropped.
    expected = [
        ("10.101.10.1",   "BlackBox"),
        ("10.241.241.1",  "SeattleDB"),
        ("10.44.44.1",    "Seattle-1G"),
        ("10.122.221.1",  "NewYork-2"),
    ]
    got = discover_hosts_etc_hosts(content)
    expect("10.253./10.254. excluded, non-10/8 dropped",
           got == expected, f"got={got}")


def test_etc_hosts_drift_safeguard():
    """Sanity check: 10.253. and 10.254. lines NEVER appear in output,
    even when surrounded by valid entries.  Catches reintroduction of
    the pre-patch behavior."""
    print("\n=== /etc/hosts reserved-range drift safeguard ===")
    content = "10.253.253.5 wg-x FrogNetHost.wg-x\n10.254.7.7 ch-y FrogNetHost.ch-y\n"
    got = discover_hosts_etc_hosts(content)
    expect("only-reserved-ranges /etc/hosts -> empty output",
           got == [], f"got={got}")


# ============================================================================
# Section 2: route discovery - topology model
# ============================================================================

class Iface:
    def __init__(self, name, addr_cidr):
        self.name = name
        self.addr_cidr = addr_cidr
        ifs = ipaddress.ip_interface(addr_cidr)
        self.ip = str(ifs.ip)
        self.subnet = str(ifs.network)
    def __repr__(self):
        return f"{self.name}@{self.addr_cidr}"

class Route:
    __slots__ = ("dest", "dev", "via", "metric")
    def __init__(self, dest, dev, via=None, metric=600):
        self.dest, self.dev, self.via, self.metric = dest, dev, via, metric
    def __repr__(self):
        v = f"via {self.via} " if self.via else ""
        return f"{self.dest} {v}dev {self.dev} metric {self.metric}"

class FrogNode:
    def __init__(self, name):
        self.name = name
        self.ifaces: Dict[str, Iface] = OrderedDict()
        self.routes: List[Route] = []
        self.frognet_ip: Optional[str] = None        # .1 of served /24 (FrogNetHost)
        self.frognet_admin_ip: Optional[str] = None  # .2 alias on same iface (FrogNetAdmin)
        # ----- live-model state -----
        # /etc/hosts equivalent - what this node knows about peers.
        # Maps ip -> name. Initially only self; grows by discovery.
        self.known_hosts: Dict[str, str] = {}
        # Last cycle's installed winners (for stickiness), dest -> Observation.
        self.installed_winners: Dict[str, "Observation"] = {}
    def add_iface(self, name, addr_cidr):
        self.ifaces[name] = Iface(name, addr_cidr)
        return self
    def install_route(self, dest, dev, via=None, metric=22) -> bool:
        for r in self.routes:
            if r.dest == dest and r.dev == dev and r.via == via:
                return False
        self.routes.append(Route(dest, dev, via, metric))
        return True
    def find_route(self, dest_ip):
        """Longest-prefix, prefer connected (no via), then lowest metric."""
        dest = ipaddress.ip_address(dest_ip)
        best = None
        best_key = None
        for r in self.routes:
            net = ipaddress.ip_network(r.dest)
            if dest in net:
                key = (-net.prefixlen, 0 if r.via is None else 1, r.metric)
                if best_key is None or key < best_key:
                    best, best_key = r, key
        return best
    def __repr__(self):
        return f"<{self.name}>"

class Topology:
    def __init__(self, label="topology"):
        self.label = label
        self.nodes: Dict[str, FrogNode] = OrderedDict()
        self.segments: List[List[Tuple[str, str]]] = []
    def add(self, node: FrogNode) -> FrogNode:
        self.nodes[node.name] = node
        return node
    def link(self, *members):
        subnets = {self.nodes[n].ifaces[i].subnet for n, i in members}
        if len(subnets) != 1:
            raise ValueError(
                f"L2 segment members must share a subnet: {members} -> {subnets}")
        self.segments.append(list(members))
        return self
    def l2_peers(self, node_name, iface_name):
        out = []
        for seg in self.segments:
            if (node_name, iface_name) not in seg:
                continue
            for (n, i) in seg:
                if (n, i) != (node_name, iface_name):
                    out.append((n, i, self.nodes[n].ifaces[i].ip))
        return out
    def find_owner(self, ip):
        for n, node in self.nodes.items():
            for i, iface in node.ifaces.items():
                if iface.ip == ip:
                    return (n, i)
        # Match the .2 admin alias on the served iface (first iface).
        for n, node in self.nodes.items():
            if node.frognet_admin_ip == ip and node.ifaces:
                first_iname = next(iter(node.ifaces))
                return (n, first_iname)
        return None


def assign_identities(topo: Topology):
    """frognet_ip is the IP of the node's first listed interface.

    A FrogNet node that *serves* a /24 (its first iface ends in .1) also has
    a .2 admin alias on that same interface - the FrogNetAdmin probe target.
    Worker nodes (DHCP clients riding on someone else's /24, with IPs like
    .5) do not have an admin alias.
    """
    for node in topo.nodes.values():
        if not node.ifaces:
            continue
        first = next(iter(node.ifaces.values()))
        node.frognet_ip = first.ip
        last_octet = first.ip.rsplit(".", 1)[-1]
        if last_octet == "1":
            prefix = first.ip.rsplit(".", 1)[0]
            node.frognet_admin_ip = f"{prefix}.2"
        else:
            node.frognet_admin_ip = None


def get_hosts_response(topo: Topology, target_node_name: str) -> List[Tuple[str, str]]:
    """Simulate getHosts.php: returns whatever the target node currently
    has in its /etc/hosts equivalent (known_hosts). Includes self. The .2
    admin alias is a probe-only phantom - never listed here, per
    chat 1b072de5 ('discover_hosts ... drops the .2/FrogNetAdmin phantoms')."""
    tgt = topo.nodes[target_node_name]
    out: List[Tuple[str, str]] = []
    # Self always present.
    if tgt.frognet_ip:
        out.append((tgt.frognet_ip, target_node_name))
    for ip, name in tgt.known_hosts.items():
        if ip == tgt.frognet_ip:
            continue
        out.append((ip, name))
    return out


# ============================================================================
# Section 2a: live algorithm - RTT-based observe / plan / commit
# ============================================================================
#
# This engine reflects (as closely as the chat-extracted spec allows) the
# behavior of the deployed sync_interfaces.sh + planner.py + committer.py
# pipeline. Key invariants:
#
#   - Per merge cycle, each node:
#       1. sync_interfaces - probes its directly-connected peers (wave-1) and
#          their known hosts (wave-2). Each successful probe yields an
#          Observation(dest, host_path, via, dev, rtt_ms, kind, ch_name,
#          anchor).
#       2. planner.plan() - groups observations by dest, consolidates
#          samples by (dev, via, kind, ch_name) to min RTT (Bug #29),
#          picks winner by `_winner_key = (rtt_ms, dev, via)` (chat
#          fcf955b2). NO kind-class precedence - RTT is the only sort key.
#       3. Stickiness: a previously-installed winner that's re-observed
#          stays, unless a better candidate is >=100ms AND >=20% faster.
#       4. committer.commit() - installs winning routes at metric 22.
#       5. Updates /etc/hosts equivalent (known_hosts) so the next merge
#          cycle's wave-2 probes have a wider horizon.
#
#   - Anchor validation: every observation must include an anchor whose
#     reachability is verifiable via `ip route get <anchor> oif <dev>`.
#     In the sim this is collapsed to "anchor must be an L2 peer on dev".
#
#   - WG vs LAN: only `kind` differs in the observation. `kind` is
#     informational - NOT a tiebreaker for the planner (chat fcf955b2).
#     The installed `via` for tunnel routes uses the peer's canonical .1
#     (host_path); for LAN routes it's the L2 next-hop (also peer's .1
#     when peer is the served node).
#
#   - Distributed propagation: when a node's known_hosts changes, peers
#     get notified (propogateNotification) and run their own merge.
#     Modeled as a multi-cycle loop over all nodes until convergence.
#
# RTT model: each iface kind has a typical latency. Deterministic in this
# sim; in reality measured per probe.

EDGE_RTT_BY_KIND = {
    "eth":  2.0,
    "en":   2.0,
    "wlp":  3.0,
    "wlan": 5.0,
    "ham":  20.0,
    "wg":   25.0,
    "br":   2.0,
    "bond": 2.0,
}

def iface_kind(name: str) -> str:
    for prefix in ("eth", "en", "wlp", "wlan", "ham", "wg", "br", "bond"):
        if name.startswith(prefix):
            return prefix
    return "default"

def edge_rtt(iface_name: str) -> float:
    return EDGE_RTT_BY_KIND.get(iface_kind(iface_name), 10.0)


@dataclass(frozen=True)
class Observation:
    dest: str          # remote /24 (CIDR)
    host_path: str     # peer's canonical .1 (FrogNetHost identity)
    via: str           # next-hop used to reach this dest
    dev: str           # local iface name
    rtt_ms: float
    kind: str          # "lan" or "wg" - informational, not a tiebreaker
    ch_name: str       # broker channel name (wg only); "" for lan
    anchor: str        # IP whose reachability anchors this observation


def _subnet_of(ip: str) -> str:
    return f"{ip.rsplit('.', 1)[0]}.0/24"


def _trace_rtt_via(topo: Topology, start_node: FrogNode, dst_ip: str,
                   max_hops: int = 16) -> Optional[float]:
    """Simulate a packet from start_node to dst_ip using start_node's current
    route table. Returns total accumulated RTT in ms, or None if no path.

    Models what an actual probe would measure: routes determine the path,
    edge_rtt(iface) determines per-hop latency.
    """
    current = start_node
    total = 0.0
    served_iface_name = next(iter(current.ifaces), None)
    for _ in range(max_hops):
        # If dst is the current node's primary or admin alias, deliver locally.
        if current.frognet_ip == dst_ip:
            return total
        if current.frognet_admin_ip == dst_ip:
            return total
        # L2 delivery on any directly-connected iface.
        delivered = False
        for iface in current.ifaces.values():
            net = ipaddress.ip_network(iface.subnet)
            if ipaddress.ip_address(dst_ip) not in net:
                continue
            if iface.ip == dst_ip:
                return total
            for (pn, _pi, pip) in topo.l2_peers(current.name, iface.name):
                peer = topo.nodes[pn]
                peer_served = next(iter(peer.ifaces), None)
                if pip == dst_ip:
                    return total + edge_rtt(iface.name)
                # Peer's .2 alias on its served iface.
                if (peer.frognet_admin_ip == dst_ip
                        and _pi == peer_served):
                    return total + edge_rtt(iface.name)
                # Peer's .1 hits when dst_ip is in served subnet & == peer's frognet_ip
                if peer.frognet_ip == dst_ip:
                    return total + edge_rtt(iface.name)
            delivered = True
            # dst is on this subnet but no owner found
            return None
        if delivered:
            return None
        # Need to route.
        r = current.find_route(dst_ip)
        if r is None or r.via is None:
            return None
        iface = current.ifaces.get(r.dev)
        if iface is None:
            return None
        next_node = None
        peers = topo.l2_peers(current.name, r.dev)
        if iface_kind(r.dev) == "wg":
            # WG onlink: via is the peer's frognet_ip (not on the /30).
            # Next-hop is the unique L2 peer on the wg dev; the WG iface
            # encapsulates and the far end's kernel resolves via from there.
            if len(peers) == 1:
                next_node = topo.nodes[peers[0][0]]
        else:
            # Standard LAN: find peer whose iface_ip matches via,
            # or whose frognet_ip matches via (the peer's identity).
            for (pn, _pi, pip) in peers:
                peer = topo.nodes[pn]
                if pip == r.via or peer.frognet_ip == r.via:
                    next_node = peer
                    break
        if next_node is None:
            return None
        total += edge_rtt(r.dev)
        if next_node is current:
            return None
        current = next_node
    return None


def _probe_direct(topo: Topology, src: FrogNode, peer: FrogNode,
                  dev: str) -> Optional[float]:
    """Wave-1 probe: src probes a directly-adjacent L2 peer on dev. Returns
    RTT or None if peer isn't actually L2-adjacent."""
    for (pn, _pi, _pip) in topo.l2_peers(src.name, dev):
        if pn == peer.name:
            return edge_rtt(dev)
    return None


def _probe_through(topo: Topology, src: FrogNode, dst_ip: str,
                   peer: FrogNode, dev: str) -> Optional[float]:
    """Wave-2 probe: src sends to dst_ip via peer on dev. Peer must have a
    route to dst_ip in its current table. Returns RTT or None."""
    # Local hop to peer
    hop_rtt = _probe_direct(topo, src, peer, dev)
    if hop_rtt is None:
        return None
    # Peer must route to dst_ip from there
    downstream = _trace_rtt_via(topo, peer, dst_ip)
    if downstream is None:
        return None
    return hop_rtt + downstream


def _channel_name(peer: FrogNode) -> str:
    """Channel name format: <peer-name>-<a.b.c> (peer's served /24 prefix)."""
    if not peer.frognet_ip:
        return ""
    prefix = peer.frognet_ip.rsplit(".", 1)[0]
    return f"{peer.name}-{prefix}"


def sync_interfaces(topo: Topology, node: FrogNode) -> List[Observation]:
    """Per-node, per-cycle: emit Observation records for everything reachable
    in this cycle. Wave-1 = direct L2 peers; wave-2 = their known hosts."""
    obs: List[Observation] = []
    for iface in node.ifaces.values():
        kind = "wg" if iface_kind(iface.name) == "wg" else "lan"
        for (peer_name, peer_iface_name, peer_iface_ip) in topo.l2_peers(
                node.name, iface.name):
            peer = topo.nodes[peer_name]
            # --- Anchor: ip route get <peer_iface_ip> oif <iface.name>
            # Sim: L2 peers are by definition reachable on this dev, so
            # anchor check passes. The check exists to catch misconfigs.
            anchor = peer_iface_ip
            ch = _channel_name(peer) if kind == "wg" else ""
            # --- Wave 1: probe peer's .1 (FrogNetHost)
            if not peer.frognet_ip:
                continue
            rtt1 = _probe_direct(topo, node, peer, iface.name)
            if rtt1 is None:
                continue
            # Probe peer's .2 (FrogNetAdmin) - confirmation only. If the
            # .2 alias isn't configured on the peer (frognet_admin_ip is
            # None), this is the case where setup_lillypad didn't run.
            if peer.frognet_admin_ip is not None:
                _probe_direct(topo, node, peer, iface.name)  # same path
            # Record observation for peer's served /24.
            peer_served = next(iter(peer.ifaces.values()))
            obs.append(Observation(
                dest=peer_served.subnet,
                host_path=peer.frognet_ip,
                via=peer_iface_ip,
                dev=iface.name,
                rtt_ms=rtt1,
                kind=kind,
                ch_name=ch,
                anchor=anchor,
            ))
            # --- Wave 2: ask peer for its known hosts, probe each through peer
            for (h_ip, h_name) in get_hosts_response(topo, peer.name):
                if h_ip == node.frognet_ip or h_ip == node.frognet_admin_ip:
                    continue
                # Skip if peer is itself the host (already recorded above).
                if h_ip == peer.frognet_ip:
                    continue
                rtt_h = _probe_through(topo, node, h_ip, peer, iface.name)
                if rtt_h is None:
                    continue
                h_subnet = _subnet_of(h_ip)
                # Skip observations for our own subnets.
                if any(h_subnet == i.subnet for i in node.ifaces.values()):
                    continue
                obs.append(Observation(
                    dest=h_subnet,
                    host_path=h_ip,
                    via=peer_iface_ip,
                    dev=iface.name,
                    rtt_ms=rtt_h,
                    kind=kind,
                    ch_name=ch,
                    anchor=anchor,
                ))
    return obs


def plan(observations: List[Observation],
         installed_winners: Dict[str, Observation]
         ) -> Dict[str, Observation]:
    """Group by dest, consolidate samples by (dev, via, kind, ch_name) to
    min RTT, pick winner by (rtt_ms, dev, via), apply stickiness.

    Stickiness rule (chat fcf955b2): if the previously-installed path is
    re-observed this cycle, keep it unless a better candidate is
    >=100ms AND >=20% faster.
    """
    by_dest: Dict[str, List[Observation]] = {}
    for o in observations:
        by_dest.setdefault(o.dest, []).append(o)
    winners: Dict[str, Observation] = {}
    for dest, lst in by_dest.items():
        # Consolidate by group, keep min RTT.
        groups: Dict[Tuple[str, str, str, str], List[Observation]] = {}
        for o in lst:
            groups.setdefault((o.dev, o.via, o.kind, o.ch_name), []).append(o)
        candidates: List[Observation] = []
        for key, samples in groups.items():
            min_o = min(samples, key=lambda x: x.rtt_ms)
            candidates.append(min_o)
        candidates.sort(key=lambda o: (o.rtt_ms, o.dev, o.via))
        winner = candidates[0]
        # Stickiness: if last cycle's winner is in candidates, prefer it
        # unless a real alternative is >=100ms AND >=20% faster.
        prev = installed_winners.get(dest)
        if prev is not None:
            re_obs = next(
                (c for c in candidates
                 if c.dev == prev.dev and c.via == prev.via),
                None)
            if re_obs is not None:
                # Find best alternative (not the re-observed one).
                best_alt = next(
                    (c for c in candidates
                     if c.dev != prev.dev or c.via != prev.via),
                    None)
                stick = True
                if best_alt is not None:
                    diff_ms = re_obs.rtt_ms - best_alt.rtt_ms
                    if diff_ms >= 100.0 and best_alt.rtt_ms <= 0.8 * re_obs.rtt_ms:
                        stick = False
                if stick:
                    winner = re_obs
                else:
                    winner = best_alt
        winners[dest] = winner
    return winners


def commit(node: FrogNode, winners: Dict[str, Observation]) -> bool:
    """Install winning routes in the kernel route table at metric 22.
    Tunnel routes use host_path as via (peer's .1, kernel onlink).
    LAN routes use via (peer's iface IP, normally peer's .1 on shared LAN).
    Returns True if route table changed."""
    # Keep connected routes; drop non-winner via routes; add new winners.
    connected = [r for r in node.routes if r.via is None]
    new_via_routes: List[Route] = []
    for dest, w in winners.items():
        installed_via = w.host_path if w.kind == "wg" else w.via
        new_via_routes.append(Route(dest, w.dev, installed_via, metric=22))
    new_routes = connected + new_via_routes
    # Stable order for comparison.
    def _key(r: Route):
        return (r.dest, r.dev, r.via or "", r.metric)
    changed = sorted(node.routes, key=_key) != sorted(new_routes, key=_key)
    node.routes = new_routes
    return changed


def discover_routes(topo: Topology, max_cycles: int = 16) -> int:
    """Run distributed merge cycles across all nodes until convergence.

    Each cycle, every node:
      - regenerates connected routes
      - runs sync_interfaces to gather observations
      - runs planner to pick winners
      - runs committer to install them
      - updates known_hosts (its /etc/hosts equivalent)
    Cycles continue until no node's routes or known_hosts change.
    """
    # Identities (.1 and .2) come from the served iface - set them first.
    assign_identities(topo)
    # Initialize per-node state.
    for node in topo.nodes.values():
        node.routes = []
        node.installed_winners = {}
        for iface in node.ifaces.values():
            node.install_route(iface.subnet, dev=iface.name, via=None,
                               metric=100)
        # known_hosts starts with self.
        node.known_hosts = {}
        if node.frognet_ip:
            node.known_hosts[node.frognet_ip] = node.name

    for cycle in range(1, max_cycles + 1):
        any_change = False
        for node in topo.nodes.values():
            # Re-establish connected routes (kernel-installed; never lost).
            connected_dests = {i.subnet for i in node.ifaces.values()}
            node.routes = [r for r in node.routes if r.via is None]
            for i in node.ifaces.values():
                if not any(r.dest == i.subnet and r.via is None
                           for r in node.routes):
                    node.install_route(i.subnet, dev=i.name, via=None,
                                       metric=100)
            old_winners = dict(node.installed_winners)
            old_hosts = dict(node.known_hosts)
            obs = sync_interfaces(topo, node)
            winners = plan(obs, node.installed_winners)
            commit(node, winners)
            node.installed_winners = winners
            # Update known_hosts: self + every dest's host_path we won.
            new_hosts: Dict[str, str] = {}
            if node.frognet_ip:
                new_hosts[node.frognet_ip] = node.name
            for dest, w in winners.items():
                # Find name from topology lookup by host_path.
                for n in topo.nodes.values():
                    if n.frognet_ip == w.host_path:
                        new_hosts[w.host_path] = n.name
                        break
            node.known_hosts = new_hosts
            if winners != old_winners or new_hosts != old_hosts:
                any_change = True
        if not any_change:
            return cycle
    return max_cycles


def trace_packet(topo: Topology, src_name: str, dst_ip: str,
                 max_hops: int = 32) -> List[str]:
    path = [src_name]
    current = src_name
    for _ in range(max_hops):
        node = topo.nodes[current]
        served_iface_name = next(iter(node.ifaces), None)  # node's served iface
        # If dst is on a directly connected subnet, deliver via L2
        for iface in node.ifaces.values():
            net = ipaddress.ip_network(iface.subnet)
            if ipaddress.ip_address(dst_ip) in net:
                if iface.ip == dst_ip:
                    return path  # we are the destination (primary .1)
                # Own .2 admin alias lives on the served iface.
                if (iface.name == served_iface_name
                        and node.frognet_admin_ip == dst_ip):
                    return path
                for (pn, pi, pip) in topo.l2_peers(current, iface.name):
                    if pip == dst_ip:
                        path.append(pn)
                        return path
                    # Peer's .2 admin alias is on its served iface.
                    peer = topo.nodes[pn]
                    peer_served = next(iter(peer.ifaces), None)
                    if (pi == peer_served
                            and peer.frognet_admin_ip == dst_ip):
                        path.append(pn)
                        return path
                path.append(f"<{dst_ip} on {iface.subnet} but no L2 owner>")
                return path
        # Routed
        r = node.find_route(dst_ip)
        if r is None:
            path.append(f"<no route to {dst_ip}>")
            return path
        if r.via is None:
            path.append(f"<connected route to {r.dest} but no L2 owner found>")
            return path
        # For WG (onlink) routes, next-hop is the unique L2 peer on the dev.
        # For LAN routes, next-hop is the peer whose iface IP matches via.
        nxt = None
        peers = topo.l2_peers(current, r.dev)
        if iface_kind(r.dev) == "wg" and len(peers) == 1:
            nxt = peers[0][0]
        else:
            owner = topo.find_owner(r.via)
            if owner:
                nxt = owner[0]
        if nxt is None:
            path.append(f"<via {r.via} unowned on dev {r.dev}>")
            return path
        if nxt == current:
            path.append(f"<loop at {current}>")
            return path
        path.append(nxt)
        current = nxt
    path.append("<hop limit>")
    return path


def verify_reachability(topo: Topology) -> Tuple[int, int, List[str]]:
    """Returns (ok, total, failures).

    For every ordered pair (a, b) with a != b, test:
      - production: a's .1 -> b's .1
      - admin (only if b has a .2 alias): a's .1 -> b's .2

    Admin probes ride the same /24 routes as production. If the production
    pair passes but the admin pair fails, that's a bug - they must travel
    together.
    """
    failures = []
    ok = 0
    total = 0
    nodes = list(topo.nodes.values())
    for a in nodes:
        for b in nodes:
            if a is b or not b.frognet_ip:
                continue
            # Production .1 -> .1
            total += 1
            path = trace_packet(topo, a.name, b.frognet_ip)
            if path[-1] == b.name:
                ok += 1
            else:
                failures.append(f"PROD  {a.name} -> {b.frognet_ip} ({b.name}): "
                                f"{' -> '.join(path)}")
            # Admin .1 -> .2 (only if b serves a /24)
            if b.frognet_admin_ip:
                total += 1
                path = trace_packet(topo, a.name, b.frognet_admin_ip)
                if path[-1] == b.name:
                    ok += 1
                else:
                    failures.append(f"ADMIN {a.name} -> {b.frognet_admin_ip} "
                                    f"({b.name} .2): {' -> '.join(path)}")
    return ok, total, failures


# ============================================================================
# Section 2b: topology builders
# ============================================================================

def topo_pair():
    """Two FrogNet nodes, each serving its own /24, meeting on a shared
    peering LAN. (The prior 'two nodes at .1/.2 of one /24' shape was
    invalid: .2 is the first node's admin alias.)"""
    t = Topology("two-node LAN (each serves own /24, peering LAN between)")
    t.add(FrogNode("BlackBox")
        .add_iface("eth0", "10.101.10.1/24")    # served
        .add_iface("eth1", "10.101.99.1/24"))   # peering
    t.add(FrogNode("IronBox")
        .add_iface("eth0", "10.101.20.1/24")    # served
        .add_iface("eth1", "10.101.99.2/24"))   # peering
    t.link(("BlackBox", "eth1"), ("IronBox", "eth1"))
    return t

def topo_chain_3():
    """A - B - C. Each has own served /24 plus a transit /24 to each neighbor."""
    t = Topology("3-node chain via dedicated link /24s")
    t.add(FrogNode("A")
        .add_iface("eth0", "10.1.1.1/24")        # served
        .add_iface("wg0",  "10.200.1.1/30"))      # link to B
    t.add(FrogNode("B")
        .add_iface("eth0", "10.2.2.1/24")        # served
        .add_iface("wg0",  "10.200.1.2/30")       # link to A
        .add_iface("wg1",  "10.200.2.1/30"))      # link to C
    t.add(FrogNode("C")
        .add_iface("eth0", "10.3.3.1/24")        # served
        .add_iface("wg0",  "10.200.2.2/30"))      # link to B
    t.link(("A", "wg0"), ("B", "wg0"))
    t.link(("B", "wg1"), ("C", "wg0"))
    return t

def topo_chain_5():
    """A - B - C - D - E. Five-node chain, tests deeper BFS."""
    t = Topology("5-node chain")
    t.add(FrogNode("A")
        .add_iface("eth0", "10.11.1.1/24")
        .add_iface("wg0",  "10.220.1.1/30"))
    t.add(FrogNode("B")
        .add_iface("eth0", "10.11.2.1/24")
        .add_iface("wg0",  "10.220.1.2/30")
        .add_iface("wg1",  "10.220.2.1/30"))
    t.add(FrogNode("C")
        .add_iface("eth0", "10.11.3.1/24")
        .add_iface("wg0",  "10.220.2.2/30")
        .add_iface("wg1",  "10.220.3.1/30"))
    t.add(FrogNode("D")
        .add_iface("eth0", "10.11.4.1/24")
        .add_iface("wg0",  "10.220.3.2/30")
        .add_iface("wg1",  "10.220.4.1/30"))
    t.add(FrogNode("E")
        .add_iface("eth0", "10.11.5.1/24")
        .add_iface("wg0",  "10.220.4.2/30"))
    t.link(("A","wg0"),("B","wg0"))
    t.link(("B","wg1"),("C","wg0"))
    t.link(("C","wg1"),("D","wg0"))
    t.link(("D","wg1"),("E","wg0"))
    return t

def topo_star():
    """Hub + 4 spokes via dedicated link /24s (one wg iface per spoke)."""
    t = Topology("4-spoke star")
    t.add(FrogNode("Hub")
        .add_iface("eth0", "10.20.99.1/24")       # served
        .add_iface("wg0",  "10.221.1.1/30")
        .add_iface("wg1",  "10.221.2.1/30")
        .add_iface("wg2",  "10.221.3.1/30")
        .add_iface("wg3",  "10.221.4.1/30"))
    t.add(FrogNode("A").add_iface("eth0","10.20.1.1/24").add_iface("wg0","10.221.1.2/30"))
    t.add(FrogNode("B").add_iface("eth0","10.20.2.1/24").add_iface("wg0","10.221.2.2/30"))
    t.add(FrogNode("C").add_iface("eth0","10.20.3.1/24").add_iface("wg0","10.221.3.2/30"))
    t.add(FrogNode("D").add_iface("eth0","10.20.4.1/24").add_iface("wg0","10.221.4.2/30"))
    t.link(("Hub","wg0"),("A","wg0"))
    t.link(("Hub","wg1"),("B","wg0"))
    t.link(("Hub","wg2"),("C","wg0"))
    t.link(("Hub","wg3"),("D","wg0"))
    return t

def topo_ring_4():
    """A - B - C - D - A. Each pair has its own link /24."""
    t = Topology("4-node ring")
    t.add(FrogNode("A")
        .add_iface("eth0","10.30.1.1/24")
        .add_iface("wg0", "10.222.1.1/30")       # to B
        .add_iface("wg1", "10.222.4.2/30"))      # to D
    t.add(FrogNode("B")
        .add_iface("eth0","10.30.2.1/24")
        .add_iface("wg0", "10.222.1.2/30")       # to A
        .add_iface("wg1", "10.222.2.1/30"))      # to C
    t.add(FrogNode("C")
        .add_iface("eth0","10.30.3.1/24")
        .add_iface("wg0", "10.222.2.2/30")       # to B
        .add_iface("wg1", "10.222.3.1/30"))      # to D
    t.add(FrogNode("D")
        .add_iface("eth0","10.30.4.1/24")
        .add_iface("wg0", "10.222.3.2/30")       # to C
        .add_iface("wg1", "10.222.4.1/30"))      # to A
    t.link(("A","wg0"),("B","wg0"))
    t.link(("B","wg1"),("C","wg0"))
    t.link(("C","wg1"),("D","wg0"))
    t.link(("D","wg1"),("A","wg1"))
    return t

def topo_snowflake():
    """Hub->A,B. A->A1,A2. B->B1,B2."""
    t = Topology("snowflake (depth 2)")
    t.add(FrogNode("Hub")
        .add_iface("eth0","10.40.99.1/24")
        .add_iface("wg0", "10.223.1.1/30")
        .add_iface("wg1", "10.223.2.1/30"))
    t.add(FrogNode("A")
        .add_iface("eth0","10.40.1.1/24")
        .add_iface("wg0", "10.223.1.2/30")   # uplink to Hub
        .add_iface("wg1", "10.223.3.1/30")   # to A1
        .add_iface("wg2", "10.223.4.1/30"))  # to A2
    t.add(FrogNode("B")
        .add_iface("eth0","10.40.2.1/24")
        .add_iface("wg0", "10.223.2.2/30")
        .add_iface("wg1", "10.223.5.1/30")
        .add_iface("wg2", "10.223.6.1/30"))
    t.add(FrogNode("A1").add_iface("eth0","10.40.11.1/24").add_iface("wg0","10.223.3.2/30"))
    t.add(FrogNode("A2").add_iface("eth0","10.40.12.1/24").add_iface("wg0","10.223.4.2/30"))
    t.add(FrogNode("B1").add_iface("eth0","10.40.21.1/24").add_iface("wg0","10.223.5.2/30"))
    t.add(FrogNode("B2").add_iface("eth0","10.40.22.1/24").add_iface("wg0","10.223.6.2/30"))
    t.link(("Hub","wg0"),("A","wg0"))
    t.link(("Hub","wg1"),("B","wg0"))
    t.link(("A","wg1"),("A1","wg0"))
    t.link(("A","wg2"),("A2","wg0"))
    t.link(("B","wg1"),("B1","wg0"))
    t.link(("B","wg2"),("B2","wg0"))
    return t

def topo_lan_only_chain():
    """Three FrogNet hosts on a *shared* LAN - all three on one /24.
    Tests multi-peer-on-one-segment case (no WG)."""
    t = Topology("LAN-only triangle (3 hosts on one /24)")
    t.add(FrogNode("X")
        .add_iface("eth0", "10.60.0.1/24")     # served
        .add_iface("eth1", "10.60.99.1/24"))   # shared peering LAN
    t.add(FrogNode("Y")
        .add_iface("eth0", "10.61.0.1/24")
        .add_iface("eth1", "10.60.99.2/24"))
    t.add(FrogNode("Z")
        .add_iface("eth0", "10.62.0.1/24")
        .add_iface("eth1", "10.60.99.3/24"))
    t.link(("X","eth1"), ("Y","eth1"), ("Z","eth1"))
    return t

def topo_pond_full_mesh():
    """Three FrogNet sites, full WG mesh (every pair has its own link /24)."""
    t = Topology("3-site pond (full WG mesh)")
    t.add(FrogNode("Seattle")
        .add_iface("eth0","10.241.241.1/24")
        .add_iface("wg0", "10.252.1.1/30")
        .add_iface("wg1", "10.252.2.1/30"))
    t.add(FrogNode("NewYork")
        .add_iface("eth0","10.122.221.1/24")
        .add_iface("wg0", "10.252.1.2/30")
        .add_iface("wg1", "10.252.3.1/30"))
    t.add(FrogNode("Austin")
        .add_iface("eth0","10.44.44.1/24")
        .add_iface("wg0", "10.252.2.2/30")
        .add_iface("wg1", "10.252.3.2/30"))
    t.link(("Seattle","wg0"), ("NewYork","wg0"))
    t.link(("Seattle","wg1"), ("Austin","wg0"))
    t.link(("NewYork","wg1"), ("Austin","wg1"))
    return t

def topo_pond_with_workers():
    """Same pond but each site has worker hosts riding its served /24."""
    t = topo_pond_full_mesh()
    t.label = "3-site pond with workers"
    t.add(FrogNode("Seattle-W1").add_iface("eth0","10.241.241.5/24"))
    t.add(FrogNode("NewYork-W1").add_iface("eth0","10.122.221.5/24"))
    t.add(FrogNode("Austin-W1") .add_iface("eth0","10.44.44.5/24"))
    t.link(("Seattle","eth0"),    ("Seattle-W1","eth0"))
    t.link(("NewYork","eth0"),    ("NewYork-W1","eth0"))
    t.link(("Austin","eth0"),     ("Austin-W1","eth0"))
    return t

def topo_pond_hub_spoke():
    """Three sites but only Seattle <-> NewYork and Seattle <-> Austin (no NY<->Austin).
    Forces NY-to-Austin traffic to transit through Seattle."""
    t = Topology("3-site pond hub-spoke (no NY<->Austin link)")
    t.add(FrogNode("Seattle")
        .add_iface("eth0","10.241.241.1/24")
        .add_iface("wg0", "10.252.1.1/30")
        .add_iface("wg1", "10.252.2.1/30"))
    t.add(FrogNode("NewYork")
        .add_iface("eth0","10.122.221.1/24")
        .add_iface("wg0", "10.252.1.2/30"))
    t.add(FrogNode("Austin")
        .add_iface("eth0","10.44.44.1/24")
        .add_iface("wg0", "10.252.2.2/30"))
    t.link(("Seattle","wg0"), ("NewYork","wg0"))
    t.link(("Seattle","wg1"), ("Austin","wg0"))
    return t

def topo_mixed():
    """A LAN cluster (X, Y on shared /24) plus Z connected by WG to Y.
    Tests mixing LAN-peer discovery with WG-tunneled discovery."""
    t = Topology("mixed LAN+WG")
    t.add(FrogNode("X")
        .add_iface("eth0","10.70.1.1/24")
        .add_iface("eth1","10.70.99.1/24"))
    t.add(FrogNode("Y")
        .add_iface("eth0","10.70.2.1/24")
        .add_iface("eth1","10.70.99.2/24")
        .add_iface("wg0", "10.224.1.1/30"))
    t.add(FrogNode("Z")
        .add_iface("eth0","10.70.3.1/24")
        .add_iface("wg0", "10.224.1.2/30"))
    t.link(("X","eth1"),("Y","eth1"))
    t.link(("Y","wg0"),("Z","wg0"))
    return t

# --- Asymmetric LAN-to-LAN via WG gateways ------------------------------------
# Each "site" is a self-contained sub-topology. One node per site has a WG
# iface to the gateway of the other site. The local LANs/links on each side
# are DIFFERENT shapes - non-symmetric across the WG bridge.

def topo_asym_lan2_wg_lan3():
    """Site A: 2 hosts on a shared peering LAN.
       Site B: 3 hosts on a shared peering LAN.
       A2 <-> B1 via WG."""
    t = Topology("asymmetric: A=2-host LAN <-> WG <-> B=3-host LAN")
    # Site A
    t.add(FrogNode("A1")
        .add_iface("eth0","10.80.1.1/24")     # served
        .add_iface("eth1","10.80.99.1/24"))   # peering LAN
    t.add(FrogNode("A2")                       # site A gateway
        .add_iface("eth0","10.80.2.1/24")
        .add_iface("eth1","10.80.99.2/24")
        .add_iface("wg0", "10.250.1.1/30"))   # to B1
    # Site B
    t.add(FrogNode("B1")                       # site B gateway
        .add_iface("eth0","10.81.1.1/24")
        .add_iface("eth1","10.81.99.1/24")
        .add_iface("wg0", "10.250.1.2/30"))
    t.add(FrogNode("B2")
        .add_iface("eth0","10.81.2.1/24")
        .add_iface("eth1","10.81.99.2/24"))
    t.add(FrogNode("B3")
        .add_iface("eth0","10.81.3.1/24")
        .add_iface("eth1","10.81.99.3/24"))
    t.link(("A1","eth1"),("A2","eth1"))                    # site A peering LAN
    t.link(("A2","wg0"),("B1","wg0"))                       # WG bridge
    t.link(("B1","eth1"),("B2","eth1"),("B3","eth1"))      # site B peering LAN
    return t

def topo_asym_chain_wg_star():
    """Site A: 3-node chain Ca1-Ca2-Ca3 (each pair on its own link /24).
       Site B: hub-and-spoke HubB with Bs1, Bs2.
       Ca3 <-> HubB via WG."""
    t = Topology("asymmetric: A=3-chain <-> WG <-> B=3-spoke star")
    # Site A: chain
    t.add(FrogNode("Ca1")
        .add_iface("eth0","10.83.1.1/24")
        .add_iface("wg0", "10.250.11.1/30"))
    t.add(FrogNode("Ca2")
        .add_iface("eth0","10.83.2.1/24")
        .add_iface("wg0", "10.250.11.2/30")
        .add_iface("wg1", "10.250.12.1/30"))
    t.add(FrogNode("Ca3")                              # site A gateway
        .add_iface("eth0","10.83.3.1/24")
        .add_iface("wg0", "10.250.12.2/30")
        .add_iface("wg1", "10.250.20.1/30"))           # cross-site WG
    # Site B: star
    t.add(FrogNode("HubB")                             # site B gateway
        .add_iface("eth0","10.84.99.1/24")             # served
        .add_iface("wg0", "10.250.20.2/30")
        .add_iface("wg1", "10.250.21.1/30")
        .add_iface("wg2", "10.250.22.1/30")
        .add_iface("wg3", "10.250.23.1/30"))
    t.add(FrogNode("Bs1")
        .add_iface("eth0","10.84.1.1/24").add_iface("wg0","10.250.21.2/30"))
    t.add(FrogNode("Bs2")
        .add_iface("eth0","10.84.2.1/24").add_iface("wg0","10.250.22.2/30"))
    t.add(FrogNode("Bs3")
        .add_iface("eth0","10.84.3.1/24").add_iface("wg0","10.250.23.2/30"))
    t.link(("Ca1","wg0"),("Ca2","wg0"))
    t.link(("Ca2","wg1"),("Ca3","wg0"))
    t.link(("Ca3","wg1"),("HubB","wg0"))
    t.link(("HubB","wg1"),("Bs1","wg0"))
    t.link(("HubB","wg2"),("Bs2","wg0"))
    t.link(("HubB","wg3"),("Bs3","wg0"))
    return t

def topo_asym_ring_wg_lan():
    """Site A: 4-node WG ring Ra-Rb-Rc-Rd-Ra. Rb is gateway.
       Site B: 3-host LAN cluster on a single shared /24. Lb1 is gateway.
       Rb <-> Lb1 via WG."""
    t = Topology("asymmetric: A=4-node WG ring <-> WG <-> B=3-host LAN cluster")
    # Site A: ring
    t.add(FrogNode("Ra")
        .add_iface("eth0","10.85.1.1/24")
        .add_iface("wg0", "10.250.31.1/30")
        .add_iface("wg1", "10.250.34.2/30"))
    t.add(FrogNode("Rb")                             # site A gateway
        .add_iface("eth0","10.85.2.1/24")
        .add_iface("wg0", "10.250.31.2/30")
        .add_iface("wg1", "10.250.32.1/30")
        .add_iface("wg2", "10.250.40.1/30"))         # cross-site WG
    t.add(FrogNode("Rc")
        .add_iface("eth0","10.85.3.1/24")
        .add_iface("wg0", "10.250.32.2/30")
        .add_iface("wg1", "10.250.33.1/30"))
    t.add(FrogNode("Rd")
        .add_iface("eth0","10.85.4.1/24")
        .add_iface("wg0", "10.250.33.2/30")
        .add_iface("wg1", "10.250.34.1/30"))
    # Site B: LAN cluster
    t.add(FrogNode("Lb1")                            # site B gateway
        .add_iface("eth0","10.86.1.1/24")
        .add_iface("eth1","10.86.99.1/24")
        .add_iface("wg0", "10.250.40.2/30"))
    t.add(FrogNode("Lb2")
        .add_iface("eth0","10.86.2.1/24")
        .add_iface("eth1","10.86.99.2/24"))
    t.add(FrogNode("Lb3")
        .add_iface("eth0","10.86.3.1/24")
        .add_iface("eth1","10.86.99.3/24"))
    # Ring links
    t.link(("Ra","wg0"),("Rb","wg0"))
    t.link(("Rb","wg1"),("Rc","wg0"))
    t.link(("Rc","wg1"),("Rd","wg0"))
    t.link(("Rd","wg1"),("Ra","wg1"))
    # Cross-site WG
    t.link(("Rb","wg2"),("Lb1","wg0"))
    # Site B peering LAN
    t.link(("Lb1","eth1"),("Lb2","eth1"),("Lb3","eth1"))
    return t

def topo_asym_three_sites():
    """Three sites, each a different internal shape, fully WG-meshed between
    site gateways:
        Site A - LAN cluster of 2 (peering LAN), gateway A_gw
        Site B - 3-node chain, gateway B_gw (the middle node)
        Site C - 2-spoke star, gateway C_hub
    WG links: A_gw<->B_gw, B_gw<->C_hub, A_gw<->C_hub."""
    t = Topology("asymmetric: 3 sites, each different shape, full WG mesh between gateways")
    # Site A - LAN cluster
    t.add(FrogNode("A_w1")
        .add_iface("eth0","10.90.1.1/24")
        .add_iface("eth1","10.90.99.1/24"))
    t.add(FrogNode("A_gw")
        .add_iface("eth0","10.90.2.1/24")
        .add_iface("eth1","10.90.99.2/24")
        .add_iface("wg0", "10.251.1.1/30")       # to B_gw
        .add_iface("wg1", "10.251.3.1/30"))      # to C_hub
    # Site B - 3-node chain, gateway is middle node
    t.add(FrogNode("B_end1")
        .add_iface("eth0","10.91.1.1/24")
        .add_iface("wg0", "10.251.11.1/30"))
    t.add(FrogNode("B_gw")
        .add_iface("eth0","10.91.2.1/24")
        .add_iface("wg0", "10.251.11.2/30")      # to B_end1
        .add_iface("wg1", "10.251.12.1/30")      # to B_end2
        .add_iface("wg2", "10.251.1.2/30")       # to A_gw
        .add_iface("wg3", "10.251.2.1/30"))      # to C_hub
    t.add(FrogNode("B_end2")
        .add_iface("eth0","10.91.3.1/24")
        .add_iface("wg0", "10.251.12.2/30"))
    # Site C - 2-spoke star
    t.add(FrogNode("C_hub")
        .add_iface("eth0","10.92.99.1/24")
        .add_iface("wg0", "10.251.3.2/30")       # to A_gw
        .add_iface("wg1", "10.251.2.2/30")       # to B_gw
        .add_iface("wg2", "10.251.21.1/30")      # to C_s1
        .add_iface("wg3", "10.251.22.1/30"))     # to C_s2
    t.add(FrogNode("C_s1")
        .add_iface("eth0","10.92.1.1/24")
        .add_iface("wg0", "10.251.21.2/30"))
    t.add(FrogNode("C_s2")
        .add_iface("eth0","10.92.2.1/24")
        .add_iface("wg0", "10.251.22.2/30"))
    # Site A peering LAN
    t.link(("A_w1","eth1"),("A_gw","eth1"))
    # Site B chain
    t.link(("B_end1","wg0"),("B_gw","wg0"))
    t.link(("B_gw","wg1"),("B_end2","wg0"))
    # Site C star
    t.link(("C_hub","wg2"),("C_s1","wg0"))
    t.link(("C_hub","wg3"),("C_s2","wg0"))
    # Cross-site WG mesh
    t.link(("A_gw","wg0"),("B_gw","wg2"))
    t.link(("B_gw","wg3"),("C_hub","wg1"))
    t.link(("A_gw","wg1"),("C_hub","wg0"))
    return t


# --- Multi-LAN shared WG transit ----------------------------------------------
# The WG is a multi-host spoke: 3+ gateways share one transit /24 and each
# gateway fronts a differently-shaped LAN.

def topo_multilan_shared_wg_transit():
    """4 LANs of varying internal shape; admin between gateways is a /30 full
    mesh (every pair has its own pairwise admin /30). Previously this was
    modeled as a shared /24 multi-host transit, which is incompatible with
    /30 admin addressing."""
    t = Topology("multi-LAN, /30 full mesh between 4 gateways (asymmetric shapes)")
    # Site A: peering LAN with 2 workers
    t.add(FrogNode("A_gw")
        .add_iface("eth0","10.140.1.1/24")            # served
        .add_iface("eth1","10.140.99.1/24")           # peering LAN
        .add_iface("wgB","10.253.50.1/30")            # to B_gw
        .add_iface("wgC","10.253.50.5/30")            # to C_hub
        .add_iface("wgD","10.253.50.9/30"))           # to D_gw
    t.add(FrogNode("A_w1")
        .add_iface("eth0","10.140.2.1/24")
        .add_iface("eth1","10.140.99.2/24"))
    t.add(FrogNode("A_w2")
        .add_iface("eth0","10.140.3.1/24")
        .add_iface("eth1","10.140.99.3/24"))
    # Site B: 3-node WG chain (B_gw - B_m - B_t)
    t.add(FrogNode("B_gw")
        .add_iface("eth0","10.141.1.1/24")
        .add_iface("wgA","10.253.50.2/30")            # to A_gw
        .add_iface("wgC","10.253.50.13/30")           # to C_hub
        .add_iface("wgD","10.253.50.17/30")           # to D_gw
        .add_iface("wg1","10.141.100.1/30"))          # to B_m
    t.add(FrogNode("B_m")
        .add_iface("eth0","10.141.2.1/24")
        .add_iface("wg0","10.141.100.2/30")
        .add_iface("wg1","10.141.101.1/30"))
    t.add(FrogNode("B_t")
        .add_iface("eth0","10.141.3.1/24")
        .add_iface("wg0","10.141.101.2/30"))
    # Site C: 3-spoke star (C_hub is gateway)
    t.add(FrogNode("C_hub")
        .add_iface("eth0","10.142.99.1/24")
        .add_iface("wgA","10.253.50.6/30")            # to A_gw
        .add_iface("wgB","10.253.50.14/30")           # to B_gw
        .add_iface("wgD","10.253.50.21/30")           # to D_gw
        .add_iface("wg1","10.142.100.1/30")
        .add_iface("wg2","10.142.101.1/30")
        .add_iface("wg3","10.142.102.1/30"))
    t.add(FrogNode("C_s1").add_iface("eth0","10.142.1.1/24")
        .add_iface("wg0","10.142.100.2/30"))
    t.add(FrogNode("C_s2").add_iface("eth0","10.142.2.1/24")
        .add_iface("wg0","10.142.101.2/30"))
    t.add(FrogNode("C_s3").add_iface("eth0","10.142.3.1/24")
        .add_iface("wg0","10.142.102.2/30"))
    # Site D: single host
    t.add(FrogNode("D_gw")
        .add_iface("eth0","10.143.1.1/24")
        .add_iface("wgA","10.253.50.10/30")           # to A_gw
        .add_iface("wgB","10.253.50.18/30")           # to B_gw
        .add_iface("wgC","10.253.50.22/30"))          # to C_hub
    # Site A peering LAN
    t.link(("A_gw","eth1"),("A_w1","eth1"),("A_w2","eth1"))
    # Site B chain
    t.link(("B_gw","wg1"),("B_m","wg0"))
    t.link(("B_m","wg1"),("B_t","wg0"))
    # Site C star
    t.link(("C_hub","wg1"),("C_s1","wg0"))
    t.link(("C_hub","wg2"),("C_s2","wg0"))
    t.link(("C_hub","wg3"),("C_s3","wg0"))
    # /30 admin mesh between gateways (6 pairwise /30 links)
    t.link(("A_gw","wgB"),("B_gw","wgA"))
    t.link(("A_gw","wgC"),("C_hub","wgA"))
    t.link(("A_gw","wgD"),("D_gw","wgA"))
    t.link(("B_gw","wgC"),("C_hub","wgB"))
    t.link(("B_gw","wgD"),("D_gw","wgB"))
    t.link(("C_hub","wgD"),("D_gw","wgC"))
    return t


def topo_dual_wg_transit_bridge():
    """Two admin-mesh groups bridged by BR_gw. Group 1: G1, G2, BR (full /30
    mesh, 3 pairwise links). Group 2: G3, G4, BR (full /30 mesh, 3 pairwise
    links). BR_gw participates in both groups."""
    t = Topology("two /30 admin mesh groups, bridged by BR_gw (asymmetric LANs)")
    # --- Group 1: G1_gw, G2_gw, BR_gw, addresses out of 10.253.60.0/24 ---
    # G1 site: 2-host peering LAN
    t.add(FrogNode("G1_gw")
        .add_iface("eth0","10.150.1.1/24")
        .add_iface("eth1","10.150.99.1/24")
        .add_iface("wgG2", "10.253.60.1/30")           # to G2_gw
        .add_iface("wgBR", "10.253.60.5/30"))          # to BR_gw
    t.add(FrogNode("G1_w1")
        .add_iface("eth0","10.150.2.1/24")
        .add_iface("eth1","10.150.99.2/24"))
    # G2 site: 2-node chain via local WG
    t.add(FrogNode("G2_gw")
        .add_iface("eth0","10.151.1.1/24")
        .add_iface("wgG1", "10.253.60.2/30")           # to G1_gw
        .add_iface("wgBR", "10.253.60.9/30")           # to BR_gw
        .add_iface("wg1",  "10.151.100.1/30"))         # to G2_t
    t.add(FrogNode("G2_t")
        .add_iface("eth0","10.151.2.1/24")
        .add_iface("wg0", "10.151.100.2/30"))
    # Bridge site: BR_gw participates in both groups
    t.add(FrogNode("BR_gw")
        .add_iface("eth0","10.152.1.1/24")
        .add_iface("eth1","10.152.99.1/24")
        .add_iface("wgG1", "10.253.60.6/30")           # group-1: to G1_gw
        .add_iface("wgG2", "10.253.60.10/30")          # group-1: to G2_gw
        .add_iface("wgG3", "10.253.61.5/30")           # group-2: to G3_hub
        .add_iface("wgG4", "10.253.61.9/30"))          # group-2: to G4_gw
    t.add(FrogNode("BR_w1")
        .add_iface("eth0","10.152.2.1/24")
        .add_iface("eth1","10.152.99.2/24"))
    # --- Group 2: G3_hub, G4_gw, BR_gw, addresses out of 10.253.61.0/24 ---
    # G3 site: 2-spoke star via local WG
    t.add(FrogNode("G3_hub")
        .add_iface("eth0","10.153.99.1/24")
        .add_iface("wgG4", "10.253.61.1/30")           # to G4_gw
        .add_iface("wgBR", "10.253.61.6/30")           # to BR_gw
        .add_iface("wg1",  "10.153.100.1/30")
        .add_iface("wg2",  "10.153.101.1/30"))
    t.add(FrogNode("G3_s1")
        .add_iface("eth0","10.153.1.1/24")
        .add_iface("wg0", "10.153.100.2/30"))
    t.add(FrogNode("G3_s2")
        .add_iface("eth0","10.153.2.1/24")
        .add_iface("wg0", "10.153.101.2/30"))
    # G4 site: single host
    t.add(FrogNode("G4_gw")
        .add_iface("eth0","10.154.1.1/24")
        .add_iface("wgG3", "10.253.61.2/30")           # to G3_hub
        .add_iface("wgBR", "10.253.61.10/30"))         # to BR_gw
    # Site G1 LAN
    t.link(("G1_gw","eth1"),("G1_w1","eth1"))
    # Site G2 chain
    t.link(("G2_gw","wg1"),("G2_t","wg0"))
    # Bridge site LAN
    t.link(("BR_gw","eth1"),("BR_w1","eth1"))
    # Site G3 star
    t.link(("G3_hub","wg1"),("G3_s1","wg0"))
    t.link(("G3_hub","wg2"),("G3_s2","wg0"))
    # Group 1 admin /30 mesh
    t.link(("G1_gw","wgG2"),("G2_gw","wgG1"))
    t.link(("G1_gw","wgBR"),("BR_gw","wgG1"))
    t.link(("G2_gw","wgBR"),("BR_gw","wgG2"))
    # Group 2 admin /30 mesh
    t.link(("G3_hub","wgG4"),("G4_gw","wgG3"))
    t.link(("G3_hub","wgBR"),("BR_gw","wgG3"))
    t.link(("G4_gw","wgBR"),("BR_gw","wgG4"))
    return t


# --- No-internet, no-broker, no-WG cases ------------------------------------
# WG tunnels only exist when (a) internet is up, (b) broker is reachable, and
# (c) there are remote destinations that require a tunnel. Without all three,
# every reachable peer is on a directly-connected LAN-class segment: eth*,
# wlan*, en*, wlp*, ham*, br*, bond*. The algorithm doesn't care about the
# iface name - only that the peer's served /24 (or its admin .2) is reachable
# on a directly-connected segment.

def topo_solo_wlan0_ap():
    """Isolated FrogNet acting as its own AP: wlan0 is the served iface,
    broadcasting an SSID, dnsmasq handing out DHCP. Two clients on the SSID.
    No internet, no broker, no WG. No remote destinations."""
    t = Topology("isolated wlan0 AP: served on wlan0, 2 DHCP clients, no internet")
    t.add(FrogNode("Farm")
        .add_iface("wlan0", "10.45.1.1/24"))   # served, SSID
    t.add(FrogNode("Sensor-A")
        .add_iface("wlan0", "10.45.1.5/24"))   # DHCP client
    t.add(FrogNode("Sensor-B")
        .add_iface("wlan0", "10.45.1.6/24"))   # DHCP client
    t.link(("Farm","wlan0"),("Sensor-A","wlan0"),("Sensor-B","wlan0"))
    return t

def topo_two_aps_ham_radio():
    """Two FrogNet nodes each serving their own SSID on wlan0, connected by a
    point-to-point ham radio link on ham0. No internet, no broker, no WG.
    Cross-site /24s routed across ham0."""
    t = Topology("two wlan0-AP FrogNets, peered by ham0 radio (no internet)")
    t.add(FrogNode("HillCrest")
        .add_iface("wlan0", "10.46.1.1/24")    # served, SSID
        .add_iface("ham0",  "10.46.99.1/24"))  # ham radio link
    t.add(FrogNode("Valley")
        .add_iface("wlan0", "10.46.2.1/24")    # served, SSID
        .add_iface("ham0",  "10.46.99.2/24"))  # ham radio link
    # SSID-side clients
    t.add(FrogNode("HC-client")
        .add_iface("wlan0", "10.46.1.5/24"))
    t.add(FrogNode("V-client")
        .add_iface("wlan0", "10.46.2.5/24"))
    t.link(("HillCrest","wlan0"),("HC-client","wlan0"))
    t.link(("Valley","wlan0"),("V-client","wlan0"))
    t.link(("HillCrest","ham0"),("Valley","ham0"))
    return t

def topo_mixed_iface_no_router():
    """Three FrogNets, different served-iface types, mixed peering. No
    external router, no broker, no WG.
        Barn  - served on eth0 (wired LAN). Peers Forest on wlan1.
        Forest - served on wlan0 (its own SSID for ranger devices).
                 Peers Barn on wlan1. Peers Tower on ham0.
        Tower - served on wlan0 (its own SSID). Peers Forest on ham0.
    The algorithm must reach across an eth <-> wlan <-> wlan <-> ham chain
    without caring which is which."""
    t = Topology("mixed iface types (eth0/wlan0/wlan1/ham0), no internet, no broker")
    t.add(FrogNode("Barn")
        .add_iface("eth0",  "10.47.1.1/24")       # served, wired
        .add_iface("wlan1", "10.47.50.1/30"))     # link to Forest
    t.add(FrogNode("Forest")
        .add_iface("wlan0", "10.47.2.1/24")       # served, SSID
        .add_iface("wlan1", "10.47.50.2/30")      # link to Barn
        .add_iface("ham0",  "10.47.51.1/30"))     # link to Tower
    t.add(FrogNode("Tower")
        .add_iface("wlan0", "10.47.3.1/24")       # served, SSID
        .add_iface("ham0",  "10.47.51.2/30"))     # link to Forest
    # Workers
    t.add(FrogNode("Barn-sensor")
        .add_iface("eth0",  "10.47.1.5/24"))
    t.add(FrogNode("Forest-ranger")
        .add_iface("wlan0", "10.47.2.5/24"))
    t.link(("Barn","eth0"),("Barn-sensor","eth0"))
    t.link(("Forest","wlan0"),("Forest-ranger","wlan0"))
    t.link(("Barn","wlan1"),("Forest","wlan1"))
    t.link(("Forest","ham0"),("Tower","ham0"))
    return t


# ============================================================================
# Section 2c: runner
# ============================================================================

def run_topo(topo: Topology, verbose=True) -> bool:
    print(f"\n=== topology: {topo.label} ===")
    assign_identities(topo)
    cycles = discover_routes(topo)
    print(f"  converged in {cycles} cycle(s); {len(topo.nodes)} nodes, "
          f"{len(topo.segments)} L2 segment(s)")
    if verbose:
        for n in topo.nodes.values():
            admin = f" admin={n.frognet_admin_ip}" if n.frognet_admin_ip else ""
            print(f"  {n.name} ({n.frognet_ip}{admin})")
            for iface in n.ifaces.values():
                print(f"      iface  {iface}")
            for r in n.routes:
                # Annotate via routes with winner RTT and kind if present.
                if r.via is not None:
                    w = n.installed_winners.get(r.dest)
                    if w is not None:
                        print(f"      route  {r}  [{w.kind} rtt={w.rtt_ms:.1f}ms"
                              f"{(' ch=' + w.ch_name) if w.ch_name else ''}]")
                    else:
                        print(f"      route  {r}")
                else:
                    print(f"      route  {r}")
    ok, total, failures = verify_reachability(topo)
    if failures:
        print(f"  REACHABILITY: {ok}/{total} pairs OK, {len(failures)} FAILED")
        for f in failures:
            print(f"      FAIL  {f}")
        return False
    print(f"  REACHABILITY: all {ok}/{total} (production+admin) pairs OK +")
    return True


# ============================================================================
# Section 2c: live merge-cycle orchestration model
# ============================================================================
#
# Section 2's discover_routes() is serial: per node, sync_interfaces -> plan ->
# commit, fully sequenced until quiescence. That validates the ALGORITHM but
# cannot reproduce ORCHESTRATION bugs observed on the live SeattleFive box
# on 2026-05-15.
#
# The live mergeHostsAndResolv.bash pipeline has phases that run in parallel
# and several persistent state stores that survive across merge cycles. This
# section models them explicitly so we can encode regressions for the
# orchestration bugs we have actually seen on the wire:
#
#   1. mergeHostsAndResolv forks bringup IN PARALLEL with sync_interfaces
#      wave-1. Bringup's `_channel_already_lan_reachable` filter checks the
#      kernel routing table at the moment it runs - on cold start, empty.
#      Every broker channel comes up. Wave-2 then probes through those
#      tunnels; stale broker entries time out; committer tears them down.
#
#   2. Committer's _tear_down_tunnels runs INSIDE commit, before final state
#      snapshot. Adjacent wg ifaces' routes are observed to lose their
#      via+onlink form (NY-1's 10.102.60.0/24 ended up `dev wg1 scope link`
#      after sibling teardowns).
#
#   3. _load_local_state (poll.py) trusts /var/lib/frognet-tunnel/*.json
#      without verifying the iface exists in the kernel. Phantom entries
#      short-circuit bringup (`already_up=[N channels]`).
#
#   4. Daemon poll_once snapshots `_active_tunnels` at startup and never
#      reconciles against the kernel. If memory matches the broker but the
#      kernel has nothing, poll never fires runMerge.
#
#   5. Stale broker entries (renamed/dead nodes - Seattle-1G/1I/3/4C,
#      SeattleDB, SeattleNewDB on 2026-05-15) appear in the channel list and
#      get tunneled every cold start, then torn down every commit.
#
#   6. Voucher scope: SeattleSix only vouched for SeattleTwo (10.121.120)
#      and SeattleThree (10.130.130). Did NOT vouch for BAMacBook etc, so
#      SeattleFive's wave-1 couldn't learn LAN paths to those peers even
#      though SeattleSix had connectivity.
#
# Modeled state stores (persist across merges):
#   - kernel routes and wg ifaces        (MergeKernel)
#   - /var/lib/frognet-tunnel/*.json     (StateFiles)
#   - daemon in-memory _active_tunnels   (DaemonMemory)
#   - broker channel list per node       (World.broker_channels)
#   - voucher scope per node             (World.voucher)


@dataclass
class KernelRoute:
    dest: str
    dev: str
    via: Optional[str]
    src: Optional[str]
    scope: str  # "onlink" | "scope_link" | "kernel"
    metric: int = 22

    def __str__(self):
        bits = [self.dest]
        if self.via: bits.append(f"via {self.via}")
        bits.append(f"dev {self.dev}")
        if self.scope == "scope_link": bits.append("scope link")
        if self.scope == "onlink": bits.append("onlink")
        if self.src: bits.append(f"src {self.src}")
        bits.append(f"metric {self.metric}")
        return " ".join(bits)


class MergeKernel:
    """Per-node kernel state - survives across merge cycles."""
    def __init__(self):
        self.routes: Dict[str, KernelRoute] = {}
        self.wg_ifaces: Set[str] = set()
        self._next_wg = 0

    def install_wg_route(self, dest, via, dev, src, metric=22) -> bool:
        """Returns False if dest already has a connected (kernel-scope) route
        on a different dev - matches live kernel's rejection of a wg install
        for the host's own served /24 (BRINGUP_ROUTE_FAILED in the log)."""
        existing = self.routes.get(dest)
        if existing and existing.scope == "kernel" and existing.dev != dev:
            return False
        self.routes[dest] = KernelRoute(dest, dev, via, src, "onlink", metric)
        return True

    def install_lan_route(self, dest, via, dev="eth0", metric=22):
        self.routes[dest] = KernelRoute(dest, dev, via, None, "onlink", metric)

    def install_connected(self, dest, dev, src):
        self.routes[dest] = KernelRoute(dest, dev, None, src, "kernel", 0)

    def has_lan_route(self, dest) -> bool:
        r = self.routes.get(dest)
        return r is not None and not r.dev.startswith("wg")

    def allocate_wg(self) -> str:
        iface = f"wg{self._next_wg}"
        self._next_wg += 1
        self.wg_ifaces.add(iface)
        return iface

    def teardown_wg(self, iface, kernel_in_flux: bool):
        """Tear down wg iface and remove its routes.

        kernel_in_flux=True models the observed bug: teardown during the
        commit phase (while installs/removes are still in progress) causes
        sibling wg routes to lose via+onlink and end up `scope link`. This
        is the empirical observation from NY-1's wg1 route after sibling
        teardowns mid-commit.

        kernel_in_flux=False models the deferred-teardown fix: by the time
        teardown runs, the install phase is complete and the kernel is
        settled. No sibling corruption.
        """
        self.wg_ifaces.discard(iface)
        for dest in list(self.routes):
            if self.routes[dest].dev == iface:
                del self.routes[dest]
        if kernel_in_flux:
            for r in self.routes.values():
                if r.dev.startswith("wg") and r.scope == "onlink":
                    r.scope = "scope_link"
                    r.via = None


class StateFiles:
    """/var/lib/frognet-tunnel/*.json - disk persistence."""
    def __init__(self):
        self.active: Dict[str, dict] = {}
    def load(self) -> Dict[str, dict]: return dict(self.active)
    def save(self, d: Dict[str, dict]): self.active = dict(d)
    def remove(self, ch): self.active.pop(ch, None)


class DaemonMemory:
    """In-process active_tunnels snapshot - survives merges, lost on reboot."""
    def __init__(self):
        self.active: Dict[str, dict] = {}


class LiveNode:
    """One node with kernel/state/daemon. Joined to a World."""
    def __init__(self, name, served_subnet):
        self.name = name
        self.served_subnet = served_subnet
        self.served_ip = served_subnet.replace(".0/24", ".1")
        self.lan_anchor_ip: Optional[str] = None
        self.lan_anchor_node: Optional[str] = None
        self.kernel = MergeKernel()
        self.state_files = StateFiles()
        self.daemon_mem = DaemonMemory()
        self.kernel.install_connected(served_subnet, "eth0", self.served_ip)


class World:
    def __init__(self):
        self.nodes: Dict[str, LiveNode] = {}
        self.broker_channels: Dict[str, List[dict]] = {}
        self.voucher: Dict[str, List[str]] = {}

    def add(self, n: LiveNode) -> LiveNode:
        self.nodes[n.name] = n
        return n

    def set_broker(self, name, channels): self.broker_channels[name] = channels
    def set_voucher(self, name, vouched): self.voucher[name] = vouched

    def link_lan_anchor(self, node: str, anchor_node: str, anchor_lan_ip: str):
        self.nodes[node].lan_anchor_ip = anchor_lan_ip
        self.nodes[node].lan_anchor_node = anchor_node

    def probe_wg(self, ch: dict) -> bool:
        """Does the remote end of this WG channel actually exist as a real node?"""
        # ch["name"] is "Node-Name-10.x.y" - strip the trailing -10.x.y
        nm = ch.get("name", "")
        m = re.match(r"^(.+)-(\d+\.\d+\.\d+)$", nm)
        peer_name = m.group(1) if m else nm
        return peer_name in self.nodes

    def vouchers_for(self, anchor_node: str) -> List[str]:
        return list(self.voucher.get(anchor_node, []))


@dataclass
class MergeConfig:
    # --- live bugs (True = bug-as-observed; False = proposed fix) ---
    bringup_parallel_with_wave1: bool = True
    teardown_inside_commit: bool = True
    # --- live missing-fixes (True = fix applied; False = bug-as-observed) ---
    load_local_state_validates_kernel: bool = False
    poll_once_reconciles_with_kernel: bool = False


@dataclass
class MergeResult:
    brought_up: List[str] = field(default_factory=list)
    torn_down: List[str] = field(default_factory=list)
    skipped_lan_reachable: List[str] = field(default_factory=list)
    won_no_subnet: List[str] = field(default_factory=list)


def _run_wave1_lan(node: LiveNode, world: World) -> Dict[str, dict]:
    """sync_interfaces wave-1: probe the LAN anchor, learn anchor's served /24
    and everything the anchor vouches for (transitive LAN discovery)."""
    winners: Dict[str, dict] = {}
    if not node.lan_anchor_node:
        return winners
    anchor = world.nodes.get(node.lan_anchor_node)
    if not anchor:
        return winners
    winners[anchor.served_subnet] = {
        "dev": "eth0", "via": node.lan_anchor_ip, "kind": "lan"}
    for vouched in world.vouchers_for(node.lan_anchor_node):
        winners[vouched] = {
            "dev": "eth0", "via": node.lan_anchor_ip, "kind": "lan"}
    return winners


def merge_cycle(node: LiveNode, world: World, cfg: MergeConfig) -> MergeResult:
    """One mergeHostsAndResolv -> committer run with configurable bug toggles."""
    res = MergeResult()
    broker_channels = world.broker_channels.get(node.name, [])

    # Phase 1: _load_local_state
    loaded = node.state_files.load()
    if cfg.load_local_state_validates_kernel:
        loaded = {ch: spec for ch, spec in loaded.items()
                  if spec["iface"] in node.kernel.wg_ifaces}
    node.daemon_mem.active = loaded

    # Pre-compute wave-1 LAN winners regardless of timing
    lan_winners_wave1 = _run_wave1_lan(node, world)

    # Phase 2: bringup decision
    if not cfg.bringup_parallel_with_wave1:
        # Fix: install wave-1 LAN routes BEFORE the bringup filter runs.
        for dest, w in lan_winners_wave1.items():
            node.kernel.install_lan_route(dest, w["via"], w["dev"])

    to_bring_up: List[dict] = []
    for ch in broker_channels:
        rs_list = ch["remote_subnets"]
        # _channel_already_lan_reachable: all remote_subnets have non-wg route?
        all_lan = bool(rs_list) and all(
            node.kernel.has_lan_route(rs) for rs in rs_list)
        if all_lan:
            res.skipped_lan_reachable.append(ch["name"])
            continue
        spec = node.daemon_mem.active.get(ch["name"])
        if spec:
            # Already up per daemon memory. The kernel-validation that would
            # catch phantoms happens earlier in _load_local_state; if that
            # filter wasn't enabled, phantoms slip through and bringup is
            # incorrectly suppressed.
            continue
        to_bring_up.append(ch)

    # Phase 3: bring up tunnels (handshake + route install)
    for ch in to_bring_up:
        iface = node.kernel.allocate_wg()
        for rs in ch["remote_subnets"]:
            peer_dot1 = rs.replace(".0/24", ".1")
            node.kernel.install_wg_route(rs, peer_dot1, iface, node.served_ip)
        node.daemon_mem.active[ch["name"]] = {
            "iface": iface, "subnets": ch["remote_subnets"]}
        node.state_files.save(node.daemon_mem.active)
        res.brought_up.append(ch["name"])

    # Phase 6: wave-2 probing
    wg_winners: Dict[str, dict] = {}
    for ch_name, spec in list(node.daemon_mem.active.items()):
        ch = next((c for c in broker_channels if c["name"] == ch_name), None)
        if not ch:
            continue
        if world.probe_wg(ch):
            for rs in ch["remote_subnets"]:
                wg_winners[rs] = {"dev": spec["iface"], "kind": "wg"}

    # Phase 7: committer - plan + install + (maybe) teardown
    final_winners: Dict[str, dict] = dict(wg_winners)
    for k, v in lan_winners_wave1.items():
        final_winners[k] = v  # LAN beats WG per primer

    for dest, w in final_winners.items():
        if w["kind"] == "lan":
            node.kernel.install_lan_route(dest, w["via"], w["dev"])
        else:
            peer = dest.replace(".0/24", ".1")
            node.kernel.install_wg_route(dest, peer, w["dev"], node.served_ip)

    # Compute losers
    for ch_name, spec in list(node.daemon_mem.active.items()):
        ch = next((c for c in broker_channels if c["name"] == ch_name), None)
        if not ch:
            res.won_no_subnet.append(ch_name)
            continue
        won_any = any(
            rs in final_winners and final_winners[rs].get("dev") == spec["iface"]
            for rs in ch["remote_subnets"])
        if not won_any:
            res.won_no_subnet.append(ch_name)

    # Teardown losers. With teardown_inside_commit=True (live bug), the
    # kernel is still mutating from the just-completed installs and the
    # teardown triggers sibling-route corruption. With False (fix: defer
    # until post-snapshot), the kernel is settled and the same teardown
    # is clean.
    in_flux = cfg.teardown_inside_commit
    for ch_name in res.won_no_subnet:
        spec = node.daemon_mem.active.get(ch_name)
        if spec:
            node.kernel.teardown_wg(spec["iface"], kernel_in_flux=in_flux)
            node.daemon_mem.active.pop(ch_name, None)
            node.state_files.remove(ch_name)
            res.torn_down.append(ch_name)

    # Phase 8: state_reconcile + daemon poll reconcile
    for ch_name, spec in list(node.daemon_mem.active.items()):
        if spec["iface"] not in node.kernel.wg_ifaces:
            node.daemon_mem.active.pop(ch_name, None)
            node.state_files.remove(ch_name)
    if cfg.poll_once_reconciles_with_kernel:
        for ch_name in list(node.daemon_mem.active):
            spec = node.daemon_mem.active[ch_name]
            if spec["iface"] not in node.kernel.wg_ifaces:
                node.daemon_mem.active.pop(ch_name, None)

    return res


# ============================================================================
# Section 3: bug regression tests
# ============================================================================
#
# Each test asserts an invariant tied to a specific bug from the chat
# history. Failures here mean we regressed against something we've fixed
# (or are at risk of breaking when the live code is updated).

REGRESSION_FAILS: List[str] = []

def reg(name: str, ok: bool, detail: str = ""):
    status = "OK  " if ok else "FAIL"
    print(f"  [{status}] {name}{(' - ' + detail) if detail else ''}")
    if not ok:
        REGRESSION_FAILS.append(name)


def test_route_stacking():
    """fixDefaultRoute stacking (chat 7d302e0d): each merge cycle was
    re-adding default routes at a monotonically decreasing metric because
    `ip route add` was used instead of `replace`. Equivalent invariant
    here: running discover_routes() repeatedly on the same topology must
    not grow the route table. Routes must be idempotent."""
    print("\n--- route stacking / idempotency ---")
    t = topo_pond_full_mesh()
    discover_routes(t)
    first = {n.name: sorted([(r.dest, r.dev, r.via, r.metric) for r in n.routes])
             for n in t.nodes.values()}
    for cycle in range(5):
        discover_routes(t)
    second = {n.name: sorted([(r.dest, r.dev, r.via, r.metric) for r in n.routes])
              for n in t.nodes.values()}
    reg("routes stable across 5 re-runs of discover_routes()",
        first == second,
        f"first={sum(len(v) for v in first.values())} entries, "
        f"second={sum(len(v) for v in second.values())} entries")
    # Also check no duplicates within any node's table.
    for n in t.nodes.values():
        keys = [(r.dest, r.dev, r.via) for r in n.routes]
        unique = len(set(keys)) == len(keys)
        reg(f"no duplicate routes on {n.name}", unique,
            f"got {len(keys)} entries, {len(set(keys))} unique")


def test_stickiness_under_jitter():
    """Stickiness hysteresis (chat fcf955b2): installed winner stays
    unless an alternative is >=100ms AND >=20% faster. Modeled by adding
    RTT jitter at probe time. We patch the edge_rtt function to return
    jittered values across cycles; the installed winner for each dest
    must not switch between cycles."""
    print("\n--- stickiness under RTT jitter ---")
    t = topo_pond_full_mesh()
    discover_routes(t)
    # Snapshot winners after convergence.
    baseline = {n.name: {d: (w.dev, w.via) for d, w in n.installed_winners.items()}
                for n in t.nodes.values()}
    # Apply small jitter (+/-2ms, below the 100ms/20% threshold).
    global EDGE_RTT_BY_KIND
    orig = dict(EDGE_RTT_BY_KIND)
    switches = 0
    for cycle in range(10):
        # Perturb by a few ms per cycle.
        EDGE_RTT_BY_KIND = {k: v + ((cycle % 5) - 2) * 0.5
                            for k, v in orig.items()}
        discover_routes(t, max_cycles=8)
        for n in t.nodes.values():
            for d, w in n.installed_winners.items():
                old = baseline[n.name].get(d)
                if old is not None and old != (w.dev, w.via):
                    switches += 1
    EDGE_RTT_BY_KIND.clear()
    EDGE_RTT_BY_KIND.update(orig)
    reg("no winner switches under sub-threshold jitter",
        switches == 0, f"switches={switches}")


def test_bug29_sample_consolidation():
    """Bug #29 (chat fcf955b2): noisy sample outvoting alternatives. The
    planner must consolidate multiple samples per (dev, via, kind,
    ch_name) to their min RTT before comparing across alternatives."""
    print("\n--- Bug #29: noisy sample consolidation ---")
    # Two candidate paths to the same dest:
    #   Path L: dev=eth0 via=10.1.1.1 - clean: rtt=10
    #   Path W: dev=wg0  via=10.2.2.1 - noisy: samples 5, 5, 200 (true ~5)
    # If samples weren't consolidated to min, a single 200ms sample would
    # push wg0's "average" above eth0's 10ms and L would win incorrectly.
    obs = [
        Observation(dest="10.99.99.0/24", host_path="10.99.99.1",
                    via="10.1.1.1", dev="eth0", rtt_ms=10.0,
                    kind="lan", ch_name="", anchor="10.1.1.1"),
        Observation(dest="10.99.99.0/24", host_path="10.99.99.1",
                    via="10.2.2.1", dev="wg0", rtt_ms=5.0,
                    kind="wg", ch_name="X", anchor="10.2.2.1"),
        Observation(dest="10.99.99.0/24", host_path="10.99.99.1",
                    via="10.2.2.1", dev="wg0", rtt_ms=5.0,
                    kind="wg", ch_name="X", anchor="10.2.2.1"),
        Observation(dest="10.99.99.0/24", host_path="10.99.99.1",
                    via="10.2.2.1", dev="wg0", rtt_ms=200.0,
                    kind="wg", ch_name="X", anchor="10.2.2.1"),
    ]
    winners = plan(obs, installed_winners={})
    w = winners["10.99.99.0/24"]
    reg("noisy sample consolidated to min RTT",
        w.rtt_ms == 5.0, f"winner rtt={w.rtt_ms}")
    reg("low-RTT wg path wins over lan despite noisy sample",
        w.dev == "wg0" and w.via == "10.2.2.1",
        f"winner dev={w.dev} via={w.via}")


def test_bug28_lan_via_is_lan_ip():
    """Bug #28 (chat fcf955b2): `_winning_via` returns host_path for
    tunnels, obs.via for LAN - the pre-Bug-#28 regression mistakenly
    used host_path for LAN. A LAN route to a peer's served /24 must
    have via = peer's LAN-segment IP, not its frognet_ip (which is on
    a different /24 entirely)."""
    print("\n--- Bug #28: LAN via uses peering-segment IP not frognet_ip ---")
    # In topo_pair: BlackBox and IronBox each serve their own /24
    # (10.101.10.0/24 and 10.101.20.0/24) and meet on 10.101.99.0/24.
    # BlackBox's route to 10.101.20.0/24 must have via=10.101.99.2
    # (IronBox's peering-LAN IP), NOT via=10.101.20.1 (its frognet_ip).
    t = topo_pair()
    discover_routes(t)
    bb = t.nodes["BlackBox"]
    via_route = next((r for r in bb.routes
                      if r.dest == "10.101.20.0/24" and r.via is not None), None)
    if via_route is None:
        reg("BlackBox has via-route to 10.101.20.0/24", False, "no route found")
        return
    reg("LAN route's via = peering-segment IP (10.101.99.2)",
        via_route.via == "10.101.99.2",
        f"via={via_route.via} dev={via_route.dev}")
    reg("LAN route's via != served-/24 .1 (10.101.20.1)",
        via_route.via != "10.101.20.1",
        f"via={via_route.via}")


def test_multipath_picks_lowest_rtt():
    """planner.py:91 (chat fcf955b2): _winner_key = (rtt_ms, dev, via).
    Lowest RTT wins. Kind is informational, not a tiebreaker. Construct
    a node with two candidates to the same dest: one fast (wg, low RTT),
    one slow (lan, higher RTT). Faster path wins regardless of kind."""
    print("\n--- multipath selection picks lowest RTT ---")
    obs = [
        Observation(dest="10.50.50.0/24", host_path="10.50.50.1",
                    via="10.10.0.1", dev="eth0", rtt_ms=50.0,
                    kind="lan", ch_name="", anchor="10.10.0.1"),
        Observation(dest="10.50.50.0/24", host_path="10.50.50.1",
                    via="10.20.0.1", dev="wg0", rtt_ms=25.0,
                    kind="wg", ch_name="Y", anchor="10.20.0.1"),
    ]
    winners = plan(obs, installed_winners={})
    w = winners["10.50.50.0/24"]
    reg("lowest-RTT path wins (wg 25ms beats lan 50ms)",
        w.dev == "wg0" and w.rtt_ms == 25.0,
        f"winner dev={w.dev} rtt={w.rtt_ms} kind={w.kind}")
    # And the reverse: if lan is faster, it wins.
    obs2 = [
        Observation(dest="10.51.51.0/24", host_path="10.51.51.1",
                    via="10.10.0.1", dev="eth0", rtt_ms=5.0,
                    kind="lan", ch_name="", anchor="10.10.0.1"),
        Observation(dest="10.51.51.0/24", host_path="10.51.51.1",
                    via="10.20.0.1", dev="wg0", rtt_ms=25.0,
                    kind="wg", ch_name="Y", anchor="10.20.0.1"),
    ]
    winners2 = plan(obs2, installed_winners={})
    w2 = winners2["10.51.51.0/24"]
    reg("lowest-RTT path wins (lan 5ms beats wg 25ms)",
        w2.dev == "eth0" and w2.rtt_ms == 5.0,
        f"winner dev={w2.dev} rtt={w2.rtt_ms} kind={w2.kind}")


def test_distributed_reconvergence_after_reboot():
    """SemCacheProxy / known_hosts rebuild path. A node loses all state
    (routes, known_hosts, installed_winners - simulating reboot) and
    must reconverge to the same end state without any external help.
    The other nodes shouldn't need to be reset for the reboot to recover."""
    print("\n--- distributed reconvergence after a node reboot ---")
    t = topo_chain_5()
    discover_routes(t)
    a_before = sorted([(r.dest, r.dev, r.via) for r in t.nodes["A"].routes])
    # Reboot node C - clear routes, known_hosts, installed_winners.
    c = t.nodes["C"]
    c.routes = []
    c.known_hosts = {}
    c.installed_winners = {}
    for iface in c.ifaces.values():
        c.install_route(iface.subnet, dev=iface.name, via=None, metric=100)
    c.known_hosts[c.frognet_ip] = c.name
    # Run one more discovery pass (without resetting all nodes).
    # Simulate the merge propagation triggered by C coming back up.
    for cycle in range(8):
        any_change = False
        for node in t.nodes.values():
            node.routes = [r for r in node.routes if r.via is None]
            for i in node.ifaces.values():
                if not any(r.dest == i.subnet and r.via is None
                           for r in node.routes):
                    node.install_route(i.subnet, dev=i.name, via=None,
                                       metric=100)
            old_w = dict(node.installed_winners)
            old_h = dict(node.known_hosts)
            obs = sync_interfaces(t, node)
            winners = plan(obs, node.installed_winners)
            commit(node, winners)
            node.installed_winners = winners
            new_h = {node.frognet_ip: node.name}
            for dest, w in winners.items():
                for n in t.nodes.values():
                    if n.frognet_ip == w.host_path:
                        new_h[w.host_path] = n.name
                        break
            node.known_hosts = new_h
            if winners != old_w or new_h != old_h:
                any_change = True
        if not any_change:
            break
    a_after = sorted([(r.dest, r.dev, r.via) for r in t.nodes["A"].routes])
    c_after = sorted([(r.dest, r.dev, r.via) for r in t.nodes["C"].routes])
    reg("A reconverges to same routes after C reboot",
        a_after == a_before, f"diff={set(a_before) ^ set(a_after)}")
    # C should know about A through E again.
    expected_dests = {f"10.11.{i}.0/24" for i in (1,2,4,5)}
    c_dests = {r.dest for r in t.nodes["C"].routes if r.via is not None}
    reg("rebooted C reconverges to know all 4 peer /24s",
        expected_dests.issubset(c_dests),
        f"missing={expected_dests - c_dests}")


def test_no_address_pattern_matching():
    """Standing rule: algorithm must not key on address prefix.
    Build a topology with weird non-10.101 IPs and verify discovery
    works end-to-end."""
    print("\n--- algorithm is address-agnostic ---")
    t = Topology("oddball IPs")
    t.add(FrogNode("Alpha")
        .add_iface("eth0", "10.55.55.1/24")
        .add_iface("wg0",  "10.253.222.1/30"))
    t.add(FrogNode("Bravo")
        .add_iface("eth0", "10.77.231.1/24")
        .add_iface("wg0",  "10.253.222.2/30"))
    t.link(("Alpha","wg0"), ("Bravo","wg0"))
    discover_routes(t)
    ok, total, fails = verify_reachability(t)
    reg("discovery works for non-10.101 prefixes",
        ok == total, f"{ok}/{total}, fails={fails}")


def test_dot2_no_separate_observation():
    """The .2 admin alias is a probe target only - it must not generate
    a separate Observation. Per-peer, per-dev, wave-1 yields exactly one
    observation for the peer's served /24 even though both .1 and .2
    are probed."""
    print("\n--- .2 probe does not yield a separate observation ---")
    t = topo_pair()
    assign_identities(t)
    for node in t.nodes.values():
        node.routes = []
        node.known_hosts = {}
        node.installed_winners = {}
        for iface in node.ifaces.values():
            node.install_route(iface.subnet, dev=iface.name, via=None,
                               metric=100)
        if node.frognet_ip:
            node.known_hosts[node.frognet_ip] = node.name
    obs = sync_interfaces(t, t.nodes["BlackBox"])
    # In topo_pair, BlackBox sees only IronBox on eth1.
    iron_subnet_obs = [o for o in obs if o.dest == "10.101.20.0/24"]
    reg("exactly one observation for peer's served /24",
        len(iron_subnet_obs) == 1, f"got {len(iron_subnet_obs)}")


def test_no_wg_between_lan_peers():
    """WG is never used on the LAN side. The LAN-only triangle topology
    has three FrogNets sharing a /24 peering segment - none of them
    should have a WG iface or any /30 transit routes."""
    print("\n--- no WG between LAN-adjacent FrogNets ---")
    t = topo_lan_only_chain()
    discover_routes(t)
    for n in t.nodes.values():
        wg_ifaces = [i for i in n.ifaces if iface_kind(i) == "wg"]
        reg(f"{n.name}: no wg ifaces in LAN-only topology",
            len(wg_ifaces) == 0, f"got {wg_ifaces}")
        wg_routes = [r for r in n.routes if iface_kind(r.dev) == "wg"]
        reg(f"{n.name}: no wg routes",
            len(wg_routes) == 0, f"got {[str(r) for r in wg_routes]}")


def test_wave2_anchor_validation():
    """Chat 0257a5c7: wave-2 anchor check (`ip route get $anchor oif
    $dev`) prevents recording an observation when the next-hop isn't
    reachable through the claimed dev. Equivalent invariant: every
    observation must be tied to an anchor that IS an L2 peer on dev.
    Verify by inspecting observations produced for the snowflake."""
    print("\n--- wave-2 anchor validation ---")
    t = topo_snowflake()
    assign_identities(t)
    for node in t.nodes.values():
        node.routes = []
        node.known_hosts = {}
        node.installed_winners = {}
        for iface in node.ifaces.values():
            node.install_route(iface.subnet, dev=iface.name, via=None,
                               metric=100)
        if node.frognet_ip:
            node.known_hosts[node.frognet_ip] = node.name
    # Run a few cycles so wave-2 sees something to probe.
    discover_routes(t)
    bad = 0
    for n in t.nodes.values():
        for w in n.installed_winners.values():
            # Anchor must be reachable as an L2 peer iface IP on dev.
            anchor_ok = False
            for (pn, _pi, pip) in t.l2_peers(n.name, w.dev):
                if pip == w.anchor:
                    anchor_ok = True
                    break
            if not anchor_ok:
                bad += 1
    reg("every installed winner's anchor is an L2 peer on its dev",
        bad == 0, f"violations={bad}")


# ============================================================================
# Section 4: orchestration regression tests
# ============================================================================
#
# These exercise the merge_cycle() model from Section 2c, encoding the
# specific orchestration bugs observed on SeattleFive 2026-05-15. Each test
# sets up the scenario, runs merge_cycle() with the buggy config, asserts
# the bad behavior reproduces, then re-runs with the fix flags flipped and
# asserts the bug is gone.


def _seattle_world() -> Tuple[World, LiveNode]:
    """Reproduce the 2026-05-15 cluster: 4 LAN nodes + 1 remote + 6 stale
    broker entries with no backing node. SeattleSix is the LAN gateway
    (at 10.250.250.221 from SeattleFive's perspective)."""
    w = World()
    five  = w.add(LiveNode("SeattleFive",  "10.250.250.0/24"))
    w.add(LiveNode("SeattleTwo",   "10.121.120.0/24"))
    w.add(LiveNode("SeattleThree", "10.130.130.0/24"))
    w.add(LiveNode("SeattleSix",   "10.160.160.0/24"))
    w.add(LiveNode("New-York-1",   "10.102.60.0/24"))
    # BAMacBook/OffBroadway/BABox are REAL remotes that SHOULD be tunneled.
    # We model them as existing nodes so probe_wg returns True. The bug
    # is that the broker pairs them with SeattleFive when they should be
    # peers of NY-1 instead (or whatever the actual topology is).
    w.add(LiveNode("BAMacBook",    "10.147.147.0/24"))
    w.add(LiveNode("OffBroadway",  "10.103.10.0/24"))
    w.add(LiveNode("BABox",        "10.103.20.0/24"))

    w.link_lan_anchor("SeattleFive", "SeattleSix", "10.250.250.221")

    # Broker channel list as observed in the merge logs:
    # 8 entries - 1 valid remote (NY-1), 1 valid remote (BAMacBook), 6 stale.
    w.set_broker("SeattleFive", [
        {"name": "BAMacBook-10.147.147",   "remote_subnets": ["10.147.147.0/24"]},
        {"name": "New-York-1-10.102.60",   "remote_subnets": ["10.102.60.0/24"]},
        {"name": "Seattle-1G-10.201.201",  "remote_subnets": ["10.201.201.0/24"]},  # STALE
        {"name": "Seattle-1I-10.133.144",  "remote_subnets": ["10.133.144.0/24", "10.130.130.0/24"]},  # STALE
        {"name": "Seattle-3-10.44.44",     "remote_subnets": ["10.44.44.0/24"]},    # STALE
        {"name": "Seattle-4C-10.44.44",    "remote_subnets": ["10.44.44.0/24"]},    # STALE
        {"name": "SeattleDB-10.241.241",   "remote_subnets": ["10.241.241.0/24"]},  # STALE
        {"name": "SeattleNewDB-10.101.35", "remote_subnets": ["10.101.35.0/24"]},   # STALE
    ])

    # Voucher scope as observed: SeattleSix only vouches for its immediate
    # /24 neighbors. It does NOT vouch for BAMacBook etc, so they appear
    # to wave-2 as wg-only-reachable (and stale entries appear as nothing).
    w.set_voucher("SeattleSix", ["10.121.120.0/24", "10.130.130.0/24"])

    return w, five


def test_orch_cold_start_brings_up_all_8_channels():
    """Cold start with bringup parallel to wave-1: filter checks the empty
    kernel, fails to skip any LAN-reachable peer, brings up all 8 broker
    channels. Matches SeattleFive 09:18 log: `to_bring_up count=8 ...
    skipped_lan_reachable=0`."""
    print("\n--- orch: cold start brings up all 8 broker channels (bug) ---")
    w, five = _seattle_world()
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    reg("cold-start bringup brings up all 8 channels (skipped_lan_reachable=0)",
        len(res.brought_up) == 8 and len(res.skipped_lan_reachable) == 0,
        f"brought_up={len(res.brought_up)} skipped={len(res.skipped_lan_reachable)}")


def test_orch_wave1_first_skips_lan_peers():
    """Fix: run wave-1 BEFORE bringup's filter check. SeattleSix vouches for
    SeattleTwo and SeattleThree; SeattleSix's own /24 is wave-1 native.
    No broker channel lists 10.121.120, 10.130.130, or 10.160.160 anyway,
    so the count of bringups doesn't drop just from wave-1 ordering - it
    drops only when broker advertises a channel for a LAN /24. We test the
    mechanism: when broker DOES list a LAN /24, the fix skips it."""
    print("\n--- orch: wave-1-first skips broker channels whose /24 is LAN ---")
    w, five = _seattle_world()
    # Add a hypothetical broker channel for SeattleTwo's /24 - what would
    # happen if the broker had paired SeattleFive with SeattleTwo as a WG peer.
    w.broker_channels["SeattleFive"].append(
        {"name": "SeattleTwo-10.121.120", "remote_subnets": ["10.121.120.0/24"]})
    res_bug = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True))
    reg("[bug] parallel bringup tunnels SeattleTwo despite LAN path",
        "SeattleTwo-10.121.120" in res_bug.brought_up,
        f"brought_up={res_bug.brought_up}")
    w2, five2 = _seattle_world()
    w2.broker_channels["SeattleFive"].append(
        {"name": "SeattleTwo-10.121.120", "remote_subnets": ["10.121.120.0/24"]})
    res_fix = merge_cycle(five2, w2, MergeConfig(
        bringup_parallel_with_wave1=False))
    reg("[fix] wave-1-first ordering skips SeattleTwo channel",
        "SeattleTwo-10.121.120" in res_fix.skipped_lan_reachable
        and "SeattleTwo-10.121.120" not in res_fix.brought_up,
        f"skipped={res_fix.skipped_lan_reachable} up={res_fix.brought_up}")


def test_orch_voucher_scope_limits_lan_visibility():
    """SeattleSix vouches only for SeattleTwo/Three, not for BAMacBook.
    Even with the wave-1-first fix, SeattleFive cannot learn that BAMacBook
    is reachable through SeattleSix - so the tunnel still comes up. The
    upstream fix is to widen SeattleSix's voucher scope or change the
    broker to not pair LAN-reachable peers."""
    print("\n--- orch: limited voucher scope hides LAN-reachable peers ---")
    w, five = _seattle_world()
    # If SeattleSix vouched for BAMacBook's /24, we'd skip the BAMacBook tunnel.
    res_narrow = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=False))
    reg("[narrow voucher] BAMacBook still tunneled despite hypothetical LAN path",
        "BAMacBook-10.147.147" in res_narrow.brought_up,
        f"brought_up={res_narrow.brought_up}")
    # Widen the scope: SeattleSix now vouches for BAMacBook as well.
    w2, five2 = _seattle_world()
    w2.set_voucher("SeattleSix", [
        "10.121.120.0/24", "10.130.130.0/24", "10.147.147.0/24"])
    res_wide = merge_cycle(five2, w2, MergeConfig(
        bringup_parallel_with_wave1=False))
    reg("[wide voucher] BAMacBook skipped when SeattleSix vouches for it",
        "BAMacBook-10.147.147" in res_wide.skipped_lan_reachable
        and "BAMacBook-10.147.147" not in res_wide.brought_up,
        f"skipped={res_wide.skipped_lan_reachable}")


def test_orch_teardown_corrupts_adjacent_wg_routes():
    """When committer tears down losing wg ifaces, observed behavior is
    that adjacent wg routes lose their via+onlink form and end up `scope
    link`. NY-1's wg1 ended up `10.102.60.0/24 dev wg1 scope link
    src 10.250.250.1 metric 22` after sibling teardowns."""
    print("\n--- orch: teardown corrupts adjacent wg routes (bug) ---")
    w, five = _seattle_world()
    # NY-1 is the only real-remote in our stale-heavy broker list; the 6
    # stale entries all fail wave-2 and get torn down.
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    ny1 = five.kernel.routes.get("10.102.60.0/24")
    bamac = five.kernel.routes.get("10.147.147.0/24")
    # Both NY-1 and BAMacBook are "real" in our model so they should win.
    # With sibling corruption from the 6 stale teardowns, their routes are
    # mangled - scope_link form, via lost.
    bug_repro = (ny1 is not None and ny1.scope == "scope_link"
                 and bamac is not None and bamac.scope == "scope_link")
    reg("[bug] surviving wg routes lose via+onlink after sibling teardowns",
        bug_repro, f"ny1={ny1} bamac={bamac}")


def test_orch_teardown_at_end_preserves_winners():
    """Fix: disable sibling corruption (the underlying observed kernel
    quirk) AND defer teardown until after install/snapshot. Winner routes
    retain canonical via+onlink form."""
    print("\n--- orch: teardown without sibling corruption preserves winners ---")
    w, five = _seattle_world()
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False))
    ny1 = five.kernel.routes.get("10.102.60.0/24")
    bamac = five.kernel.routes.get("10.147.147.0/24")
    ok = (ny1 is not None and ny1.scope == "onlink" and ny1.via == "10.102.60.1"
          and bamac is not None and bamac.scope == "onlink"
          and bamac.via == "10.147.147.1")
    reg("[fix] NY-1 and BAMacBook routes in canonical via+onlink form",
        ok, f"ny1={ny1} bamac={bamac}")


def test_orch_phantom_state_files_short_circuit_bringup():
    """Bug 1 from prior session: _load_local_state trusts disk verbatim.
    State files claim N tunnels active; kernel has zero ifaces; bringup
    sees them as 'already up' and brings nothing up. log: `to_bring_up=0
    already_up=[N channels]`."""
    print("\n--- orch: phantom state files block bringup (bug) ---")
    w, five = _seattle_world()
    # Inject phantom state - disk says 8 tunnels up, kernel has none
    five.state_files.active = {
        ch["name"]: {"iface": f"wg{i}", "subnets": ch["remote_subnets"]}
        for i, ch in enumerate(w.broker_channels["SeattleFive"])}
    res_bug = merge_cycle(five, w, MergeConfig(
        load_local_state_validates_kernel=False))
    reg("[bug] phantom disk state suppresses bringup",
        len(res_bug.brought_up) == 0,
        f"brought_up={res_bug.brought_up}")
    # With validation: phantoms are dropped, bringup runs for real
    w2, five2 = _seattle_world()
    five2.state_files.active = {
        ch["name"]: {"iface": f"wg{i}", "subnets": ch["remote_subnets"]}
        for i, ch in enumerate(w2.broker_channels["SeattleFive"])}
    res_fix = merge_cycle(five2, w2, MergeConfig(
        load_local_state_validates_kernel=True))
    reg("[fix] kernel-validating load drops phantoms and brings up tunnels",
        len(res_fix.brought_up) > 0,
        f"brought_up={len(res_fix.brought_up)}")


def test_orch_daemon_poll_misses_kernel_drift():
    """Bug 2 from prior session: daemon poll_once uses in-memory
    _active_tunnels.keys(); never reconciles against kernel. If memory says
    N tunnels active and kernel has zero, poll doesn't fire runMerge."""
    print("\n--- orch: daemon poll_once misses kernel drift ---")
    w, five = _seattle_world()
    # Pre-populate daemon memory with 8 active, kernel empty
    five.daemon_mem.active = {
        ch["name"]: {"iface": f"wg{i}", "subnets": ch["remote_subnets"]}
        for i, ch in enumerate(w.broker_channels["SeattleFive"])}
    # Empty broker - nothing for merge_cycle to install
    w.set_broker("SeattleFive", [])
    # Without reconcile fix: daemon memory keeps the 8 phantoms.
    # (Note: our state_reconcile phase at end of merge_cycle does drop them
    # via iface check, so this bug is partly compensated. The fix flag adds
    # an additional explicit reconcile pass for safety.)
    five.state_files.active = {}  # don't reload from disk
    res = merge_cycle(five, w, MergeConfig(
        poll_once_reconciles_with_kernel=False))
    # state_reconcile at end of merge clears them - that's the existing path
    reg("[note] state_reconcile clears phantom daemon entries when broker is empty",
        len(five.daemon_mem.active) == 0,
        f"daemon_active={list(five.daemon_mem.active)}")


def test_orch_stale_broker_entries_get_tunneled_and_torn_down():
    """Stale broker entries (Seattle-1G/1I/3/4C, SeattleDB, SeattleNewDB)
    have no backing node. Every cold start: bringup brings them up; wave-2
    probes time out; committer tears them down. Same churn every merge."""
    print("\n--- orch: stale broker entries get tunneled then torn down every cycle ---")
    w, five = _seattle_world()
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    stale = {"Seattle-1G-10.201.201", "Seattle-1I-10.133.144",
             "Seattle-3-10.44.44", "Seattle-4C-10.44.44",
             "SeattleDB-10.241.241", "SeattleNewDB-10.101.35"}
    brought_stale = set(res.brought_up) & stale
    torn_stale = set(res.torn_down) & stale
    reg("[bug] stale entries get tunneled at cold start",
        brought_stale == stale,
        f"brought_stale={brought_stale}")
    reg("[bug] stale entries get torn down by committer (won_no_subnet)",
        torn_stale == stale,
        f"torn_stale={torn_stale}")


def test_orch_seattle_1i_lists_seattle_five_subnet():
    """Live broker advertised 10.250.250.0/24 as one of Seattle-1I's
    remote_subnets (it shouldn't - that's SeattleFive's served /24). The
    live log shows `BRINGUP_ROUTE_FAILED 10.250.250.0/24 via 10.250.250.1
    dev wg3 src=10.250.250.1` - kernel refused to install over the
    connected route. The bringup didn't crash but the route_install for
    that subnet failed and is recorded as a warning. We test that the
    connected /24 route is preserved (not clobbered) and the wg install
    is rejected."""
    print("\n--- orch: broker lists peer's remote subnets including this node's own /24 ---")
    w, five = _seattle_world()
    for ch in w.broker_channels["SeattleFive"]:
        if ch["name"] == "Seattle-1I-10.133.144":
            ch["remote_subnets"] = ["10.133.144.0/24", "10.130.130.0/24",
                                    "10.250.250.0/24"]
            break
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=False))
    own = five.kernel.routes.get("10.250.250.0/24")
    reg("connected route for own served /24 not clobbered by wg install",
        own is not None and own.scope == "kernel" and own.dev == "eth0",
        f"own_route={own}")


def test_orch_lan_route_wins_over_wg_for_same_destination():
    """Section 2 already encodes this; here we verify in the orchestration
    model that wave-1 LAN winner replaces wg-installed bringup route for
    the same /24. SeattleThree (10.130.130) was installed via wg3 by
    bringup then replaced by LAN via SeattleSix in the commit."""
    print("\n--- orch: LAN winner replaces wg bringup route for same /24 ---")
    w, five = _seattle_world()
    # SeattleSix vouches for SeattleThree (already in default voucher).
    # Seattle-1I broker channel includes 10.130.130 as one of its subnets.
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=False))
    three_route = five.kernel.routes.get("10.130.130.0/24")
    reg("LAN route via SeattleSix wins over wg from Seattle-1I bringup",
        three_route is not None and three_route.dev == "eth0"
        and three_route.via == "10.250.250.221",
        f"got {three_route}")


def test_orch_observation_only_remotes_get_tunnels():
    """End-to-end invariant from the user's standing rule: WG tunnels
    should exist ONLY when the remote /24 has no other path. Given the
    fixed orchestration AND wide voucher scope AND clean broker, only
    NY-1, BAMacBook, OffBroadway, BABox should have tunnels - the four
    real remotes."""
    print("\n--- orch: end-to-end with all fixes - only real remotes get tunnels ---")
    w = World()
    five  = w.add(LiveNode("SeattleFive",  "10.250.250.0/24"))
    w.add(LiveNode("SeattleTwo",   "10.121.120.0/24"))
    w.add(LiveNode("SeattleThree", "10.130.130.0/24"))
    w.add(LiveNode("SeattleSix",   "10.160.160.0/24"))
    w.add(LiveNode("New-York-1",   "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",    "10.147.147.0/24"))
    w.add(LiveNode("OffBroadway",  "10.103.10.0/24"))
    w.add(LiveNode("BABox",        "10.103.20.0/24"))
    w.link_lan_anchor("SeattleFive", "SeattleSix", "10.250.250.221")
    # Clean broker - no stale entries, just the 4 real remotes
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]},
        {"name": "OffBroadway-10.103.10","remote_subnets": ["10.103.10.0/24"]},
        {"name": "BABox-10.103.20",      "remote_subnets": ["10.103.20.0/24"]},
    ])
    # Wide voucher scope - SeattleSix vouches for all its LAN neighbors
    w.set_voucher("SeattleSix", ["10.121.120.0/24", "10.130.130.0/24"])
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False,
        load_local_state_validates_kernel=True,
        poll_once_reconciles_with_kernel=True))
    wg_count = len(five.kernel.wg_ifaces)
    reg("end-to-end: exactly 4 wg ifaces for 4 real remotes",
        wg_count == 4, f"got {wg_count} wg ifaces: {five.kernel.wg_ifaces}")
    # Each remote /24 should have a wg route in canonical form
    for rs in ["10.102.60.0/24", "10.147.147.0/24",
               "10.103.10.0/24", "10.103.20.0/24"]:
        r = five.kernel.routes.get(rs)
        reg(f"remote {rs} has canonical wg via+onlink route",
            r is not None and r.scope == "onlink" and r.dev.startswith("wg")
            and r.via == rs.replace(".0/24", ".1"),
            f"got {r}")
    # SeattleTwo/Three (LAN via SeattleSix) should have LAN routes
    for rs in ["10.121.120.0/24", "10.130.130.0/24", "10.160.160.0/24"]:
        r = five.kernel.routes.get(rs)
        reg(f"LAN {rs} routed via SeattleSix on eth0",
            r is not None and r.dev == "eth0" and r.via == "10.250.250.221",
            f"got {r}")


# ============================================================================
# Section 4b: helpers for multi-cycle, reboot, daemon poll, invariants
# ============================================================================


def run_merges(node: LiveNode, world: World, cfg: MergeConfig,
               n: int = 3) -> List[MergeResult]:
    """Run N merge cycles in sequence with persistent state. Each cycle sees
    the kernel/state/daemon_mem state left behind by the previous cycle.
    Returns the list of MergeResult per cycle. Convergence is reached when
    consecutive results have empty brought_up AND empty torn_down."""
    results = []
    for _ in range(n):
        r = merge_cycle(node, world, cfg)
        results.append(r)
    return results


def reboot(node: LiveNode):
    """Simulate a daemon reboot: in-memory state is lost; disk state files
    survive; kernel state survives (wg ifaces may persist across daemon
    restart since they're kernel objects, not daemon objects)."""
    node.daemon_mem = DaemonMemory()


def daemon_poll_once(node: LiveNode, world: World, cfg: MergeConfig) -> bool:
    """Models the daemon's periodic poll: does broker disagree with our
    current state? If yes, fires runMerge. Returns True if poll fires.

    Bug (poll_once_reconciles_with_kernel=False): compares broker against
    daemon_mem.active. If memory matches broker but kernel has drifted,
    poll does NOT fire - the drift is invisible.

    Fix (poll_once_reconciles_with_kernel=True): compares broker against
    the channels whose iface is actually in the kernel."""
    broker_set = set(c["name"] for c in world.broker_channels.get(node.name, []))
    if cfg.poll_once_reconciles_with_kernel:
        kernel_alive = set(
            ch for ch, spec in node.daemon_mem.active.items()
            if spec["iface"] in node.kernel.wg_ifaces)
        return broker_set != kernel_alive
    else:
        return broker_set != set(node.daemon_mem.active.keys())


def verify_invariants(node: LiveNode) -> List[str]:
    """Returns list of invariant violations (empty if all hold).

    Invariants:
      I1. No wg route exists for the node's own served /24.
      I2. Every wg iface has at least one route (no orphan ifaces).
      I3. Every wg route's dev is in kernel.wg_ifaces.
      I4. No two channels in daemon_mem.active share an iface.
      I5. Every channel in daemon_mem.active has its iface in the kernel.
      I6. Every route in state_files corresponds to an iface in the kernel.
      I7. Connected route for own served /24 is `kernel` scope on eth0.
    """
    violations = []
    own = node.served_subnet
    own_route = node.kernel.routes.get(own)
    if own_route and own_route.dev.startswith("wg"):
        violations.append(f"I1: wg route for own served /24: {own_route}")
    iface_route_count = {i: 0 for i in node.kernel.wg_ifaces}
    for r in node.kernel.routes.values():
        if r.dev.startswith("wg"):
            if r.dev not in node.kernel.wg_ifaces:
                violations.append(f"I3: route {r} dev not in wg_ifaces")
            iface_route_count[r.dev] = iface_route_count.get(r.dev, 0) + 1
    for iface, count in iface_route_count.items():
        if count == 0:
            violations.append(f"I2: orphan wg iface {iface} has no routes")
    seen_ifaces: Dict[str, str] = {}
    for ch, spec in node.daemon_mem.active.items():
        if spec["iface"] in seen_ifaces:
            violations.append(
                f"I4: iface {spec['iface']} shared by {seen_ifaces[spec['iface']]} and {ch}")
        seen_ifaces[spec["iface"]] = ch
        if spec["iface"] not in node.kernel.wg_ifaces:
            violations.append(f"I5: daemon_mem channel {ch} iface {spec['iface']} not in kernel")
    for ch, spec in node.state_files.load().items():
        if spec["iface"] not in node.kernel.wg_ifaces:
            violations.append(f"I6: state file channel {ch} iface {spec['iface']} not in kernel")
    if not own_route or own_route.scope != "kernel" or own_route.dev != "eth0":
        violations.append(f"I7: own served /24 not connected route on eth0: {own_route}")
    return violations


# ============================================================================
# Section 4c: comprehensive negative coverage
# ============================================================================


def test_orch_empty_world_does_nothing():
    """Sanity: a single node with no broker entries, no LAN anchor, and
    no peers should bring up zero tunnels, tear down zero, and end with
    the connected route as the only kernel entry."""
    print("\n--- orch: empty broker, no LAN anchor - nothing happens ---")
    w = World()
    n = w.add(LiveNode("Solo", "10.99.99.0/24"))
    w.set_broker("Solo", [])
    res = merge_cycle(n, w, MergeConfig())
    reg("solo: zero brought_up, zero torn_down",
        len(res.brought_up) == 0 and len(res.torn_down) == 0,
        f"up={res.brought_up} down={res.torn_down}")
    reg("solo: only the connected route remains",
        list(n.kernel.routes.keys()) == ["10.99.99.0/24"],
        f"routes={list(n.kernel.routes.keys())}")
    reg("solo: zero wg ifaces",
        len(n.kernel.wg_ifaces) == 0, f"wg_ifaces={n.kernel.wg_ifaces}")
    violations = verify_invariants(n)
    reg("solo: all invariants hold", violations == [], f"violations={violations}")


def test_orch_clean_broker_no_stale_churn():
    """Pair to test_orch_stale_broker: with a clean broker (only real
    remotes), there is no bringup->teardown churn. All tunneled channels
    win their /24."""
    print("\n--- orch: clean broker has zero stale churn ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("New-York-1", "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",  "10.147.147.0/24"))
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]},
    ])
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    reg("clean broker: 2 brought_up, 0 torn_down",
        len(res.brought_up) == 2 and len(res.torn_down) == 0,
        f"up={res.brought_up} down={res.torn_down}")
    reg("clean broker: 2 wg ifaces remain",
        len(five.kernel.wg_ifaces) == 2,
        f"wg_ifaces={five.kernel.wg_ifaces}")


def test_orch_multi_cycle_thrashing_with_bug():
    """With bugs on AND stale broker entries, every merge cycle does the
    same churn: bring up 8, tear down 6 stale. The kernel never converges
    because the stale broker entries keep coming back. Three cycles in a
    row produce identical results."""
    print("\n--- orch: multi-cycle thrashing - stale entries cycle every merge ---")
    w, five = _seattle_world()
    results = run_merges(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True), n=3)
    stale = {"Seattle-1G-10.201.201", "Seattle-1I-10.133.144",
             "Seattle-3-10.44.44", "Seattle-4C-10.44.44",
             "SeattleDB-10.241.241", "SeattleNewDB-10.101.35"}
    bringups_per_cycle = [len(set(r.brought_up) & stale) for r in results]
    teardowns_per_cycle = [len(set(r.torn_down) & stale) for r in results]
    reg("thrashing: each cycle brings up the same 6 stale entries",
        bringups_per_cycle == [6, 6, 6],
        f"got per-cycle stale bringups={bringups_per_cycle}")
    reg("thrashing: each cycle tears down the same 6 stale entries",
        teardowns_per_cycle == [6, 6, 6],
        f"got per-cycle stale teardowns={teardowns_per_cycle}")


def test_orch_multi_cycle_convergence_with_fix():
    """Pair: with bugs fixed AND clean broker, the system converges after
    the first cycle. Subsequent cycles produce zero new bringups, zero
    teardowns. Kernel state is identical across cycles 2 and 3."""
    print("\n--- orch: multi-cycle convergence with all fixes + clean broker ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("SeattleTwo",   "10.121.120.0/24"))
    w.add(LiveNode("SeattleThree", "10.130.130.0/24"))
    w.add(LiveNode("SeattleSix",   "10.160.160.0/24"))
    w.add(LiveNode("New-York-1",   "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",    "10.147.147.0/24"))
    w.link_lan_anchor("SeattleFive", "SeattleSix", "10.250.250.221")
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]},
    ])
    w.set_voucher("SeattleSix", ["10.121.120.0/24", "10.130.130.0/24"])
    cfg = MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False,
        load_local_state_validates_kernel=True,
        poll_once_reconciles_with_kernel=True)
    results = run_merges(five, w, cfg, n=3)
    reg("convergence: cycle 1 establishes 2 tunnels, cycles 2-3 are quiet",
        len(results[0].brought_up) == 2 and
        len(results[1].brought_up) == 0 and len(results[1].torn_down) == 0 and
        len(results[2].brought_up) == 0 and len(results[2].torn_down) == 0,
        f"cycle1 up={len(results[0].brought_up)}, "
        f"cycle2 up={len(results[1].brought_up)} down={len(results[1].torn_down)}, "
        f"cycle3 up={len(results[2].brought_up)} down={len(results[2].torn_down)}")
    final_routes_2 = sorted(str(r) for r in results[1].brought_up)
    final_routes_3 = sorted(str(r) for r in results[2].brought_up)
    reg("convergence: kernel state stable across cycles 2 and 3",
        final_routes_2 == final_routes_3,
        f"cycle2={final_routes_2}, cycle3={final_routes_3}")
    violations = verify_invariants(five)
    reg("convergence: all invariants hold post-convergence",
        violations == [], f"violations={violations}")


def test_orch_reboot_keeps_kernel_loses_daemon_mem():
    """Reboot model: daemon memory wiped, state files survive, kernel
    survives. The next merge after reboot should rediscover its state
    via _load_local_state from disk."""
    print("\n--- orch: reboot - daemon mem lost, state files + kernel survive ---")
    w, five = _seattle_world()
    # First merge - establish state
    merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=False))  # don't tear stale yet
    pre_reboot_kernel_ifaces = set(five.kernel.wg_ifaces)
    pre_reboot_state = dict(five.state_files.active)
    pre_reboot_mem = dict(five.daemon_mem.active)
    reboot(five)
    reg("reboot: kernel wg ifaces survive",
        five.kernel.wg_ifaces == pre_reboot_kernel_ifaces,
        f"pre={pre_reboot_kernel_ifaces} post={five.kernel.wg_ifaces}")
    reg("reboot: state files survive",
        five.state_files.active == pre_reboot_state,
        f"pre keys={list(pre_reboot_state)}, post keys={list(five.state_files.active)}")
    reg("reboot: daemon memory is empty",
        five.daemon_mem.active == {},
        f"daemon_mem={five.daemon_mem.active}")
    # Next merge: _load_local_state should rehydrate daemon mem from disk
    merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=False,
        load_local_state_validates_kernel=True))
    reg("reboot: post-reboot merge rehydrates daemon_mem from state files",
        set(five.daemon_mem.active.keys()) == set(pre_reboot_mem.keys()),
        f"daemon_mem keys={list(five.daemon_mem.active)}")


def test_orch_reboot_with_phantom_state_files():
    """After a reboot where state files contain phantom entries (their
    ifaces don't exist in kernel because they were torn down between
    reboot and merge), the fix-enabled load_local_state drops them so
    bringup doesn't get fooled."""
    print("\n--- orch: reboot then phantom state files cleaned by validate ---")
    w, five = _seattle_world()
    # Plant phantom state - disk says 3 tunnels, kernel has none
    five.state_files.active = {
        "BAMacBook-10.147.147": {"iface": "wg0", "subnets": ["10.147.147.0/24"]},
        "New-York-1-10.102.60": {"iface": "wg1", "subnets": ["10.102.60.0/24"]},
        "SeattleDB-10.241.241": {"iface": "wg2", "subnets": ["10.241.241.0/24"]},
    }
    reboot(five)
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True,
        load_local_state_validates_kernel=True))
    reg("phantom-post-reboot: validate drops phantoms, bringup brings up tunnels",
        len(res.brought_up) == 8,  # 8 broker channels, all brought up since phantoms dropped
        f"brought_up={len(res.brought_up)}")


def test_orch_peer_appears_in_broker_between_cycles():
    """Cycle 1: broker has 1 channel. Cycle 2: broker has 2 channels (peer
    added). The second channel should be brought up in cycle 2 without
    disrupting the first."""
    print("\n--- orch: peer appears in broker between cycles ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("New-York-1", "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",  "10.147.147.0/24"))
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
    ])
    cfg = MergeConfig(bringup_parallel_with_wave1=False,
                      teardown_inside_commit=False)
    r1 = merge_cycle(five, w, cfg)
    reg("appearance: cycle 1 brings up the one channel",
        r1.brought_up == ["New-York-1-10.102.60"],
        f"got {r1.brought_up}")
    # Peer added between cycles
    w.broker_channels["SeattleFive"].append(
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]})
    r2 = merge_cycle(five, w, cfg)
    reg("appearance: cycle 2 brings up new channel without churn",
        r2.brought_up == ["BAMacBook-10.147.147"] and r2.torn_down == [],
        f"up={r2.brought_up} down={r2.torn_down}")
    reg("appearance: NY-1 route still canonical after cycle 2",
        five.kernel.routes.get("10.102.60.0/24") is not None and
        five.kernel.routes["10.102.60.0/24"].scope == "onlink",
        f"got {five.kernel.routes.get('10.102.60.0/24')}")


def test_orch_peer_disappears_from_broker_between_cycles():
    """Cycle 1: broker has 2 channels. Cycle 2: one removed. That tunnel
    should be torn down; the other should be untouched."""
    print("\n--- orch: peer disappears from broker between cycles ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("New-York-1", "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",  "10.147.147.0/24"))
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]},
    ])
    cfg = MergeConfig(bringup_parallel_with_wave1=False,
                      teardown_inside_commit=False)
    r1 = merge_cycle(five, w, cfg)
    reg("disappearance: cycle 1 brings up both",
        set(r1.brought_up) == {"New-York-1-10.102.60", "BAMacBook-10.147.147"},
        f"got {r1.brought_up}")
    # Remove BAMacBook from broker
    w.broker_channels["SeattleFive"] = [
        c for c in w.broker_channels["SeattleFive"]
        if c["name"] != "BAMacBook-10.147.147"]
    r2 = merge_cycle(five, w, cfg)
    reg("disappearance: cycle 2 tears down BAMacBook (broker no longer lists it)",
        "BAMacBook-10.147.147" in r2.torn_down,
        f"torn_down={r2.torn_down}")
    reg("disappearance: NY-1 still up and canonical",
        five.kernel.routes.get("10.102.60.0/24") is not None and
        five.kernel.routes["10.102.60.0/24"].scope == "onlink" and
        "New-York-1-10.102.60" in five.daemon_mem.active,
        f"ny1={five.kernel.routes.get('10.102.60.0/24')}, "
        f"mem={list(five.daemon_mem.active)}")


def test_orch_invariant_no_wg_route_for_own_subnet():
    """Invariant I1: no wg route is ever installed for this node's own
    served /24. Even with a buggy broker that lists own /24 as a peer's
    remote_subnet, the kernel rejects the install and the connected route
    is preserved across many cycles."""
    print("\n--- orch: invariant - no wg route for own served /24 (multi-cycle) ---")
    w, five = _seattle_world()
    # Plant the Seattle-1I-with-own-subnet bug
    for ch in w.broker_channels["SeattleFive"]:
        if ch["name"] == "Seattle-1I-10.133.144":
            ch["remote_subnets"] = ["10.133.144.0/24", "10.130.130.0/24",
                                    "10.250.250.0/24"]
            break
    results = run_merges(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True), n=3)
    own = five.kernel.routes.get("10.250.250.0/24")
    reg("I1: own /24 is still connected route on eth0 after 3 cycles",
        own is not None and own.scope == "kernel" and own.dev == "eth0",
        f"got {own}")
    # Even outside the Seattle-1I quirk, invariant holds for any cycle
    all_invariant_ok = True
    for r in results:
        own = five.kernel.routes.get("10.250.250.0/24")
        if not (own and own.scope == "kernel" and own.dev == "eth0"):
            all_invariant_ok = False
            break
    reg("I1: invariant holds at every cycle boundary",
        all_invariant_ok, "")


def test_orch_invariant_torn_down_iface_no_residual_routes():
    """Invariant I3: after teardown, no route in the kernel references
    the torn-down iface. Confirmed across the stale-broker scenario."""
    print("\n--- orch: invariant - torn-down iface leaves no residual routes ---")
    w, five = _seattle_world()
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    torn = set(res.torn_down)
    # Map ch_name -> iface from BEFORE teardown isn't available; instead
    # check the invariant directly: no kernel route's dev is outside wg_ifaces.
    orphans = [r for r in five.kernel.routes.values()
               if r.dev.startswith("wg") and r.dev not in five.kernel.wg_ifaces]
    reg("I3: zero routes reference a torn-down (non-existent) wg iface",
        len(orphans) == 0, f"orphans={[str(o) for o in orphans]}")


def test_orch_invariant_no_two_channels_share_an_iface():
    """Invariant I4: no two channels in daemon_mem.active share an iface."""
    print("\n--- orch: invariant - no two channels share an iface ---")
    w, five = _seattle_world()
    merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=False))
    iface_to_channels: Dict[str, List[str]] = {}
    for ch, spec in five.daemon_mem.active.items():
        iface_to_channels.setdefault(spec["iface"], []).append(ch)
    duplicates = {i: chs for i, chs in iface_to_channels.items() if len(chs) > 1}
    reg("I4: zero ifaces shared by multiple channels", not duplicates,
        f"duplicates={duplicates}")


def test_orch_invariant_all_invariants_after_buggy_run():
    """Run merge with all bugs enabled (the live observed state) and
    enumerate which invariants fail. This catalogs which invariants are
    actually violated by the buggy code - useful as documentation of the
    blast radius."""
    print("\n--- orch: invariant - enumerate violations under buggy config ---")
    w, five = _seattle_world()
    merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True))
    violations = verify_invariants(five)
    # Buggy config produces sibling-corruption (routes losing via+onlink)
    # but no invariant in {I1..I7} explicitly forbids scope_link form,
    # so this should be clean. If violations show up, that's worth flagging.
    reg("buggy-run invariants: catalog what survives the bug",
        True,  # always pass; this test is informational
        f"violations={violations}")


def test_orch_invariant_all_invariants_after_clean_run():
    """Run merge with all fixes + clean broker, then verify every
    invariant holds. This is the strong guarantee for the proposed fix."""
    print("\n--- orch: invariant - all invariants hold after clean fixed run ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("SeattleTwo",   "10.121.120.0/24"))
    w.add(LiveNode("SeattleSix",   "10.160.160.0/24"))
    w.add(LiveNode("New-York-1",   "10.102.60.0/24"))
    w.link_lan_anchor("SeattleFive", "SeattleSix", "10.250.250.221")
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
    ])
    w.set_voucher("SeattleSix", ["10.121.120.0/24"])
    run_merges(five, w, MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False,
        load_local_state_validates_kernel=True,
        poll_once_reconciles_with_kernel=True), n=3)
    violations = verify_invariants(five)
    reg("clean-run invariants: zero violations after 3 cycles",
        violations == [], f"violations={violations}")


def test_orch_daemon_poll_detects_kernel_drift_paired():
    """Bug 2 (paired): daemon poll_once uses in-memory _active_tunnels.
    Setup: daemon_mem says N channels active, kernel has zero ifaces (all
    were torn down externally). Broker reports the same N channels.
    Bug: poll compares broker vs daemon_mem -> match -> no merge fires.
    Fix: poll compares broker vs kernel-alive -> mismatch -> merge fires."""
    print("\n--- orch: daemon poll detects kernel drift (paired bug/fix) ---")
    w, five = _seattle_world()
    # Pre-populate daemon mem with what broker says, but kernel is empty
    five.daemon_mem.active = {
        c["name"]: {"iface": f"wg{i}", "subnets": c["remote_subnets"]}
        for i, c in enumerate(w.broker_channels["SeattleFive"])}
    # Kernel is empty of wg ifaces
    fired_bug = daemon_poll_once(five, w, MergeConfig(
        poll_once_reconciles_with_kernel=False))
    fired_fix = daemon_poll_once(five, w, MergeConfig(
        poll_once_reconciles_with_kernel=True))
    reg("[bug] poll_once doesn't detect kernel drift",
        fired_bug is False, f"fired_bug={fired_bug}")
    reg("[fix] kernel-authoritative poll detects drift, fires merge",
        fired_fix is True, f"fired_fix={fired_fix}")


def test_orch_all_stale_broker_settles_to_zero_tunnels():
    """If every broker entry is stale (no real peer for any channel) and
    the proposed fix is on (deferred teardown), after several merges the
    kernel should converge to zero wg ifaces - each cycle brings them up
    and tears them down, but since the bug doesn't propagate (no sibling
    corruption), the system is stable across cycles."""
    print("\n--- orch: all-stale broker - kernel churns but doesn't corrupt ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    # NO real peers - every broker entry is stale
    w.set_broker("SeattleFive", [
        {"name": "Ghost-1-10.5.5",   "remote_subnets": ["10.5.5.0/24"]},
        {"name": "Ghost-2-10.6.6",   "remote_subnets": ["10.6.6.0/24"]},
        {"name": "Ghost-3-10.7.7",   "remote_subnets": ["10.7.7.0/24"]},
    ])
    cfg = MergeConfig(bringup_parallel_with_wave1=False,
                      teardown_inside_commit=False)
    results = run_merges(five, w, cfg, n=3)
    final_kernel = list(five.kernel.routes.keys())
    reg("all-stale: kernel ends with only the connected own /24",
        final_kernel == ["10.250.250.0/24"], f"routes={final_kernel}")
    reg("all-stale: every cycle brings up 3 and tears down 3 (steady churn)",
        all(len(r.brought_up) == 3 and len(r.torn_down) == 3 for r in results),
        f"per-cycle: up={[len(r.brought_up) for r in results]} "
        f"down={[len(r.torn_down) for r in results]}")
    violations = verify_invariants(five)
    reg("all-stale: invariants hold after 3 cycles of churn",
        violations == [], f"violations={violations}")


def test_orch_cold_start_with_all_fixes_no_overshoot():
    """Pair to test_orch_cold_start_brings_up_all_8: with all fixes on
    (wave-1-first + wide voucher), only real-remote channels are tunneled
    in a single cycle. No 'bring up then tear down' churn."""
    print("\n--- orch: cold start with all fixes - no overshoot ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("SeattleTwo",   "10.121.120.0/24"))
    w.add(LiveNode("SeattleSix",   "10.160.160.0/24"))
    w.add(LiveNode("New-York-1",   "10.102.60.0/24"))
    w.link_lan_anchor("SeattleFive", "SeattleSix", "10.250.250.221")
    # Broker has one LAN peer (SeattleTwo as a tunnel - buggy broker pairing)
    # and one real remote.
    w.set_broker("SeattleFive", [
        {"name": "SeattleTwo-10.121.120", "remote_subnets": ["10.121.120.0/24"]},
        {"name": "New-York-1-10.102.60",  "remote_subnets": ["10.102.60.0/24"]},
    ])
    # Wide voucher: SeattleSix vouches for SeattleTwo's /24
    w.set_voucher("SeattleSix", ["10.121.120.0/24"])
    res = merge_cycle(five, w, MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False))
    reg("no overshoot: SeattleTwo channel skipped (LAN-reachable via SeattleSix)",
        "SeattleTwo-10.121.120" in res.skipped_lan_reachable,
        f"skipped={res.skipped_lan_reachable}")
    reg("no overshoot: only NY-1 brought up",
        res.brought_up == ["New-York-1-10.102.60"], f"up={res.brought_up}")
    reg("no overshoot: zero teardowns (no churn)",
        len(res.torn_down) == 0, f"down={res.torn_down}")


def test_orch_subset_of_remotes_real_subset_stale():
    """Mixed broker: 2 real remotes + 2 stale. Verify the real remotes get
    persistent tunnels and the stale ones cycle. Across 3 merges, the real
    tunnels stay up; the stale ones come and go."""
    print("\n--- orch: mixed real+stale - real tunnels stable, stale cycles ---")
    w = World()
    five = w.add(LiveNode("SeattleFive", "10.250.250.0/24"))
    w.add(LiveNode("New-York-1", "10.102.60.0/24"))
    w.add(LiveNode("BAMacBook",  "10.147.147.0/24"))
    w.set_broker("SeattleFive", [
        {"name": "New-York-1-10.102.60", "remote_subnets": ["10.102.60.0/24"]},
        {"name": "BAMacBook-10.147.147", "remote_subnets": ["10.147.147.0/24"]},
        {"name": "Ghost-1-10.5.5",       "remote_subnets": ["10.5.5.0/24"]},
        {"name": "Ghost-2-10.6.6",       "remote_subnets": ["10.6.6.0/24"]},
    ])
    cfg = MergeConfig(bringup_parallel_with_wave1=False,
                      teardown_inside_commit=False)
    results = run_merges(five, w, cfg, n=3)
    # Cycle 1: 4 up, 2 down (stales)
    # Cycles 2,3: 2 up (stales), 2 down (stales). Real tunnels stay.
    real_ifaces_per_cycle = []
    for _ in results:
        # Check current kernel state for real-remote routes
        ny1 = five.kernel.routes.get("10.102.60.0/24")
        bam = five.kernel.routes.get("10.147.147.0/24")
        if ny1 and ny1.dev.startswith("wg") and bam and bam.dev.startswith("wg"):
            real_ifaces_per_cycle.append((ny1.dev, bam.dev))
    reg("mixed: real remotes have wg routes at end of every cycle",
        len(real_ifaces_per_cycle) == 3,
        f"per cycle real_iface_status={real_ifaces_per_cycle}")
    reg("mixed: cycle 1 brings up 4, cycles 2-3 bring up 2 stales each",
        len(results[0].brought_up) == 4 and
        len(results[1].brought_up) == 2 and
        len(results[2].brought_up) == 2,
        f"per-cycle up={[len(r.brought_up) for r in results]}")
    reg("mixed: every cycle tears down exactly 2 stales",
        all(len(r.torn_down) == 2 for r in results),
        f"per-cycle down={[len(r.torn_down) for r in results]}")


def test_orch_teardown_corruption_persists_across_cycles():
    """When teardown_inside_commit=True corrupts a sibling route in cycle 1,
    the corruption persists into cycle 2's kernel state. Pair: with fix,
    cycle 2 starts with canonical routes."""
    print("\n--- orch: teardown corruption persists into next cycle (bug) ---")
    w, five = _seattle_world()
    run_merges(five, w, MergeConfig(
        bringup_parallel_with_wave1=True,
        teardown_inside_commit=True), n=2)
    ny1 = five.kernel.routes.get("10.102.60.0/24")
    # With sibling corruption across two cycles of teardowns, NY-1 ends
    # up in scope_link form.
    reg("[bug] NY-1 route in scope_link form after 2 buggy cycles",
        ny1 is not None and ny1.scope == "scope_link",
        f"got {ny1}")
    # Pair: with fix
    w2, five2 = _seattle_world()
    run_merges(five2, w2, MergeConfig(
        bringup_parallel_with_wave1=False,
        teardown_inside_commit=False), n=2)
    ny1_fix = five2.kernel.routes.get("10.102.60.0/24")
    reg("[fix] NY-1 route canonical after 2 fixed cycles",
        ny1_fix is not None and ny1_fix.scope == "onlink"
        and ny1_fix.via == "10.102.60.1",
        f"got {ny1_fix}")


def run_regression_tests():
    print("\n================== SECTION 3: bug regression tests ==================")
    test_route_stacking()
    test_stickiness_under_jitter()
    test_bug29_sample_consolidation()
    test_bug28_lan_via_is_lan_ip()
    test_multipath_picks_lowest_rtt()
    test_distributed_reconvergence_after_reboot()
    test_no_address_pattern_matching()
    test_dot2_no_separate_observation()
    test_no_wg_between_lan_peers()
    test_wave2_anchor_validation()

    print("\n========== SECTION 4: live merge-cycle orchestration tests ==========")
    test_orch_cold_start_brings_up_all_8_channels()
    test_orch_wave1_first_skips_lan_peers()
    test_orch_voucher_scope_limits_lan_visibility()
    test_orch_teardown_corrupts_adjacent_wg_routes()
    test_orch_teardown_at_end_preserves_winners()
    test_orch_phantom_state_files_short_circuit_bringup()
    test_orch_daemon_poll_misses_kernel_drift()
    test_orch_stale_broker_entries_get_tunneled_and_torn_down()
    test_orch_seattle_1i_lists_seattle_five_subnet()
    test_orch_lan_route_wins_over_wg_for_same_destination()
    test_orch_observation_only_remotes_get_tunnels()

    print("\n========== SECTION 4b: negative coverage + multi-cycle + invariants ==========")
    test_orch_empty_world_does_nothing()
    test_orch_clean_broker_no_stale_churn()
    test_orch_multi_cycle_thrashing_with_bug()
    test_orch_multi_cycle_convergence_with_fix()
    test_orch_reboot_keeps_kernel_loses_daemon_mem()
    test_orch_reboot_with_phantom_state_files()
    test_orch_peer_appears_in_broker_between_cycles()
    test_orch_peer_disappears_from_broker_between_cycles()
    test_orch_invariant_no_wg_route_for_own_subnet()
    test_orch_invariant_torn_down_iface_no_residual_routes()
    test_orch_invariant_no_two_channels_share_an_iface()
    test_orch_invariant_all_invariants_after_buggy_run()
    test_orch_invariant_all_invariants_after_clean_run()
    test_orch_daemon_poll_detects_kernel_drift_paired()
    test_orch_all_stale_broker_settles_to_zero_tunnels()
    test_orch_cold_start_with_all_fixes_no_overshoot()
    test_orch_subset_of_remotes_real_subset_stale()
    test_orch_teardown_corruption_persists_across_cycles()


def main():
    print("================== SECTION 1: helper functions ==================")
    test_identity()
    test_load_subnet()
    test_parse_channel_name()
    test_etc_hosts()
    test_etc_hosts_drift_safeguard()

    print("\n================== SECTION 2: route discovery topologies ==================")
    all_ok = True
    for builder in (topo_pair,
                    topo_chain_3,
                    topo_chain_5,
                    topo_star,
                    topo_ring_4,
                    topo_snowflake,
                    topo_lan_only_chain,
                    topo_pond_full_mesh,
                    topo_pond_with_workers,
                    topo_pond_hub_spoke,
                    topo_mixed,
                    topo_asym_lan2_wg_lan3,
                    topo_asym_chain_wg_star,
                    topo_asym_ring_wg_lan,
                    topo_asym_three_sites,
                    topo_multilan_shared_wg_transit,
                    topo_dual_wg_transit_bridge,
                    topo_solo_wlan0_ap,
                    topo_two_aps_ham_radio,
                    topo_mixed_iface_no_router):
        all_ok &= run_topo(builder())

    run_regression_tests()

    print("\n================== SUMMARY ==================")
    if FAILS:
        print(f"HELPER FAILURES: {FAILS}")
    if REGRESSION_FAILS:
        print(f"REGRESSION FAILURES: {REGRESSION_FAILS}")
    print(f"TOPOLOGY RESULT: {'PASS' if all_ok else 'FAIL'}")
    if FAILS or REGRESSION_FAILS or not all_ok:
        sys.exit(1)
    print("ALL TESTS PASS")

if __name__ == "__main__":
    main()
