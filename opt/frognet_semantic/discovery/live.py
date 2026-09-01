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
live.py - production entry point. Wires the REAL backends into the proven
discovery/orchestration and runs one merge pass on the box.

This is the integration glue that makes the port deployable. It is UNVALIDATED in
this environment (no hardware / network disabled); every gatherer mirrors the
exact bash source command and is flagged for on-box verification. The decision
logic it drives is the same code proven byte-exact against the oracle in the sim.

Usage on a node:  python3 -m discovery.live
"""
from __future__ import annotations

import json
import os
import subprocess

from .kernel import RealKernel
from .routes import Routes
from .discovery import Discovery
from .propagate import Propagator, propagate_notification
from .real_backends import RealEcho, RealRtt, RealGetHosts, RealBroker, RealPing, RealReflect, RealVerify
from .sources import HostStore
from .mapinterfaces import RealInterfaceMap, classify
from .fixdefault import FixDefaultRoute
from .runmerge import run_merge

IP = "/usr/sbin/ip"


def _ensure_bundle_on_path() -> None:
    """[BUNDLE_PATH_V1] The merge-end election hooks (reach_plane tuple write +
    service-host election) live in the Communicator bundle, installed at
    /etc/frognet_bundles/communicator. runMerge launches discovery with
    PYTHONPATH=/opt/frognet_semantic ONLY, so `import frognet_tuples` /
    `frognet_service_hosts` fail there (`No module named ...`) and both hooks
    silently no-op. Add the bundle dir to sys.path so they resolve. core.* still
    comes from /opt/frognet_semantic, already on the path. Order: FROGNET_BUNDLE_DIR
    env override, the canonical install path, then a repo-relative path for in-tree
    and sim runs. Idempotent; silent when the dir is absent (hooks degrade as before).
    """
    import sys as _sys
    import os as _os
    cands = []
    env = _os.environ.get("FROGNET_BUNDLE_DIR")
    if env:
        cands.append(env)
    cands.append("/etc/frognet_bundles/communicator")
    here = _os.path.dirname(_os.path.abspath(__file__))
    repo = _os.path.abspath(_os.path.join(here, "..", "..", ".."))
    cands.append(_os.path.join(repo, "etc", "frognet_bundles", "communicator"))
    for d in cands:
        if d and _os.path.isdir(d) and d not in _sys.path:
            _sys.path.insert(0, d)



# [SENTINEL_DIR_HONOURED_V1] Every sentinel path goes through here.
#
# run_discovery_oracles.py:59 gives each oracle its own FROGNET_SENTINEL_DIR
# (and simulation/run_all.py:39 does the same for the whole suite) so a test run
# cannot read or write the box's real sentinels.  live.py hardcoded
# "/etc/sentinels" in twelve places and ignored it, with two consequences:
#
#   writes -- the oracle redirects, live.py writes to /etc/sentinels anyway, the
#             oracle looks in its temp dir and finds nothing.  That is
#             test_boot_gate_oracle's "_mark_discovered does not write the
#             discovered sentinel".
#   reads  -- frognet_hosts / frognet_neighbor_via / capability tuples come from
#             the LIVE node, so a fixture-driven oracle silently validates
#             against this machine's real mesh.  That is why
#             test_child_onlink_uplink_oracle saw 10.130.130.1 and why
#             test_service_election_oracle's "no capability" case came back with
#             a real cpu_bench off this box.
#
# Read the env var per call, not once at import: oracles set it after importing.
def _sent(name=""):
    import os as _o
    d = _o.environ.get("FROGNET_SENTINEL_DIR", "/etc/sentinels")
    return _o.path.join(d, name) if name else d


def _run(*argv):
    return subprocess.run(list(argv), capture_output=True, text=True).stdout


def _local_ips() -> set:
    out = set(["127.0.0.1"])
    for ln in _run(IP, "-4", "-o", "addr", "show").splitlines():
        p = ln.split()
        if "inet" in p:
            out.add(p[p.index("inet") + 1].split("/")[0])
    return out


def _leases() -> list:
    path = "/var/lib/misc/dnsmasq.leases"
    if not os.path.isfile(path):
        return []
    ips = []
    for ln in open(path):
        f = ln.split()
        if len(f) >= 3 and f[2].startswith("10."):
            ips.append(f[2])
    return sorted(set(ips))


def _active_states():
    d = "/var/lib/frognet-tunnel/active"
    states = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return states
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            j = json.load(open(os.path.join(d, n)))
        except (OSError, ValueError):
            continue
        iface = j.get("interface", "")
        subs = j.get("remote_subnets", []) or []
        if iface:
            states.append((iface, subs))
    return states


def _wg_kernel_nets(devs):
    nets = {}
    for dev in devs:
        if not dev.startswith("wg"):
            continue
        out = []
        for ln in _run(IP, "-4", "-o", "route", "show", "dev", dev, "scope", "link").splitlines():
            cidr = ln.split()[0]
            if cidr.endswith("/24"):
                out.append(cidr)
        nets[dev] = out
    return nets


def _arp_neigh(devs):
    neigh = {}
    for dev in devs:
        if dev.startswith("wg") or dev in ("frognet0", "lo"):
            continue
        ips = []
        for ln in _run(IP, "-4", "neigh", "show", "dev", dev).splitlines():
            p = ln.split()
            if p and p[0].startswith("10.") and "FAILED" not in ln:
                ips.append(p[0])
        neigh[dev] = ips
    return neigh


# ---- fixDefaultRoute + manageResolv gatherers (Real) ----------------------

def _up_devs():
    """Devices in kernel UP/UNKNOWN state (matches bash dev_is_up)."""
    out = set()
    for ln in _run(IP, "-o", "link", "show").splitlines():
        parts = ln.split(":", 2)
        if len(parts) >= 2 and ("state UP" in ln or "state UNKNOWN" in ln):
            out.add(parts[1].strip().split("@")[0])
    return out


def _connected_prefixes(devs):
    """{dev: {'a.b.c', ...}} - /24 prefixes of each dev's IPv4 addrs."""
    out = {}
    for dev in devs:
        pfx = set()
        for ln in _run(IP, "-4", "-o", "addr", "show", "dev", dev).splitlines():
            p = ln.split()
            if "inet" in p:
                ip = p[p.index("inet") + 1].split("/")[0]
                pfx.add(".".join(ip.split(".")[:3]))
        out[dev] = pfx
    return out


