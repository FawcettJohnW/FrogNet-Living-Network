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
descend - immediate connections, a bounded getHosts crawl to a seed list, then a
parallel every-interface probe.

John's algorithm, 2026-07-08:

  * The DOWNSTREAM and UPSTREAM immediate connections are REAL, not seeds - the
    DHCP clients we serve (downstream) and the uplink neighbours we connect
    through (upstream). We already have them; each is one hop, on a known
    interface. Walk it there.

  * From those real connections we getHosts their CHILDREN - those become seeds.
    We keep getHosts-ing each new seed's children to a FIXPOINT (grandchildren,
    great-grandchildren, and deeper), so a chain longer than three hops still
    surfaces its far end in a single merge. Every node learns every other node.

  * With the seed list in hand, we probe EVERY interface to see which one has a
    route to each seed. In parallel. Whichever interface answers is the
    candidate.

The point of the split: an immediate connection is one hop and its interface is a
known fact, so it is not probed everywhere. A seed is more than one hop away -
reached through some relay we can't name in advance - so it IS probed on every
interface, because a route can exist on an interface the crawl never traversed.
No vouch gate, no parent_via path-pinning; a seed answers on an interface or it
does not.

Candidates are written in promote()'s format (rtt|via|dev|onlink|src|kind|host)
into disc.CAND; promote() selects and installs unchanged.
"""
from __future__ import annotations

import subprocess
import time as _time
import threading as _threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .discovery import net_dot, dest24_of


def _via(dot2: str, dev: str) -> str:
    """Kernel next-hop to dot2 scoped to dev, "" for on-link. The via promote
    installs, so the /24 winner rides the path the probe proved."""
    try:
        r = subprocess.run(["ip", "-o", "route", "get", dot2, "oif", dev],
                           capture_output=True, text=True, timeout=2)
        toks = (r.stdout or "").split()
        if "via" in toks:
            return toks[toks.index("via") + 1]
    except Exception:
        pass
    return ""


def _children(disc, ip):
    """getHosts on a host - the hosts it serves/knows, [(cip, cname), ...]. This
    is the live crawl query (NOT the /etc/hosts source): the connection tells us
    who is beyond it."""
    # A host at ip=X.Y.Z.1 is probed on its own subnet: identity X.Y.Z.1, admin
    # X.Y.Z.2. But a DHCP GUEST on OUR OWN subnet holds a NON-.1/.2 lease (e.g.
    # 10.250.250.191 = Seattle3, whose real identity is 10.130.130.1). For it,
    # net_dot(ip,1)/net_dot(ip,2) resolve to OUR OWN .1/.2 - so we'd query
    # OURSELVES over the LAN and get our own host list back, mislabelling the guest
    # as us and never learning the subnet it serves (its /24 then gets no route and
    # HOSTS_GATE drops it). Probe the guest at its actual lease address instead.
    _last = ip.rsplit(".", 1)[1]
    if disc.is_on_local_subnet(ip) and _last not in ("1", "2"):
        host_path = ip
        pip = ip
    else:
        host_path = net_dot(ip, 1)
        pip = net_dot(ip, 2)
    log = getattr(disc, "log", lambda *_a, **_k: None)
    try:
        rows = disc.gethosts.get_hosts(host_path, pip) or []
    except Exception as e:
        log(f"DESCEND_GETHOSTS ip={ip} host_path={host_path} pip={pip} ERROR={e!r}")
        return []
    # [DESCEND_CHILDREN_DIAG_V1] Show exactly what getHosts returned so the 'host=1'
    # naming and empty-grandchildren questions stop being guesswork: the query
    # target, the row count, and each row's raw repr + how it unpacks to (ip,name).
    log(f"DESCEND_GETHOSTS ip={ip} host_path={host_path} pip={pip} rows={len(rows)}")
    for r in rows:
        if isinstance(r, tuple):
            cip = r[0] if len(r) > 0 else ""
            cname = r[1] if len(r) > 1 else ""
        else:
            cip, cname = r, ""
        log(f"DESCEND_CHILD raw={r!r} -> ip={cip!r} name={cname!r}")
    return rows


def _measure(disc, pip, dev):
    """Measure rtt to pip on dev using the SAME backend the walk uses -
    disc.rtt.measure_rtt (frognet_alive), which returns a ms string ("" = dead or
    channel-rejected). disc.verify is a separate optional post-finalize check that
    is often None; using it for discovery measurement (as an earlier version did)
    silently measured nothing. Returns float ms, "LOOP", or None."""
    r = getattr(disc, "rtt", None)
    if r is None:
        return None
    try:
        s = r.measure_rtt(dev, pip)
    except Exception:
        return None
    if s == "LOOP":
        return "LOOP"
    try:
        return float(s) if s not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _add_host(disc, name, host_path, dev, authoritative=False):
    """Name a host - through the propagator when one is wired (so the epidemic
    notify fires), else straight to hosts. Mirrors the walk's add-host path.
    Deduped per host identity (.1) for THIS pass: a host reached on several devs
    (multi-tunnel winner+fallback) is a route decision, but it is announced/
    propagated ONCE, not once per dev - matching the walk's single announce."""
    if not name:
        return
    seen = getattr(disc, "_descend_named", None)
    if seen is None:
        seen = disc._descend_named = set()
    if host_path in seen:
        return
    seen.add(host_path)
    prop = getattr(disc, "propagator", None)
    if prop is not None:
        prop.add_host_and_propagate(name, host_path, dev, authoritative=authoritative)
    elif hasattr(disc, "hosts"):
        disc.hosts.add_host(name, host_path, dev, authoritative=authoritative)


