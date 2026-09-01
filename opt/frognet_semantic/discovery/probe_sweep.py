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
probe_sweep - measure every (target, interface) route candidate CONCURRENTLY.

The target list is the hosts we already know exist (from the transient DB, minus
the level-1 direct contacts, which are marked off before we get here). For each
remaining target we have to find which of our interfaces carries it and how fast.
That is a ping down every interface, and doing them one at a time is what made the
old walk take minutes: the wall-clock cost was the SUM of every dead path's
timeout. Fan them out and the cost becomes the SINGLE longest timeout instead -
same probes, same negatives, but the waiting overlaps.

Design points that make it safe rather than just fast:

  * Race-free routing-table use. Each (target, dev) CELL owns a unique probe
    metric, so no two concurrent workers ever touch the same route. `ip route`
    ops on distinct routes are serialized by the kernel; distinct metrics keep
    them distinct. Multiple candidate `via`s for the same (target, dev) are tried
    sequentially WITHIN that cell (a dev-bound socket can't disambiguate two
    routes to one dest on one dev), while the TxD matrix runs in parallel - which
    is where the "shitload" of parallelism lives.

  * Bounded, self-destructing workers. The probe itself is bounded by the socket
    connect/frame timeouts (RealVerify.ct/.ft), so a worker cannot hang past
    ct+ft no matter what the far side does. Each worker deletes its own route in
    a `finally`, so a probe that dies still cleans up. A per-future wall-clock
    ceiling is the belt-and-suspenders: we stop waiting on a wedged cell and
    treat it as dead. Nothing is left parked on a connect() the kernel won't time
    out for a minute.

  * Loop detection is the probe's, not ours. _ping_pong already returns "LOOP"
    for a route that bends back through us (the :9009 loop frame). We consume that
    verdict: a LOOP candidate is pruned before winner selection and never costs
    its full deadline.