def _route_get(host):
    """`ip route get host` -> (dev, nh). nh = via if present else host."""
    raw = _run(IP, "route", "get", host).splitlines()
    if not raw:
        return None
    p = raw[0].split()
    dev = p[p.index("dev") + 1] if "dev" in p else ""
    nh = p[p.index("via") + 1] if "via" in p else host
    return (dev, nh) if dev else None


def _get_default_route(host, timeout=15):
    """curl getDefaultRoute.php through the local proxy (Host header = peer),
    so hop-kind classification is honored. Returns parsed JSON dict or None."""
    r = subprocess.run(
        ["/usr/bin/curl", "-fsS", "--max-time", str(timeout),
         "-H", f"Host: {host}", "http://127.0.0.1/getDefaultRoute.php"],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _known_hosts():
    """10.x peers from sentinels/frognet_hosts if present, else /etc/hosts."""
    src = _sent("frognet_hosts")
    if not (os.path.isfile(src) and os.path.getsize(src) > 0):
        src = "/etc/hosts"
    out = set()
    try:
        for ln in open(src):
            f = ln.split()
            if f and f[0].startswith("10."):
                out.add(f[0])
    except OSError:
        return []
    return sorted(out)


def _semantic_cidrs():
    """/24 CIDRs listed in /etc/frognet/semantic_hosts (col 1)."""
    path = "/etc/frognet/semantic_hosts"
    out = set()
    try:
        for ln in open(path):
            ln = ln.split("#", 1)[0].strip()
            if ln:
                out.add(ln.split()[0])
    except OSError:
        pass
    return out


def _forced_line():
    """(gw, dev|'') from /etc/frognet/forced_default_route, or None."""
    path = "/etc/frognet/forced_default_route"
    try:
        line = open(path).readline().split("#", 1)[0].strip()
    except OSError:
        return None
    if not line:
        return None
    parts = line.split()
    import re
    if not re.match(r"^(\d+\.){3}\d+$", parts[0]):
        return None
    return (parts[0], parts[1] if len(parts) > 1 else "")


def _write_exit_sentinel(text):
    os.makedirs(_sent(), exist_ok=True)
    with open(_sent("exit_host.tsv"), "w") as f:
        f.write(text)


def _local_domain():
    try:
        for ln in open("/etc/dnsmasq.d/opts_only.conf"):
            if ln.startswith("domain="):
                d = ln.strip().split("=", 1)[1]
                if d:
                    return d
    except OSError:
        pass
    hn = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    return hn.split(".", 1)[1] if "." in hn else hn


def _wan_ns_from_exit_sentinel():
    """field 5 (csv) of exit_host.tsv -> [ns, ...]; empty if absent."""
    path = _sent("exit_host.tsv")
    try:
        if os.path.getsize(path) == 0:
            return []
        line = open(path).readline().rstrip("\n")
    except OSError:
        return []
    cols = line.split("\t")
    if len(cols) >= 5 and cols[4].strip():
        return [x for x in cols[4].split(",") if x]
    return []


def _tunnel_peer_resolvers(local_ips=()):
    """The machine at the far end of every directly-connected WG tunnel.

    Same contract as manageResolv.bash [PEER_PRIMARY_IS_A_RESOLVER_V1], reading
    the same sentinel, so the two paths cannot disagree:

        # dev<TAB>primary_ip<TAB>name -- written by the merge
        wg2<TAB>10.250.250.1<TAB>Seattle5

    Field 2 as written. Nothing here re-derives it from the kernel table:
    AllowedIPs is 10.0.0.0/8 on every FrogNet tunnel and names no pond, and an
    interface's several /24 routes are mostly TRANSIT rather than the pond AT
    THE END of it. Both measured on live nodes 2026-08-12
    ([TUNNEL_PEER_IS_KNOWN_AT_WALK_TIME_V1]).

    No fallback when the sentinel is absent: the merge has not run and this node
    has no peers to name yet. Never ourselves -- 127.0.0.1 already covers
    own-pond names and a duplicate burns a timeout slot.
    """
    self_nets = {".".join(ip.split(".")[:3]) + ".1" for ip in local_ips
                 if ip.count(".") == 3}
    out = []
    try:
        rows = open(_sent("tunnel_peers.tsv")).read().splitlines()
    except OSError:
        return out
    for ln in rows:
        f = ln.split("\t")
        if len(f) < 2 or f[0].lstrip().startswith("#"):
            continue
        peer = f[1].strip()
        if not peer or peer.startswith("127.") or peer in self_nets:
            continue
        if peer not in out:
            out.append(peer)
    return out


def _lan_neighbor_resolvers(local_ips=()):
    """The FrogNet .1 of every node sitting directly on one of our LAN segments.

    [LAN_NEIGHBOR_IS_A_RESOLVER_V1] _tunnel_peer_resolvers covers the machine at
    the far end of a wg tunnel. It does NOT cover a FrogNet that reached us over
    eth0/wlan by taking a DHCP lease on our segment -- there is no tunnel, so
    tunnel_peers.tsv never names it, and that node's pond was unresolvable from
    here. Measured on Seattle3, 2026-08-13: `nslookup brokerhost.seattle2`
    returned SERVFAIL while the identical query run ON Seattle2 answered
    10.120.120.63.

    Reads the sentinel the merge already writes for exactly this join --
    /etc/sentinels/frognet_neighbor_via, [NEIGHBOR_FROGNET_ADDR_V1],
    written at the tail of this same module:

        # <their DHCP lease on our segment> <their FrogNet .1>
        10.250.250.221 10.160.160.1

    Field 2, as written. The VALUE is the routable identity that its dnsmasq is
    bound to and that answers for its pond; the KEY is only a lease on our wire
    and answers for nothing. Nothing here re-derives the pair from ARP or the
    kernel table: the merge learned it from the neighbour's own echo line, which
    is the only place the lease-to-identity mapping exists.

    No fallback when the sentinel is absent - the merge has not run and this node
    has no LAN neighbours to name yet. Never ourselves: 127.0.0.1 and
    [SELF_IP_IS_A_RESOLVER_V1] already cover own-pond names and a duplicate burns
    a timeout slot.
    """
    self_nets = {".".join(ip.split(".")[:3]) + ".1" for ip in local_ips
                 if ip.count(".") == 3}
    out = []
    try:
        rows = open(_sent("frognet_neighbor_via")).read().splitlines()
    except OSError:
        return out
    for ln in rows:
        if ln.lstrip().startswith("#"):
            continue
        f = ln.split()
        if len(f) < 2:
            continue
        dot1 = f[1].strip()
        if not dot1 or dot1.startswith("127.") or dot1 in self_nets:
            continue
        if dot1 not in out:
            out.append(dot1)
    return out


def _default_gateway(k):
    """The via of the default route, or "" - the one resolver whose
    reachability the route itself implies."""
    for line in k.show_default():
        parts = line.split()
        if "via" in parts:
            via = parts[parts.index("via") + 1]
            if via and via.count(".") == 3 and not via.startswith("127."):
                return via
    return ""


def _write_dnsmasq_upstream(k, logger=print):
    """Write /etc/sentinels/dnsmasq_upstream.conf - dnsmasq's ONLY upstream
    (it is configured resolv-file=<this>, no-resolv unset).

    This stage was in manageResolv.bash and did not come across in the python
    cutover, so on the live path nothing maintained the file: dnsmasq had no
    upstream, and since resolv.conf points every local client at 127.0.0.1,
    nothing external resolved.

    Next hop is field 2 of exit_host.tsv (the gateway the default route points
    at, per [DNS_NEXTHOP_ONLY_V1]); with no sentinel yet, the default route's
    gateway directly, which is the same address. Never 127.x - that is a loop.
    Empty when there is no default route, which is the honest state for a node
    that has not merged.
    """
    path = _sent("dnsmasq_upstream.conf")
    next_hop = ""
    try:
        if os.path.getsize(_sent("exit_host.tsv")) > 0:
            cols = open(_sent("exit_host.tsv")).readline().rstrip("\n").split("\t")
            if len(cols) >= 2:
                next_hop = cols[1].strip()
    except OSError:
        pass
    if not next_hop:
        next_hop = _default_gateway(k)
    body = ""
    if next_hop and not next_hop.startswith("127."):
        body = f"nameserver {next_hop}\n"
    if _write_if_changed(path, body, logger):
        logger(f"DNSMASQ_UPSTREAM next_hop={next_hop or '<none>'}")
        subprocess.run(["pkill", "-HUP", "-x", "dnsmasq"], check=False)


def _run_fixdefault_and_resolv(k, devs, dev_ip, local_ips, logger=print,
                               self_identity=""):
    """fixDefaultRoute (Mode A + B + forced) then manageResolv, over the real
    kernel. This is the merge tail bash did and live.main was missing."""
    from .fixdefault import FixDefaultRoute
    from .resolv import build_resolv, wan_ns_from_default
    fdr = FixDefaultRoute(
        k,
        dev_ip4={d: dev_ip.get(d, "") for d in devs},
        up_devs=_up_devs(),
        connected_prefixes=_connected_prefixes(devs),
        ping=RealPing(),
        frognet_interfaces=devs,
        local_ips=local_ips,
        forced_line=_forced_line(),
        logger=logger,
        route_get=_route_get,
        get_default_route=_get_default_route,
        known_hosts=_known_hosts,
        semantic_cidrs=_semantic_cidrs(),
        write_exit_sentinel=_write_exit_sentinel,
        self_identity=self_identity,
    )
    fdr.run()
    # manageResolv: 127.0.0.1 + WAN ns.
    #
    # [DEFAULT_ROUTE_IS_ALWAYS_A_RESOLVER_V1] The default route's gateway and
    # every directly-connected tunnel peer are ALWAYS nameservers. This was an
    # `or`: with exit_host.tsv present the fallback never ran, so a node whose
    # sentinel named an unreachable resolver had no working WAN nameserver at
    # all despite a good default route. exit_host entries still come first.
    # Order matters: resolvers that can answer FrogNet names come FIRST, and
    # the default route's gateway goes LAST. It is the one that knows nothing
    # about any pond -- put it ahead of a tunnel peer and every FrogNet name
    # eats a timeout against the WAN before reaching a resolver that has it.
    # [LAN_NEIGHBOR_IS_A_RESOLVER_V1] LAN neighbours sit with the tunnel peers,
    # ahead of the default gateway, for the same reason: they can answer FrogNet
    # names and it cannot. A node reached over eth0/wlan is no less a resolver
    # than one reached over wg - the bearer is not the point, the pond behind it
    # is.
    wan_ns = (_wan_ns_from_exit_sentinel()
              + _tunnel_peer_resolvers(local_ips)
              + _lan_neighbor_resolvers(local_ips)
              + wan_ns_from_default(k))
    hn = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    # [SELF_IP_IS_A_RESOLVER_V1] This node's own address goes directly under
    # 127.0.0.1. self_identity when the caller has it; otherwise the .1 we hold
    # on the 10/8 plane, which is the address dnsmasq is bound to.
    _self_ns = self_identity or next(
        (ip for ip in sorted(local_ips)
         if ip.startswith("10.") and ip.endswith(".1")), "")
    content = "\n".join(
        build_resolv(_local_domain(), hn, wan_ns, self_ip=_self_ns)) + "\n"
    _write_if_changed("/etc/resolv.conf", content, logger)
    _write_dnsmasq_upstream(k, logger)


def build_live(logger=print, self_identity: str = ""):
    """Construct a Discovery wired entirely to Real backends + a RealKernel."""
    k = RealKernel()
    routes = Routes(k, logger=logger)
    hoststore = HostStore()
    broker = RealBroker()
    local_ips = _local_ips()

    # dev_src: transit /30 src per wg dev (RealKernel route show)
    dev_src = {}
    for ln in _run(IP, "-4", "-o", "route", "show").splitlines():
        p = ln.split()
        if "dev" in p and "src" in p:
            dev = p[p.index("dev") + 1]
            if dev.startswith("wg"):
                dev_src[dev] = p[p.index("src") + 1]

    # [TRANSIT_FROM_WINNERS_V1] Derive the role signals promote() needs to
    # classify winners as transit:
    #   own_subnet      - this node's served /24 (from self_identity .1)
    #   has_own_uplink  - gateway iff a non-FrogNet iface holds a non-10.x addr
    #                     (a real WAN). Mirrors peer._node_has_own_uplink. A
    #                     gateway transits its ENTIRE downstream LAN subtree.
    #   uplink_dev      - on a LAN-child, the mesh-ward dev (dev of the FrogNet
    #                     default route) to EXCLUDE; empty on a gateway.
    own_subnet = ""
    if self_identity:
        own_subnet = ".".join(self_identity.split(".")[:3]) + ".0/24"
    has_own_uplink = False
    for ln in _run(IP, "-4", "-o", "addr", "show").splitlines():
        p = ln.split()
        if len(p) < 4 or p[2] != "inet":
            continue
        ifc = p[1]
        if ifc == "lo" or ifc.startswith("frognet") or ifc.startswith("wg"):
            continue
        addr = p[3].split("/", 1)[0]
        if not addr.startswith("10."):
            has_own_uplink = True
            break
    uplink_dev = ""
    if not has_own_uplink:
        for ln in _run(IP, "-4", "-o", "route", "show", "default").splitlines():
            p = ln.split()
            if "dev" in p:
                uplink_dev = p[p.index("dev") + 1]
                break

    # [VOUCH_TS_GATE_V1] Evidence provider: the vouched hosts' OWN freshest
    # self-assertions - SD:capability tuples on the control plane. Resolves
    # the PREVIOUS merge's control line (best available evidence at walk
    # time). Any failure returns via exception -> the gate goes off loudly;
    # an empty store (cold mesh) also disables the gate. Fetched lazily,
    # once per merge, only if a vouch is actually consumed.
    def _caps_ages():
        import time as _t
        from core import frognet_tuples as _T
        rows = _T._values_raw("", "databasehost_control.frognet", timeout=3.0)
        now = int(_t.time())
        ages = {}
        for _r in rows:
            _sn = _r.get("SensorName", "")
            if not _sn.startswith("SD:capability.host:"):
                continue
            _ip = _sn[len("SD:capability.host:"):].split(":", 1)[0]
            _d = _r.get("data") or {}
            try:
                _age = now - int(_d.get("ts", 0) or 0)
            except (TypeError, ValueError):
                continue
            if _ip not in ages or _age < ages[_ip]:
                ages[_ip] = _age
        return ages

    disc = Discovery(routes, RealEcho(), RealRtt(), RealGetHosts(), broker,
                     hoststore, local_ips, dev_src, logger=logger,
                     propagator=Propagator(hoststore, logger=logger),
                     reflect=RealReflect(), self_identity=self_identity,
                     verify=RealVerify(), caps=_caps_ages,
                     own_subnet=own_subnet, has_own_uplink=has_own_uplink,
                     uplink_dev=uplink_dev)
    # [WAVE_PARALLEL_V1] Parallelize the wave-1 probe phase live (overlaps the
    # echo timeouts that dominate merge wall time). Output is proven identical to
    # sequential (test_wave_parallel_equivalence_oracle). Toggle with
    # FROGNET_PARALLEL_WAVES=0 and tune workers with FROGNET_WAVE_WORKERS.
    import os as _os
    disc.parallel_waves = _os.environ.get("FROGNET_PARALLEL_WAVES", "1") not in ("0", "false", "no")
    try:
        disc.wave_workers = max(1, int(_os.environ.get("FROGNET_WAVE_WORKERS", "8")))
    except ValueError:
        disc.wave_workers = 8
    return disc, k, broker, local_ips


def check_ip_collision(ip, reflect=None, logger=print):
    """Startup duplicate-IP check (emitter side). Fire a reflect probe for `ip`
    BEFORE claiming/binding it. Under the shipped reflect contract only a live
    holder of `ip` returns 200 REFLECT_OK (handler rule 1: target-local), so:
        OK   -> TAKEN  (another box already holds this 10.; do not claim)
        else -> FREE   (no live holder reached)
    Pre-bind this needs no GUID: we are not yet a holder, so any 200 is
    necessarily another box. Runtime dup detection AFTER binding would need the
    proposed g=<fnid GUID> extension to tell self from impostor."""
    reflect = reflect or RealReflect()
    verdict = reflect.probe(ip, ip)
    taken = (verdict == "OK")
    logger(f"check_ip_collision ip={ip} verdict={verdict or 'none'} "
           f"result={'TAKEN' if taken else 'FREE'}")
    return taken


def _reconcile_tunnels(logger=print):
    """Bring-up stage - broker-authoritative wg reconcile, run synchronously
    BEFORE discovery, exactly as mergeHostsAndResolv.bash does:

        PYTHONPATH=/opt/frognet_semantic python3 -m internet_tunnels_v3 bring-up-only

    Brings up wg ifaces the broker says should exist, tears down the rest. The
    merge port is not a full merge without this - discovery only routes over
    tunnels, it never creates them. Skippable via FROGNET_SKIP_BRINGUP=1.
    Overridable: FROGNET_BRINGUP_PYTHONPATH, FROGNET_BRINGUP_PY.
    """
    import os
    if os.environ.get("FROGNET_SKIP_BRINGUP"):
        logger("reconcile_tunnels SKIPPED (FROGNET_SKIP_BRINGUP set)")
        return 0
    env = dict(os.environ)
    env["PYTHONPATH"] = os.environ.get("FROGNET_BRINGUP_PYTHONPATH",
                                       "/opt/frognet_semantic")
    py = os.environ.get("FROGNET_BRINGUP_PY", "/usr/bin/python3")
    logger("reconcile_tunnels START (internet_tunnels_v3 bring-up-only)")
    try:
        r = subprocess.run([py, "-m", "internet_tunnels_v3", "bring-up-only"],
                           env=env, capture_output=True, text=True)
    except FileNotFoundError as e:
        logger(f"reconcile_tunnels rc=127 error={e}")
        return 127
    for ln in (r.stdout or "").splitlines():
        logger(f"reconcile: {ln}")
    for ln in (r.stderr or "").splitlines():
        logger(f"reconcile: {ln}")
    logger(f"reconcile_tunnels rc={r.returncode}")
    return r.returncode


def main():
    logger = print
    # bring-up stage: reconcile wg ifaces to broker state BEFORE we gather/probe,
    # so discovery sees the tunnels that should exist (mergeHostsAndResolv order).
    _reconcile_tunnels(logger)
    imap = RealInterfaceMap()
    ifaces, leases_nonempty = imap.gather()
    cls = classify(ifaces, leases_nonempty=leases_nonempty)
    devs = cls["FROGNET_INTERFACES"].split()

    # [LEAF_SRC_PIN_V1] This node's identity = the .1 of its served FROGNET /24
    # (the hostapd interface), regardless of which interface it currently
    # egresses through. On a leaf that reaches the mesh over a borrowed lease,
    # the egress iface address is NOT this, so routes must pin it as src.
    self_identity = ""
    _frognet_devs = cls.get("FROGNET_FROGNET_DEVS", "").split()
    if _frognet_devs:
        # [SELF_IDENTITY_SKIP_RESERVED_V1] Identity is the node's OWN /24 .1
        # (e.g. 10.160.160.1), never a pool address. A chorus dev (frognet0,
        # 10.254.x) or transit dev (10.253.x) must NOT source the identity: if it
        # does, the node adopts a 10.254.x self-identity. Take the first frognet
        # dev whose address is a real node /24, skipping the reserved pools.
        for _dev in _frognet_devs:
            _iip = next((i.ip4() for i in ifaces if i.dev == _dev), "")
            if not _iip:
                continue
            if _iip.startswith("10.253.") or _iip.startswith("10.254."):
                logger(f"SELF_IDENTITY_SKIP dev={_dev} ip={_iip} reason=reserved_pool")
                continue
            self_identity = ".".join(_iip.split(".")[:3]) + ".1"
            break

    disc, k, broker, local_ips = build_live(logger, self_identity=self_identity)

    # run_tunnel_health_check: probe each tunnel, derive dead ifaces, filter devs
    from .healthcheck import HealthCheck
    active_states_full = _active_states()
    health_states = []
    for iface, subs in active_states_full:
        ch = broker.channel_for_iface(iface)
        if ch and subs:
            health_states.append((iface, ch, subs[0]))
    # [PINGPONG_HEALTH_V1] Gate tunnel liveness on the :9009 ping-pong (reuse the
    # same RealVerify discovery walks with), not the HTTP frognet_echo.php gate.
    hc = HealthCheck(disc.r, disc.verify, logger=logger,
                     dev_src={i.dev: i.ip4() for i in ifaces})
    dead, devs = hc.run(health_states, devs)
    disc.DEAD_IFACES = set(dead)

    seeds = dict(
        active_devs=devs,
        dev_ip={i.dev: i.ip4() for i in ifaces},
        leases=_leases(),
        disk_cache=[],
        active_states=_active_states(),
        wg_kernel_nets=_wg_kernel_nets(devs),
        arp_neigh=_arp_neigh(devs),
    )
    upstream_seed = {}  # descend_upstream seeds (seed_from_dev_ip) - fill from cls if needed

    # bringup peers come from handshake_rtts (direct channels)
    hs = broker._handshake()
    bringup = [(ch.rsplit("-", 1)[0], v.get("peer_dot_one"))
               for ch, v in hs.items() if v.get("peer_dot_one")]

    eth0_ip = next((i.ip4() for i in ifaces if i.dev == "eth0"), "")
    domain = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip().split(".", 1)
    domain = domain[1] if len(domain) > 1 else ""

    # [HOSTS_NAME_CHANGE_RUNAGAIN_V1] Snapshot the names currently in /etc/hosts
    # ({.1 ip: domain}) so the merge can detect a peer whose authoritative name
    # changed (old name deprecated) and request another converge pass.
    prior_names = {}
    try:
        for ln in open("/etc/hosts"):
            f = ln.split()
            if len(f) >= 2 and f[0].startswith("10.") and f[0].rsplit(".", 1)[-1] == "1":
                for tok in f[1:]:
                    if tok.startswith("FrogNetHost."):
                        prior_names[f[0]] = tok.split(".", 1)[1]
                        break
    except OSError:
        prior_names = {}

    out = run_merge(disc, k, seeds, upstream_seed,
                    local=(self_identity or eth0_ip, domain), bringup_peers=bringup,
                    prior_names=prior_names,
                    concurrent_attempt=False, logger=logger)
    # [SYNC_ON_ROUTE_MUTATION_V1] carry whether the committed route table changed
    # this merge through to the sync trigger; routing deltas, not host-table
    # deltas, are what neighbors must be told about.
    out["route_table_mutated"] = bool(getattr(disc.r, "route_table_mutated", False))
    # [TRANSIT_FROM_WINNERS_V1] Persist the transit set promote() derived from
    # the winners so the tunnel daemon's _sync_transit_subnets posts it. Written
    # as a sentinel (one /24 per line) - the same cross-process channel discovery
    # uses for frognet_hosts/exit_host. The daemon reads this instead of
    # re-deriving transit from a route scan or DHCP-lease probe.
    out["computed_transit"] = list(getattr(disc, "computed_transit", []))
    try:
        import os as _os
        _os.makedirs(_sent(), exist_ok=True)
        with open(_sent("transit_subnets.tsv"), "w") as _tf:
            _tf.write("".join(s + "\n" for s in out["computed_transit"]))
        logger(f"TRANSIT_SENTINEL wrote {len(out['computed_transit'])} /24(s): "
               f"{out['computed_transit']}")
    except OSError as _e:
        logger(f"TRANSIT_SENTINEL write failed: {_e}")

    # [REACH_PLANE_TUPLE_V1] Persist this node's loopback-validated WAN plane to a
    # tuple so the merge-end service-host election can split LAN vs WAN by READING,
    # not re-probing. The reflect loopback detector already ran in the walk (LOOP
    # candidates were dropped); the surviving wg* winners are computed_wan. Stable
    # for this merge epoch - only the next merge re-walks. Host-scoped + addressed by
    # this node's IP so the election picks OUR view. Degrades silently if the
    # transient isn't writable (election then treats nothing as WAN -> media stays
    # inclusive, self-corrects next merge).
    out["computed_wan"] = list(getattr(disc, "computed_wan", []))
    try:
        from core import frognet_tuples as _T
        _T.put("discovery", "reach_plane", _T.host_scope(),
               {"wan_subnets": out["computed_wan"],
                "self_ip": self_identity or eth0_ip})
        logger(f"REACH_PLANE_TUPLE wrote wan={out['computed_wan']}")
    except Exception as _e:
        logger(f"REACH_PLANE_TUPLE write failed: {_e}")

    # [NEIGHBOR_FROGNET_ADDR_V1] Persist {dhcp_next_hop: FrogNet .1} so
    # propogateNotification's neighbor scan sends to a downstream child's FrogNet
    # address, not its DHCP lease on our segment.
    try:
        import os as _os
        _os.makedirs(_sent(), exist_ok=True)
        _nv = getattr(disc, "neighbor_via_map", {}) or {}
        with open(_sent("frognet_neighbor_via"), "w") as _nf:
            _nf.write("".join(f"{dhcp} {dot1}\n" for dhcp, dot1 in sorted(_nv.items())))
        logger(f"NEIGHBOR_VIA_SENTINEL wrote {len(_nv)} mapping(s): {dict(_nv)}")
    except OSError as _e:
        logger(f"NEIGHBOR_VIA_SENTINEL write failed: {_e}")

    # [SERVICE_HOSTS_ELECTION_V1] Generic, dynamic service-host election. Derive the
    # live service set by consolidating the registered candidate tuples, elect each
    # role through its registry handler over capability memory, and emit
    # <role>.frognet lines into the committed block. ONE loop for databasehost,
    # mediahost, and any future role - no per-role hook. Degrades to no role lines
    # (the highest-IP databasehost floor stands) if the transient is unreachable.
    # [PUBLISH_BEFORE_ELECT_V1] Every merge, this node re-publishes its own <role>/
    # capability (perf inside the blob) to BOTH control + data BEFORE the election
    # reads - so the control holder's SELECT over capability tuples sees fresh perf.
    # Best-effort: a miss self-heals on the confirmation merge a host-change triggers.
    try:
        from core.role_publish import publish_all
        from discovery.hosts import control_host_ip, CONTROL_NAME
        # Publish THIS node's capability to the SAME fresh control the election below
        # reads from - the deterministic highest .1 computed from this merge's block -
        # not the databasehost_control.frognet NAME, which resolves through the prior
        # merge's /etc/hosts (not committed yet) and so lands this node's perf on the
        # wrong control, leaving the fresh control's election ranking on stale data.
        _ctl = control_host_ip(out.get("etc_hosts", [])) or CONTROL_NAME
        _reports = publish_all(control=_ctl, logger=logger)
        # [PUBLISH_EVIDENCE_V1] Say that it happened, not only that it failed. A silent
        # success means a merge log cannot answer "did this node re-assert its
        # capability?", and that is the question every stale-ballot investigation
        # starts with: a row unrefreshed past FROGNET_BALLOT_MAX_AGE_S is refused by
        # every other node, so a machine that stops merging quietly stops being
        # electable anywhere - with nothing in its own log to show for it.
        _ok = sorted(x for r in _reports for x in [r.get("role")] if r.get("written") and x)
        _bad = [(r.get("role"), r.get("consistency"), r.get("refused") or r.get("error"))
                for r in _reports
                if not r.get("written") or r.get("consistency") != "ok"]
        import time as _pt
        _now = int(_pt.time())
        _ts = {r.get("role"): r.get("ts") for r in _reports if r.get("ts")}
        _age = {k: (_now - int(v)) for k, v in _ts.items() if v}
        logger(f"PUBLISH_CAPABILITY control={_ctl} wrote={_ok} "
               f"ts={_ts} ts_age_at_write={_age}"
               + (f" NOT_WRITTEN={_bad}" if _bad else ""))
    except Exception as _e:
        logger(f"PUBLISH_BEFORE_ELECT failed: {_e}")
    try:
        from core.frognet_service_hosts import apply_to_etc_hosts
        from discovery.hosts import control_host_ip, CONTROL_NAME
        # Read candidates straight from the control IP this merge just wrote, not via
        # databasehost_control.frognet - the new control line isn't committed to
        # /etc/hosts yet, so resolving the name here would lag a merge. The IP is
        # deterministic (highest .1) and already in out['etc_hosts'].
        _ctl = control_host_ip(out.get("etc_hosts", [])) or CONTROL_NAME
        # [SERVICE_HOSTS_ELECTION_V1] POST-merge determination. Every component's host
        # (databasehost, mediahost, boardgame, ...) is its UnREST callback's pure pick
        # over the capability tuples in databasehost_control - identical on every node by
        # construction. No floor, no per-node reachability cull. self_ip = this node's .1,
        # used only for local-by-definition when a callback names no winner. This is a
        # read; it does NOT set runAgain (only route changes re-run the merge).
        # [LAN_IS_ATTACHED_V1] LAN service roles (mediahost) must elect only over
        # this node's directly-attached segments - not over boxes reached via a relay.
        # disc.local_subnets is the attached-/24 prefix set; render as '10.x.y.0/24'.
        _lan = {p + ".0/24" for p in getattr(disc, "local_subnets", set())}
        # [DBHOST_COMPLETENESS_BARRIER_V1] Only DECIDE the service roles once _control
        # holds a fresh capability record for every live machine. When ready, the ONE
        # election loop sets databasehost AND (off the same machinery) mediahost and
        # boardgame together. Until ready, pin databasehost at the control (highest .1,
        # always valid) and leave the other role lines as committed, so a later merge -
        # after the missing nodes publish - decides over the COMPLETE pool and every node
        # agrees. A lone FrogNetHost is ready at once and elects itself for every role.
        # [ROLE_ELECTION_UNCOUPLED_V1] The election runs EVERY converged merge, for
        # every registered role. It used to sit behind the databasehost completeness
        # barrier, so a pond whose barrier never opened elected nothing at all - no
        # mediahost line, no boardgame line, and no error to say so.
        # [ROLE_BARRIER_PER_ROLE_V1] Each role asks its OWN barrier. mediahost waits
        # on mediahost capability records, not on databasehost ones -- those are not an
        # input to the media election and used to hold it shut for no reason.
        from discovery.hosts import role_barrier_ready
        from core.frognet_service_hosts import service_host_lines, commit_service_lines
        try:
            from core.role_registry import ROLE_HANDLERS as _RH
            _roles = list(_RH)
        except Exception:
            _roles = ["databasehost", "mediahost", "boardgame"]
        _ready_roles = set()
        for _role in _roles:
            _rdy, _alive, _recorded = role_barrier_ready(out.get("etc_hosts", []),
                                                         role=_role, dbhost=_ctl,
                                                         logger=logger)
            if _rdy:
                _ready_roles.add(_role)
        # [EVERY_FROGNETHOST_IS_A_CANDIDATE_V1] The converged hosts block IS the
        # candidate pool: every live .1 FrogNetHost is a candidate for every role by
        # definition, whether or not its capability row landed this pass.
        _role_lines = service_host_lines(dbhost=_ctl, logger=logger,
                                         etc_hosts=out.get("etc_hosts", []),
                                         self_ip=(self_identity or eth0_ip),
                                         lan_subnets=_lan,
                                         local_ips=set(getattr(disc, "local_ips", ())))
        out["etc_hosts"] = commit_service_lines(out.get("etc_hosts", []), _role_lines,
                                                _ready_roles, _ctl, logger=logger)
        logger(f"SERVICE_BARRIERS ready={sorted(_ready_roles)} "
               f"of {sorted(_roles)}")
        logger(f"SERVICE_HOSTS lines now: "
               f"{[l for l in out['etc_hosts'] if l.endswith('.frognet')]}")
    except Exception as _e:
        logger(f"SERVICE_HOSTS skipped: {_e}")

    _commit_hosts(out, logger=logger)
    _arm_elector(logger=logger)
    _mark_discovered(logger=logger)
    # merge tail: fixDefaultRoute (Mode A + B + forced) then manageResolv,
    # over the real kernel - the steps bash ran after the host merge.
    _run_fixdefault_and_resolv(k, devs, {i.dev: i.ip4() for i in ifaces},
                               local_ips, logger=logger,
                               self_identity=self_identity)
    return out


def _write_if_changed(path, content, logger=print):
    import os
    try:
        if open(path).read() == content:
            logger(f"HOSTS_KEEP path={path} reason=unchanged")
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".frognet.tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)
    logger(f"HOSTS_WRITE path={path} lines={content.count(chr(10))}")
    return True


def _write_forwarders(etc_hosts_lines, logger=print):
    """Emit /etc/dnsmasq.d/frognet_forwarders_auto.conf from the committed host
    table. Faithful port of mergeHostsAndResolv.bash stage_dnsmasq:

      - one server=/<dom>/<ip> and server=/.<dom>/<ip> pair per FrogNetHost.<dom>
        line, sorted -u, under the same '# AUTO-GENERATED' header the bash wrote
      - then listen-address=127.0.0.1,<self .1> and bind-interfaces

    The listen lines are part of THIS file on a live node, not opts_only.conf, so
    omitting them would silently drop dnsmasq's listen-address on the next merge.
    self .1 comes from the host table's own self line, which HOSTS_FORMAT_V2 marks
    with a trailing bare 'FrogNetHost' (hosts.py _normalize_line) - no shell-out to
    getEth0Address, and it cannot disagree with the table being written.

    Returns True if the file changed.
    """
    pairs, self_ip = set(), ""
    for ln in etc_hosts_lines:
        parts = ln.split()
        if len(parts) < 2 or not parts[0].startswith("10."):
            continue
        ip = parts[0]
        for f in parts[1:]:
            if f.startswith("FrogNetHost."):
                dom = f[len("FrogNetHost."):]
                if dom:
                    pairs.add((dom, ip))
                break
        if len(parts) >= 3 and parts[2] == "FrogNetHost":
            self_ip = ip
    if not pairs:
        logger("DNSMASQ_FORWARDERS skipped reason=no_frognethost_lines")
        return False
    body = ["# AUTO-GENERATED"]
    lines = []
    for dom, ip in pairs:
        lines.append(f"server=/{dom}/{ip}")
        lines.append(f"server=/.{dom}/{ip}")
    body.extend(sorted(lines))
    if self_ip:
        body.append(f"listen-address=127.0.0.1,{self_ip}")
        body.append("bind-interfaces")
    else:
        logger("DNSMASQ_FORWARDERS no_listen_address reason=no_self_line_in_host_table")
    changed = _write_if_changed("/etc/dnsmasq.d/frognet_forwarders_auto.conf",
                                "\n".join(body) + "\n", logger)
    logger(f"DNSMASQ_FORWARDERS domains={len(pairs)} changed={'YES' if changed else 'NO'}")
    return changed


def _reload_dnsmasq(logger=print):
    """SIGHUP dnsmasq so it re-reads /etc/hosts and serves the new .frognet mapping to
    downstream resolvers. dnsmasq is the only resolver FrogNet runs on a gateway, so
    this is unconditional in mechanism. Best-effort: no dnsmasq (non-gateway) -> no-op."""
    import subprocess
    try:
        r = subprocess.run(["pkill", "-HUP", "-x", "dnsmasq"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger("DNSMASQ_RELOAD sent=SIGHUP" if r.returncode == 0
               else "DNSMASQ_RELOAD no-op reason=no_dnsmasq")
    except Exception as e:
        logger(f"DNSMASQ_RELOAD skipped err={e}")


def _mark_discovered(logger=print):
    """[BOOT_GATE_V1] Signal that discovery has completed a merge and committed resolvable
    service hosts. frognet-discovered.service blocks on this sentinel; frognet-discovered.target
    gates consumer services (dashboard, gps) so they never start against an unresolved network.
    Written at every merge completion; the first write latches the target (RemainAfterExit)."""
    import os
    try:
        os.makedirs(_sent(), exist_ok=True)
        with open(_sent("discovered"), "w") as f:
            f.write("1")
    except OSError as e:
        logger(f"BOOT_GATE mark skipped err={e}")


def _arm_elector(logger=print):
    """[ELECTOR_ARM_V1] Stamp merge completion so the dbhost elector knows it is inside
    its post-merge window. The elector re-evaluates databasehost.frognet 1/min for 10 min
    after each merge (candidates fill in async after convergence: frognets emit on the
    _control change, registered DB boxes on their nslookup-delta). Every merge re-arms."""
    import os, time
    try:
        os.makedirs(_sent(), exist_ok=True)
        with open(_sent("last_merge"), "w") as f:
            f.write(str(int(time.time())))
    except OSError as e:
        logger(f"ELECTOR_ARM skipped err={e}")


def _topology_changed(old_content: str, new_content: str) -> bool:
    """[SERVICE_HOSTS_NO_SYNC_V1] True only if the host files differ in a TOPOLOGY
    line. Service-role lines end in `.frognet` (databasehost/databasehost_control/
    mediahost/boardgame/aihost ...); they are a DERIVED election result every node
    computes identically from databasehost_control, so a change confined to them is
    NOT a topology change and must NEVER trigger re-sync/propagation (that is the
    merge-storm: each node re-derives a role line, the byte differs, it propagates,
    the neighbor re-derives, forever). Strip `.frognet` lines from both sides and
    compare what remains - node-identity / route-bearing lines only."""
    strip = lambda s: [l for l in s.splitlines() if not l.endswith(".frognet")]
    return strip(old_content) != strip(new_content)


def _commit_hosts(out, logger=print):
    """Persist the merge's host output - the step bash mergeHostsAndResolv did
    and live.main was missing. frognet_hosts -> /etc/sentinels/frognet_hosts;
    the assembled /etc/hosts (header+127+sorted block) -> /etc/hosts."""
    import os
    def _read(p):
        try:
            return open(p).read()
        except OSError:
            return ""
    fh_old = _read(_sent("frognet_hosts"))
    eh_old = _read("/etc/hosts")
    changed = False
    fh = "\n".join(out.get("frognet_hosts", []))
    if fh:
        changed |= _write_if_changed(_sent("frognet_hosts"), fh + "\n", logger)
    eh = "\n".join(out.get("etc_hosts", []))
    if eh:
        changed |= _write_if_changed("/etc/hosts", eh + "\n", logger)
    # [DNSMASQ_FORWARDERS_V1] stage_dnsmasq, ported from mergeHostsAndResolv.bash
    # (lines 206-228). The bash tail emitted /etc/hosts AND the dnsmasq per-domain
    # forwarders from the same host table in the same pass; the python cutover
    # (runMerge -> discovery.live) ported stage_hosts and manageResolv but not this
    # stage, so nothing wrote frognet_forwarders_auto.conf any more and a node lost
    # every remote-domain delegation. dnsmasq does not infer delegation from
    # /etc/hosts: an A record for FrogNetHost.<dom> does not tell it to send <dom>
    # queries there. Only a server=/<dom>/<ip> line does. Emitted here, off the
    # host table just committed, so the two can never diverge again.
    if eh:
        changed |= _write_forwarders(out.get("etc_hosts", []), logger)
    # [DNSMASQ_RELOAD_V1] dnsmasq serves the merge-written /etc/hosts as .frognet DNS
    # to downstream clients (off-mesh DB candidates resolve databasehost_control.frognet
    # via this node, their nameserver per resolv.conf files->dns). dnsmasq re-reads
    # /etc/hosts only on SIGHUP, so a float would otherwise be invisible to them until
    # cache expiry. Gate on a REAL hosts change (the _write_if_changed verdict), so it
    # fires only when the mapping actually moved - no churn on no-op merges.
    if changed:
        _reload_dnsmasq(logger)
    # [SYNC_ON_ROUTE_MUTATION_V1] the convergence edge-trigger is keyed on the
    # ROUTING TABLE, not /etc/hosts. Any committed route add/replace/del this merge
    # (winner install, verify-backout, fallback, sweep of a real route) means
    # neighbors must re-sync - even when the host table is byte-identical, which is
    # the common case (hosts come from the broker and persist while routes churn).
    # [SERVICE_HOSTS_NO_SYNC_V1] A TOPOLOGY host-table change (a node-identity /
    # route-bearing line) still counts as a secondary trigger - but a change confined
    # to `<role>.frognet` SERVICE lines does NOT: those are a derived election result,
    # identical on every node, and firing sync on them is the merge-storm loop. So the
    # host-table trigger is `_topology_changed`, which ignores `.frognet` lines.
    # Probe /32s (metric 6) are excluded at the rtmut source so per-walk scratch churn
    # doesn't fire routes_changed. Clear first so a stale sentinel from an
    # already-converged cycle can't re-fire.
    routes_changed = bool(out.get("route_table_mutated"))
    topo_changed = (_topology_changed(fh_old, fh + "\n") if fh else False) \
                   or (_topology_changed(eh_old, eh + "\n") if eh else False)
    # [SYNC_ON_ROUTE_MUTATION_V1] sync_required (the FILE) is the per-pass
    # `sync_required`, colliding with the EXTERNAL "please run a merge" nudge other
    # scripts touched on the same path. Different concepts. This one is discovery's
    # "this pass changed a route/topology" edge-trigger, the raw input to the
    # present = this pass changed nothing (converged); absent = a route/topology
    # propagate-on-converged gate in runMerge (NOT the loop control; runAgain is).
# [SYNC_ON_ROUTE_MUTATION_V1] sync_required (the FILE) is the per-pass "this pass
    # changed a route or the topology" edge-trigger. It is the raw input to the
    # propagate decision; the ACTUAL notify-the-world gate lives in runMerge
    # ([PROPAGATE_ON_CONVERGED_V1]): propagate once, on the converged pass
    # (runAgain=0), iff any pass in the chain was dirty. So this file just records
    # "did THIS pass change something", and the bash chain turns that into "announce
    # only when settled". Present = changed this pass; absent = clean pass.
    sr = _sent("sync_required")
    try:
        os.remove(sr)
    except OSError:
        pass
    if routes_changed or topo_changed:
        os.makedirs(_sent(), exist_ok=True)
        open(sr, "w").close()
        reason = ("routes_changed" if routes_changed and not topo_changed
                  else "hosts_changed" if topo_changed and not routes_changed
                  else "routes+hosts_changed")
        logger(f"sync_required set reason={reason}")


if __name__ == "__main__":
    main()