def _prove_dot1(disc, x_ip, dev, via):
    """[PROVE_DOT1_V1] Prove the PRODUCTION .1 of X through (dev, via) with the
    :9009 ping-pong - the authoritative route-liveness gate.

    A seg-relay does NOT forward the .2 discovery plane onward, so a node BEHIND a
    relay never answers reflect on .2 even when its production .1 is reachable
    through that same next hop (John's `ip r r <dest>/24 via <relay> && ping
    <dest>.1` proof). Reflect-on-.2 is only the fast path; the .1 :9009 ping-pong is
    the authority. Install a transient /32 to X's .1 on this avenue, ping-pong THROUGH
    it, tear down. PONG proves the route carries. Returns float rtt, "LOOP", or None.
    No verify backend wired (most oracles) -> None, so this is inert there."""
    verify = getattr(disc, "verify", None)
    if verify is None:
        return None
    d1 = net_dot(x_ip, 1)
    src = disc.dev_src(dev) if hasattr(disc, "dev_src") else ""
    if disc.r.probe_install(d1, dev, via, src) != 0:
        return None
    try:
        v = verify.measure_or_loop(d1, dev)
    finally:
        disc.r.probe_delete(d1)
    if v == "LOOP":
        return "LOOP"
    if isinstance(v, float):
        return v
    return None


