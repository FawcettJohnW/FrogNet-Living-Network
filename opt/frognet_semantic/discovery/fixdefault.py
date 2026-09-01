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
fixdefault.py - fixDefaultRoute, ported from the bash original. Default-route
(non-10.x) selection, separate from FrogNet's 10.x table.

Live path (Mode A, the one the oracle exercises):
  forced_override_check (none) -> purge_dead_defaults (self-gw / dev-down / ping)
  -> discover_offlan_gateways (Source A: surviving non-10 defaults; Source B:
     infer .1 on non-10 frognet-iface addrs, ping) -> compute_install_metrics
  -> install_offlan_defaults (metric-aware skip if already_exact) -> DECISION.

Mode B (mesh exit-host synthesis via getDefaultRoute.php) IS ported and live:
  choose_exit_hosts -> curl getDefaultRoute.php on each directly-attached 10.x
  peer (via the local proxy with the peer's Host header, so FAST/SEM hop kind is
  honored), keep peers whose JSON is fresh (ts+ttl, ttl<=180) and exit_present,
  rank FAST>SEM then lowest host IP, cap at exit_max_defaults -> install_exit_
  defaults lays a ranked default-route ladder (primary + backups) and writes the
  exit sentinel. run() reaches it at step 4 when Mode A finds no own off-LAN
  gateway. This is the chain-through-a-peer rule: a node with no direct non-10
  uplink takes its default route (and therefore its DNS next hop) from whichever
  attached peer advertises a live exit, which is how the default route walks the
  mesh out to the WAN edge one hop at a time.

  The Mode A oracle does not exercise Mode B (Mode A finds a candidate first);
  Mode B has live backends wired in live.py and is covered by its own path there,
  not by the Mode A oracle.

Injected (kernel-edge, mocked in sim, live-only otherwise):
  dev_ip4[dev]            -> the dev's primary IPv4 (or "")
  up_devs                 -> set of devs in kernel UP/UNKNOWN state
  connected_prefixes[dev] -> set of /24 prefixes ("a.b.c") connected on dev
  ping(via, dev)          -> bool (ping -I dev)
  frognet_interfaces      -> ordered iface list (mapInterfaces runtime value)
  forced_line             -> (gw, dev) | None  (/etc/frognet/forced_default_route)
