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
routes.py - the route-writing layer of discovery, ported from sync_interfaces.sh.

Everything that touches the routing table funnels through RTMUT (bash line 49),
which here delegates to an injected kernel backend (kernel.RealKernel in prod,
kernel.FakeKernel in the sim). Same module, two backends.

Ported pieces (bash line refs):
  RTMUT                 (49)   single mutation chokepoint + [DIAG-ROUTE] readback
  probe_install         (228)  .2/32 metric-6 discovery route (LAN via/onlink | wg dev)
  probe_delete          (236)  del .2/32 metric 6
  route_matches         (259)  keyed by (dest, metric); first match wins
  install_if_changed    (276)  compare-then-act: KEEP if correct, else replace
  _sweep_probe_routes   (128)  del transient discovery /32s.  METRIC = 5 (see below)

[SWEEP_METRIC_V2 - John 2026-06-03]
  The tar source filters `metric 6`. Production was changed to `metric 5`; that
  was the change that made convergence work. The metric-6 inline probes are
  probe_delete'd inline; the entry+exit sweep removes the metric-5 .2/32 admin
  aliases (they are transient, NOT durable). SWEEP_METRIC = 5.

Constants from sync_interfaces.sh:
  PROBE_METRIC=6  WINNER_METRIC=22  ALIAS_METRIC=5  FALLBACK_BASE=100
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from .kernel import normalize_dest, _parse_route_argv

PROBE_METRIC = 6
WINNER_METRIC = 22
ALIAS_METRIC = 5
FALLBACK_BASE = 100
SWEEP_METRIC = 5   # [SWEEP_METRIC_V2] changed from 6

# [RUNAGAIN_ON_REAL_DELTA_V1] ------------------------------------------------
# runAgain used to key on route_table_mutated, which is set the moment a write
# RETURNS RC=0 -- not when the table ends up different. `ip route replace` on an
# already-correct route returns 0, a reaped /24 that promote re-installs next
# pass returns 0, and either one latched runAgain on a node whose table never
# moved. The gate is now a before/after comparison of the real table: take a
# fingerprint at the top of the pass, take another after the tail (fixDefault
# and manageResolv included -- they write routes too), and re-run ONLY if the
# two differ. Writes are still logged; they just no longer VOTE.
#
# Excluded from the fingerprint, and nothing else:
#   - host routes at PROBE_METRIC (6): the walk's scratch routes, installed and
#     deleted every pass by construction.
#   - host routes at ALIAS_METRIC (5): the same, per the WINNER_HYSTERESIS note.
#   - anything at metric >= FALLBACK_BASE (100): secondary paths traffic never
#     rides. [FALLBACK_NOT_CONVERGENCE_V1 - John 2026-06-12] exempted these at
#     the rtmut source because a jittery secondary tunnel reshuffles its dev
#     under RTT noise every pass while the winner is already settled, and
#     counting that latches the node forever. A before/after comparison sees the
#     same reshuffle as a real delta -- MORE readily than a write counter did,
#     because a fallback appearing at a new metric is a genuine table
#     difference. Seattle5 2026-09-12 06:51:02 is the proof: `slash24_written=0`
#     (the old gate correctly ignored it) alongside `table_changed=1` with
#     `+ 10.123.123.0/24 dev wg2 metric 101` and `+ 10.155.155.0/24 dev wg2
#     metric 101`. Excluding them here keeps the exemption John already made.
# "Host route" means a bare address with no prefix length, which is how both
# `ip route show` and normalize_dest render a /32 -- matching on the literal
# "/32" suffix silently matches nothing.
# Both are already excluded from route_table_mutated at the rtmut source, so
# this keeps the same scope while changing the question from "did we write?" to
# "is it different?".
#
# NOT excluded: the default route. Per the rule -- an actual change to the
# routing table -- a default that moves is a change and re-runs the pass. If a
# jittery upstream turns out to flip it every pass, exclude `default` HERE, in
# one place, rather than reintroducing a write-counter.
SCRATCH_HOST_METRICS = (PROBE_METRIC, ALIAS_METRIC)