def _probe_through(disc, x_ip, dev, via):
    """Evaluate next-hop (dev, via) toward node X. THE RULE: never ping X's far end
    - that times out (~14s) whenever this avenue does not actually forward to X and
    stalls the merge. Install the probe route to X on this avenue, then use the
    REFLECT probe to ask 'does this path reach X?' (fast OK/LOOP/None). If OK, rank
    by the RTT to the NEXT HOP (the tunnel peer for a tunnel, the neighbour N for a
    LAN avenue) - both fast. Returns rtt float, "LOOP", or None.

      via="" -> tunnel scope-link; next hop is the tunnel peer (dev's /30 .1).
      via=N  -> LAN neighbour N; next hop is N.
    """
    # [REFLECT_FAILCACHE_V1] Per-merge unreachable cache keyed on (target, via) -
    # NOT on target alone: X may be unreachable via one neighbour but reachable via
    # another, so caching by target would drop a valid avenue. Keying by the exact
    # next hop collapses repeated identical probes (same target re-probed via the
    # same neighbour across crawl levels / re-invocations) without suppressing any
    # other avenue. 3s TTL; the cache lives on `disc`, which is fresh per merge, so
    # it never persists across merges. Guarded by a lock (probes run in parallel).
    fc = getattr(disc, "_probe_fail_cache", None)
    if fc is None:
        fc = disc._probe_fail_cache = {}
        disc._probe_fail_lock = _threading.Lock()
    fkey = (x_ip, via or dev)
    now = _time.monotonic()
    with disc._probe_fail_lock:
        exp = fc.get(fkey)
        if exp is not None:
            if exp > now:
                return None                 # failed this hop <3s ago - skip
            fc.pop(fkey, None)

    def _fail():
        with disc._probe_fail_lock:
            disc._probe_fail_cache[fkey] = _time.monotonic() + 3.0
        return None

    d2 = net_dot(x_ip, 2)
    src = disc.dev_src(dev) if hasattr(disc, "dev_src") else ""
    reflect = getattr(disc, "reflect", None)
    ident = getattr(disc, "self_identity", "")
    if reflect is not None and ident:
        # Probe the DISCOVERY plane (.2), never the production .1. The .2 alias is
        # bound on every node purely for discovery/health, so the reflect handler
        # answers on it (is_local_ip matches the bound alias) - but it carries NONE
        # of the production data-plane traffic. Probing .1 here rode the production
        # plane and blocked for the full 15s RPC budget behind the saturated
        # database host (.1), stalling the whole node. .2 keeps discovery isolated.
        if disc.r.probe_install(d2, dev, via, src) != 0:
            return _fail()
        try:
            verdict = reflect.probe(ident, d2)
        finally:
            disc.r.probe_delete(d2)
        if verdict == "LOOP":
            return "LOOP"
        if verdict != "OK":
            # [PROVE_DOT1_V1] .2 didn't reflect - but the .2 discovery plane is not
            # forwarded across a seg-relay, so a node BEHIND a relay lands here even
            # though its production .1 is reachable through this same next hop. Fall
            # back to the authoritative gate: prove the .1 via the :9009 ping-pong
            # THROUGH this avenue. PONG -> real, proven route (rank by that rtt).
            # No pong -> this path genuinely does not reach X.
            r1 = _prove_dot1(disc, x_ip, dev, via)
            if r1 == "LOOP":
                return "LOOP"
            if isinstance(r1, float):
                return r1
            return _fail()              # this path does not reach X
        # reachable: rank by RTT to the NEXT HOP (fast), not X's far end.
        nh = via if via else _tunnel_peer(disc, dev)
        if nh:
            r = _measure(disc, net_dot(nh, 2), dev)
            if isinstance(r, float):
                return r
        return 1.0                      # reachable but next-hop rtt unknown
    # no reflect backend (sim/degraded): direct measure on .2 so the oracle harness
    # (which has no reflect) is unaffected.
    if disc.r.probe_install(d2, dev, via, src) != 0:
        return _fail()
    try:
        r = _measure(disc, d2, dev)
    finally:
        disc.r.probe_delete(d2)
    if r is None:
        return _fail()
    return r


def _tunnel_peer(disc, dev):
    """The .1 next hop of a tunnel dev's /30 (the peer we forward through)."""
    dm = getattr(disc, "dev_ip", None) or {}
    dip = dm.get(dev, "")
    if not dip:
        return ""
    # /30: peer is the other usable addr; use the .1-of-subnet convention the
    # kernel route uses for scope-link tunnels (peer answers reflect).
    return dip


def _emit(disc, ip, name, dev, rtt, form, log, authoritative=False):
    dest24 = dest24_of(ip)
    via = _via(net_dot(ip, 2), dev)
    src = disc.dev_src(dev) if hasattr(disc, "dev_src") else ""
    kind = "tunnel" if dev.startswith("wg") else "lan"
    r = int(round(rtt))
    disc.CAND[dest24].append(f"{r}|{via}|{dev}|0|{src}|{kind}|{name}")
    # [DESCEND_NAME_V1] A found host needs a NAME in /etc/hosts, not just a route.
    # add_host feeds reconcile_added -> the frognet block. Seeds carry their name
    # from getHosts (relayed => authoritative=False); immediates carry their own
    # echo name (authoritative=True). Nameless (empty) is skipped by add_host.
    _add_host(disc, name, net_dot(ip, 1), dev, authoritative=authoritative)
    log(f"CANDIDATE dest={dest24} via={via} dev={dev} onlink=0 rtt={r} "
        f"kind={kind} form={form} host={name}")