"""
from __future__ import annotations

from .kernel import _parse_route_argv

DEFAULT_ROUTE_METRIC = 601
FALLBACK_STRIDE = 1


class FixDefaultRoute:
    def __init__(self, kernel, *, dev_ip4, up_devs, connected_prefixes, ping,
                 frognet_interfaces, local_ips, forced_line=None,
                 logger=lambda s: None, route_get=None, get_default_route=None,
                 known_hosts=None, semantic_cidrs=None, write_exit_sentinel=None,
                 exit_max_defaults=3, self_identity: str = ""):
        self.k = kernel
        self.dev_ip4 = dict(dev_ip4)
        self.up_devs = set(up_devs)
        self.connected_prefixes = {d: set(p) for d, p in connected_prefixes.items()}
        self.ping = ping
        self.frognet_interfaces = list(frognet_interfaces)
        self.local_ips = set(local_ips)
        self.forced_line = forced_line
        self.log = logger
        # Mode B / forced backends (optional; live wires Real, oracle omits)
        self.route_get = route_get                 # host_ip -> (dev, nh) | None
        self.get_default_route = get_default_route  # host_ip -> json dict | None
        self.known_hosts = known_hosts             # () -> [peer .1, ...]
        self.semantic_cidrs = set(semantic_cidrs or ())
        self.write_exit_sentinel = write_exit_sentinel  # (text) -> None
        self.exit_max_defaults = exit_max_defaults
        # [LEAF_SRC_PIN_V1] identity .1 to pin as the default route's preferred
        # source; "" -> omit (kernel default). See discovery.Discovery.
        self.self_identity = self_identity

    def _src_args(self, via):
        # [LEAF_SRC_PIN_V1] Pin our identity as preferred-source ONLY for
        # mesh-egress defaults - next-hop is a 10.x frognet node. For a WAN /
        # off-lan gateway the source must stay the WAN interface address, so
        # leave it to the kernel.
        if self.self_identity and self._is_10x(via):
            return ["src", self.self_identity]
        return []

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _is_ip(s):
        import re
        return bool(re.match(r"^(\d+\.){3}\d+$", s or ""))

    @staticmethod
    def _is_10x(s):
        return (s or "").startswith("10.")

    @staticmethod
    def _pfx24(ip):
        return ".".join(ip.split(".")[:3])

    def is_local_ip(self, ip):
        return ip in self.local_ips

    def dev_is_up(self, dev):
        return dev in self.up_devs

    def collect_defaults(self):
        """[(metric, via, dev, rawline)] for each default route."""
        rows = []
        for line in self.k.show_default():
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is None or not e.via or not e.dev:
                continue
            rows.append((e.metric, e.via, e.dev, line))
        return rows

    def _delete_default(self, via, dev, metric):
        if metric and metric != 0:
            self.k.route("del", "default", "via", via, "dev", dev, "metric", str(metric))
        else:
            self.k.route("del", "default", "via", via, "dev", dev)

    def _nh_is_on_connected_subnet(self, nh, dev):
        return self._pfx24(nh) in self.connected_prefixes.get(dev, set())

    def _default_already_installed(self, want_via, want_dev, want_metric, want_onlink):
        want_src = self.self_identity if (self.self_identity and self._is_10x(want_via)) else ""
        for line in self.k.show_default():
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is None:
                continue
            if not (e.via == want_via and e.dev == want_dev and e.metric == int(want_metric)):
                continue
            has = "onlink" if e.onlink else ""
            if has != want_onlink:
                continue
            # [LEAF_SRC_PIN_V1] also require the preferred-source to match, so an
            # existing src-less default is re-installed once to gain its src.
            if (e.src or "") != want_src:
                continue
            return True
        return False

    # -- phase 1: purge -----------------------------------------------------
    def purge_dead_defaults(self):
        rows = self.collect_defaults()
        self.log(f"purge_scan existing_defaults={len(rows)}")
        if not rows:
            self.log("purge_scan status=no_defaults_to_consider")
            return
        kept = purged = structural = 0
        for met, via, dev, _raw in rows:
            if self.is_local_ip(via):
                self.log(f"PURGE decision=delete reason=self_gateway via={via} dev={dev} metric={met}")
                self._delete_default(via, dev, met); structural += 1; continue
            # [LAN_GW_DOT1_V1] Internally, a default route's gateway is a
            # FrogNet .1.  Every /24 is served by its own .1, which is that
            # subnet's gateway and nameserver.  A 10.x default via anything
            # else is invalid by construction.  External (non-10.x) gateways
            # are not touched by this rule.
            #
            # Scope: this governs defaults we did NOT write -- a DHCP-supplied
            # router option, a leftover.  It is the only rule applied to them.
            #
            # Deliberately BEFORE dev_is_up and BEFORE the ping.  Liveness is
            # the wrong test: a non-FrogNet device sitting on a FrogNet segment
            # answers ICMP perfectly well, so a ping-based decision votes KEEP
            # and the bad default survives every sweep.  Observed: a consumer
            # router at 10.250.250.85, holding a lease on the segment, taken as
            # the default gateway on three separate nodes.  Being reachable is
            # not being a gateway.
            if self._is_10x(via) and via != self._pfx24(via) + ".1":
                self.log(f"PURGE decision=delete reason=lan_gw_not_dot1 "
                         f"via={via} dev={dev} metric={met}")
                self._delete_default(via, dev, met); structural += 1; continue
            if not self.dev_is_up(dev):
                self.log(f"PURGE decision=delete reason=dev_down via={via} dev={dev} metric={met}")
                self._delete_default(via, dev, met); structural += 1; continue
            self.log(f"purge_ping_fire via={via} dev={dev} metric={met}")
            if self.ping(via, dev):
                self.log(f"PURGE decision=keep reason=ping_ok via={via} dev={dev} metric={met}")
                kept += 1
            else:
                self.log(f"PURGE decision=delete reason=ping_fail via={via} dev={dev} metric={met}")
                self._delete_default(via, dev, met); purged += 1
        self.log(f"purge_summary total={len(rows)} kept={kept} purged={purged}")

    # -- phase 2: discover off-LAN -----------------------------------------
    def discover_offlan_gateways(self):
        seen = set()
        out = []
        # Source A: surviving non-10 defaults (already alive post-purge)
        for met, via, dev, _raw in sorted(self.collect_defaults(), key=lambda r: r[0]):
            if not (self._is_ip(via) and not self._is_10x(via)):
                continue
            key = f"{via}|{dev}"
            if key in seen:
                continue
            seen.add(key)
            out.append((via, dev))
        # Source B: infer .1 on every non-10 addr of every frognet iface, ping
        cands = []
        for dev in self.frognet_interfaces:
            if not self.dev_is_up(dev):
                continue
            ip4 = self.dev_ip4.get(dev, "")
            if not ip4 or not self._is_ip(ip4) or self._is_10x(ip4):
                continue
            gw = self._pfx24(ip4) + ".1"
            if not self._is_ip(gw) or self.is_local_ip(gw):
                continue
            key = f"{gw}|{dev}"
            if key in seen:
                continue
            seen.add(key)
            cands.append((gw, dev))
        for gw, dev in cands:
            if self.ping(gw, dev):
                out.append((gw, dev))
        return out

    # -- phase 3: metrics + install ----------------------------------------
    def compute_install_metrics(self, n, excl_pairs):
        if not (isinstance(n, int) and n > 0):
            return []
        excl = set(excl_pairs)
        survivor_min = None
        for met, via, dev, _raw in self.collect_defaults():
            if f"{via}|{dev}" in excl:
                continue
            if survivor_min is None or met < survivor_min:
                survivor_min = met
        desired = DEFAULT_ROUTE_METRIC
        stride = FALLBACK_STRIDE
        top_needed = desired + (n - 1) * stride
        if survivor_min is not None and top_needed >= survivor_min:
            desired -= (top_needed - survivor_min + 1)
        if desired < 1:
            if survivor_min is not None and survivor_min <= n:
                return []
            desired = 1
        return [desired + i * stride for i in range(n)]

    def install_offlan_defaults(self, candidates):
        if not candidates:
            return
        cand_pairs = [f"{v}|{d}" for v, d in candidates if v and d]
        metrics = self.compute_install_metrics(len(candidates), cand_pairs)
        self.log(f"compute_install_metrics n={len(candidates)} result={' '.join(map(str, metrics))}")
        if not metrics:
            self.log("INSTALL decision=skip_all reason=metric_collision_with_survivor")
            return
        inst_exact, inst_pair = [], []
        for i, (via, dev) in enumerate(candidates):
            metric = metrics[i]
            if not (via and dev and metric):
                self.log(f"INSTALL decision=skip reason=malformed_candidate via={via} dev={dev} metric={metric}")
                continue
            onlink = "" if self._nh_is_on_connected_subnet(via, dev) else "onlink"
            role = "primary" if i == 0 else "fallback"
            self.log(f"INSTALL role={role} via={via} dev={dev} metric={metric} onlink={onlink or 'no'}")
            if self._default_already_installed(via, dev, metric, onlink):
                self.log(f"INSTALL_skip via={via} dev={dev} metric={metric} reason=already_exact")
                inst_exact.append(f"{via}|{dev}|{metric}")
                inst_pair.append(f"{via}|{dev}")
                continue
            args = ["add", "default", "via", via, "dev", dev, "metric", str(metric)]
            if onlink:
                args.append("onlink")
            args += self._src_args(via)
            rc = self.k.route(*args)
            self.log(f"INSTALL_rc via={via} dev={dev} metric={metric} rc={rc}")
            if rc == 0:
                inst_exact.append(f"{via}|{dev}|{metric}")
                inst_pair.append(f"{via}|{dev}")
        # OFFLAN_DEDUP_SWEEP: remove non-installed duplicates of an installed pair
        if not inst_pair:
            return
        for met, via, dev, _raw in self.collect_defaults():
            exact = f"{via}|{dev}|{met}"
            pair = f"{via}|{dev}"
            if exact in inst_exact:
                continue
            if pair not in inst_pair:
                continue
            self.log(f"OFFLAN_DEDUP_SWEEP via={via} dev={dev} metric={met} reason=duplicate_of_installed")
            self._delete_default(via, dev, met)

    # -- main --------------------------------------------------------------
    # -- forced override (operator-explicit) -------------------------------
    def install_forced_default(self, gw, dev):
        self.log(f"FORCED_ROUTE gw={gw} dev={dev or 'auto'} action=install")
        if not dev and self.route_get:
            rg = self.route_get(gw)
            if rg:
                dev = rg[0]
        if not dev:
            self.log(f"FORCED_ROUTE action=cannot_resolve_dev gw={gw}")
            return False
        onlink = "" if self._nh_is_on_connected_subnet(gw, dev) else "onlink"
        if self._default_already_installed(gw, dev, DEFAULT_ROUTE_METRIC, onlink):
            self.log(f"FORCED_INSTALL action=skip_noop_replace via={gw} dev={dev} "
                     f"metric={DEFAULT_ROUTE_METRIC} onlink={onlink or 'no'}")
        else:
            args = ["replace", "default", "via", gw, "dev", dev,
                    "metric", str(DEFAULT_ROUTE_METRIC)]
            if onlink:
                args.append("onlink")
            args += self._src_args(gw)
            self.k.route(*args)
        # remove every other default
        for met, via, dv, _raw in self.collect_defaults():
            if via == gw and dv == dev and met == DEFAULT_ROUTE_METRIC:
                continue
            self.log(f"FORCED removing_other_default via={via} dev={dv} metric={met}")
            self._delete_default(via, dv, met)
        if self.write_exit_sentinel:
            self.write_exit_sentinel(f"\t{gw}\t{dev}\t\t\n")
        return True

    # -- Mode B: mesh exit-host synthesis via getDefaultRoute.php -----------
    def _link_kind_for_nh(self, nh):
        cidr = self._pfx24(nh) + ".0/24"
        return "SEM" if cidr in self.semantic_cidrs else "FAST"

    @staticmethod
    def _exit_json_usable(j):
        if not isinstance(j, dict):
            return False
        try:
            if not (j.get("ok") is True and j.get("exit_present") is True):
                return False
            ts = int(j.get("ts", 0)); ttl = int(j.get("ttl_sec", 0))
        except (TypeError, ValueError):
            return False
        if ttl <= 0 or ttl > 180:
            return False
        import time as _t
        return _t.time() <= ts + ttl

    def choose_exit_hosts(self):
        """Ranked exit-capable peers: [(host, dev, nh, kind)], FAST>SEM then
        lowest host IP (lexical), capped at exit_max_defaults."""
        if not (self.route_get and self.get_default_route and self.known_hosts):
            return []
        cands = []
        for h in self.known_hosts():
            if not (self._is_ip(h) and self._is_10x(h)) or self.is_local_ip(h):
                continue
            rg = self.route_get(h)
            if not rg:
                continue
            dev, nh = rg
            if not (dev and self._is_ip(nh)):
                continue
            if not self.ping(nh, dev):
                continue
            j = self.get_default_route(h)
            if not self._exit_json_usable(j):
                continue
            kind = self._link_kind_for_nh(nh)
            krank = 0 if kind == "FAST" else 1
            cands.append((krank, h, dev, nh, kind))
        cands.sort(key=lambda c: (c[0], c[1]))
        return [(h, dev, nh, kind) for (_k, h, dev, nh, kind) in cands[:self.exit_max_defaults]]

    def install_exit_defaults(self, exits):
        kept = []
        first = None
        for i, (host, dev, nh, kind) in enumerate(exits):
            if not (dev and self._is_ip(nh)):
                continue
            metric = DEFAULT_ROUTE_METRIC + i  # FALLBACK_STRIDE = 1
            role = "primary" if i == 0 else "backup"
            self.log(f"EXIT_INSTALL role={role} default via={nh} dev={dev} "
                     f"metric={metric} exit_host={host} kind={kind}")
            if self._default_already_installed(nh, dev, metric, ""):
                self.log(f"EXIT_INSTALL action=skip_noop_replace via={nh} dev={dev} metric={metric}")
            else:
                self.k.route("replace", "default", "via", nh, "dev", dev,
                             "metric", str(metric), *self._src_args(nh))
            kept.append(f"{nh}|{dev}|{metric}")
            if first is None:
                first = (host, nh, dev)
        if not kept:
            return False
        # sweep any default not in the kept ladder (installed set is authoritative)
        for met, via, dev, _raw in self.collect_defaults():
            if f"{via}|{dev}|{met}" in kept:
                continue
            self.log(f"EXIT_SWEEP_STALE via={via} dev={dev} metric={met}")
            self._delete_default(via, dev, met)
        if first and self.write_exit_sentinel:
            self.write_exit_sentinel(f"{first[0]}\t{first[1]}\t{first[2]}\n")
        return True

    def run(self):
        self.log(f"ENTER default_metric={DEFAULT_ROUTE_METRIC} ping_wait=2")
        # 0) forced override wins and short-circuits everything
        if self.forced_line:
            gw, dev = self.forced_line
            if self.install_forced_default(gw, dev):
                self.log("DECISION action=forced_installed")
                return
            self.log("FORCED_ROUTE action=install_failed fallback=normal_discovery")
        else:
            self.log("forced_override status=none")
        # 1) purge
        self.purge_dead_defaults()
        # 2) discover off-LAN
        cands = self.discover_offlan_gateways()
        liststr = "".join(f"{v}\t{d}," for v, d in cands)
        self.log(f"offlan_candidates count={len(cands)} list={liststr}")
        # 3) install Mode A
        if cands:
            self.install_offlan_defaults(cands)
            self.log(f"DECISION action=installed_primary_plus_fallbacks candidates={len(cands)}")
            return
        # 4) Mode B: exit-host synthesis (no-op if backends absent, e.g. oracle)
        exits = self.choose_exit_hosts()
        if exits:
            self.log(f"EXIT_HOST_CANDIDATES count={len(exits)}")
            if self.install_exit_defaults(exits):
                self.log(f"DECISION action=installed_exit_synth_ladder exits={len(exits)}")
                return
        # 5) nothing found
        self.log("DECISION action=no_install reason=no_offlan_and_no_exit_host")
