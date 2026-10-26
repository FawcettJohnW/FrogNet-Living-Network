#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_loopfree.py - [S4_LOOPFREE_V1] how loop prevention SCALES, three mechanisms side by side, as a control-plane model
over graphs (John Day's question: "how does it scale?"). Every number here is MODELED, from the mechanism each protocol
specifies; the running-code grounding is separate (see GROUNDING below). The point is not an absolute millisecond count
-- it is how each cost GROWS with network diameter D and with the number of affected routes R, which is what "scale"
means for loop prevention.

THE THREE MECHANISMS (and exactly what each does about loops)

  isis   Plain link-state (FRR isisd), NO loop prevention. On a change the endpoints re-originate LSPs; the flood
         ripples outward and every node re-runs SPF and installs immediately. Loops are not eliminated: while the flood
         is in flight, two nodes can briefly disagree on the next hop and a packet hairpins between them -- a TRANSIENT
         micro-loop, squelched only by TTL (Day's point: TTL squelches looping packets, it does not eliminate loops).
         No traffic is held.

  spb    IEEE 802.1aq Shortest Path Bridging, loop prevention by AGREEMENT (modeled from RFC 6329). Same LSP flood, then
         each bridge exchanges an agreement digest of its computed state with each neighbour and HOLDS forwarding on an
         affected tree until adjacent digests match. Agreement ripples outward from the change, so full agreement takes
         ~D link-times. Loops are eliminated (nothing forwards on a tree until both ends agree) at the cost of a
         BLACKHOLE window: affected traffic is dropped while the hold is in force.

  frognet  FrogNet proof-of-route, loop prevention by VERIFICATION (the real mechanism; modeled here for scale). Every
         node computes from one consistent link-state held in shared RAM (the held read hands every reader the same
         graph). Before a route is installed it is PROVEN by a reflect probe (/reflect?o=&c=, [REFLECT_PROBE_V1]): the
         probe runs the candidate path and a box that sees the probe return to its origin answers REFLECT_LOOP, so the
         candidate is refused (LOOP_9009) and never installed. Loops are eliminated by refusing a looping route at
         install, at the cost of an UNAVAILABILITY window (the route is not usable until the probe clears it) and a
         per-route probe cost.

WHAT SCALES WITH WHAT -- the finding this model exists to produce:
  - Time to forwarding grows ~linearly with D for all three (flood / agreement / visibility all ripple at link speed).
  - Loop elimination is NOT free for the two that achieve it: spb pays a blackhole window ~D; frognet pays an
    unavailability window ~2D (a prove round-trip) and a per-route probe cost.
  - The distinguishing axis is the CONTROL COST: spb's agreement is per-LINK (grows with edges E, i.e. with density and
    D); frognet's proof is per-ROUTE (grows with the number of affected routes R x path length). So on a dense core
    with few changed routes spb is cheaper; as the number of affected routes climbs, frognet's per-route proving is the
    one that grows. Neither "wins" everywhere -- which is the honest answer to give Day.

MODELING ASSUMPTIONS (stated so they can be checked, not hidden):
  - One link-time (delta) per hop; flood, agreement ripple and probe round-trip are all counted in delta.
  - isis time-to-forward = D; spb = 2D (flood then agree); frognet = D (change visible across the region) + 2*depth
    (prove round-trip). These constants are the model; the GROWTH is the result.
  - Messages: an LSP flood is ~2E transmissions (two re-originated LSPs, each crossing every edge once). spb adds
    AGREE_ROUNDS digest exchanges per edge. frognet adds N shared-RAM reads of the change plus, per affected route, a
    probe that traverses the path out and back.
  - Byte sizes (LSP, digest, probe, shared-RAM fact) are first-order constants, documented below and tunable.
  - Transient micro-loops (isis) are counted structurally: an ordered pair (x -> y) for a destination t where x's NEW
    next hop is y and y's OLD next hop for t is x -- the classic two-node hairpin that exists until the later of the two
    updates lands. This is a lower bound on transient loops (it counts the 2-cycles; longer transient cycles exist too).

GROUNDING (measured, elsewhere -- this file does not claim to run the protocols):
  - frognet's refusal is real code: discovery.sim.loopcheck shows the :9009 reflect probe returns LOOP and
    discovery.walk drops the candidate; discovery.sim.loop_scenarios counts the back-vouch loops (36 across its 6
    topologies) that the gate refuses.
  - isis transient micro-loops are to be MEASURED on the live five-machine bed (real FRR isisd); this model's isis curve
    is the expectation that measurement confirms or corrects.
  - spb has no open implementation, so it is modeled only, and labelled so everywhere.

usage:
  s4_loopfree.py            # demo: one event on a few topologies, all three arms
  s4_loopfree.py sweep      # the scaling sweep (sizes x topologies), JSON to stdout
"""
import json
import os
import sys
from collections import deque

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_tree as T                                               # noqa: E402  (Graph)

# ---- first-order byte sizes (documented, tunable) ----------------------------------------------------------------
LSP_HDR = 48            # IS-IS LSP header + fixed fields
LSP_PER_ADJ = 8         # bytes per advertised adjacency (neighbour TLV)
DIGEST_BYTES = 32       # an SPB agreement digest (a hash + a little framing)
PROBE_BYTES = 24        # a reflect probe: origin IP + counter + framing (o/c, no GUID)
FACT_BYTES = 268        # a FrogNet shared-RAM fact, the measured per-node median from the efficiency run
AGREE_ROUNDS = 2        # SPB digest exchanges per edge to settle one agreement handshake (clean case)


# ---- topologies: each returns (Graph, label); diameter is computed, not asserted --------------------------------
def chain(n):
    g = T.Graph()
    for i in range(n - 1):
        g.edge(i, i + 1, 1)
    return g, f"chain(N={n})"


def ring(n):
    g = T.Graph()
    for i in range(n):
        g.edge(i, (i + 1) % n, 1)
    return g, f"ring(N={n})"


def hub(n):
    g = T.Graph()
    for i in range(1, n):
        g.edge(0, i, 1)
    return g, f"hub(N={n})"


def mesh(k):
    g = T.Graph()
    def nm(r, c):
        return r * k + c
    for r in range(k):
        for c in range(k):
            if c + 1 < k:
                g.edge(nm(r, c), nm(r, c + 1), 1)
            if r + 1 < k:
                g.edge(nm(r, c), nm(r + 1, c), 1)
    return g, f"mesh({k}x{k}, N={k*k})"


def tree(fanout, depth):
    g = T.Graph()
    nxt = [0]
    def grow(node, d):
        if d == depth:
            return
        for _ in range(fanout):
            nxt[0] += 1
            child = nxt[0]
            g.edge(node, child, 1)
            grow(child, d + 1)
    grow(0, 0)
    return g, f"tree(fanout={fanout}, depth={depth}, N={nxt[0]+1})"


# ---- graph primitives: BFS distance, next hop, diameter, edges ---------------------------------------------------
def _bfs(g, src):
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in g.adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def all_dist(g):
    return {t: _bfs(g, t) for t in g.nodes}


def diameter(g, dist=None):
    dist = dist or all_dist(g)
    return max((d for dmap in dist.values() for d in dmap.values()), default=0)


def edges(g):
    return {frozenset(e) for e in g.cost if len(set(e)) == 2}


def nexthop(g, x, t, dist):
    """The shortest-path next hop from x toward t (min-name tie-break), or None if unreachable."""
    dt = dist[t]
    if x not in dt or x == t:
        return None
    best = dt[x]
    cand = [v for v in g.adj.get(x, ()) if dt.get(v, 1 << 30) == best - 1]
    return min(cand) if cand else None


def cut(g, a, b):
    """A copy of g with the undirected edge {a,b} removed."""
    h = T.Graph()
    for (u, v), w in g.cost.items():
        if {u, v} == {a, b}:
            continue
        h.cost[(u, v)] = w
        h.adj.setdefault(u, set()).add(v)
        h.nodes.update((u, v))
    for u in g.nodes:
        h.nodes.add(u)
        h.adj.setdefault(u, set())
    return h


# ---- the event: cut one representative edge, find what changed ---------------------------------------------------
def central_edge(g, dist):
    """Pick a deterministic, high-impact edge to cut: the one carrying the most shortest paths (max edge
    betweenness, approximated by counting next-hop usage). Reproducible for a given graph."""
    use = {}
    for t in g.nodes:
        for x in g.nodes:
            nh = nexthop(g, x, t, dist)
            if nh is not None:
                use[frozenset((x, nh))] = use.get(frozenset((x, nh)), 0) + 1
    e = max(edges(g), key=lambda e: use.get(e, 0))
    a, b = tuple(e)
    return a, b


def event(g):
    """Cut the central edge; return the before/after facts needed by every arm:
    affected routes (next hop changed), dead routes (now unreachable), micro-loop 2-cycles, and path depths."""
    d0 = all_dist(g)
    D = diameter(g, d0)
    a, b = central_edge(g, d0)
    h = cut(g, a, b)
    d1 = all_dist(h)

    affected, dead, microloops, depths = 0, 0, 0, []
    for t in g.nodes:
        for x in g.nodes:
            if x == t:
                continue
            nh0 = nexthop(g, x, t, d0)
            nh1 = nexthop(h, x, t, d1)
            if x not in d1[t]:                       # t became unreachable from x
                dead += 1
                continue
            if nh1 != nh0:
                affected += 1
                depths.append(d1[t][x])              # post-event path depth (probe reaches this far)
                # transient two-node hairpin: x's NEW next hop y had OLD next hop back to x for t
                y = nh1
                if y is not None and nexthop(g, y, t, d0) == x:
                    microloops += 1
    E = len(edges(g))
    avg_deg = (2 * E / len(g.nodes)) if g.nodes else 0
    return {
        "N": len(g.nodes), "E": E, "D": D, "cut": [a, b],
        "affected": affected, "dead": dead, "microloop_2cycles": microloops,
        "max_depth": max(depths, default=0), "sum_depth": sum(depths), "avg_deg": round(avg_deg, 2),
        "partitioned": dead > 0,
    }


# ---- the three arm models ----------------------------------------------------------------------------------------
def arms(ev):
    N, E, D = ev["N"], ev["E"], ev["D"]
    R, md, sd = ev["affected"], ev["max_depth"], ev["sum_depth"]
    lsp_bytes = LSP_HDR + LSP_PER_ADJ * ev["avg_deg"]
    flood_msgs = 2 * E                                            # two re-originated LSPs, each crosses every edge once
    flood_bytes = flood_msgs * lsp_bytes

    isis = {
        "loops": "transient (TTL-squelched)",
        "transient_microloops": ev["microloop_2cycles"],
        "loop_lifetime_delta": D,                                 # until the later update lands, bounded by the flood span
        "time_to_forward_delta": D,
        "blackhole_delta": 0,
        "control_msgs": flood_msgs,
        "control_bytes": round(flood_bytes),
        "scales_with": "diameter (flood) ; loop exposure grows with D",
    }
    spb = {
        "loops": "eliminated (held until agreement)",
        "transient_microloops": 0,
        "time_to_forward_delta": 2 * D,                           # flood (D) then agreement ripple (D)
        "blackhole_delta": D,                                     # forwarding held during the agreement ripple
        "control_msgs": flood_msgs + AGREE_ROUNDS * 2 * E,        # + digest exchange per edge per round, both directions
        "control_bytes": round(flood_bytes + AGREE_ROUNDS * 2 * E * DIGEST_BYTES),
        "scales_with": "diameter (blackhole ~D) ; control per-EDGE (~E)",
        "modeled": True,
    }
    probe_msgs = 2 * sd                                           # each affected route proven: path out and back
    frognet = {
        "loops": "eliminated (looping route refused at install, LOOP_9009)",
        "transient_microloops": 0,
        "time_to_forward_delta": D + 2 * md,                      # change visible across region (~D) + prove round-trip
        "unavailable_delta": 2 * md,                              # route not installed until its probe clears
        "control_msgs": N + probe_msgs,                           # N held-read reads of the change + per-route probes
        "control_bytes": round(N * FACT_BYTES + probe_msgs * PROBE_BYTES),
        "scales_with": "diameter (unavailable ~2D) ; control per-ROUTE (~R x depth)",
        "refused_candidates_grounded_by": "discovery.sim.loop_scenarios / loopcheck (real code)",
    }
    return {"isis": isis, "spb": spb, "frognet": frognet}


def measure(g, label=""):
    ev = event(g)
    return {"label": label, "event": ev, "arms": arms(ev)}


# ---- the sweep ---------------------------------------------------------------------------------------------------
def topologies_for(nodes):
    """A diameter spread at ~this many nodes: hub (D=2), mesh (D~2(sqrt N -1)), ring (D~N/2), chain (D=N-1)."""
    import math
    k = max(2, round(math.sqrt(nodes)))
    out = [hub(nodes), mesh(k), ring(nodes), chain(nodes)]
    return out


def sweep(sizes=(21, 50, 100, 200)):
    rows = []
    for n in sizes:
        for g, label in topologies_for(n):
            rows.append(measure(g, label))
    return rows


def _table(rows):
    """A compact, side-by-side scaling table: the growth of the costs that matter, keyed by topology and diameter."""
    hdr = (f"{'topology':<18}{'N':>5}{'D':>5}{'aff':>6}{'uloop':>7}"
           f"{'  | isis tfwd/bh/msg':>22}{'  | spb tfwd/bh/msg':>22}{'  | frognet tfwd/unav/msg':>28}")
    out = [hdr, "-" * len(hdr)]
    for r in rows:
        e, a = r["event"], r["arms"]
        out.append(
            f"{r['label']:<18}{e['N']:>5}{e['D']:>5}{e['affected']:>6}{a['isis']['transient_microloops']:>7}"
            f"   {a['isis']['time_to_forward_delta']:>4}/{a['isis']['blackhole_delta']:>3}/{a['isis']['control_msgs']:>6}"
            f"   {a['spb']['time_to_forward_delta']:>4}/{a['spb']['blackhole_delta']:>3}/{a['spb']['control_msgs']:>6}"
            f"      {a['frognet']['time_to_forward_delta']:>4}/{a['frognet']['unavailable_delta']:>4}/{a['frognet']['control_msgs']:>7}")
    out.append("\n(times in link-delays delta; bh=blackhole/hold, unav=unavailability; msg=control messages. "
               "All MODELED; spb has no implementation; isis loops to be measured on the bed.)")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        rows = sweep()
        print(_table(rows))
        print("\n=== JSON ===")
        print(json.dumps(rows, indent=1))
    else:
        for g, label in [hub(21), mesh(5), ring(21), chain(21)]:
            print(f"\n== {label} ==")
            print(json.dumps(measure(g, label), indent=1))
