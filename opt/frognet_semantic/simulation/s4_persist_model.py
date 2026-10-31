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
s4_persist_model.py - [S4_PERSIST_MODEL_V1] MODELED. The same question as s4_persist.py (tier 4, real FRR), carried to
sizes one machine cannot run as real daemons (1k .. 100k nodes), and the same timeline read two ways:

  E1  IS-IS: how long does an inconsistency -- and any forwarding loop it makes -- last, in time and in SPF runs?
  E2  SPB (802.1aq agreement protocol): which nodes ever see a neighbour's topology digest differ from their own, how
      many digest exchanges does each of them make before its ports agree, and how long are its ports held?

Nothing here is measured. Every timing rule is FRR 8.4.4's own, transcribed from its source:
  hold timer      adjacency drops hold = hello x multiplier after the last hello heard (silent loss); carrier at once
  LSP generation  _lsp_regenerate_schedule (isis_lsp.c): pending regen absorbs later changes; within lsp-gen-interval of
                  the last generation (whole seconds, time(NULL)) wait 1000 x (interval - diff) ms, else 100 ms
  SPF schedule    _isis_spf_schedule (isis_spf.c): with spf-delay-ietf, spf_backoff_schedule (lib/spf_backoff.c) --
                  RFC 8405 QUIET / SHORT_WAIT / LONG_WAIT, holddown restarted by every trigger, time-to-learn started
                  on leaving QUIET; without it, min spf-interval in whole seconds (monotime): run now if >= interval
                  since the last run, else wait interval - diff
  flooding        each LSP version reaches node m at its generation time + the least (link delay + per-hop
                  processing) over the surviving links
  next hops       every equal-cost neighbour (metric 10 on every circuit, as on the bed), from the node's LSDB view at
                  its SPF run; an edge leaves the view when EITHER end's LSP stops listing it (two-way check)
  FIB install     run + SPF time + install base + per-route time x the route's place among the routes that run changed
The free constants (per-hop processing, SPF time, install base, per-route time) are CALIBRATED against the tier 4 runs
(`calibrate`), and reported with every result.