def _echo_name(disc, ip):
    """Immediate connection's OWN name from its echo self-report (CSV field 0)."""
    try:
        el = disc.echo.echo_probe(net_dot(ip, 2)) or ""
    except Exception:
        el = ""
    return el.split(",")[0].strip() if el else ""


def _os_env_on(name, default=True):
    import os as _o
    v=_o.environ.get(name)
    return default if v is None else v not in ("0","false","no","")


def _crawl_to_fixpoint(fr, child_fn, echo_fn, skip_fn, log, max_levels=64):
    import os as _os
    max_levels = int(_os.environ.get("FROGNET_DESCEND_MAX_LEVELS", str(max_levels)))
    """BFS the getHosts frontier to a FIXPOINT so EVERY reachable node is discovered in
    one merge, not just children + grandchildren. fr: [(ip, dev, root_via)] frontier;
    child_fn(pip)->[(cip,cname)|cip]; echo_fn(cip)->name; skip_fn(cip,cname)->bool.
    seeds{} dedups (each node crawled once), so the frontier empties exactly when the
    whole reachable set is found; max_levels only guards a pathological cycle. root_via
    is carried UNCHANGED down the branch -- we only ever route to the first hop.
    Returns seeds: {cip: {name, dev, via}}."""
    seeds = {}
    level_names = {1: "children", 2: "grandchildren"}
    level = 0
    while fr and level < max_levels:
        level += 1
        nxt = []
        for pip_, pdev, root_via in fr:
            for child in child_fn(pip_):
                cip, cname = child if isinstance(child, tuple) else (child, "")
                if not cname:
                    cname = echo_fn(cip)
                if skip_fn(cip, cname):
                    continue
                if cip not in seeds:
                    seeds[cip] = {"name": cname, "dev": pdev, "via": root_via}
                    nxt.append((cip, pdev, root_via))
        log(f"DESCEND_SEEDS level={level_names.get(level, f'L{level}')} "
            f"new={len(nxt)} total={len(seeds)}")
        fr = nxt
    return seeds


