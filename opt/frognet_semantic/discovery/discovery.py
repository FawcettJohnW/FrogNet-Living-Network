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
discovery.py - the discovery decision engine, ported from sync_interfaces.sh.

The bash globals (CAND, WALK_SEEN, DEAD_IFACES, LOCAL_IPS/SUBNETS) become
instance state. walk() / promote() keep the exact control flow; all I/O is via
injected sources (sources.py) and the proven route writer (routes.Routes over
kernel). Nothing here invents a path or an arithmetic network - the echo is
self-describing (.1 in field 2); .2 proves, .1 carries.

bash line refs in comments.
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

# [FAILFAST_V1] Hard import. If core.not_frognet cannot load, the install is
# broken; a silent no-op stub here previously disabled the whole negative-cache
# subsystem without a word.
from core.not_frognet import mark as _nf_mark, is_marked as _nf_is_marked

from .routes import Routes, PROBE_METRIC, WINNER_METRIC, ALIAS_METRIC, FALLBACK_BASE

# [WINNER_HYSTERESIS_V1] Changing a winner is an `ip route replace` - it yanks the
# next-hop on the /24 and interrupts any flow in flight. So a challenger must be a
# CLEAR win to justify that: beat the installed metric-22 winner's measured RTT by
# at least 50% (be <= half of it) to flip. Anything less is jitter between near-
# equal paths (e.g. three LAN relays to one box at single-digit ms); keeping the
# settled winner stops the /24 rewriting every pass and latching runAgain forever.
# Tunable via FROGNET_WINNER_HYSTERESIS (1.0 = flip on any improvement, 0.0 = never).
WINNER_HYSTERESIS = float(os.environ.get("FROGNET_WINNER_HYSTERESIS", "0.50"))

# Build identifier - printed once at import so the running build is visible in
# the merge/reconcile logs. If you don't see this line, this file isn't loaded.
__BUILD__ = "discovery.py DESCEND+NAMING+TUNNEL+METRICS+NOCURL+BGIDLE  build=20260710-ax-LANVIA-CLEAN"
print(f"[FROGNET-BUILD] {__BUILD__}", file=sys.stderr)

_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
MAX_DEPTH = 2


def is_ip(s: str) -> bool:
    return bool(_IP_RE.match(s or ""))


def is_10x(s: str) -> bool:
    return (s or "").startswith("10.")


def net_dot(ip: str, n) -> str:          # 10.x.y.z N -> 10.x.y.N   (bash 151)
    return ip.rsplit(".", 1)[0] + "." + str(n)


def dest24_of(ip: str) -> str:           # bash 152
    return ip.rsplit(".", 1)[0] + ".0/24"