The loop detector is the tier 4 rule (a cycle in a destination's next-hop graph, dead links carry nothing) applied
incrementally: when a node's next hops change, every cycle through it up to --max-cycle nodes is found by DFS, and an
open loop closes the moment one of its edges is gone. `check` runs it against s4_persist.analyse on the same modeled
timelines; they must agree.

usage:
  s4_persist_model.py run --topo ring|grid|mesh --n N --event cut|silent|partition|nodefail [--profile tuned|default]
                          [--dests K] [--seed S] [--cal FILE] [--out FILE]
  s4_persist_model.py check                   # incremental detector == s4_persist.analyse, small graphs
  s4_persist_model.py calibrate DIR...        # fit the free constants from tier 4 result dirs
"""
import argparse
import heapq
import json
import math
import os
import random
import sys
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra, shortest_path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_persist as P                                             # noqa: E402  (topology, split, analyse, cycles)

BUILD = "s4-persist-model-2026-10-31-r4"
INF = float("inf")

PROFILES = {
    # hello s, multiplier, lsp-gen-interval s, spf-delay-ietf (init, short, long, holddown, time-to-learn) ms or None,
    # spf-interval s -- the same two profiles s4_isis.py configures on the bed
    "tuned": {"hello": 1.0, "mult": 3, "lsp_gen": 1, "ietf": (50, 200, 5000, 10000, 500), "spf_interval": 1},
    "default": {"hello": 10.0, "mult": 3, "lsp_gen": 30, "ietf": None, "spf_interval": 1},
}

# Free constants, replaced by `calibrate` output (--cal). Units: ms, except per-route (us) and the SPF term.
CAL = {"carrier_ms": 1.0, "proc_ms": 2.0, "instab_ms": 100.0, "spf_base_ms": 0.5, "spf_ns_per_nlogn": 50.0,
       "fib_base_ms": 2.0, "fib_us_per_route": 30.0, "source": "uncalibrated defaults"}


# --------------------------------------------------------------------------------------------------------------- graph
class Graph:
    def __init__(self, t):
        self.t = t
        self.n = t["n"]
        self.links = t["links"]                          # [(a, b, delay_ms)], index = edge id
        self.nbrs = [[] for _ in range(self.n)]          # node -> [(nbr, edge id)]
        for j, (a, b, _) in enumerate(self.links):
            self.nbrs[a].append((b, j))
            self.nbrs[b].append((a, j))

    def csr(self, drop=frozenset(), weight=None):
        """adjacency matrix, row u col v = the hop u->v. drop holds edge ids (both directions) or (edge id, 0|1) for
        one direction: 0 = a->b, 1 = b->a, a and b as in links"""
        rows, cols, w = [], [], []
        for j, (a, b, d) in enumerate(self.links):
            if j in drop:
                continue
            x = 1.0 if weight is None else weight(d)
            if (j, 0) not in drop:
                rows.append(a); cols.append(b); w.append(x)
            if (j, 1) not in drop:
                rows.append(b); cols.append(a); w.append(x)
        return csr_matrix((w, (rows, cols)), shape=(self.n, self.n))


# ------------------------------------------------------------------------------------------------------------ scenario
def scenario(g, event, prof, cal, rnd):
    """-> (failed edge ids, {(node, edge): detection ms}). Event at t = 0. The failed links are the ones s4_persist.py
    silences/cuts for the same topology and event."""
    t = g.t
    if event in ("cut", "silent"):
        F = [P.split(t)[0]]
    elif event == "partition":
        F = P.split(t)
    else:
        victim = t["n"] // 2
        F = [j for j, (x, y, _) in enumerate(t["links"]) if victim in (x, y)]
    hold = prof["hello"] * prof["mult"] * 1000.0
    det = {}
    for j in F:
        a, b, _ = g.links[j]
        for x in (a, b):
            # nodefail as on the bed: the node's links go silent, its daemons keep running (isolated, like a node cut
            # off by a failed line card), so it detects and reprograms its own FIB as well
            if event == "cut":
                det[(x, j)] = cal["carrier_ms"]
            else:
                det[(x, j)] = hold - rnd.uniform(0, prof["hello"] * 1000.0)   # last hello heard U(0, hello) before
    return F, det


def lsp_versions(det_times, prof, cal, epoch_ms):
    """One origin's LSP generations after the event (FRR _lsp_regenerate_schedule). det_times: [(ms, edge)] its own
    adjacencies going down. -> [(gen_ms, frozenset(edges it no longer lists))]. epoch_ms places t=0 inside a wall
    second, since FRR compares whole seconds."""
    sec = lambda ms: math.floor((epoch_ms + ms) / 1000.0)
    out, last_gen, pending, down = [], None, None, set()
    evs = sorted(det_times)
    i = 0
    while i < len(evs) or pending is not None:
        if pending is not None and (i >= len(evs) or pending <= evs[i][0]):
            cur = frozenset(e for tt, e in evs if tt <= pending)
            if not out or cur != out[-1][1]:
                out.append((pending, cur))
            last_gen, pending = pending, None
            continue
        tt, e = evs[i]; i += 1
        if pending is not None:
            continue                                      # regeneration already pending: it will carry this change
        if last_gen is not None and sec(tt) - sec(last_gen) < prof["lsp_gen"]:
            pending = tt + 1000.0 * (prof["lsp_gen"] - (sec(tt) - sec(last_gen)))
        else:
            pending = tt + cal["instab_ms"]
    return out


def spf_runs(triggers, prof, epoch_ms):
    """SPF run times for one node from its sorted trigger times (FRR _isis_spf_schedule + spf_backoff_schedule)."""
    runs = []
    if prof["ietf"]:
        init, short, long_, holddown, ttl = prof["ietf"]
        state, learn_at, hold_at, pending = "QUIET", None, None, None
        for tt in triggers:
            # timers that fire before this trigger, in time order: pending run, learn, holddown
            while True:
                nxt = min([x for x in (pending, learn_at, hold_at) if x is not None and x <= tt], default=None)
                if nxt is None:
                    break
                if nxt == pending:
                    runs.append(pending); pending = None
                elif nxt == learn_at:
                    state, learn_at = "LONG_WAIT", None
                else:
                    state, learn_at, hold_at = "QUIET", None, None
            if state == "QUIET":
                state, learn_at, hold_at, delay = "SHORT_WAIT", tt + ttl, tt + holddown, init
            else:
                hold_at, delay = tt + holddown, (short if state == "SHORT_WAIT" else long_)
            if pending is None:
                pending = tt + delay
        if pending is not None:
            runs.append(pending)
    else:
        iv = prof["spf_interval"]
        sec = lambda ms: math.floor((epoch_ms + ms) / 1000.0)
        last, pending = -1e12, None
        for tt in triggers:
            if pending is not None and pending <= tt:
                runs.append(pending); last, pending = pending, None
            if pending is not None:
                continue
            diff = sec(tt) - sec(last)
            pending = tt if diff >= iv else tt + 1000.0 * (iv - diff)
        if pending is not None:
            runs.append(pending)
    return runs


# --------------------------------------------------------------------------------------------------------------- model
def model(t, event, profile="tuned", seed=1, k_dests=64, cal=None, max_cycle=8, spb=True, dests=None):
    """-> dict of results (E1 loops and windows, E2 SPB agreement), plus the modeled FIB timeline for `check`."""
    cal = dict(CAL, **(cal or {}))
    prof = PROFILES[profile]
    rnd = random.Random(seed * 7919 + 17)
    g = Graph(t)
    n = g.n
    F, det = scenario(g, event, prof, cal, rnd)
    Fset = frozenset(F)
    fidx = {e: i for i, e in enumerate(F)}                       # failed edge -> index i
    # [DIRECTED_LSDB_V1] FRR's SPF takes the hop a->b from a's own LSP alone (process_lsp: the only "two way" test
    # it makes is to skip links back to the root), so each end's LSP removes ONE direction of a failed link. Bit 2i is
    # a->b of failed link i (a = its lower-numbered end), bit 2i+1 is b->a. Found on the bed: a node whose first
    # LSP came from the far end of the link kept its route until the near end's LSP reached it.
    dbit = lambda e, frm: 2 * fidx[e] + (0 if frm == g.links[e][0] else 1)
    epoch = rnd.uniform(0, 1000.0)

    # LSP versions per origin
    per_origin = defaultdict(list)
    for (x, e), tt in det.items():
        per_origin[x].append((tt, e))
    versions = {o: lsp_versions(v, prof, cal, epoch) for o, v in per_origin.items()}
    origins = sorted(versions)

    # flooding: least (delay + per-hop processing) over the surviving links, from every origin
    surv = g.csr(drop=Fset, weight=lambda d: d + cal["proc_ms"])
    fl = dijkstra(surv, directed=False, indices=origins) if origins else np.zeros((0, n))
    # known-down time of each failed edge at each node: own detection, or first LSP from either end not listing it
    kt = np.full((2 * len(F), n), INF)
    trig = [[] for _ in range(n)]
    for oi, o in enumerate(origins):
        arr = fl[oi]
        prev = frozenset()
        for gen, cur in versions[o]:
            at = gen + arr
            for m in np.nonzero(np.isfinite(at))[0]:
                trig[m].append(float(at[m]))
            for e in cur - prev:
                kt[dbit(e, o)] = np.minimum(kt[dbit(e, o)], at)
            prev = cur
    # [OWN_ADJ_NO_SPF_TRIGGER_V1] a node's own adjacency loss does not schedule SPF directly; its own LSP
    # regeneration does (FRR logs every SPF schedule with its caller: on the bed, the first trigger at a detecting
    # node is lsp_inc_seqno, ~100 ms after detection, never an adjacency callback). From detection on, though, any SPF
    # it runs leaves the adjacency out (isis_spf_preload_tent takes only UP adjacencies), so the edge is known at det.
    for (x, e), tt in det.items():
        kt[dbit(e, x), x] = min(kt[dbit(e, x), x], tt)

    spf_ms = cal["spf_base_ms"] + cal["spf_ns_per_nlogn"] * n * math.log2(max(n, 2)) / 1e6
    # each node: SPF runs, the LSDB view (bitmask of failed edges known down) at each run
    runs, run_views = [], []
    for m in range(n):
        tr = sorted(trig[m])
        rs = spf_runs(tr, prof, epoch)
        col = kt[:, m]
        vs = []
        for r in rs:
            mask = 0
            for i in np.nonzero(col <= r)[0]:
                mask |= 1 << int(i)
            vs.append(mask)
        runs.append(rs)
        run_views.append(vs)

    # destinations: a sample (all of them when n <= k_dests)
    if dests is None:
        dests = list(range(n)) if n <= k_dests else sorted(random.Random(seed).sample(range(n), k_dests))
    didx = {d: i for i, d in enumerate(dests)}
    dist_cache = {}

    def dist_for(mask):
        if mask not in dist_cache:
            drop = frozenset((F[b // 2], b & 1) for b in range(2 * len(F)) if mask >> b & 1)
            # distance from every node TO each destination over the hops this view still has: search the reversed
            # graph from the destination
            dist_cache[mask] = shortest_path(g.csr(drop=drop).T.tocsr(), directed=True, unweighted=True, indices=dests)
        return dist_cache[mask]

    def nh(m, di, mask):
        D = dist_for(mask)[di]
        dm = D[m]
        if not np.isfinite(dm) or dm == 0:
            return None if not np.isfinite(dm) else frozenset()
        return frozenset(y for y, e in g.nbrs[m]
                         if not (e in fidx and mask >> dbit(e, m) & 1) and D[y] == dm - 1)

    # FIB timeline per destination: at each run whose view differs from the node's installed view, the routes that
    # change are installed in prefix order after SPF time + install base, one per-route time each
    fib_events = defaultdict(list)                              # dest -> [(t, node, nhset|None)]
    installed = [0] * n
    initial = {}
    for m in range(n):
        initial[m] = {d: nh(m, didx[d], 0) for d in dests}
    route_changes_est = 0
    spf_counted = {m: [] for m in range(n)}
    for m in range(n):
        cur = dict(initial[m])
        for r, mask in zip(runs[m], run_views[m]):
            spf_counted[m].append(r)
            if mask == installed[m]:
                continue
            new = {d: nh(m, didx[d], mask) for d in dests}
            changed = [d for d in dests if new[d] != cur[d]]
            frac = len(changed) / len(dests)
            total = frac * n                                    # routes this run changes, all destinations
            route_changes_est += total
            for rank, d in enumerate(changed):                  # prefix order = node order
                pos = (rank + 0.5) / max(len(changed), 1) * total
                at = r + spf_ms + cal["fib_base_ms"] + cal["fib_us_per_route"] * pos / 1000.0
                fib_events[d].append((at, m, new[d]))
            cur = new
            installed[m] = mask

    # loops: incremental detector per destination
    comp = P.components(range(n), [l[:2] for j, l in enumerate(g.links) if j not in Fset])
    loops = []
    window = {}
    for d in dests:
        ev = sorted(fib_events[d], key=lambda x: (x[0], x[1]))
        if not ev:
            continue
        window[d] = ev[-1][0]
        loops += detect(g, Fset, {m: initial[m][d] for m in range(n)}, ev, d, max_cycle)
    for l in loops:
        l["dest_reachable"] = comp[l["dest"]] == comp[l["nodes"][0]]
        end = l["end"] if l["end"] is not None else max(window.values())
        l["lifetime_ms"] = round(end - l["start"], 3)
        per = [sum(1 for x in runs[nd] if l["start"] <= x <= end) for nd in l["nodes"]]
        l["spf_runs_during_max"] = max(per) if per else 0
        fx = [x for nd in l.get("fixer", []) for x in runs[nd] if l["start"] <= x <= end]
        l["spf_runs_survived"] = max(0, len(fx) - 1) if fx else 0
        l["start_ms"] = round(l["start"], 3)

    res = {"build": BUILD, "MODELED": True, "topo": t["kind"], "n": n, "links": len(g.links), "event": event,
           "profile": profile, "seed": seed, "failed_links": len(F), "dests_sampled": len(dests), "cal": cal,
           "spf_ms_modeled": round(spf_ms, 3), "route_changes_est": round(route_changes_est),
           "spf_runs_after_event": {"max": max(len(r) for r in runs), "mean": round(sum(len(r) for r in runs) / n, 2)},
           "window_max_ms": round(max(window.values(), default=0), 3)}
    summarize(res, loops)
    if spb:
        res["spb"] = spb_agreement(g, F, Fset, fidx, kt, runs, run_views, spf_ms, cal, n, route_changes_est)
    res["_fib"] = (initial, fib_events, dests, Fset)
    return res


def summarize(res, loops):
    res["loops_n"] = len(loops)
    for cls, sel in (("live", True), ("gone", False)):
        ls = [l for l in loops if l["dest_reachable"] == sel]
        res[f"{cls}_loops_n"] = len(ls)
        res[f"{cls}_max_loop_ms"] = max((l["lifetime_ms"] for l in ls), default=0)
        res[f"{cls}_p50_loop_ms"] = float(np.median([l["lifetime_ms"] for l in ls])) if ls else 0
        res[f"{cls}_max_spf_runs"] = max((l["spf_runs_during_max"] for l in ls), default=0)
        res[f"{cls}_survived_hist"] = {str(k): sum(1 for l in ls if l["spf_runs_survived"] == k)
                                       for k in sorted({l["spf_runs_survived"] for l in ls})}
        res[f"{cls}_cycle_len_hist"] = {str(k): sum(1 for l in ls if len(l["nodes"]) == k)
                                        for k in sorted({len(l["nodes"]) for l in ls})}


# ------------------------------------------------------------------------------------------------------------ detector
def detect(g, Fset, init_nh, events, dest, max_cycle):
    """[INCREMENTAL_LOOP_DETECT_V1] one destination's FIB timeline -> loops [{dest, nodes, start, end, fixer}].
    A loop is an elementary cycle of next hops (dead links carry nothing). It opens when a change makes it, and closes
    at the first change that removes one of its edges. New cycles must pass through the node that changed, so only
    cycles through it are searched (DFS, up to max_cycle nodes)."""
    nhs = dict(init_nh)
    dead = {frozenset(g.links[e][:2]) for e in Fset}
    succ = lambda x: [y for y in (nhs.get(x) or ()) if y != dest and frozenset((x, y)) not in dead]
    open_ = {}                                          # cycle (tuple, rotated to min) -> start
    by_node = defaultdict(set)
    out = []

    def canon(cyc):
        i = cyc.index(min(cyc))
        return tuple(cyc[i:] + cyc[:i])

    def alive(c):
        return all(c[(i + 1) % len(c)] in succ(c[i]) for i in range(len(c)))

    i = 0
    while i < len(events):
        tt = events[i][0]
        changed = set()
        while i < len(events) and events[i][0] == tt:
            _, m, v = events[i]
            nhs[m] = v
            changed.add(m)
            i += 1
        for m in changed:
            for c in list(by_node[m]):
                if c in open_ and not alive(c):
                    out.append({"dest": dest, "nodes": sorted(c), "start": open_.pop(c), "end": tt,
                                "fixer": sorted(changed & set(c))})
                    for x in c:
                        by_node[x].discard(c)
        for m in changed:
            # cycles through m: DFS from m, simple paths, back to m
            stack = [(m, [m], iter(succ(m)))]
            found = 0
            while stack and found < 64:
                x, path, it = stack[-1]
                y = next(it, None)
                if y is None:
                    stack.pop()
                    continue
                if y == m:
                    c = canon(path)
                    if c not in open_:
                        open_[c] = tt
                        for z in c:
                            by_node[z].add(c)
                    found += 1
                elif y not in path and len(path) < max_cycle:
                    stack.append((y, path + [y], iter(succ(y))))
    for c, st in open_.items():
        out.append({"dest": dest, "nodes": sorted(c), "start": st, "end": None, "fixer": []})
    return out


# ------------------------------------------------------------------------------------------------------------------ E2
def spb_agreement(g, F, Fset, fidx, kt, runs, run_views, spf_ms, cal, n, route_changes_est):
    """[SPB_AGREEMENT_V1] 802.1aq agreement protocol (Seaman, aq-seaman-agreement-protocol-0910-v2) on the same
    timeline. One participant per port. A bridge's digest summarises its topology view; each time it changes the bridge
    sends it on every port with agree=False, and once its own forwarding matches that view sends agree=True ("two
    messages, one from each participant" per change). A port is agreed when both ends hold the same digest and both
    have said agree=True; until then forwarding on it for the changed trees is restricted (SPBM unicast frames with no
    permitted FDB entry are discarded, not flooded).
    Two readings of WHEN the digest changes, both reported:
      lsdb  every change of the LSDB topology (each LSP that removes an edge): the most exchanges there can be
      spf   at each SPF run, i.e. the view the bridge computes forwarding from (coalesced by the RFC 8405 backoff)
    Counted, as Day asked: how many bridges ever see a neighbour's digest differ from their own, how many times each
    of them does before its ports agree, the messages it took, and how long ports were held."""
    fib_lag = spf_ms + cal["fib_base_ms"] + cal["fib_us_per_route"] * (route_changes_est / max(n, 1)) / 1000.0

    def mask_of(col, tt):
        mask = 0
        for i in np.nonzero(col <= tt)[0]:
            mask |= 1 << int(i)
        return mask

    lsdb_tl = []
    for m in range(n):
        col = kt[:, m]
        tl = [(0.0, 0)]
        for tt in sorted({float(x) for x in col if np.isfinite(x)}):
            tl.append((tt, mask_of(col, tt)))
        lsdb_tl.append(tl)
    spf_tl = []
    for m in range(n):
        tl = [(0.0, 0)]
        for r, mask in zip(runs[m], run_views[m]):
            if mask != tl[-1][1]:
                tl.append((r + spf_ms, mask))
        spf_tl.append(tl)
    # forwarding aligned to a view: the FIB install of the first run that computed it
    aligned = []
    for m in range(n):
        al = {}
        for r, mask in zip(runs[m], run_views[m]):
            al.setdefault(mask, r + fib_lag)
        aligned.append(al)

    def view_at(tl, tt):
        v = 0
        for x, mask in tl:
            if x > tt:
                break
            v = mask
        return v

    out = {}
    for mode, dig in (("lsdb", lsdb_tl), ("spf", spf_tl)):
        detect_n, sent = defaultdict(int), defaultdict(int)
        port_ag, port_msgs, restricted = [], [], 0.0
        for j, (a, b, delay) in enumerate(g.links):
            if j in Fset:
                continue
            ch = len(dig[a]) - 1 + len(dig[b]) - 1
            if ch == 0:
                continue
            for x, y in ((a, b), (b, a)):
                for tt, mask in dig[x][1:]:
                    sent[x] += 2
                    if view_at(dig[y], tt + delay) != mask:
                        detect_n[y] += 1
            port_msgs.append(2 * ch)
            fa, fb = dig[a][-1][1], dig[b][-1][1]
            ta = max(dig[a][-1][0], aligned[a].get(fa, INF))
            tb = max(dig[b][-1][0], aligned[b].get(fb, INF))
            t_ag = max(ta, tb) + delay if fa == fb else INF
            port_ag.append(t_ag)
            first = min(x[0] for x in dig[a][1:] + dig[b][1:])
            if np.isfinite(t_ag):
                restricted += t_ag - first
        views = [len(dig[m]) - 1 for m in range(n)]
        dc = sorted(detect_n.values())
        fin = [x for x in port_ag if np.isfinite(x)]
        pct = lambda v, q: float(np.percentile(v, q)) if v else 0.0
        out[mode] = {
            "views_per_bridge": {"p50": pct(views, 50), "p99": pct(views, 99), "max": max(views)},
            "bridges_detecting": len(dc), "frac_detecting": round(len(dc) / n, 4),
            "mismatches_per_detecting": {"p50": pct(dc, 50), "p99": pct(dc, 99), "max": max(dc, default=0)},
            "msgs_per_port": {"p50": pct(port_msgs, 50), "max": max(port_msgs, default=0)},
            "msgs_per_bridge_max": max(sent.values(), default=0), "msgs_total": sum(sent.values()),
            "ports_reagreeing": len(port_ag), "ports_never_agreed": len(port_ag) - len(fin),
            "agree_ms": {"p50": pct(fin, 50), "max": max(fin, default=0)},
            "restricted_port_s": round(restricted / 1000.0, 3),
        }
    out["MODELED"] = True
    return out


# --------------------------------------------------------------------------------------------------------------- check
def check():
    """[MODEL_DETECTOR_EQUIV_V1] the incremental detector must report the same loops (destination, nodes, start,
    end) as s4_persist.analyse -- the tier 4 detector -- on the same modeled timeline, over every destination"""
    ok = True
    for kind, n, ev, prof in (("ring", 16, "partition", "tuned"), ("grid", 25, "nodefail", "tuned"),
                              ("mesh", 32, "partition", "tuned"), ("mesh", 48, "nodefail", "default"),
                              ("mesh", 64, "partition", "tuned")):
        t = P.topology(kind, n, 1)
        r = model(t, ev, prof, seed=1, k_dests=n, max_cycle=n, spb=False)
        initial, fib_events, dests, Fset = r["_fib"]
        allev = sorted((tt / 1000.0, m, d, (set(v) if v is not None else None))
                       for d in dests for tt, m, v in fib_events[d])
        init = {m: {d: set(v) for d, v in initial[m].items() if v is not None} for m in range(n)}
        dead = frozenset(frozenset(t["links"][j][:2]) for j in Fset)
        ref, _ = P.analyse(init, [(tt, m, d, v) for tt, m, d, v in allev], 0.0, {d: d for d in dests}, dead)
        a = sorted((l["dest"], tuple(l["nodes"]), round(l["start"] * 1000, 3),
                    None if l["end"] is None else round(l["end"] * 1000, 3)) for l in ref)
        b = sorted((l["dest"], tuple(l["nodes"]), round(l["start"], 3),
                    None if l["end"] is None else round(l["end"], 3)) for l in r_loops(r, t, ev, prof))
        same = a == b
        print(f"  check {kind}{n} {ev:9s} {prof:7s} analyse={len(a):4d} incremental={len(b):4d} "
              f"{'PASS' if same else 'FAIL'}")
        if not same:
            print("    only analyse:", [x for x in a if x not in b][:5])
            print("    only incremental:", [x for x in b if x not in a][:5])
        ok &= same
    print("CHECK", "PASS" if ok else "FAIL")
    return ok


def r_loops(r, t, ev, prof):
    initial, fib_events, dests, Fset = r["_fib"]
    g = Graph(t)
    out = []
    for d in dests:
        e = sorted(fib_events[d], key=lambda x: (x[0], x[1]))
        out += detect(g, Fset, {m: initial[m][d] for m in range(t["n"])}, e, d, t["n"])
    return out


# ----------------------------------------------------------------------------------------------------------- calibrate
LOGLINE = __import__("re").compile(r"^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d\.\d+).*SPF schedule called.*Caller: (\w+)")


def _abs(s):
    from datetime import datetime
    return datetime.strptime(s, "%Y/%m/%d %H:%M:%S.%f").timestamp()


def calibrate(dirs):
    """[CALIBRATE_V1] fit the free constants from tier 4 runs (result dirs of s4_persist.py r3, or r2 after reanalyse):
      fib_base_ms       SPF run -> first route change it makes (median over every run that changed a route); this
                        includes FRR's SPF time at bed sizes, so spf_base_ms is set to 0 and the N log N term carries
                        only the growth beyond the bed
      fib_us_per_route  spacing of successive route changes after one run (median slope over runs with >= 2 changes)
      proc_ms           per-hop flooding cost: with the origins' generation times (isisd 'lsp_inc_seqno' after the
                        event), the first 'lsp_update' at every other node is predicted as gen + sum(link delay) +
                        hops x proc_ms; proc_ms is the value with the least median error (silent/cut runs, 2 origins)"""
    lag0, slope, flood = [], [], []
    for w in dirs:
        r = json.load(open(f"{w}/result.json"))
        if "t_event" not in r:
            continue
        a = r["args"]
        t = P.topology(a["topo"], a["n"], a["seed"], a["sites"], a["wan_ms"], a["lan_ms"])
        own = P.owners(t)
        t_ev = r["t_event"]
        callers = {}
        for i in t["nodes"]:
            runs = [x for x in P.read_spf(f"{w}/n{i}/isisd.log") if x >= t_ev]
            ev = sorted(e[0] for e in P.read_monitor(f"{w}/n{i}/mon.txt", i, own) if e[0] >= t_ev)
            for k, rr in enumerate(runs):
                nxt = runs[k + 1] if k + 1 < len(runs) else INF
                ch = [x for x in ev if rr <= x < nxt]
                if ch:
                    lag0.append((ch[0] - rr) * 1000)
                if len(ch) >= 2:
                    slope.append((ch[-1] - ch[0]) / (len(ch) - 1) * 1e6)
            cs = []
            for line in open(f"{w}/n{i}/isisd.log"):
                m = LOGLINE.match(line)
                if m and _abs(m.group(1)) >= t_ev:
                    cs.append((_abs(m.group(1)), m.group(2)))
            callers[i] = cs
        if a["event"] in ("silent", "cut"):
            j = P.split(t)[0]
            o1, o2, _ = t["links"][j]
            gen = {o: min((x for x, c in callers[o] if c == "lsp_inc_seqno"), default=None) for o in (o1, o2)}
            if None in gen.values():
                continue
            g = Graph(t)
            lat = dijkstra(g.csr(drop={j}, weight=lambda d: d), directed=False, indices=[o1, o2])
            hop = shortest_path(g.csr(drop={j}), directed=False, unweighted=True, indices=[o1, o2])
            for m in t["nodes"]:
                if m in (o1, o2):
                    continue
                first = min((x for x, c in callers[m] if c == "lsp_update"), default=None)
                if first is not None:
                    flood.append((first, [(gen[o1], lat[0][m], hop[0][m]), (gen[o2], lat[1][m], hop[1][m])]))
    best = None
    for proc in np.arange(0.0, 20.01, 0.1):
        err = [abs(first - min(gn + (l + h * proc) / 1000.0 for gn, l, h in src)) * 1000 for first, src in flood]
        if err and (best is None or np.median(err) < best[1]):
            best = (float(proc), float(np.median(err)))
    cal = dict(CAL)
    cal.update({"spf_base_ms": 0.0,
                "fib_base_ms": round(float(np.median(lag0)), 3) if lag0 else CAL["fib_base_ms"],
                "fib_us_per_route": round(float(np.median(slope)), 3) if slope else CAL["fib_us_per_route"],
                "proc_ms": round(best[0], 2) if best else CAL["proc_ms"],
                "source": f"calibrated from {len(dirs)} tier 4 runs",
                "fit": {"lag0_n": len(lag0), "lag0_p10_p50_p90_ms": [round(float(np.percentile(lag0, q)), 3) for q in (10, 50, 90)] if lag0 else None,
                        "slope_n": len(slope), "slope_p10_p50_p90_us": [round(float(np.percentile(slope, q)), 3) for q in (10, 50, 90)] if slope else None,
                        "flood_n": len(flood), "flood_median_err_ms": round(best[1], 3) if best else None}})
    return cal


def compare(dirs, cal, seeds=5):
    """[MODEL_VS_TIER4_V1] the model on the same topology, event and profile as each tier 4 run (every destination,
    several seeds for the hold-timer phases) beside what the real FRR bed measured"""
    rows = []
    for w in dirs:
        r = json.load(open(f"{w}/result.json"))
        if "live_loops_n" not in r:
            continue
        a = r["args"]
        t = P.topology(a["topo"], a["n"], a["seed"], a["sites"], a["wan_ms"], a["lan_ms"])
        ms = []
        for sd in range(1, seeds + 1):
            m = model(t, a["event"], a["profile"], seed=sd, k_dests=a["n"], cal=cal, max_cycle=a["n"], spb=False)
            m.pop("_fib")
            ms.append(m)
        med = lambda k: float(np.median([m[k] for m in ms]))
        rows.append({"run": os.path.basename(w), "n": a["n"],
                     "t4": {k: r.get(k) for k in ("live_loops_n", "live_max_loop_ms", "gone_loops_n", "gone_max_loop_ms",
                                                   "window_max_ms", "live_survived_hist", "gone_survived_hist")},
                     "t4_spf_mean": round(sum(len(v) for v in r["spf_ms"].values()) / a["n"], 2),
                     "model_med": {k: med(k) for k in ("live_loops_n", "live_max_loop_ms", "gone_loops_n",
                                                        "gone_max_loop_ms", "window_max_ms")},
                     "model_spf_mean": float(np.median([m["spf_runs_after_event"]["mean"] for m in ms])),
                     "model_survived_max": max(max([int(k) for k in m["live_survived_hist"]] +
                                                   [int(k) for k in m["gone_survived_hist"]] + [0]) for m in ms)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check")
    c = sp.add_parser("calibrate")
    c.add_argument("dirs", nargs="+")
    c.add_argument("--out")
    v = sp.add_parser("compare")
    v.add_argument("dirs", nargs="+")
    v.add_argument("--cal")
    v.add_argument("--seeds", type=int, default=5)
    v.add_argument("--out")
    r = sp.add_parser("run")
    r.add_argument("--topo", default="mesh", choices=["ring", "grid", "mesh"])
    r.add_argument("--n", type=int, default=1000)
    r.add_argument("--event", default="nodefail", choices=["cut", "silent", "partition", "nodefail"])
    r.add_argument("--profile", default="tuned", choices=list(PROFILES))
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--dests", type=int, default=64)
    r.add_argument("--max-cycle", type=int, default=8)
    r.add_argument("--cal")
    r.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "check":
        sys.exit(0 if check() else 1)
    if a.cmd == "calibrate":
        cal = calibrate(a.dirs)
        txt = json.dumps(cal, indent=1)
        if a.out:
            open(a.out, "w").write(txt)
        print(txt)
        return
    if a.cmd == "compare":
        rows = compare(a.dirs, json.load(open(a.cal)) if a.cal else None, a.seeds)
        if a.out:
            json.dump(rows, open(a.out, "w"), indent=1)
        for r in rows:
            print(json.dumps(r))
        return
    cal = json.load(open(a.cal)) if a.cal else None
    t = P.topology(a.topo, a.n, a.seed)
    res = model(t, a.event, a.profile, a.seed, a.dests, cal, a.max_cycle)
    del res["_fib"]
    txt = json.dumps(res, indent=1)
    if a.out:
        open(a.out, "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