def descend(disc, active_devs, immediate, *, active_states=None, max_workers=64,
            logger=None):
    """immediate: [(ip, dev)] - the real downstream + upstream connections, each
    with the interface it is connected on. active_states: [(iface, [remote /24s])]
    - the broker's authoritative table of which /24s are reachable through each
    tunnel. Returns number of candidates emitted. Populates disc.CAND; promote()
    installs."""
    active_states = active_states or []
    log = logger or disc.log
    active_devs = list(active_devs)
    disc._descend_named = set()   # per-pass naming dedup

    # ---- Level 0: immediate connections. REAL, one hop, on their known dev.
    seen = set()
    frontier = []
    seg_roots = []
    # [IMMEDIATE_PARALLEL_V1] Pre-probe every immediate neighbour's echo + measure
    # IN PARALLEL. Each is an independent address; serially they each block up to the
    # echo/connect timeout (~15s), so 4 attached eth0 neighbours = up to a minute of
    # dead wall-clock. Fan them out, then run the (order-independent) classification
    # loop below over the cached results single-threaded - all the shared-state
    # mutation (seen, seg_roots, neighbor_via_map, emits) stays serial and unchanged.
    def _probe_immediate(ip, dev):
        probe_addr = ip if disc.is_on_local_subnet(ip) else net_dot(ip, 2)
        el = ""
        try:
            el = disc.echo.echo_probe(probe_addr) or disc.echo.echo_probe(ip) or ""
        except Exception:
            el = ""
        v = _measure(disc, probe_addr, dev)
        return ip, dev, el, v

    _uniq_imm = []
    _seen_pre = set()
    for ip, dev in immediate:
        if not ip or ip in _seen_pre or disc.is_local_ip(ip):
            continue
        _seen_pre.add(ip)
        _uniq_imm.append((ip, dev))
    _probe_results = {}
    if _uniq_imm:
        _iw = min(len(_uniq_imm), max(1, getattr(disc, "wave_workers", 8) * 8))
        with ThreadPoolExecutor(max_workers=_iw, thread_name_prefix="imm") as _ex:
            for _f in as_completed([_ex.submit(_probe_immediate, ip, dev)
                                    for ip, dev in _uniq_imm]):
                _rip, _rdev, _rel, _rv = _f.result()
                _probe_results[_rip] = (_rel, _rv)

    for ip, dev in immediate:
        if not ip or ip in seen or disc.is_local_ip(ip):
            continue
        seen.add(ip)
        # Cached parallel probe result (echo line + measured rtt) for this neighbour.
        el, v = _probe_results.get(ip, ("", None))
        # [TUNNEL_PEER_HEALTH_EMIT_V1] The .2 discovery plane is silent over some
        # tunnels on real hardware: echo/measure to <peer>.2 does not answer across
        # wg even when the tunnel carries. But the tunnel's own peer .1 is the SAME
        # liveness the healthcheck already proved with a SRCLESS :9009 ping-pong.
        # (An identity-src :9009 here false-negatives - the peer cannot return to our
        # .1 over the tunnel - which is exactly why the .2 path and dev_src's
        # identity-src probe both fail on the direct peer while healthcheck passes.)
        # Re-run that exact srcless .1 probe; a PONG proves the peer is reachable, so
        # emit it as a MEASURED immediate rather than discarding a health-proven peer.
        # Behind-peer .1's (a different /24 reached THROUGH this tunnel) do NOT pong
        # srcless - their return path needs our propagated identity - so they stay
        # None here and fall through to the seed probe (identity-src) unchanged.
        if (v is None and dev.startswith("wg")
                and getattr(disc, "verify", None) is not None):
            d1h = net_dot(ip, 1)
            if disc.r.probe_install(d1h, dev, "", "") == 0:   # SRCLESS - mirrors healthcheck
                try:
                    hv = disc.verify.measure_or_loop(d1h, dev)
                finally:
                    disc.r.probe_delete(d1h)
                if isinstance(hv, float):
                    v = hv
        fields = el.split(",") if el else []
        ename = fields[0].strip() if fields else ""
        eid1 = fields[1].strip() if len(fields) > 1 else ""
        eup = fields[2].strip() if len(fields) > 2 else ""   # echoed upstream/segment addr
        off_subnet_lan = (not dev.startswith("wg")
                          and not disc.is_on_local_subnet(ip))
        if off_subnet_lan and not (el and eid1 == ip):
            # off-subnet LAN address that does NOT echo as its own identity is a
            # relayed address leaked into ARP (handled via its on-subnet seg-relay),
            # not a real neighbour - skip so it emits no bogus direct candidate.
            continue
        if (eid1 and eid1.startswith("10.") and dest24_of(eid1) != dest24_of(ip)
                and not disc.is_local_ip(eid1)):
            # SEG-RELAY: on-subnet neighbour whose echo identity is a REMOTE /24
            # (e.g. eth0 .230 that echoes as New-York-2 / 10.28.28.1). Route that
            # remote /24 VIA the relay's routable .1 (or the address itself).
            rtt = v if isinstance(v, float) else 9999.0
            relay_via = net_dot(ip, 1) if ip.rsplit(".", 1)[1] == "2" else ip
            _emit_vouch(disc, eid1, ename, dev, relay_via, rtt, log, form="SEGRELAY")
            if hasattr(disc, "neighbor_via_map"):
                disc.neighbor_via_map[ip] = eid1
            seg_roots.append((eid1, dev, relay_via))
        elif off_subnet_lan:
            # A FrogNet neighbour answering on a LAN dev - its own /24 is a
            # DIFFERENT network (that is normal; every node is its own /24). How we
            # reach it depends on its echoed upstream (field 3):
            #   * upstream is an address on OUR segment (this dev's subnet) -> reach
            #     it via that on-segment leg (seg-relay to its own /24).
            #   * else -> it sits directly on our segment: route its /24 ON THIS DEV.
            # LAN beats any WAN/tunnel vouch for the same /24 (precedence).
            rtt = v if isinstance(v, float) else 9999.0
            if eup and eup.startswith("10.") and disc.is_on_local_subnet(eup) \
                    and eup != ip:
                _emit_vouch(disc, ip, ename or _echo_name(disc, ip), dev, eup, rtt,
                            log, form="LAN_VIA_SEG")
                if hasattr(disc, "neighbor_via_map"):
                    disc.neighbor_via_map[eup] = ip
            else:
                _emit(disc, ip, ename or _echo_name(disc, ip), dev, rtt,
                      "LAN_NEIGHBOR", log, authoritative=True)
            frontier.append(ip)
            continue
        elif isinstance(v, float):
            _emit(disc, ip, ename or _echo_name(disc, ip), dev, v, "IMMEDIATE", log,
                  authoritative=True)
        elif el and eid1 == ip:
            # [IMMEDIATE_ECHO_NO_RTT_V1] A directly-attached immediate that ECHOED its
            # OWN identity (eid1 == ip) is a real host even when we could not measure a
            # float RTT - e.g. the .1 gateway of a subnet we hold a guest LEASE on (our
            # upstream: 130 is a guest on 250's LAN, so 10.250.250.1 is directly
            # attached). Without this it is used as a via but never added as a host, so
            # it is absent from the hosts table and can never be the control .1.
            _emit(disc, ip, ename or _echo_name(disc, ip), dev, 9999.0, "IMMEDIATE",
                  log, authoritative=True)
        # Expand regardless of our own RTT: even a connection we time out on can
        # name its children (the getHosts is a query TO it, one hop away).
        frontier.append(ip)

    # ---- Levels 1 & 2: children, then grandchildren, via getHosts.
    #      Each seed REMEMBERS the parent it was learned through and the dev/via
    #      that reached that parent - because a getHosts child is reached by one
    #      more hop THROUGH its parent (walk's [VOUCH_ROUTE_V1]/[HOP_VIA_V1]).
    #      A relay-only child (e.g. New-York-2 via 10.102.60.230 on eth0) will
    #      NEVER answer a direct probe on our interfaces; it is routed through the
    #      parent that serves it. seed_ip -> {name, dev, via}
    seeds = {}
    # frontier carries (ip, dev, root_via): root_via is the FIRST-HOP next-hop we
    # reach this whole branch through - the immediate connection at the root. It
    # stays CONSTANT down children and grandchildren, because we only ever route to
    # the next hop: everything beyond the immediate is reached THROUGH that same
    # immediate, never through an intermediate child we have no route to. Tunnel
    # root -> "" (rides the tunnel); LAN root -> the immediate's .1.
    fr = []
    for ip, dev in immediate:
        if ip in seen and not disc.is_local_ip(ip):
            if dev.startswith("wg"):
                # Tunnel root: children ride the tunnel (scope-link, via "").
                root_via = ""
            elif disc.is_on_local_subnet(ip):
                # LAN neighbour on MY OWN segment (e.g. eth0 10.250.250.191 on
                # Seattle5's 10.250.250): it is a DIRECT next hop on the wire.
                # Children learned through it route VIA ITS ADDRESS on this dev -
                # NOT the subnet .1 (which is me). This is the short LAN path.
                root_via = ip
            else:
                # off-subnet LAN neighbour (uplink gateway on a different /24)
                root_via = net_dot(ip, 1)
            fr.append((ip, dev, root_via))
    # seg-relays are crawled like any frontier entry so their children surface as
    # seeds. NO relay tagging: every seed is resolved uniformly by _probe_and_emit,
    # which probes each next-hop avenue and PROVES it (reflect on .2, or the .1
    # :9009 ping-pong for a node behind a relay). A getHosts listing is a claim, not
    # a route - there is no vouch path.
    fr.extend(list(seg_roots))                                     # (ip, dev, root_via)
    # [DESCEND_FRONTIER_FROM_KNOWN_V1] Seed the frontier ALSO from what we ALREADY know:
    # the installed /24 routes (the route-bearing hosts). The crawl itself stays two
    # levels (children + grandchildren); DEPTH comes from re-merging. Each pass advances
    # the frontier edge two hops deeper -- merge 1 crawls Sea5 -> Sea6, Sea3; merge 2's
    # frontier now includes Sea3, so it crawls Sea3 -> Sea2 -- and runAgain (routes_mutated)
    # keeps the merges coming until a pass installs no new /24. A frontier pinned only to
    # the immediate never moved, so the crawl stalled at the grandchild and runAgain went
    # to 0 one hop short. root_via is the route's own first hop (constant down the branch).
    _seen_fr = {e[0] for e in fr}
    try:
        _tbl = disc.r.k.route_show("") if _os_env_on("FROGNET_DESCEND_FRONTIER_FROM_ROUTES", True) else ""
    except Exception:
        _tbl = ""
    for _ln in _tbl.replace(";", "\n").split("\n"):
        _p = _ln.split()
        if not _p or not _p[0].startswith("10.") or not _p[0].endswith("/24"):
            continue
        _one = net_dot(_p[0].split("/")[0], 1)
        if _one in _seen_fr or disc.is_local_ip(_one) or disc.is_on_local_subnet(_one):
            continue
        _dev = _p[_p.index("dev") + 1] if "dev" in _p else ""
        _via = _p[_p.index("via") + 1] if "via" in _p else ""
        if _via:
            _rv = _via
        elif _dev.startswith("wg"):
            _rv = ""                       # tunnel: children ride the tunnel (via "")
        else:
            continue                       # directly-connected LAN: already immediate/local
        fr.append((_one, _dev, _rv))
        _seen_fr.add(_one)
    # ---- Crawl two levels (children + grandchildren) from every frontier seed. The seed
    #      set now includes the known route-bearing hosts, so successive runAgain-driven
    #      merges walk the chain to its end; a single pass is still bounded (2 levels).
    def _skip(cip, cname):
        if cname and cname.endswith(".frognet"):
            return True
        if not (cip and cip.startswith("10.")):
            return True
        if disc.is_local_ip(cip) or disc.is_on_local_subnet(cip):
            return True
        return False
    seeds = _crawl_to_fixpoint(
        fr,
        lambda pip_: _children(disc, pip_),
        lambda cip: _echo_name(disc, cip),
        _skip,
        log,
        max_levels=2,
    )

    if not seeds:
        log("DESCEND probe_seeds=0")
        return 0

    # ---- Resolve each node to its SHORTEST next hop. THE ALGORITHM:
    #  Every node is already in /etc/hosts (identical on every node), so we are not
    #  finding nodes - we are finding the shortest NEXT HOP to each. For each node X:
    #  probe X THROUGH every directly-connected neighbour, on EVERY bearer equally
    #  (tunnels are optional; LAN is first-class). Every neighbour that answers is a
    #  positive avenue; emit it with its measured RTT. promote installs all avenues
    #  ranked by RTT (winner metric 22, fallbacks 100+); the loop detector prunes
    #  hairpins; kind is NEVER a tiebreaker. A route only ever reaches the next hop.
    #
    #  Directly-connected next hops, all bearers equal:
    #    - tunnel dev  -> next hop is the dev itself (via="" scope-link on its /30)
    #    - LAN neighbour N on one of our segments -> next hop is (N's dev, via=N)
    next_hops = []
    for ip, dev in immediate:
        if disc.is_local_ip(ip):
            continue
        if dev.startswith("wg"):
            next_hops.append((dev, "", ""))        # tunnel is the next hop
        elif disc.is_on_local_subnet(ip):
            # LAN neighbour on my own segment. It is already a routable host on the
            # wire, so it IS its own next hop - route VIA the neighbour's address.
            # Only when we reached it at a .2 DISCOVERY ALIAS is the routable gateway
            # its .1; a plain host address (e.g. 10.250.250.191) routes via itself.
            # (net_dot(ip,1) here would be the subnet .1 = THIS node - a route via
            # ourselves, which is wrong.)
            route_via = net_dot(ip, 1) if ip.rsplit(".", 1)[1] == "2" else ip
            next_hops.append((dev, ip, route_via))
    # dedup, preserve order
    seen_nh = set(); nh = []
    for h in next_hops:
        if h not in seen_nh:
            seen_nh.add(h); nh.append(h)
    next_hops = nh

    def _probe_node(sip, name, avenues):
        """Probe node sip through the given avenues (dev, probe_via, route_via).
        Returns the answering avenues. THE RULE: route only to the next hop -
        avenues are the directly-reachable next hops (immediate neighbours for
        level 1; the parent's .1 for a deeper node, which the previous level's
        promote already installed a route to)."""
        if disc.is_local_ip(sip) or disc.is_on_local_subnet(sip):
            return sip, name, []
        results = []
        for dev, probe_via, route_via in avenues:
            if route_via and route_via == net_dot(sip, 1):
                continue
            v = _probe_through(disc, sip, dev, probe_via)
            if v == "LOOP":
                log(f"IFACE_SKIP dest={dest24_of(sip)} dev={dev} via={route_via} reason=loop")
                getattr(disc, "loop_memo", set()).add((dest24_of(sip), route_via))
                continue
            if isinstance(v, float):
                results.append((route_via, dev, v))
        return sip, name, results

    def _probe_and_emit(batch):
        """Probe a batch of (sip, avenues) in parallel, emit answering candidates."""
        cnt = 0
        if not batch:
            return 0
        workers = min(getattr(disc, "wave_workers", 8) * 8, max(1, len(batch)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="descend") as ex:
            futs = [ex.submit(_probe_node, sip, seeds[sip]["name"], avs)
                    for sip, avs in batch]
            for fut in as_completed(futs):
                sip, name, results = fut.result()
                if results:
                    for route_via, dev, rtt in results:
                        if route_via:
                            _emit_vouch(disc, sip, name, dev, route_via, rtt, log, form="HOP")
                        else:
                            _emit(disc, sip, name, dev, rtt, "HOP", log)
                elif name and not (disc.is_local_ip(sip) or disc.is_on_local_subnet(sip)):
                    _add_host(disc, name, net_dot(sip, 1),
                              seeds[sip].get("dev", ""), authoritative=False)
                cnt += 1
        return cnt

    # ---- probe: ONE uniform pass, every seed through every next-hop avenue -----
    # Gather -> locate -> route: probe each seed through every next_hop (tunnel /
    # gateway / on-segment neighbour). Each avenue is PROVEN - reflect on .2 (fast),
    # or the .1 :9009 ping-pong for a node behind a seg-relay whose .2 the relay
    # won't forward. A node reached through a relay is not special: the relay's
    # segment address is simply one of its next_hop avenues, and the .1 probe rides
    # `via <relay> dev eth0` - exactly John's proven manual route. Multiple answering
    # avenues per /24 is the goal; promote ranks by RTT. No vouch, no relay taxonomy.
    n = 0
    direct_batch = [(sip, next_hops) for sip in seeds
                    if not (disc.is_local_ip(sip) or disc.is_on_local_subnet(sip))]
    n += _probe_and_emit(direct_batch)
    log(f"DESCEND done candidates={n}")
    return n


def _emit_vouch(disc, ip, name, dev, via, rtt, log, form="SEED"):
    """A getHosts child (or seg-relay dest) routed through its via. kind='vouch' so
    promote does not verify-backout it. rtt is the measured value if a direct probe
    answered, else a high fallback so a directly-measured path wins."""
    dest24 = dest24_of(ip)
    src = disc.dev_src(dev) if hasattr(disc, "dev_src") else ""
    kind = "tunnel" if dev.startswith("wg") else "lan"
    r = int(round(rtt)) if isinstance(rtt, float) else 9999
    disc.CAND[dest24].append(f"{r}|{via}|{dev}|0|{src}|vouch|{name}")
    _add_host(disc, name, net_dot(ip, 1), dev, authoritative=False)
    log(f"CANDIDATE dest={dest24} via={via} dev={dev} onlink=0 rtt={r} "
        f"kind=vouch form={form} host={name}")