_EXPIRES = re.compile(r"\s+expires\s+\d+sec")


def _canon_route_line(line: str) -> str:
    """One `ip -o -4 route show` record, whitespace-collapsed and stripped of
    fields that change on their own (lease countdowns). Returns "" for a record
    that must not participate in the comparison."""
    line = _EXPIRES.sub("", line)
    fields = line.split()
    if not fields:
        return ""
    dest = fields[0]
    metric = None
    if "metric" in fields:
        try:
            metric = int(fields[fields.index("metric") + 1])
        except (ValueError, IndexError):
            metric = None
    is_host = dest.endswith("/32") or ("/" not in dest and dest != "default")
    if is_host and metric in SCRATCH_HOST_METRICS:
        return ""
    if metric is not None and metric >= FALLBACK_BASE:
        return ""
    return " ".join(fields)


def table_fingerprint(kernel) -> frozenset:
    """Canonical set of the kernel's IPv4 routes, for before/after comparison.

    A set, not a list: `ip route show` orders by prefix then metric, so two
    identical tables always compare equal, and a reordering that the kernel
    would never produce cannot fake a delta.
    """
    return frozenset(
        c for c in (_canon_route_line(ln) for ln in kernel.table()) if c
    )


def fingerprint_delta(before: frozenset, after: frozenset):
    """(added, removed) as sorted lists -- what to LOG when the pass re-runs, so
    a non-converging chain names the routes that actually moved instead of
    leaving it to be inferred."""
    return sorted(after - before), sorted(before - after)