class Discovery:
    def __init__(self, routes: Routes, echo, rtt, gethosts, broker, hoststore,
                 local_ips, dev_src_map, logger=lambda s: None, propagator=None,
                 reflect=None, self_identity: str = "", verify=None,
                 own_subnet: str = "", has_own_uplink: bool = True,
                 uplink_dev: str = "", caps=None):
        self.r = routes
        # [VOUCH_TS_GATE_V1] caps: zero-arg callable -> {host_dot1_ip: ts_age_s}
        # from the control plane's SD:capability tuples, or None when no
        # evidence system is reachable (cold boot / control down) - gate off.
        self.caps = caps
        self._caps_cache = ...   # Ellipsis = not yet fetched this merge
        self.echo = echo
        self.rtt = rtt
        self.gethosts = gethosts
        self.broker = broker
        self.hosts = hoststore
        self.propagator = propagator
        self.reflect = reflect          # [REFLECT_PROBE_V1 emitter] or None (skip)
        self.verify = verify            # [VERIFY_ROUTE_V1] post-finalize alive or None
        self.self_identity = self_identity  # this node's identity string (build_live wires it)
        self.log = logger
        self.local_ips = set(local_ips)
        # [NEIGHBOR_FROGNET_ADDR_V1] DHCP next-hop -> directly-attached neighbor's
        # FrogNet .1. A downstream LAN child is reached `via <its DHCP lease on our
        # segment>`, but its identity (and where its proxy answers) is the .1 of
        # the subnet it serves. We learn the mapping at the seg_relay echo (we
        # probe the on-segment client directly and it returns its identity). The
        # neighbor scan translates downstream vias through this so notifications
        # go to the FrogNet address, not the DHCP lease.
        self.neighbor_via_map: dict = {}
        self.local_subnets = {ip.rsplit(".", 1)[0] for ip in local_ips}
        self.dev_src_map = dict(dev_src_map)
        # [TRANSIT_FROM_WINNERS_V1] Transit advertisement is derived from the
        # winners promote() installs, not a separate route scan or DHCP-lease
        # probe. Each winning /24 on a SERVED dev is a subnet that enters this
        # node from below; per John's rule, a GATEWAY (own WAN uplink) therefore
        # transits its ENTIRE downstream LAN subtree. A LAN-child excludes its
        # mesh-ward uplink dev (the path UP), advertising only children below it.
        self.own_subnet = own_subnet            # this node's served /24, e.g. 10.250.250.0/24
        self.has_own_uplink = bool(has_own_uplink)  # True=gateway (non-10.x WAN), False=LAN-child
        self.uplink_dev = uplink_dev            # mesh-ward dev to EXCLUDE on a LAN-child
        self.computed_transit: list[str] = []   # filled by promote(); read by the sync path
        # [REACH_PLANE_V1] /24s reached over a tunnel (wg* winner) = the WAN plane,
        # from the SAME loopback-validated winners (LOOP candidates were already
        # dropped in walk). filled by promote(); persisted to a tuple by the merge so
        # the merge-end service-host election can split LAN vs WAN by reading, not
        # re-probing. Complement of computed_transit (which is the local gatewayed set).
        self.computed_wan: list[str] = []
        # bash globals
        self.CAND: dict[str, list[str]] = defaultdict(list)
        self.WALK_SEEN: set[str] = set()
        self.loop_memo: set = set()   # [LOOP_MEMO_V1] (dest,via) that looped, this run
        self.DEAD_IFACES: set[str] = set()
        # [WAVE_PARALLEL_V1] prewarm cache + lock; parallel_waves opt-in flag.
        self._probe_cache: dict = {}
        import threading as _thr
        self._probe_cache_lock = _thr.Lock()
        self.parallel_waves = False
        self.wave_workers = 8

    # -- helpers (bash 148-157) --------------------------------------------
    def is_local_ip(self, ip: str) -> bool:
        return ip in self.local_ips

    def is_on_local_subnet(self, ip: str) -> bool:
        return ip.rsplit(".", 1)[0] in self.local_subnets

    def dev_src(self, dev: str) -> str:
        # The source for a DISCOVERED FrogNet route is this node's identity (.1 of
        # its served /24) - the only address reachable from everywhere in the mesh.
        # NOT the transit /30 (dev_src_map) and NOT a borrowed uplink lease: a host
        # beyond a tunnel cannot route a reply back to a /30 transit address or a
        # lease (neither is propagated), so sourcing from them silently breaks the
        # return path for every child behind the peer (NY1: 10.120.120/10.160.160 via
        # wg2 src=10.253.203.90 -> 100% loss). Safe because identity_preflight
        # guarantees this node bears its identity. Falls back to the old behavior
        # only when no identity is threaded (sim setups that don't model one).
        if self.self_identity:
            return self.self_identity
        return self.dev_src_map.get(dev, "") if dev.startswith("wg") else ""

    # =====================================================================
    #  [WAVE_PARALLEL_V1] Breadth-first wave scheduler.
    #
    #  John's model: wave 1 = immediate local addresses; wave 2 = their children
    #  (all of them); wave 3 = grandchildren. Dedup by DESTINATION before probing
    #  - we don't care who reached a dest or by what path, only that the dest is
    #  probed ONCE - so every dest in a wave is independent and they parallelize
    #  with no shared .2/32 collision (each dest has a unique .2).
    #
    #  Design that preserves EXACT sequential output: only the network-bound
    #  probe phase is parallelized. For each distinct (dev, ip, parent_via) in a
    #  wave we PREWARM the echo/rtt/reflect results into a cache (parallel,
    #  isolated .2 per dest). Then the UNMODIFIED sequential walk() runs over the
    #  same seeds - its echo/rtt/reflect calls hit the warm cache instead of
    #  re-probing - so all CAND/WALK_SEEN/hosts writes, vouch gating, and child
    #  gathering happen single-threaded and identically to today. The parallelism
    #  overlaps the slow I/O (echo timeouts dominate wall time); the decision
    #  logic is byte-for-byte the existing code.
    # =====================================================================
    def _probe_cache_key(self, dev, pip, disc_via):
        return f"{dev}|{pip}|{disc_via}"

    def _prewarm_probe(self, dev, ip, parent_via):
        """Pure-ish network probe for ONE dest, run in a worker thread. Installs
        this dest's unique .2/32, runs echo, and caches the result. Returns the
        cache key list it populated. NO writes to CAND/WALK_SEEN/hosts. Routes
        for distinct dests never collide (unique .2); same-dest dedup upstream
        guarantees one worker per .2."""
        if not (dev and is_ip(ip) and is_10x(ip)):
            return
        if ip.startswith("10.254.") or self.is_local_ip(ip):
            return
        kind = "tunnel" if dev.startswith("wg") else "lan"
        # mirror walk's dest_seed derivation for the common (.1 seed) case; the
        # on-segment-client identity acquisition stays in walk() (rare, cheap).
        host_path = ""
        if kind == "lan" and self.is_on_local_subnet(ip) and ip.rsplit(".", 1)[1] != "1":
            return  # identity-acquisition path handled in walk(), not prewarmed
        dest_seed = host_path or ip
        pip = net_dot(dest_seed, 2)
        disc_via = "" if kind == "tunnel" else (parent_via if parent_via else ip)
        src = self.dev_src(dev)
        key = self._probe_cache_key(dev, pip, disc_via)
        with self._probe_cache_lock:
            if key in self._probe_cache:
                return
        # install this dest's unique probe route, echo, cache. Leave the route
        # up (walk reuses it for getHosts; walk does the probe_delete).
        rc = self.r.probe_install(pip, dev, disc_via, src, onlink=False)
        el = self.echo.echo_probe(pip) if rc == 0 else ""
        rtt = self.rtt.measure_rtt(dev, pip) if el else ""
        with self._probe_cache_lock:
            self._probe_cache[key] = {"echo": el, "rtt": rtt, "rc": rc}

    def prewarm_wave(self, seeds, max_workers=8):
        """Prewarm probes for a wave of (dev, ip, parent_via) seeds in parallel,
        deduped by destination .2 so each dest is probed exactly once."""
        import concurrent.futures as _cf
        # dedup by the cache key's dest component
        seen_keys = set()
        work = []
        for dev, ip, parent_via in seeds:
            if not (dev and is_ip(ip) and is_10x(ip)):
                continue
            kind = "tunnel" if dev.startswith("wg") else "lan"
            disc_via = "" if kind == "tunnel" else (parent_via if parent_via else ip)
            pip = net_dot(ip, 2)
            k = self._probe_cache_key(dev, pip, disc_via)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            work.append((dev, ip, parent_via))
        if not work:
            return 0
        # [PREWARM_PER_PIP_SERIAL_V1] A probe route is `<pip>/32 metric 6`; the
        # kernel keys a route on (dest, metric, table) - dev is NOT in the key. So
        # two `ip route replace <pip>/32 dev wgX metric 6` for the SAME pip on
        # different devs CLOBBER each other, and whichever wins the race is the dev
        # the echo/rtt is actually measured over - regardless of which (dev,pip)
        # candidate the worker thinks it is probing. The live Seattle5 trace shows
        # exactly this: `spec="... dev wg0 ..." after="... dev wg1 ..."`. The corrupted
        # per-dev rtt makes a relay-reached subnet (seen over every tunnel at
        # near-equal cost) reshuffle its winner every pass -> slash24 mutates and
        # the merge never converges. Fix: parallelize ACROSS distinct pips, run one
        # pip's devs SEQUENTIALLY so each (dev,pip) probe owns the /32 while it
        # measures. Distinct dests (the common case) still run fully in parallel.
        from collections import OrderedDict
        groups: "OrderedDict[str, list]" = OrderedDict()
        for dev, ip, parent_via in work:
            groups.setdefault(net_dot(ip, 2), []).append((dev, ip, parent_via))

        def _run_group(items):
            for a in items:
                self._prewarm_probe(*a)

        with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(_run_group, list(groups.values())))
        self.log(f"WAVE_PREWARM seeds={len(seeds)} distinct={len(work)} "
                 f"pips={len(groups)} cached={len(self._probe_cache)}")
        return len(work)

    # =====================================================================
    #  walk - depth-2 discovery (bash 350). Prove .2 path, record candidate,
    #  getHosts, recurse. NO /24 installed here.
    # =====================================================================
    # =====================================================================
    #  [ALIVE_9009_RETRY_V1] retry a NEGATIVE :9009 verdict before believing
    #  it. Field case (New-York-2 runMerge 2026-06-20): the ping-pong is
    #  acknowledged to false-negative on high-RTT/tunnel paths; one miss at
    #  the walk dropped measured candidates to vouch-only and flapped the
    #  single-winner roles pass to pass. Decisive verdicts - a float rtt,
    #  "LOOP", "REFUSED" ([NF_CONTRACT_V1] definitive absence), alive=True -
    #  are NEVER retried; only the ambiguous negative (None / alive False)
    #  is re-asked, up to $FROGNET_ALIVE_9009_TRIES (default 3, read at call
    #  time). A truly dead path still fails after all tries: the gate is
    #  unchanged, the MEASUREMENT is made honest. This is not a fallback -
    #  nothing is hidden and the final negative is still believed.
    # =====================================================================
    @staticmethod
    def _alive_tries() -> int:
        return max(1, int(os.environ.get("FROGNET_ALIVE_9009_TRIES", "3")))

    # =====================================================================
    #  [VOUCH_TS_GATE_V1] John's rule, 2026-07-06: "The tuple has a
    #  timestamp. Using the timestamp is the responsibility of the
    #  consumer." A vouch is hearsay about a host; the host's OWN freshest
    #  assertion is its SD:capability tuple (re-asserted every ~5 min by
    #  every living node, reaped when dead). Before spending probes on a
    #  vouch, the consumer checks whether the vouched .1 still asserts
    #  itself. Absent-or-ancient in a POPULATED store => the vouch is
    #  hearsay about a node that stopped speaking => skip candidate,
    #  propagation, and the child walk. An unreachable or EMPTY store means
    #  no evidence system yet (cold bootstrap): gate off, legacy behavior,
    #  said loudly once.
    # =====================================================================
    def _caps_ages(self):
        if self._caps_cache is ...:
            ages = None
            if self.caps is not None:
                try:
                    ages = self.caps()
                except Exception as e:
                    self.log(f"VOUCH_TS_GATE evidence_fetch_failed err={e} "
                             f"gate=off")
                    ages = None
            if not ages:          # None or {} - no evidence system
                if self.caps is not None:
                    self.log("VOUCH_TS_GATE store_empty_or_unreachable gate=off")
                ages = None
            self._caps_cache = ages
        return self._caps_cache

    def _vouch_stale(self, dot1: str):
        """None = fresh or gate off; else a reason string for the log."""
        ages = self._caps_ages()
        if ages is None:
            return None
        max_age = int(os.environ.get("FROGNET_VOUCH_MAX_AGE_S", "3600"))
        age = ages.get(dot1)
        if age is None:
            return "no_self_assertion_in_populated_store"
        if age > max_age:
            return f"self_assertion_age={age}s>max={max_age}s"
        return None

    def _alive_measure(self, target: str, dev: str):
        last = None
        fails = []
        for _ in range(self._alive_tries()):
            v = self.verify.measure_or_loop(target, dev)
            if isinstance(v, float) or v == "LOOP" or v == "REFUSED":
                return v
            fails.append(getattr(self.verify, "_last_fail", None) or "?")
            last = v
        # [DIAG_TUNNEL_DARK_V1] Every retry missed. For tunnel devs, capture the
        # complete reachable state AT the miss - per-try failure reasons, the
        # WireGuard view, and the kernel's chosen route - so an intermittent
        # dark window self-documents instead of needing a lucky live wg-show.
        # Collection is bounded ([KERNEL_CMD_DEADLINE_V1] _run) and must never
        # change the verdict.
        if dev.startswith("wg"):
            from .kernel import _run
            _, hs = _run(["wg", "show", dev, "latest-handshakes"])
            _, tx = _run(["wg", "show", dev, "transfer"])
            # oif-constrained: the probe socket is BINDTODEVICE-pinned, so the
            # unbound `ip route get` view misleads (proven 2026-07-06: printed
            # the ISP-default path for a wg2-bound probe). Capture BOTH views -
            # the bound path the socket actually used, and the unbound path
            # everything non-pinned would take (the 10/8-leak detector).
            _, rt = _run(["ip", "route", "get", target, "oif", dev])
            _, rt_unbound = _run(["ip", "route", "get", target])
            self.log(f"[DIAG-TUNNEL-DARK] dev={dev} target={target} "
                     f"tries={len(fails)} fails={fails} "
                     f"handshakes={hs.strip()!r} transfer={tx.strip()!r} "
                     f"route_bound={rt.strip()!r} "
                     f"route_unbound={rt_unbound.strip()!r}")
        return last

    def _alive_ok(self, dest1: str, dev: str) -> bool:
        for _ in range(self._alive_tries()):
            if self.verify.alive(dest1, dev):
                return True
        return False

    def walk(self, dev: str, ip: str, depth: int, parent_via: str = "") -> None:
        # [HOP_VIA_V1] dedup PER PATH ATTEMPTED, not per destination. The same
        # node may be reachable more than one way (a direct one-hop seed AND a
        # getHosts relay); each distinct next-hop attempt must be allowed so a
        # failed/looping relay path can't poison the working direct path. Keyed
        # on the candidate via (parent_via for a child, else the seed ip).
        wkey = f"{dev}|{ip}|{parent_via or ip}"
        if not (dev and is_ip(ip) and is_10x(ip)):
            return
        if ip.startswith("10.254."):                 # chorus on-link (bash 356)
            return
        if self.is_local_ip(ip):                      # that's us (357)
            return
        if self.is_on_local_subnet(ip) and ip.rsplit(".", 1)[1] == "2":  # (360)
            return
        if wkey in self.WALK_SEEN:                     # (361)
            return
        self.WALK_SEEN.add(wkey)

        # [NOT_FROGNET_SKIP_V1] A host proven non-FrogNet earlier THIS session
        # (not_frognet.flush() clears the cache at each merge top) is not
        # re-probed. The walk now READS the same negative cache it already
        # writes via _nf_mark - without this read the cache was write-only here,
        # so every convergence pass within one merge re-paid the full probe
        # budget on the same stray LAN clients. is_marked() protects .1/.2, so a
        # real FrogNet node that is merely down is never skipped.
        if _nf_is_marked(ip):
            self.log(f"WALK dev={dev} ip={ip} decision=SKIP_NOT_FROGNET "
                     f"reason=cached_non_frognet_this_session")
            return

        kind = "tunnel" if dev.startswith("wg") else "lan"

        # ---- identity acquisition for on-segment non-.1 clients (bash 378-393)
        host_path = ""
        seg_relay = ""
        if kind == "lan" and self.is_on_local_subnet(ip) and ip.rsplit(".", 1)[1] != "1":
            # [PINGPONG_GATE_V1] Gate the on-segment leg on the :9009 ping-pong -
            # the SAME probe every other route kind uses - never the HTTP echo.
            # The daemon binds 0.0.0.0:9009 so it answers on this LAN address;
            # the proxy echo can answer only on the node's .1 identity, so an echo
            # gate false-negatives a live on-segment FrogNet node AND never runs
            # the loop check. Ping-pong gates reachability and loop here too; echo
            # is demoted below to a post-gate identity fetch only.
            if self.verify is not None:
                gate = self._alive_measure(ip, dev)   # [ALIVE_9009_RETRY_V1]
                if gate == "LOOP":
                    self.log(f"WALK dev={dev} ip={ip} decision=LOOP_9009 "
                             f"target={ip} via_candidate=onlink dev={dev} "
                             f"reason=on_segment_path_loops_through_us")
                    return
                if not isinstance(gate, float):
                    # [NOT_FROGNET_V1]+[NF_CONTRACT_V1] Only a DEFINITIVE refusal
                    # marks the negative cache (not_frognet.py contract: a
                    # timeout must NEVER mark - a slow real node was previously
                    # poisoned here until the next merge).
                    if gate == "REFUSED":
                        _nf_mark(ip, "on_segment_client_no_pong")
                        self.log(f"WALK dev={dev} ip={ip} decision=NOT_FROGNET "
                                 f"reason=on_segment_client_no_pong")
                    else:
                        self.log(f"WALK dev={dev} ip={ip} decision=NOT_ALIVE "
                                 f"reason=no_pong_timeout unmarked=1")
                    return
            # Passed the ping-pong gate (or no verify backend wired) -> acquire
            # identity (which /24 it fronts) via echo. With a verify backend echo
            # is ONLY an identity fetch; with none, echo is still the gate.
            pre = self.echo.echo_probe(ip)
            if not pre:
                if self.verify is not None:
                    self.log(f"WALK dev={dev} ip={ip} decision=ALIVE_NO_IDENTITY "
                             f"reason=pong_ok_echo_no_identity")
                    return
                _nf_mark(ip, "on_segment_client_no_echo")
                self.log(f"WALK dev={dev} ip={ip} decision=NOT_FROGNET "
                         f"reason=on_segment_client_no_echo")
                return
            fields = pre.split(",")
            host_path = fields[1].strip() if len(fields) > 1 else ""
            if not (is_ip(host_path) and is_10x(host_path)):
                self.log(f"WALK dev={dev} ip={ip} decision=BAD_IDENTITY")
                return
            seg_relay = ip
            if self.is_on_local_subnet(host_path):     # hosts our own net -> nothing (390)
                return

        # destination network: client -> its hosted net; else seed IS the .1 (396)
        dest_seed = host_path or ip
        pip = net_dot(dest_seed, 2)                     # probe target = dest .2
        self.log(f"WALK dev={dev} ip={ip} depth={depth} kind={kind} probe={pip} "
                 f"parent_via={parent_via or 'none'} seg_relay={seg_relay or 'none'}")

        # ---- candidate next-hop (hop-to-hop, empirical) -------------------
        # [HOP_VIA_V1] The via is the ONE-HOP address we are talking to, never
        # the dest's own .1/.2 and never onlink. For a directly-seeded neighbor
        # that is `ip` (the address we reached it at on this connected link);
        # for a getHosts child it is the via we used to reach its PARENT
        # (propagated down) - reaching the child is one hop: hand it to the
        # parent, whose own routing makes the next decision. Tunnels keep the
        # dev-only form (matches the box's `dev wgN scope link` winners).
        if kind == "tunnel":
            cands = [("TUNNEL", "")]
        else:
            cands = [("HOP", parent_via if parent_via else ip)]

        src = self.dev_src(dev)
        chosen_label = ""
        chosen_via = ""
        echo_line = ""
        rtt = ""

        # [PINGPONG_FASTPATH_V1] If a winner route for this dest is ALREADY
        # installed on this dev and the dest still answers :9009 over it, the path
        # is up and working - confirm it in place and skip the probe-install/echo
        # candidate dance entirely. getHosts (the 2nd/3rd-level investigation) still
        # runs below, and an unconfirmed dest is still reaped (it never lands in
        # CAND/winners). This never blanket-trusts the table: it fires ONLY for the
        # dest being discovered on this seed, ONLY when the installed winner is on
        # this same dev (so the candidate it records is byte-identical to what the
        # full walk would, hence install_if_changed is a KEEP - no mutation), and
        # ONLY on a live :9009 float. A LOOP or no-answer on the existing route does
        # NOT fast-path: it falls through to the full walk, which re-derives and, if
        # it cannot re-confirm, leaves the dest out of `verified` so the reap removes
        # it. The :9009 probe (and the identity echo) ride the existing /24 - no /32
        # is installed, so there is nothing to tear down. echo here is identity-only
        # (per John: the tunnel ping OR frognet_echo across the tunnel both serve).
        if self.verify is not None and self.r.winner_dev(dest24_of(dest_seed)) == dev:
            fp = self._alive_measure(pip, dev)   # [ALIVE_9009_RETRY_V1]
            self.log(f"[DIAG-FASTPATH] dest={dest24_of(dest_seed)} dev={dev} "
                     f"target={pip} verdict="
                     f"{'LOOP' if fp == 'LOOP' else ('rtt' if isinstance(fp, float) else 'none')} "
                     f"value={fp}")
            if isinstance(fp, float):
                chosen_label = "TUNNEL" if kind == "tunnel" else "HOP"
                chosen_via = "" if kind == "tunnel" else (parent_via if parent_via else ip)
                echo_line = self.echo.echo_probe(pip) or ""
                rtt = str(int(fp))
                self.log(f"WALK dev={dev} ip={ip} pip={pip} decision=FASTPATH_KEEP "
                         f"existing_winner_dev={dev} rtt={rtt}")

        for label, disc_via in (cands if not chosen_label else []):  # bash 422-432
            onlink = False                              # hop routes never use onlink
            # [WAVE_PARALLEL_V1] Reuse the prewarm's cached echo for identity on a
            # cache hit; the prewarm left the .2/32 installed so the gate below
            # still rides it. Cache miss installs live.
            ckey = self._probe_cache_key(dev, pip, disc_via)
            cached = None
            with self._probe_cache_lock:
                cached = self._probe_cache.get(ckey)
            if cached is not None and cached["rc"] != 0:
                continue
            if cached is None:
                if self.r.probe_install(pip, dev, disc_via, src, onlink=onlink) != 0:
                    continue

            if self.verify is not None:
                # [PINGPONG_GATE_V1] Reachability is gated on the :9009 ping-pong
                # for EVERY candidate kind - tunnel, LAN, relayed alike - never the
                # HTTP echo. echo false-negatives a peer whose proxy is down while
                # its daemon answers :9009, and never catches a hairpin; the :9009
                # probe (post [PINGPONG_RETURN_DEV_V1]) no longer false-negatives
                # over tunnels, so nothing is left to justify an echo gate. LOOP ->
                # this path bends back through us: drop the whole seed (folds in the
                # old standalone alive-step check below). None -> not reachable on
                # this candidate, try the next. float -> reachable; echo is consulted
                # ONLY for identity, with a seed-.1 fallback (broker/getHosts name it
                # below). An echo-less child whose :9009 is dead falls through to
                # FAIL_ECHO and is served by the vouch path exactly as before.
                v = self._alive_measure(pip, dev)   # [ALIVE_9009_RETRY_V1]
                self.log(f"[DIAG-LOOP9009] dev={dev} ip={ip} target={pip} "
                         f"via={disc_via or 'onlink'} label={label} "
                         f"verdict={'LOOP' if v == 'LOOP' else ('rtt' if isinstance(v, float) else 'none')} "
                         f"value={v}")
                if v == "LOOP":
                    self.log(f"WALK dev={dev} ip={ip} decision=LOOP_9009 "
                             f"target={pip} via_candidate={disc_via or 'onlink'} "
                             f"dev={dev} reason=candidate_path_loops_through_us")
                    self.r.probe_delete(pip)
                    return
                if not isinstance(v, float):
                    self.r.probe_delete(pip)
                    continue
                el = cached["echo"] if cached is not None else self.echo.echo_probe(pip)
                chosen_label = label
                chosen_via = disc_via
                echo_line = el or ""
                if cached is not None:
                    rtt = cached["rtt"] or str(int(v))
                elif el:
                    rtt = self.rtt.measure_rtt(dev, pip)
                else:
                    rtt = str(int(v))          # echo-less: the ping-pong rtt
                break
            else:
                # legacy echo gate (no :9009 verify backend wired - sim/oracle)
                el = cached["echo"] if cached is not None else self.echo.echo_probe(pip)
                if el:
                    chosen_label = label
                    chosen_via = disc_via
                    echo_line = el
                    rtt = (cached["rtt"] if cached is not None
                           else self.rtt.measure_rtt(dev, pip))
                    break                              # keep /32 up (.2 stays); proof + getHosts
                self.r.probe_delete(pip)
        if not chosen_label:
            self.log(f"WALK dev={dev} ip={ip} pip={pip} decision=FAIL_ECHO "
                     f"tried={[c[0] for c in cands]}")
            return

        # echo identity (bash 438-445)
        ef = echo_line.split(",")
        host = ef[0].strip() if ef else ""
        hp2 = ef[1].strip() if len(ef) > 1 else ""
        if hp2 and is_ip(hp2) and is_10x(hp2):
            host_path = hp2
        # [PINGPONG_GATE_V1] An echo-less candidate (any kind) was selected on the
        # :9009 gate and has no self-report; fall back to the seed .1 so the
        # broker-authority override / getHosts name below can name it. verify=None
        # keeps the legacy contract (echo was the gate; host_path is always set).
        if self.verify is not None and not (is_ip(host_path) and is_10x(host_path)):
            host_path = net_dot(dest_seed, 1)
        if not self.is_local_ip(ip) and self.is_local_ip(host_path):
            self.log(f"WALK dev={dev} ip={ip} decision=DNAT_LOOPBACK returned_local={host_path}")
            self.r.probe_delete(pip)
            return

        # [BROKER_AUTHORITY] broker overrides self-report (bash 447-461)
        dest1 = net_dot(host_path, 1)
        b = self.broker.broker_for_peer_ip(dest1)
        if b:
            b_ch, b_sub, b_one = b
            if b_sub and b_one and is_ip(b_one) and is_10x(b_one):
                host_path = b_one
                host = b_ch.rsplit("-", 1)[0]          # ${b_ch%-*.*.*} ~ strip -10.x.y
                if dev.startswith("wg"):
                    iface_ch = self.broker.channel_for_iface(dev)
                    if iface_ch and iface_ch != b_ch:
                        self.log(f"WALK_REJECT dev={dev} ip={ip} dev_channel={iface_ch} "
                                 f"broker_channel={b_ch} reason=wg_channel_mismatch")
                        self.r.probe_delete(pip)
                        return

        # production via = the one-hop address that answered (no onlink).
        # [HOP_VIA_V1] No recompute to a .1 - we install exactly the next hop we
        # proved. Tunnels keep the dev-only scope-link form.
        dest24 = dest24_of(host_path)
        if chosen_label == "TUNNEL":
            p_via, p_onlink = "", 0
        else:  # HOP
            p_via, p_onlink = chosen_via, 0
            # [ATTACHED_ONLINK_SRC_V1] next-hop-only rule: a via that falls inside
            # its OWN destination /24 means this is a segment we are directly on
            # (our uplink lease), reached on-link - NOT through a next-hop. Express
            # it scope-link with src=identity (no via), never `via <dest's .1>`.
            if p_via and dest24_of(p_via) == dest24:
                p_via, p_onlink = "", 0
                src = self.self_identity or src

        if not rtt:
            rtt = "999999"
        self.CAND[dest24].append(f"{rtt}|{p_via}|{dev}|{p_onlink}|{src}|{kind}|{host}")
        self.log(f"CANDIDATE dest={dest24} via={p_via} dev={dev} onlink={p_onlink} "
                 f"rtt={rtt} kind={kind} form={chosen_label} host={host}")

        # side effects (bash 481-485): addHostAndPropogate, then discovery_cache
        # [HOSTS_AUTHORITATIVE_NAME_V1] `host` here is the node's own echo
        # self-report (or the broker override at BROKER_AUTHORITY above) - the
        # authoritative name for host_path. It wins over any relayed vouch name.
        if self.propagator is not None:
            self.propagator.add_host_and_propagate(host, host_path, dev,
                                                   authoritative=True)
        else:
            self.hosts.add_host(host, host_path, dev, authoritative=True)
        if self.is_on_local_subnet(ip) and not ip.startswith("10.253."):
            self.hosts.cache_success(ip, dev, host, host_path, rtt)

        # [NEIGHBOR_FROGNET_ADDR_V1] If we reached this node by directly echoing
        # its on-segment (DHCP) address (seg_relay set), it is an L2-adjacent
        # downstream neighbor whose DHCP lease is seg_relay and whose FrogNet
        # identity is host_path (.1). Record the mapping so the notification fans
        # out to host_path, not to the DHCP lease. Upstream gateways need no entry
        # - their next-hop already IS their .1 identity.
        if seg_relay and host_path and is_ip(host_path):
            self.neighbor_via_map[seg_relay] = host_path

        # getHosts + recurse children (bash 487-507)
        # [HOP_VIA_V1] children inherit OUR next hop: reaching them is one more
        # hop through this same neighbor, so they ride the via that answered for
        # us. (Tunnel children keep host_path; their dev-only form ignores it.)
        if depth < MAX_DEPTH:
            children = self.gethosts.get_hosts(host_path, pip)
            self.r.probe_delete(pip)
            child_parent_via = chosen_via if kind != "tunnel" else host_path
            # [WAVE_PARALLEL_V1] wave-2/3: prewarm THIS relay's child probes in
            # parallel before the sequential recursion below. A relay's children
            # are distinct dests (distinct .2/32) so their probes never collide on
            # a /32; the same dest reached via a different relay lands in a
            # different getHosts expansion - a separate, sequential prewarm batch -
            # so no two concurrent probes ever share a .2. Each self.walk(child)
            # then reads the warm cache instead of paying its own echo timeout,
            # collapsing a relay's N sequential dead-child timeouts into one
            # window. Pure network-latency overlap: the recursion visits children
            # in the same order and the cache yields the same echo/rtt a live probe
            # would, so CAND/VOUCH/hosts output is byte-identical to sequential
            # (proved by test_wave_parallel_equivalence_oracle, which drives a
            # depth-2 child).
            if self.parallel_waves and children:
                self.prewarm_wave(
                    [(dev, (c[0] if isinstance(c, tuple) else c), child_parent_via)
                     for c in children],
                    max_workers=self.wave_workers)
            for child in children:
                cip, cname = child if isinstance(child, tuple) else (child, "")
                if not cip or cip == host_path:
                    continue
                # [VOUCH_ROUTE_V1] getHosts is the authority: the upstream is
                # telling us which hosts it serves, so establish a route to each
                # THROUGH the upstream (the path that reached it) and record the
                # host -- even when a direct echo to that host fails. A deep node
                # cannot echo a far host over its borrowed uplink lease (the return
                # path breaks), but the upstream forwards it, so the route is valid.
                # Skip our own identity and any subnet we are directly on (those are
                # on-link). For a tunnel upstream the child rides the tunnel dev
                # (scope-link, no via); for a LAN upstream it goes via the upstream's
                # next-hop. The recursive walk still runs below: a successful echo
                # adds a lower-rtt candidate that wins over this fallback. kind=
                # "vouch" marks the candidate so promote does NOT verify-backout it
                # (the vouch IS the validation; a direct alive() would fail for the
                # same return-path reason the echo did).
                if not self.is_local_ip(cip) and not self.is_on_local_subnet(cip):
                    cdest = dest24_of(cip)
                    cvia = "" if kind == "tunnel" else child_parent_via
                    # [VOUCH_TRANSIT_GATE_V1] getHosts returns the relay's FULL
                    # propagated host list, not just the hosts it actually relays.
                    # Only vouch a child through this relay if the relay genuinely
                    # transits the child's /24; otherwise the candidate is a loop
                    # (a downstream leaf that merely KNOWS the subnet from
                    # propagation, e.g. New-York-2 vouching Seattle5 back through
                    # us; or a tunnel peer that reaches the subnet via the chain
                    # back through us, e.g. BAMacBook/New-York-1 vouching Seattle3
                    # to Seattle5). The relay's transit set comes from the broker
                    # transit map (node_transit.json), populated once every node
                    # advertises transit_subnets. self.broker.transits() returns
                    # True when the map is absent, so this gate is INERT until the
                    # data is live and behaviour matches the pre-gate build.
                    # host_path is the relay's identity (.1); the recursive walk
                    # below still runs, so a real echo can add a winning candidate
                    # even when the vouch is suppressed.
                    _stale = self._vouch_stale(cdest1_pre := net_dot(cdest.split("/")[0], 1))
                    if _stale is not None:
                        self.log(f"VOUCH_STALE dest={cdest} host={cname} "
                                 f"from={host_path} reason={_stale} "
                                 f"skip=candidate+propagation+walk")
                        continue
                    if self.broker.transits(host_path, cdest):
                        # [REFLECT_VOUCH_GATE_V1] The transit-map gate above is
                        # INERT (transits() fail-opens when node_transit.json is
                        # absent), so a bent vouch - New-York-2 vouching Seattle5
                        # back through us - sails into CAND and installs as a
                        # black-hole LAN route. Vouches never echo, so they never
                        # hit the walk's reflect probe (line ~194). Fire it HERE,
                        # against the vouched dest's identity (.1): if the path to
                        # cdest bends back through us, the reflect vhost returns 508
                        # -> LOOP -> skip the vouch. Conservative like the walk
                        # probe: only an explicit LOOP rejects; None/OK proceed, so
                        # this is inert where :18432 isn't trapped and exact where
                        # it is. This is the loop defense that was missing from the
                        # vouch path entirely.
                        cdest1 = net_dot(cdest.split("/")[0], 1)
                        lverdict = None
                        if self.verify is not None and cdest1:
                            # [LOOP_DETECT_9009_V1] same :9009 loop check on the
                            # vouched dest's .1; an explicit LOOP skips the vouch
                            # so a bent vouch never installs a black-hole route.
                            lverdict = self._alive_measure(cdest1, dev)   # [ALIVE_9009_RETRY_V1]
                            self.log(f"[DIAG-LOOP9009] vouch dest={cdest} "
                                     f"target={cdest1} dev={dev} value={lverdict}")
                        if lverdict == "LOOP":
                            self.log(f"VOUCH_SKIP dest={cdest} via={cvia} dev={dev} "
                                     f"host={cname} from={host_path} "
                                     f"reason=loop_9009 target={cdest1}")
                        else:
                            self.CAND[cdest].append(
                                f"1000000|{cvia}|{dev}|0|{src}|vouch|{cname}")
                            self.log(f"VOUCH dest={cdest} via={cvia} dev={dev} "
                                     f"host={cname} from={host_path}")
                    else:
                        self.log(f"VOUCH_SKIP dest={cdest} via={cvia} dev={dev} "
                                 f"host={cname} from={host_path} "
                                 f"reason=relay_no_transit")
                    if cname:
                        # [HOSTS_AUTHORITATIVE_NAME_V1] cname is the RELAY's
                        # getHosts name - hearsay, not the peer's own echo. It is
                        # a fallback only: if this node also echoes the peer
                        # directly, that authoritative name supersedes this one.
                        if self.propagator is not None:
                            self.propagator.add_host_and_propagate(
                                cname, cip, dev, authoritative=False)
                        else:
                            self.hosts.add_host(cname, cip, dev,
                                                authoritative=False)
                self.walk(dev, cip, depth + 1, child_parent_via)
            return
        self.r.probe_delete(pip)

    # =====================================================================
    #  promote (bash 515) - proven .2 candidates -> production .1 routes.
    # =====================================================================
    def promote(self) -> None:
        winners: list[tuple[str, str]] = []   # (dest_cidr, dev) for rank-0 winners
        for dest in self._cand_order():
            lines = [l for l in self.CAND[dest] if l]
            # sort by rtt asc (numeric), stable (bash sort -t'|' -k1,1n)
            lines.sort(key=lambda l: int(l.split("|", 1)[0]))
            # [WINNER_HYSTERESIS_V1] Keep a settled metric-22 winner unless a
            # MEASURED challenger beats it by >=50%. Changing a winner is an
            # `ip route replace` that yanks the next-hop on the /24 and interrupts
            # any flow in flight, so the bar to do it is a CLEAR win, not jitter.
            # Stickiness is on the FULL (via, dev) identity, not dev alone: several
            # relays can reach one dest over the SAME dev at near-equal RTT (e.g.
            # Seattle2 via .191 vs .130.130.1 vs .130.130.2, all eth0, single-digit
            # ms). A dev-only check never engages there, so pure lowest-rtt flips
            # the via every pass under noise -> route_table_mutated -> runAgain
            # latches forever. Subsumes the dev case (a different dev is a different
            # identity); tunnels carry via="" and match too. Only measured
            # candidates get stickiness (a vouch-sentinel incumbent earns none).
            inc_dev = self.r.winner_dev(dest)
            inc_via = self.r.winner_via(dest)
            # =============================================================
            # [ROUTE_INCUMBENCY_HOLD_V1] John's rule, 2026-07-06, verbatim:
            #   If you have a candidate path for a remote machine
            #     If there is already a .1 path for that candidate
            #       If that .1 path is alive and healthy
            #         THEN LEAVE THE FUCKING ROUTE ALONE
            # An installed winner whose dest .1 answers over ITS OWN path is
            # untouchable. No measured challenger displaces it (supersedes the
            # 2026-06-24 50% improvement bar), no vouch reshuffle touches it,
            # nothing mutates, the latch cannot re-arm on this dest. Candidates
            # matter only when there is no incumbent or the incumbent is dead.
            # Health check mirrors the vouch gate's evidence split: reflect
            # chain for relayed (via) incumbents, :9009 pong bound to the
            # incumbent dev for direct/tunnel ones.
            # =============================================================
            if inc_dev:
                _d1 = net_dot(dest.split("/")[0], 1)
                _healthy = None
                if inc_via and self.reflect is not None and self.self_identity:
                    _rv = self.reflect.probe(self.self_identity, _d1)
                    self.log(f"[DIAG-REFLECT] hold dest={dest} target={_d1} "
                             f"via={inc_via} dev={inc_dev} "
                             f"o={self.self_identity} value={_rv}")
                    _healthy = (_rv == "OK")
                elif not inc_via and self.verify is not None:
                    _healthy = self._alive_ok(_d1, inc_dev)
                if _healthy:
                    _d2 = net_dot(dest.split("/")[0], 2)
                    # alias refresh only (/32s never flag mutation)
                    self.r.install_if_changed(f"{_d2}/32", inc_via, inc_dev,
                                              ALIAS_METRIC, 0,
                                              self.self_identity or "")
                    self.log(f"PROMOTE_HOLD dest={dest} via={inc_via or '-'} "
                             f"dev={inc_dev} reason=incumbent_dot1_alive_"
                             f"leave_the_route_alone")
                    winners.append((dest, inc_dev))
                    continue
                if _healthy is False:
                    self.log(f"PROMOTE_HOLD_RELEASED dest={dest} "
                             f"via={inc_via or '-'} dev={inc_dev} "
                             f"reason=incumbent_dot1_not_alive")
            r0 = lines[0].split("|") if lines else None
            if inc_dev and r0 and (r0[1] != inc_via or r0[2] != inc_dev):
                inc_lines = [l for l in lines
                             if l.split("|")[1] == inc_via
                             and l.split("|")[2] == inc_dev
                             and l.split("|", 1)[0] != "1000000"]
                best_rtt = next((int(l.split("|", 1)[0]) for l in lines
                                 if l.split("|", 1)[0] != "1000000"), None)
                if inc_lines and best_rtt is not None:
                    inc_line = min(inc_lines, key=lambda l: int(l.split("|", 1)[0]))
                    inc_rtt = int(inc_line.split("|", 1)[0])
                    if best_rtt >= inc_rtt * WINNER_HYSTERESIS:
                        lines.remove(inc_line)
                        lines.insert(0, inc_line)
                        self.log(f"PROMOTE_STICKY dest={dest} incumbent_via={inc_via or '-'} "
                                 f"incumbent_dev={inc_dev} incumbent_rtt={inc_rtt} "
                                 f"best_rtt={best_rtt} "
                                 f"reason=challenger_below_improve_threshold_kept_incumbent")
            # [VOUCH_INCUMBENCY_V1] When NO measured candidate survived (rank0 will
            # be a vouch either way), hearsay is choosing between hearsay - and the
            # tie previously broke on WALK ORDER, a per-pass lottery that flipped
            # the winner dev every pass on a dest whose .2 echo alternates
            # (Seattle5 field log 2026-07-06: wg2->wg0->... slash24_mutated=1 each
            # pass, runAgain latched, merge never completed). Break the vouch tie
            # by incumbency: if a vouch matching the installed winner's (via,dev)
            # is in the pool, it goes first. This does NOT let a vouch incumbent
            # beat a measured challenger - measurement still beats hearsay; the
            # [ALIVE_GATE_VOUCH_V1] alive gate below still applies unchanged, so a
            # genuinely dark incumbent still loses its seat.
            if (inc_dev and lines
                    and lines[0].split("|", 1)[0] == "1000000"
                    and (lines[0].split("|")[1] != inc_via
                         or lines[0].split("|")[2] != inc_dev)):
                inc_vouch = [l for l in lines
                             if l.split("|", 1)[0] == "1000000"
                             and l.split("|")[1] == inc_via
                             and l.split("|")[2] == inc_dev]
                if inc_vouch:
                    lines.remove(inc_vouch[0])
                    lines.insert(0, inc_vouch[0])
                    self.log(f"PROMOTE_STICKY dest={dest} incumbent_via={inc_via or '-'} "
                             f"incumbent_dev={inc_dev} "
                             f"reason=vouch_tie_broken_by_incumbency_no_measured_candidate")
            dest2 = net_dot(dest.split("/")[0], 2)
            dest1 = net_dot(dest.split("/")[0], 1)
            # [PROMOTE_WHY_V1] Explain the decision: dump EVERY candidate this dest
            # is choosing between, in the exact sorted order promote walks, so the
            # final table can be traced to its inputs. rtt=1000000 is the vouch
            # sentinel (no measured echo); lower rtt = measured, sorts first; ties
            # break on insertion (walk) order. Read together with the per-candidate
            # PROMOTE_* lines below: rank 0 = winner, vouches never become metric-100
            # fallbacks, one route per dev.
            if lines:
                summary = "; ".join(
                    f"#{i} rtt={l.split('|')[0]} kind={l.split('|')[5]} "
                    f"via={l.split('|')[1] or '-'} dev={l.split('|')[2]} "
                    f"host={l.split('|')[6]}"
                    for i, l in enumerate(lines))
                self.log(f"PROMOTE_CONSIDER dest={dest} n_candidates={len(lines)} "
                         f"sorted_by=rtt_asc :: {summary}")
            else:
                self.log(f"PROMOTE_CONSIDER dest={dest} n_candidates=0 "
                         f"reason=no_candidate_survived_walk note=route_not_installed")
            used_dev: set[str] = set()
            dest_metrics: set[int] = set()   # [PRUNE_DEST_EXTRAS_V1] metrics installed this pass
            rank = 0
            for line in lines:
                rtt, via, dev, onlink, src, kind, host = line.split("|")
                if not dev or dev in used_dev:          # one route per dev (bash 529)
                    self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} "
                             f"kind={kind} rtt={rtt} reason="
                             f"{'no_dev' if not dev else 'dev_already_used_this_dest'}")
                    continue
                if (dest, via) in self.loop_memo:
                    self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} reason=loop_memo_this_run")
                    continue
                onl = int(onlink)
                if rank == 0:
                    is_vouch = (rtt == "1000000")
                    # [ALIVE_GATE_VOUCH_V1] Split the alive check by evidence class.
                    #
                    # MEASURED winner (rtt != sentinel): a real .2 echo already
                    # PROVED this path carries. Keep [ALIVE_NONDESTRUCTIVE_V1]:
                    # install and KEEP regardless of the :9009 alive result, which
                    # is advisory only - the bound-socket :9009 probe false-
                    # negatives over tunnels, so it must not back out a route the
                    # echo proved. Log SUSPECT for diagnostics, leave it in place.
                    #
                    # VOUCH winner (rtt == sentinel, NO echo proof): a vouch is a
                    # relay's hearsay, not proof. It may only win if the dest's .1
                    # answers a :9009 PONG over THIS candidate's route. No PONG ->
                    # do NOT install, NOT a winner, NOT a transit; fall through to
                    # the next candidate (e.g. the tunnel-side vouch). If no
                    # candidate is alive, this dest installs NOTHING - honest
                    # absence, never a black-hole LAN fallback. ("If you can't
                    # reach it, don't add it.")
                    #
                    # This is the rule the RealVerify docstring and
                    # ny2_alive_gate STATE. The prior blanket ALIVE_NONDESTRUCTIVE
                    # exemption for vouches installed the New-York-2 / BABox LAN
                    # black holes, advertised them as transit, and oscillated:
                    # depth-0 the reflect gate is inert (no route yet) so the bent
                    # eth0 vouch installs; depth-1 the now-installed route loops so
                    # the reflect gate skips it and it is reaped; depth-2 the loop
                    # is gone so it reinstalls - slash24_mutated every pass, never
                    # converges. Gating the vouch on alive means the looping route
                    # is never installed, so there is nothing to oscillate.
                    # [REFLECT_VOUCH_GATE_V2] A vouch is a RELAYED route: the dest
                    # is reachable only THROUGH the relay, never directly. The
                    # :9009 alive() probe is a raw direct TCP connect to dest1:9009
                    # (only the proxy speaks :9009, on-segment), so it returns None
                    # for every relayed dest no matter how reachable, dropping it as
                    # vouch_not_alive. The reflect chain is the verifier built for
                    # this: the proxy reflect vhost forwards /reflect?o&c hop-by-hop,
                    # c+1 each box, until it reaches dest1 (OK) or bends back (LOOP).
                    # Gate the vouch on reflect when we have it; fall back to :9009
                    # alive() only when no reflect backend is wired.
                    if is_vouch:
                        rverdict = None
                        if self.reflect is not None and src:
                            rverdict = self.reflect.probe(src, dest1)
                            self.log(f"[DIAG-REFLECT] promote dest={dest} "
                                     f"target={dest1} via={via} dev={dev} "
                                     f"o={src} value={rverdict}")
                        if rverdict == "OK":
                            pass  # reached through the relay -> install
                        elif rverdict == "LOOP":
                            self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} "
                                     f"kind=vouch rtt={rtt} reason=reflect_loop")
                            continue
                        elif self.reflect is not None:
                            self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} "
                                     f"kind=vouch rtt={rtt} reason=reflect_no_reach")
                            continue
                        elif self.verify is not None and not self._alive_ok(dest1, dev):   # [ALIVE_9009_RETRY_V1]
                            self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} "
                                     f"kind=vouch rtt={rtt} reason="
                                     f"vouch_not_alive_no_install")
                            continue
                    self.r.install_if_changed(dest, via, dev, WINNER_METRIC, onl, src)
                    self.r.install_if_changed(f"{dest2}/32", via, dev, ALIAS_METRIC, onl, src)
                    dest_metrics.add(WINNER_METRIC)
                    why = ("lowest_measured_rtt" if not is_vouch
                           else "alive_vouch_no_measured_candidate")
                    self.log(f"PROMOTE_WINNER dest={dest} via={via} dev={dev} "
                             f"rtt={rtt} kind={kind} metric={WINNER_METRIC} "
                             f"reason=rank0_{why}")
                    # [TRANSIT_FROM_WINNERS_V1] record the installed winner so
                    # transit can be derived from the chosen routes + their devs.
                    winners.append((dest, dev))
                    if self.verify is not None:
                        if self._alive_ok(dest1, dev):   # [ALIVE_9009_RETRY_V1]
                            self.log(f"VERIFY_OK dest={dest} via={via} dev={dev} "
                                     f"reason=alive_9009_pong")
                        else:
                            self.log(f"VERIFY_SUSPECT dest={dest} via={via} dev={dev} "
                                     f"reason=alive_9009_no_pong kind={kind} "
                                     f"note=measured_kept_not_backed_out")
                else:
                    if kind == "vouch":
                        # [VOUCH_ROUTE_V1] a vouch is authority-only routing: it wins
                        # when it is the sole/best candidate, but never piles on as a
                        # metric-100 backup when a real echo winner already exists
                        # (a hub vouched by many tunnels would otherwise grow a
                        # redundant fallback per peer).
                        self.log(f"PROMOTE_DROP dest={dest} via={via} dev={dev} "
                                 f"kind=vouch rtt={rtt} reason="
                                 f"vouch_not_used_as_fallback_winner_exists")
                        continue
                    fb = FALLBACK_BASE + rank - 1
                    self.r.install_if_changed(dest, via, dev, fb, onl, src)
                    dest_metrics.add(fb)
                    self.log(f"PROMOTE_FALLBACK dest={dest} via={via} dev={dev} "
                             f"rtt={rtt} kind={kind} metric={fb} "
                             f"reason=rank{rank}_secondary_path_higher_metric")
                used_dev.add(dev)
                rank += 1

            # [PRUNE_DEST_EXTRAS_V1] This dest's table is now exactly the winner +
            # fallbacks promote installed this pass; delete any stale copy at a
            # metric this pass did NOT write (a LAN corpse left under a tunnel
            # winner, or a higher-metric leftover from a prior pass that ranked
            # more devs). Holds the /24 to one coherent plane and stops a later
            # pass tripping route_table_mutated on the corpse. No-op for a dest
            # with no winner (reap_unverified_winners removes the whole dest).
            if dest_metrics:
                self.r.prune_dest_extras(dest, dest_metrics, logger=self.log)

        # [TRANSIT_FROM_WINNERS_V1] Derive transit from the winners just
        # installed. A winning /24 is transit iff it egresses a SERVED dev -
        # i.e. it entered this node from below. Exclusions: own subnet; infra
        # ranges 10.253 (transit /30) and 10.254 (chorus); mesh/tunnel devs
        # (wg*, frognet0, lo); and, on a LAN-child only, the mesh-ward uplink
        # dev (the path toward this node's own gateway). On a GATEWAY there is
        # no FrogNet uplink dev to exclude (its uplink is a non-10.x WAN, never
        # a winner dev), so every LAN /24 it reaches becomes transit - "if this
        # is a gateway, everything LAN is routed."
        transit: set[str] = set()
        for dest, dev in winners:
            net = dest.split("/")[0]
            if self.own_subnet and dest == self.own_subnet:
                continue
            if not net.startswith("10."):
                continue
            if net.startswith("10.253.") or net.startswith("10.254."):
                continue
            if dev.startswith("wg") or dev in ("frognet0", "lo"):
                continue
            if (not self.has_own_uplink) and self.uplink_dev and dev == self.uplink_dev:
                continue
            transit.add(dest)
        self.computed_transit = sorted(transit)
        # [REACH_PLANE_V1] WAN plane = winner /24s reached OVER a tunnel (wg* dev),
        # excluding infra ranges. These are exactly the candidates that "trip
        # loopback" - reached across the overlay, not on the local link. Derived from
        # the same loopback-validated winners; no extra probe. The merge writes this
        # to the reach_plane tuple and the election reads it.
        wan: set[str] = set()
        for dest, dev in winners:
            net = dest.split("/")[0]
            if not net.startswith("10."):
                continue
            if net.startswith("10.253.") or net.startswith("10.254."):
                continue
            if dev.startswith("wg"):
                wan.add(dest)
        self.computed_wan = sorted(wan)
        self.log(f"TRANSIT_FROM_WINNERS gateway={self.has_own_uplink} "
                 f"uplink_dev={self.uplink_dev or '-'} "
                 f"own={self.own_subnet or '-'} transit={self.computed_transit} "
                 f"wan={self.computed_wan}")

        # [REAP_STALE_WINNERS_V1] Snapshot-vs-winners reconciliation: delete any
        # /24 that was in the table at entry but was NOT installed as a winner
        # this pass. This is what removes a reflect-detected loop route - promote
        # logs VOUCH_SKIP reason=reflect_loop and installs NOTHING, but a stale
        # copy from a prior merge lingers until reaped here. verified = the /24s
        # promote just installed as rank-0 winners.
        # [REAP_STALE_WINNERS_V1] Canonical reconciliation: runMerge does a
        # COMPLETE discovery every pass, so the winners built from the .2 probe
        # routes ARE the whole truth. Delete every /24 that is not a winner this
        # pass - no legacy, no carryover. This removes reflect-skipped loop
        # routes (absent from winners) and anything that moved or departed.
        verified = {dest for (dest, _dev) in winners}
        own = {self.own_subnet} if self.own_subnet else set()
        self.r.reap_unverified_winners(verified, own_subnets=own, logger=self.log)

    def _cand_order(self):
        """bash iterates `${!CAND[@]}` (hash order). The simulator wants
        determinism; the caller may set self._promote_order to the oracle's
        observed dest order. Default: insertion order."""
        order = getattr(self, "_promote_order", None)
        if order:
            return [d for d in order if d in self.CAND]
        return list(self.CAND.keys())

    # =====================================================================
    #  seed loops (bash 561-630). Providers injected by the simulator so the
    #  exact seed inputs from the oracle drive the same ported logic.
    # =====================================================================
    def tunnel_immediates(self, active_states=None, wg_kernel_nets=None):
        """[DESCEND_TUNNEL_IMMEDIATE_V1 + DESCEND_TUNNEL_KERNEL_V1] Peer .1 of every
        subnet reachable over a healthy wg tunnel, as (peer1, iface) immediates.

        A wg peer is NOT an ARP neighbour (point-to-point, no L2 ARP), so immediates
        built from arp_neigh alone miss the whole far side; on a tunnel-only node
        EVERY real neighbour is across a tunnel.

        TWO sources, unioned, KERNEL FIRST. The kernel FIB (`ip route show dev wgN
        scope link`) is ground truth: it is what the box can actually route right
        now, and it is re-read from the kernel on every merge. The tunnel daemon's
        /var/lib/frognet-tunnel/active/*.json is a CACHE that may be stale, empty,
        or absent - if the daemon has not written it (dir empty), sourcing immediates
        from it alone yields ZERO tunnel immediates: no wg probes, no candidates for
        the far side, and reap_unverified_winners then deletes the daemon's own
        scope-link /24s every pass. Seattle5 2026-07-23: wg0/wg1 healthy with live
        handshakes and 10.111.11.0/24 + 10.102.60.0/24 in the FIB, active/ empty,
        and every remote node fell out of the host table. A merge must REBUILD
        reachability from the kernel, never ASSUME a cached file is present."""
        out, seen = [], set()
        dead = getattr(self, "DEAD_IFACES", set())

        def _add(iface, subnet):
            if not iface or iface in dead:
                return
            if not (subnet and subnet.startswith("10.")):
                return
            base = subnet.split("/")[0]
            if base.startswith("10.253.") or base.startswith("10.254."):
                return                      # transit/chorus carry no identity
            peer1 = base.rsplit(".", 1)[0] + ".1"
            if self.is_local_ip(peer1):
                return
            key = (peer1, iface)
            if key not in seen:
                seen.add(key)
                out.append(key)

        for iface, subs in (wg_kernel_nets or {}).items():   # kernel = ground truth
            for subnet in (subs or []):
                _add(iface, subnet)
        for iface, subs in (active_states or []):            # daemon cache = bonus
            for subnet in (subs or []):
                _add(iface, subnet)
        return out

    def descend_downstream(self, active_devs, dev_ip=None, leases=None,
                           disk_cache=None, active_states=None,
                           wg_kernel_nets=None, arp_neigh=None):
        # [DESCEND_V1] John's algorithm, 2026-07-08. Immediate connections are
        # REAL (downstream DHCP clients + upstream uplink neighbours), each one hop
        # on a known interface. From them we getHosts children, then grandchildren
        # -> the seed list. Every seed (>1 hop) is then probed on EVERY interface
        # in parallel; whichever answers is the candidate. Replaces the seed-crawl
        # + vouch gate; promote() installs from CAND unchanged.
        from . import descend as _descend
        dev_ip = dev_ip or {}
        leases = leases or []
        arp_neigh = arp_neigh or {}
        active_states = active_states or []
        immediate = []
        immediate.extend(self.tunnel_immediates(active_states, wg_kernel_nets))
        # downstream: a DHCP lease belongs to the interface whose subnet it is on
        # (dnsmasq only leases within a served interface's range). One hop, there.
        for ip in leases:
            if not (ip and ip.startswith("10.")):
                continue
            pref = ip.rsplit(".", 1)[0]
            for dev in active_devs:
                dip = dev_ip.get(dev, "")
                if dip and dip.rsplit(".", 1)[0] == pref:
                    immediate.append((ip, dev))
                    break
        # upstream + on-segment: every ARP neighbour is a one-hop connection on
        # the interface it was seen on (the uplink relay lands here on wlanN).
        for dev in active_devs:
            if dev in ("frognet0", "lo"):
                continue
            for nip in arp_neigh.get(dev, []):
                if (nip and nip.startswith("10.")
                        and not nip.startswith("10.253.")
                        and not nip.startswith("10.254.")
                        and not self.is_local_ip(nip)):
                    immediate.append((nip, dev))
        # [UPSTREAM_LEASE_DOT1_V1] Our own client lease DETERMINES the uplink: if we
        # hold a NON-.1 address on a /24, that subnet's .1 is our directly-attached
        # upstream gateway - a real immediate connection, whether or not its ARP
        # entry is fresh this pass. Deriving upstream from arp_neigh alone drops the
        # gateway from the host list the moment its ARP ages out (the route via it
        # persists under incumbency-hold, so the symptom is a HOST that vanishes
        # while its route stays), which collapses databasehost_control (highest .1)
        # to a lower node and splits the election. Seattle3 (10.250.250.191 on wlan1)
        # transits 10.250.250.1 but never lists Seattle5 - this is that. Add it.
        for dev in active_devs:
            if dev in ("frognet0", "lo"):
                continue
            dip = dev_ip.get(dev, "")
            if (dip and dip.startswith("10.")
                    and not dip.startswith("10.253.")
                    and not dip.startswith("10.254.")
                    and dip.rsplit(".", 1)[1] != "1"):
                up1 = dip.rsplit(".", 1)[0] + ".1"
                if not self.is_local_ip(up1):
                    immediate.append((up1, dev))
        _descend.descend(self, active_devs, immediate,
                         active_states=active_states,
                         max_workers=getattr(self, "wave_workers", 8) * 8)

    def descend_upstream(self, active_devs, seed_from_dev):
        for dev in active_devs:
            s = seed_from_dev.get(dev, "")
            if s:
                self.walk(dev, s, 1)