The caller decides WHAT to sweep. Incumbency-hold belongs upstream: if a target's
installed route still echoes its .1 alive, don't put it in the candidate list at
all - the sweep is the cold-start / broken-route cost, not a per-pass re-measure.
"""
from __future__ import annotations

import threading
from collections import namedtuple, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Probe metrics for the parallel sweep live in their own high band so they never
# collide with winner (22), fallback (100), alias (5) or the serial probe (6).
SWEEP_METRIC_BASE = 2000
DEFAULT_MAX_WORKERS = 64

Candidate = namedtuple("Candidate", "target dev via")          # via="" => on-link
ProbeResult = namedtuple("ProbeResult", "target dev via verdict rtt_ms")
#   verdict: "ALIVE" (rtt_ms set) | "LOOP" | "REFUSED" | "DEAD"


def _route_add(routes, target, dev, via, metric):
    # k.route(*args) runs `ip route <args>` - verb first, no "route" prefix.
    # Use k.route directly (not rtmut): transient probe scaffolding must never
    # flip route_table_mutated, or the sweep itself would trigger runAgain.
    if via:
        routes.k.route("replace", f"{target}/32", "via", via,
                       "dev", dev, "metric", str(metric))
    else:
        routes.k.route("replace", f"{target}/32", "dev", dev, "metric", str(metric))


def _route_del(routes, target, metric):
    try:
        routes.k.route("del", f"{target}/32", "metric", str(metric))
    except Exception:
        pass


def _probe_cell(cell_key, vias, metric, verify, routes, logger):
    """One (target, dev) cell. Try its candidate vias sequentially on a single
    owned metric; return the best (lowest-RTT ALIVE) result for the cell, or the
    most informative non-alive verdict (LOOP > REFUSED > DEAD) if none echoed."""
    target, dev = cell_key
    best = None
    fallback = None
    rank = {"LOOP": 3, "REFUSED": 2, "DEAD": 1}
    for via in vias:
        try:
            _route_add(routes, target, dev, via, metric)
            v = verify.measure_or_loop(target, dev)   # float | "LOOP" | "REFUSED" | None
        except Exception as e:
            v = None
            if logger:
                logger(f"[SWEEP] probe_error target={target} dev={dev} "
                       f"via={via or '-'} err={e}")
        finally:
            _route_del(routes, target, metric)

        if isinstance(v, float):
            r = ProbeResult(target, dev, via, "ALIVE", round(v, 3))
            if best is None or r.rtt_ms < best.rtt_ms:
                best = r
            # a live path is the answer for this cell; stop trying more vias
            break
        else:
            verdict = v if v in ("LOOP", "REFUSED") else "DEAD"
            cand = ProbeResult(target, dev, via, verdict, None)
            if fallback is None or rank[verdict] > rank[fallback.verdict]:
                fallback = cand

    return best or fallback


def sweep(candidates, verify, routes, *, max_workers=DEFAULT_MAX_WORKERS,
          base_metric=SWEEP_METRIC_BASE, cell_ceiling_s=None, logger=None):
    """Measure all candidates concurrently. Returns (winners, all_results).

      winners     : {target: ProbeResult}   fastest ALIVE route per target
      all_results : [ProbeResult, ...]       every cell's outcome (for logging)

    candidates    : iterable of Candidate(target, dev, via)
    verify        : RealVerify (uses ._ping_pong, .ct, .ft)
    routes        : Routes    (uses .k.route for install/del)
    """
    # Group candidate vias by (target, dev) cell.
    cells = defaultdict(list)
    for c in candidates:
        cells[(c.target, c.dev)].append(c.via)

    if not cells:
        return {}, []

    # Per-future wall-clock ceiling: worst case a cell walks all its vias, each
    # bounded by connect+frame. Give slack so we never abandon a cell that is
    # still legitimately measuring.
    per_probe = float(getattr(verify, "ct", 2.0)) + float(getattr(verify, "ft", 3.0))
    if cell_ceiling_s is None:
        widest = max(len(v) for v in cells.values())
        cell_ceiling_s = per_probe * widest + 2.0

    results = []
    metric_of = {}
    for i, key in enumerate(cells):
        metric_of[key] = base_metric + i

    workers = min(max_workers, len(cells))
    if logger:
        logger(f"[SWEEP] start cells={len(cells)} workers={workers} "
               f"per_probe_s={per_probe:.1f} cell_ceiling_s={cell_ceiling_s:.1f}")

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="probesweep") as ex:
        fut_key = {
            ex.submit(_probe_cell, key, cells[key], metric_of[key],
                      verify, routes, logger): key
            for key in cells
        }
        for fut in as_completed(fut_key):
            key = fut_key[fut]
            try:
                r = fut.result(timeout=cell_ceiling_s)
            except Exception:
                # Wedged or errored past the ceiling: treat as dead, and make
                # sure its owned route can't leak.
                t, _dev = key
                _route_del(routes, t, metric_of[key])
                r = ProbeResult(t, _dev, "", "DEAD", None)
            if r is not None:
                results.append(r)

    # Backstop: no owned metric should survive, but never leave scaffolding.
    for key, m in metric_of.items():
        _route_del(routes, key[0], m)

    # Winner per target: lowest-RTT ALIVE. LOOP/REFUSED/DEAD never win.
    winners = {}
    for r in results:
        if r.verdict != "ALIVE":
            continue
        cur = winners.get(r.target)
        if cur is None or r.rtt_ms < cur.rtt_ms:
            winners[r.target] = r

    if logger:
        alive = sum(1 for r in results if r.verdict == "ALIVE")
        loop = sum(1 for r in results if r.verdict == "LOOP")
        dead = sum(1 for r in results if r.verdict == "DEAD")
        logger(f"[SWEEP] done cells={len(results)} alive={alive} loop={loop} "
               f"dead={dead} winners={len(winners)}")
        for t, w in sorted(winners.items()):
            logger(f"[SWEEP_WINNER] target={t} dev={w.dev} "
                   f"via={w.via or '-'} rtt_ms={w.rtt_ms}")

    return winners, results


def build_candidates(targets, interfaces, via_for=None):
    """Expand targets x interfaces into Candidates.

    targets    : [target_ip, ...]   the .1s from /etc/hosts, level-1 already removed
    interfaces : [dev, ...]         every interface to try, eth0 included
    via_for    : optional fn(target, dev) -> [via, ...]. Default: on-link only
                 (via=""), i.e. let the interface's own routing carry it. Supply
                 relay next-hops here when a target is reached through a relay on
                 that dev.
    """
    out = []
    for t in targets:
        for dev in interfaces:
            vias = via_for(t, dev) if via_for else [""]
            for via in (vias or [""]):
                out.append(Candidate(t, dev, via))
    return out