class Routes:
    """Route-writer bound to a kernel backend and a logger.

    logger: callable(str) -> None.  Emits the [DIAG-ROUTE]/ROUTE_INSTALL/etc.
            lines in the same shape as the bash log so traces correlate.
    clock:  callable() -> float, injectable for deterministic ts in tests.
    """

    def __init__(self, kernel, logger: Callable[[str], None] = lambda s: None,
                 clock: Callable[[], float] = time.time):
        self.k = kernel
        self.log = logger
        self.clock = clock
        # [SYNC_ON_ROUTE_MUTATION_V1] Set True the moment any COMMITTED route is
        # added/replaced/deleted this merge. sync_required keys on THIS, not on
        # /etc/hosts: the routing table is what neighbors need to hear about, and
        # it can churn (winner installed, verify-backout, sweep) while the host
        # table sits unchanged. Transient probe /32s (metric 6) are excluded - they
        # install+delete every walk regardless of convergence, so flagging them
        # would fire sync every cycle as noise.
        self.route_table_mutated = False
        # [MUTATED_DEST_TRACE_V1] Names the exact /24 dest(s) that flipped
        # route_table_mutated this pass, so converge_decision can report WHICH
        # winner moved instead of leaving it to be inferred from the table.
        self.mutated_slash24: set[str] = set()

    # -- RTMUT (bash 49): run the mutation, read it back, log it ------------
    # [WRITE_ONLY_REAL_CHANGES_V1] Every route write in the merge funnels
    # through here, so the compare-before-write lives here and not in each
    # caller. install_if_changed had its own check (route_matches -> KEEP), but
    # probe_install, probe_delete, sweep_probe_routes, reap_unverified_winners,
    # prune_dest_extras and healthcheck._probe all wrote unconditionally. The
    # walk therefore re-issued `ip route replace` for every probe /32 on every
    # candidate dev on every pass -- Seattle5 2026-09-12 06:52:16 shows the same
    # five /32s replaced and deleted across wg0/wg1/wg2/eth0 in a four-second
    # window, all of them rc=0, almost all of them writing what was already
    # there.
    #
    # A write of an identical route is not free. `ip route replace` is one
    # RTM_NEWROUTE with NLM_F_REPLACE and the FIB swap is atomic, so it does not
    # open a window with no route -- but it still emits an RTM_NEWROUTE
    # notification to every netlink subscriber on the box, and on this fleet
    # NetworkManager is one of them, with 90-frognet-merge behind it. A no-op
    # write is an event other components have to react to.
    #
    # So: an add/replace whose exact spec is already installed at that metric
    # does not run, and a del whose target is already absent does not run. Both
    # return rc=0, because the requested end state holds. Nothing else changes:
    # a route that really is being added, deleted or changed is written exactly
    # as before.
    def _installed_entries(self, dest: str):
        show = self.k.route_show(dest)
        if not show:
            return []
        out = []
        for line in [x for x in show.replace(";", "\n").splitlines() if x.strip()]:
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is not None:
                out.append(e)
        return out

    def _already_satisfied(self, args: tuple) -> bool:
        """True when running this spec would not change the table."""
        verb, want, raw_dest = _parse_route_argv(list(args[1:]) if args and args[0] == "route"
                                                 else list(args))
        if want is None or not raw_dest:
            return False
        have = self._installed_entries(raw_dest)
        if verb == "del":
            # `del DEST metric M` is metric-scoped; a bare `del DEST` is not.
            if getattr(want, "_metric_explicit", False):
                return not any(e.metric == want.metric for e in have)
            return not have
        if verb in ("add", "replace", "change"):
            for e in have:
                if e.metric != want.metric:
                    continue
                if (e.via == want.via and e.dev == want.dev
                        and bool(e.onlink) == bool(want.onlink)
                        and e.src == want.src):
                    return True
                # an entry exists at this metric but differs -> the write is real
                return False
            return False
        return False

    def rtmut(self, *args: str, caller: str = "?",
              flag_mutation: bool = True) -> int:
        ts = self.clock()
        spec = " ".join(args)
        verb = args[1] if len(args) > 1 else ""
        dest = args[2] if len(args) > 2 else ""
        # [WRITE_ONLY_REAL_CHANGES_V1] Nothing to do -> touch nothing. Logged as
        # NOOP rather than silently skipped, so a pass that writes nothing is
        # still visible in the trace and the reason is on the line.
        if verb in ("add", "replace", "change", "del") and dest:
            try:
                if self._already_satisfied(args):
                    present = self.k.route_show(dest) if dest else ""
                    self.log(
                        f"[DIAG-ROUTE] NOOP ts={ts} caller={caller} verb={verb} "
                        f"dest={dest} reason="
                        f"{'already_absent' if verb == 'del' else 'already_exact'} "
                        f"spec=\"ip {spec}\" have=\"{present or 'ABSENT'}\""
                    )
                    return 0
            except Exception as _e:   # a parse failure must never block a write
                self.log(f"[DIAG-ROUTE] NOOP_CHECK_SKIPPED caller={caller} "
                         f"dest={dest} err={_e!r}")
        rc = self.k.route(*args[1:]) if args and args[0] == "route" else self.k.route(*args)
        present = self.k.route_show(dest) if dest else ""
        # [RUNAGAIN_ON_MUTATION_V1] A run is "clean" (converged) iff the WINNER
        # SET did not move. Per John's rule, the /24 winner table settling is the
        # signal - not /32s (probe metric-6 AND alias metric-5 both churn every
        # pass) and not transit /30s. So flag route_table_mutated ONLY on a
        # successful add/replace/del of a /24 destination by a caller that can
        # actually change the winner set (promote's install_if_changed).
        # [REAP_NOT_CONVERGENCE_V1] reap_unverified_winners is the exception: it
        # ONLY ever deletes /24s that are NOT winners this pass, so by
        # construction its deletes cannot move the winner set. If something
        # external re-seeds a dead /24 every cycle (e.g. a dead-tunnel bringup
        # re-installing BABox's 10.111.11.0/24), counting that reap as a /24
        # mutation livelocks runAgain forever on an already-converged node.
        # Reap therefore passes flag_mutation=False: the delete still happens and
        # is logged, but it does not block convergence.
        # [FALLBACK_NOT_CONVERGENCE_V1 - John 2026-06-12] A fallback's dev reshuffling
        # under RTT noise (metric >= FALLBACK_BASE, i.e. 100/101) is a secondary-path
        # change traffic never rides - flagging it latches runAgain forever on a node
        # with a jittery secondary tunnel (NY-2 over wg0/wg1/wg2 swapping rank-1/rank-2
        # every pass while the winner is already settled). So exempt ONLY fallback
        # metrics: a winner install/replace (metric 22) and a full /24 removal (bare
        # del, no metric) still gate convergence. (prune_dest_extras already passes
        # flag_mutation=False.)
        if rc == 0 and verb in ("add", "replace", "del", "change"):
            is_slash24 = dest.endswith("/24")
            metric = None
            if "metric" in args:
                try:
                    metric = int(args[args.index("metric") + 1])
                except (ValueError, IndexError):
                    metric = None
            is_fallback = (metric is not None and metric >= FALLBACK_BASE)
            if is_slash24 and flag_mutation and not is_fallback:
                self.route_table_mutated = True
                self.mutated_slash24.add(dest)
        self.log(
            f"[DIAG-ROUTE] MUTATE ts={ts} caller={caller} verb={verb} rc={rc} "
            f"dest={dest} spec=\"ip {spec}\" after=\"{present or 'ABSENT'}\""
        )
        return rc

    # -- probe_install (bash 228) / probe_delete (236) ---------------------
    def probe_install(self, pip: str, dev: str, disc_via: str, src: str = "",
                      onlink: bool = False) -> int:
        # disc_via empty (tunnel) -> <pip>/32 dev wgN [src S]
        # disc_via set    (LAN)   -> <pip>/32 via <disc_via> dev <dev> [onlink]
        # onlink defaults OFF: hop-to-hop routes use a real on-segment next-hop
        # (the address we are talking to), which resolves normally - no onlink.
        if not disc_via:
            args = ["route", "replace", f"{pip}/32", "dev", dev, "metric", str(PROBE_METRIC)]
            if src:
                args += ["src", src]
        else:
            args = ["route", "replace", f"{pip}/32", "via", disc_via, "dev", dev]
            if onlink:
                args += ["onlink"]
            args += ["metric", str(PROBE_METRIC)]
        rc = self.rtmut(*args, caller="probe_install")
        if rc == 2 and disc_via and not onlink:
            # [ONLINK_RETRY_V1] gateway not on a connected /24 (child->parent uplink);
            # re-assert the probe route on-link so the avenue can be proven at all.
            args = ["route", "replace", f"{pip}/32", "via", disc_via, "dev", dev,
                    "onlink", "metric", str(PROBE_METRIC)]
            rc = self.rtmut(*args, caller="probe_install")
        return rc

    def probe_delete(self, pip: str) -> int:
        return self.rtmut("route", "del", f"{pip}/32", "metric", str(PROBE_METRIC),
                          caller="probe_delete")

    # -- route_matches (bash 259) ------------------------------------------
    # 0/True  = an entry for dest at this metric already matches exactly (LEAVE)
    # 1/False = absent or differs (write it)
    def route_matches(self, dest: str, w_via: str, w_dev: str,
                      w_metric: int, w_onlink: int, w_src: str = "") -> bool:
        show = self.k.route_show(dest)
        if not show:
            return False
        for line in [x for x in show.replace(";", "\n").splitlines() if x.strip()]:
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is None:
                continue
            if e.metric != w_metric:
                continue
            # bash returns on the FIRST line matching this metric
            c_onlink = 1 if e.onlink else 0
            base = (e.via == w_via and e.dev == w_dev and c_onlink == w_onlink)
            # src is compared in ONE direction only: an installed route still
            # carrying a src pin when the wanted route is src-less (the pre-fix
            # too-wide LAN-via pin) must NOT read as "already correct", or a healthy
            # KEEP strands it forever (reap keeps verified winners). Other src
            # differences (wg identity vs transit /30, etc.) are left to their own
            # install path - comparing them here churns wg routes and breaks
            # winner hysteresis.
            if base and w_src == "" and e.src:
                return False
            return base
        return False

    # -- winner_dev [WINNER_HYSTERESIS_V1] ---------------------------------
    # dev of the currently-installed metric-22 winner for dest, or None if
    # absent. promote uses it to keep a settled winner unless a challenger is
    # meaningfully faster - it must measure at or below HALF the incumbent's RTT
    # (WINNER_HYSTERESIS = 0.50, discovery.py:33). Two near-equal tunnel paths
    # otherwise flip the winner every pass under RTT noise and latch runAgain
    # forever.
    def winner_dev(self, dest: str) -> str:
        show = self.k.route_show(dest)
        if not show:
            return ""
        for line in [x for x in show.replace(";", "\n").splitlines() if x.strip()]:
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is None:
                continue
            if e.metric == WINNER_METRIC:
                return e.dev
        return ""

    # -- winner_via [WINNER_HYSTERESIS_V1] via-level companion to winner_dev ----
    # The via (next-hop) of the installed metric-22 winner, or "" if absent / a
    # direct (no-via) route. promote keys stickiness on the full (via, dev)
    # identity, not dev alone: several relays can reach ONE dest over the SAME dev
    # at near-equal RTT, so a dev-only check never engages and the lowest-noise
    # relay wins every pass -> the /24 via flips -> route_table_mutated -> runAgain
    # latches forever. With the via, the incumbent relay is held unless a
    # challenger beats it by >10% (same threshold as the dev case).
    def winner_via(self, dest: str) -> str:
        show = self.k.route_show(dest)
        if not show:
            return ""
        for line in [x for x in show.replace(";", "\n").splitlines() if x.strip()]:
            _, e, _ = _parse_route_argv(["__x__", *line.split()])
            if e is None:
                continue
            if e.metric == WINNER_METRIC:
                return e.via or ""
        return ""

    # -- install_if_changed (bash 276) -------------------------------------
    def install_if_changed(self, dest: str, via: str, dev: str, metric: int,
                           onlink: int, src: str = "") -> None:
        # [DOWNSTREAM_VIA_SRC_LESS_V1] John's rule (SRC_PIN_SCOPE): a /24 reached
        # VIA a first hop is src-LESS. The reply returns via that hop to this
        # node's lease on the wire, not to an off-segment identity the far side
        # may have no route back to - the too-wide src pin that broke Seattle5.
        # Only the node's own connected first-hop (no via) and the exit default
        # (installed in fixdefault, not here) keep src=identity. wg egress is the
        # documented exception: a bare tunnel route sources from the transit /30
        # and black-holes children beyond the peer (NY1), so identity src stays.
        # [LAN_VIA_NO_ONLINK_V1] A LAN via-route's gateway .1 sits on this node's
        # connected lease subnet, reached scope-link - NO onlink. onlink here
        # shadows the kernel's connected-subnet route and makes plain via-installs
        # fail "Nexthop has invalid gateway" (root-caused 2026-06-01). onlink is
        # valid ONLY on a wg relay (via peer.1 that is OFF the dest). See the
        # four-shapes doctrine.
        if via and not dev.startswith("wg"):
            src = ""
            onlink = 0
        if self.route_matches(dest, via, dev, metric, onlink, src):
            cur = self.k.route_show(dest)
            self.log(f"[DIAG-ROUTE] KEEP dest={dest} via={via} dev={dev} "
                     f"metric={metric} reason=already_correct cur=\"{cur or 'ABSENT'}\"")
            return
        args = ["route", "replace", dest]
        if via:
            args += ["via", via]
        args += ["dev", dev, "metric", str(metric)]
        if onlink == 1 and via:
            args += ["onlink"]
        if src:
            args += ["src", src]
        rc = self.rtmut(*args, caller="install_if_changed")
        if rc == 2 and via and onlink != 1 and dev.startswith("wg"):
            # [ONLINK_RETRY_V1] rc=2 == "Nexthop has invalid gateway" on a wg relay:
            # peer.1 is reachable through the tunnel but not on a connected /24, so
            # re-assert with `onlink` (the flag the kernel needs when the gateway is
            # off-subnet). The avenue was already proven by the probe/reflect/:9009
            # gate, so this is not a black-hole route. Restricted to wg: a LAN via
            # that returns rc=2 is a shadowed-scope-link symptom (some node wrongly
            # installed onlink on a LAN route) - the fix is to stop doing that, never
            # to pin onlink here.
            args2 = ["route", "replace", dest, "via", via, "dev", dev,
                     "metric", str(metric), "onlink"]
            if src:
                args2 += ["src", src]
            rc = self.rtmut(*args2, caller="install_if_changed")
            if rc == 0:
                onlink = 1
        if rc == 0:
            self.log(f"ROUTE_INSTALL dest={dest} via={via} dev={dev} "
                     f"metric={metric} onlink={onlink}")
        else:
            self.log(f"ROUTE_INSTALL_FAILED dest={dest} via={via} dev={dev} metric={metric}")

    # -- reap_unverified_winners [REAP_STALE_WINNERS_V1] ------------------
    def reap_unverified_winners(self, verified, own_subnets=(), logger=None):
        """Delete every /24 route that is NOT a verified winner this pass.

        John's rule, canonical: runMerge does a COMPLETE discovery every time.
        The winners list (built from the .2 probe routes) IS the whole truth -
        there is no legacy, no carryover. If a /24 was not discovered as a winner
        this pass, it is not real and must be deleted. The only /24 exempt is the
        node's own connected subnet (kernel-proto, not a discovered route).

        This is what removes a reflect-detected loop route (VOUCH_SKIP installs
        nothing, so the dest is simply absent from `verified` -> reaped), a route
        to a departed node, or any path that moved - all without a "was it
        considered" qualifier, because a full discovery leaves nothing to assume.

        verified    = /24 dests installed as winners this pass (canonical set).
        own_subnets = this node's connected /24(s), never reaped.
        """
        log = logger or self.log
        verified = set(verified)
        own = set(own_subnets)
        # [REAP_NONAUTHORITATIVE_HOLD_V1] A pass that produced ZERO winners did not
        # do a complete discovery - it did no discovery (uplink blip, getHosts
        # failure, total isolation). "No winners" is not "everything is dead"; it
        # is "no evidence this pass." Reaping the whole table on it is the failure
        # that razed the node to its connected /24. No winners -> hold everything,
        # reap nothing. The reap only speaks when the pass actually found routes.
        if not verified:
            log("REAP_SUMMARY reaped=0 verified=0 reason=nonauthoritative_pass_hold_all")
            return
        full = self.k.route_show()
        reaped = 0
        seen = set()
        for line in full.replace(";", "\n").splitlines():
            parts = line.split()
            if not parts:
                continue
            dest = parts[0]
            if not dest.endswith("/24") or not dest.startswith("10."):
                continue
            if dest in verified or dest in own or dest in seen:
                continue
            # never reap the kernel-proto connected /24 (own served subnet)
            if "proto kernel" in line:
                continue
            seen.add(dest)
            rc = self.rtmut("route", "del", dest, caller="reap_unverified_winners",
                             flag_mutation=False)
            if rc == 0:
                reaped += 1
                log(f"REAP_STALE dest={dest} "
                    f"reason=not_a_winner_this_pass_full_discovery_canonical")
        log(f"REAP_SUMMARY reaped={reaped} verified={len(verified)}")
        return reaped

    # -- prune_dest_extras [PRUNE_DEST_EXTRAS_V1] -------------------------
    def prune_dest_extras(self, dest: str, keep_metrics, logger=None) -> int:
        """Delete every route for THIS /24 whose metric was not installed this
        pass. promote() lays down the winner (metric 22) plus one fallback per
        extra dev (100, 101, ...); install_if_changed REPLACES per (dest, metric),
        so a metric promote wrote carries the right dev, but a metric a PRIOR pass
        wrote and this pass did not (a LAN corpse when this pass chose a tunnel, a
        higher-metric leftover from a pass that ranked more devs) survives untouched
        - reap_unverified_winners keeps it because the dest IS a winner. That is the
        'route is both local and wg' state and the stale copy a later pass trips on.

        keep_metrics = the set of metrics promote installed for `dest` this pass.
        Deletes use flag_mutation=False: removing a non-winner copy cannot move the
        winner set, so it must NOT flip route_table_mutated (else a node whose only
        change is cleaning a corpse livelocks runAgain - same rule as the reap).
        """
        log = logger or self.log
        cur = self.k.route_show(dest)
        if not cur:
            return 0
        keep = {int(m) for m in keep_metrics}
        pruned = 0
        seen_metrics = set()
        for line in cur.replace(";", "\n").splitlines():
            parts = line.split()
            if not parts or parts[0] != dest:
                continue
            # never prune the kernel-proto connected route: it is the OS's own
            # route for a locally-addressed interface (NM/dhcp assign it an EXPLICIT
            # metric - 100 for eth, 600 for wifi - so the metric-None heuristic below
            # does NOT catch it). Deleting it strips this node off a LAN it is a
            # client on. Same invariant reap_unverified_winners already enforces.
            if "proto kernel" in line:
                continue
            metric = None
            for i, tok in enumerate(parts):
                if tok == "metric" and i + 1 < len(parts):
                    try:
                        metric = int(parts[i + 1])
                    except ValueError:
                        metric = None
            # a /24 with no explicit metric is a kernel-proto connected route or a
            # default-metric copy - never a promote artifact; leave it alone.
            if metric is None or metric in keep or metric in seen_metrics:
                continue
            seen_metrics.add(metric)
            rc = self.rtmut("route", "del", dest, "metric", str(metric),
                            caller="prune_dest_extras", flag_mutation=False)
            if rc == 0:
                pruned += 1
                log(f"PRUNE_DEST_EXTRA dest={dest} metric={metric} "
                    f"reason=not_installed_this_pass")
        return pruned

    def sweep_probe_routes(self) -> int:
        n = 0
        full = self.k.route_show()  # full table (RealKernel joins lines with ';')
        targets = []
        for line in full.replace(";", "\n").splitlines():
            if not line.startswith("10."):
                continue
            # Transient discovery /32s: the metric-5 admin alias AND the metric-6
            # probe route. A probe whose inline probe_delete did not fire (crash /
            # interrupt / skipped path) leaks a metric-6 /32 that OUTRANKS the real
            # metric-22 route to the same node - so it must be swept, not just the
            # metric-5 alias. Only /32s (never a real /24 winner, which is metric 22+).
            if not (f"metric {SWEEP_METRIC}" in line or f"metric {PROBE_METRIC}" in line):
                continue
            cidr = line.split()[0]
            if not cidr.endswith("/32"):
                # bare host form (no /32 suffix) from `ip route` is still a /32
                if "/" in cidr:
                    continue
            targets.append(cidr)
        for cidr in targets:
            if self.rtmut("route", "del", cidr, caller="_sweep_probe_routes") == 0:
                n += 1
        self.log(f"PROBE_SWEEP swept={n}")
        return n
