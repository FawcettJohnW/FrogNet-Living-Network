#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_tree.py - [S4_TREE_V1] Chapter 12's distribution tree, three ways, on one weighted graph, so the properties of a
construction separate from the routing information it uses (John Day's question: does it follow from the structure, or
from a choice?). Built to run on the live layered bed's topology (hub + egresses + children, IS-IS metric-style wide =
one cost per hop) AND on small controlled graphs that hold the conditions he named.

  converging   [CH12_CONVERGING_V1] each member sends a Create_Flow Response toward the source; it follows the
               intra-domain routing (the FIB). Where responses pass through the same node they merge: the union of the
               members' reverse paths is the tree. SINGLE deterministic next hop -> a tree every time; PER-FLOW ECMP ->
               members behind a tie can arrive through different border routers and a member downstream gets duplicates.
  forward      [CH12_FORWARD_V1] the source reads the shared link-state (every node holds it: IS-IS floods it, or the
               shared RAM) and computes the tree forward (shortest-path tree, one parent per node). A tree by
               construction: no duplicates. The comparison for "built from shared state instead of the responses' paths."
  exact        [CH12_EXACT_V1] the minimum-cost (Steiner) tree spanning source+members on the same graph: exact
               (Dreyfus-Wagner) for few terminals, else the metric-closure MST 2-approximation. The baseline for "x
               times optimal".

Cost is directed, so an asymmetric graph shows the reversed-direction case: a tree built on the cheap toward-source
costs but USED outward, where those same edges are dear, costs far more than the optimal outward tree -- a choice (which
direction), not a property. metrics(): reach, edges, cost, duplicates (members getting >1 copy), ratio to exact.

usage: s4_tree.py            # the demo: the bed topology, then the ECMP case, then the reversed-cost case
"""
import heapq
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


class Graph:
    """Directed costs (undirected edges are added both ways). cost[(u, v)] is the cost of sending u->v."""
    def __init__(self):
        self.nodes, self.cost, self.adj = set(), {}, {}

    def edge(self, u, v, w=1, w_back=None):
        self.nodes.update((u, v))
        self.cost[(u, v)] = w
        self.adj.setdefault(u, set()).add(v)
        if w_back is not None:                       # a directed/asymmetric edge: give the reverse its own cost
            self.cost[(v, u)] = w_back
            self.adj.setdefault(v, set()).add(u)
        elif w_back is None and w is not None:       # symmetric by default
            self.cost.setdefault((v, u), w)
            self.adj.setdefault(v, set()).add(u)
        self.adj.setdefault(v, set())
        return self

    def dijkstra(self, src):
        """Least cost from src to every node, over cost[(u, v)] in the src->node direction."""
        dist = {src: 0}
        pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            for v in self.adj.get(u, ()):
                nd = d + self.cost[(u, v)]
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def dist_to(self, src):
        """Least cost from every node TO src (Dijkstra on the transpose): what the FIB minimises toward src."""
        radj = {}
        for (u, v) in self.cost:
            radj.setdefault(v, set()).add(u)
        dist = {src: 0}
        pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            for w in radj.get(u, ()):                # w -> u is a real edge; we walk it backwards from src
                nd = d + self.cost[(w, u)]
                if nd < dist.get(w, float("inf")):
                    dist[w] = nd
                    heapq.heappush(pq, (nd, w))
        return dist


def from_plan(p):
    """The bed's physical graph as a Graph, one unit cost per hop (IS-IS metric-style wide, no per-link metric)."""
    import s4_bed as B
    g = Graph()
    for u, nbrs in B.adjacency(p).items():
        for v in nbrs:
            g.edge(u, v, 1)
    return g


# ---- the three constructions -------------------------------------------------------------------------------------

def converging(g, src, members, ecmp=False):
    """The tree the converging Create_Flow Responses form: the union of each member's route toward src (the FIB).
    Returns (edges_used, copies) where edges_used is the set of directed delivery edges src->...->member (the reverse
    of the responses), and copies[m] is how many distinct src->m paths that union holds (1 == a tree at m)."""
    d2s = g.dist_to(src)
    # the FIB at u toward src: the next hop(s) v minimising cost(u, v) + dist_to_src(v)
    def nexthops(u):
        best, who = float("inf"), []
        for v in g.adj.get(u, ()):
            if v in d2s and (c := g.cost[(u, v)] + d2s[v]) <= best:
                if c < best:
                    best, who = c, [v]
                elif v not in who:
                    who.append(v)
        return who if ecmp else (who[:1] and [min(who)])      # single: one deterministic next hop (lowest name)

    used = set()                                               # directed response edges u->v (toward src)
    frontier = [m for m in members if m in d2s]
    seen = set()
    while frontier:
        u = frontier.pop()
        if u == src or u in seen:
            continue
        seen.add(u)
        for v in nexthops(u):
            used.add((u, v))
            frontier.append(v)
    delivery = {(v, u) for (u, v) in used}                     # used outward: src -> ... -> member
    # copies[m] = number of distinct src->m paths in the delivery DAG (counts the ECMP diamonds John Day described)
    out = {}
    for a, b in delivery:
        out.setdefault(a, []).append(b)
    from functools import lru_cache
    @lru_cache(maxsize=None)
    def paths_to(x):
        if x == src:
            return 1
        preds = [a for (a, b) in delivery if b == x]
        return sum(paths_to(a) for a in preds) if preds else 0
    copies = {m: paths_to(m) for m in members}
    return delivery, copies


def forward(g, src, members):
    """The source builds the tree from the shared link-state, forward: a shortest-path tree, one parent per node.
    A tree by construction. Returns the set of directed tree edges src->...->member."""
    dist = g.dijkstra(src)
    parent = {}
    for x in g.nodes:
        if x == src or x not in dist:
            continue
        cand = [u for u in g.nodes if x in g.adj.get(u, ()) and u in dist and dist[u] + g.cost[(u, x)] == dist[x]]
        if cand:
            parent[x] = min(cand)                              # one deterministic parent (lowest name): a tree
    edges = set()
    for m in members:
        x = m
        while x in parent:
            edges.add((parent[x], x))
            x = parent[x]
    return edges


def exact(g, src, members):
    """The minimum-cost tree over terminals = {src} | members, rooted at src (a Steiner arborescence: directed, so an
    asymmetric graph's answer differs by direction). Exact Dreyfus-Wagner on the metric closure for a small terminal
    set, else the metric-closure MST 2-approximation (an upper bound). Returns (cost, method)."""
    terms = sorted(set(members) | {src})
    apsp = {u: g.dijkstra(u) for u in g.nodes}                 # all-pairs over the directed cost (symmetric unless set)
    INF = float("inf")
    def d(a, b):
        return apsp[a].get(b, INF)
    n = len(terms)
    if n <= 11 and len(g.nodes) <= 40:
        # dp[mask][v]: least-cost arborescence that spans the terminals in mask with v in the tree. A strict subset of
        # mask has a smaller integer, so increasing mask order has every subset ready. Each mask: the split merge, then
        # one metric-closure relaxation (d is already all-pairs shortest, so a single pass suffices).
        dp = [dict() for _ in range(1 << n)]
        for i, t in enumerate(terms):
            dp[1 << i] = {v: d(v, t) for v in g.nodes}         # outward: v -> terminal (root-at-src arborescence)
        for mask in range(1, 1 << n):
            if mask & (mask - 1) == 0:
                continue
            base = {}
            for v in g.nodes:
                best = INF
                sub = (mask - 1) & mask
                while sub:
                    other = mask ^ sub
                    if sub < other:                            # each unordered split once
                        best = min(best, dp[sub].get(v, INF) + dp[other].get(v, INF))
                    sub = (sub - 1) & mask
                base[v] = best
            dp[mask] = {v: min(base[u] + d(v, u) for u in g.nodes) for v in g.nodes}
        return dp[(1 << n) - 1].get(src, INF), "exact(dreyfus-wagner)"
    # MST 2-approximation on the metric closure of the terminals (an upper bound on the optimum)
    inz, cost, chosen = {terms[0]}, 0, set(terms[1:])
    while chosen:
        a, b, w = min(((a, b, d(a, b)) for a in inz for b in chosen), key=lambda t: t[2])
        inz.add(b); chosen.discard(b); cost += w
    return cost, "mst-2approx"


# ---- metrics -----------------------------------------------------------------------------------------------------

def cost_of(g, edges, outward=True):
    """Sum of the directed cost of the tree's edges, in the direction they are USED (src->member when outward).
    outward=False sums the toward-source direction -- the cost the converging construction THOUGHT it was paying."""
    return sum(g.cost[e if outward else (e[1], e[0])] for e in edges)


def metrics(g, src, members, edges, copies=None, exact_cost=None):
    reached = {m for m in members if copies.get(m, 0) > 0} if copies is not None \
        else {m for m in members if any(b == m for _, b in edges) or m == src}
    c = cost_of(g, edges, outward=True)
    out = {"reach": f"{len(reached)}/{len(members)}", "edges": len(edges), "cost": c}
    if copies is not None:
        out["duplicate_members"] = sorted(m for m in members if copies.get(m, 0) > 1)
        out["copies_max"] = max((copies.get(m, 0) for m in members), default=0)
    if exact_cost:
        out["x_optimal"] = round(c / exact_cost, 2)
    return out


def compare(g, src, members, ecmp=False, label=""):
    xc, meth = exact(g, src, members)
    cv_edges, copies = converging(g, src, members, ecmp=ecmp)
    fw_edges = forward(g, src, members)
    rows = {
        "converging": metrics(g, src, members, cv_edges, copies=copies, exact_cost=xc),
        "forward": metrics(g, src, members, fw_edges, exact_cost=xc),
        "exact": {"cost": xc, "method": meth},
    }
    return {"label": label, "src": src, "members": sorted(members), "ecmp": ecmp, "trees": rows}


# ---- the controlled cases that isolate John Day's choices from the construction's properties ------------------

def ecmp_case():
    """[CH12_ECMP_CASE_V1] two equal-cost border routers to a shared member set: src-b1-m*, src-b2-m*. Single next hop
    is a clean tree; per-flow ECMP delivers every member twice -- a choice (ECMP), not the construction."""
    d = Graph()
    for b in ("b1", "b2"):
        d.edge("src", b, 1)
        for m in ("m1", "m2", "m3"):
            d.edge(b, m, 1)
    return {"single_next_hop": compare(d, "src", ["m1", "m2", "m3"], ecmp=False)["trees"],
            "per_flow_ecmp": compare(d, "src", ["m1", "m2", "m3"], ecmp=True)["trees"]}


def reversed_case():
    """[CH12_REVERSED_CASE_V1] a trunk cheap toward the source and dear outward: the converging tree is built on the
    toward-source costs it 'sees' but used outward, where it is dear. The reversed-direction blow-up -- a direction
    choice the chapter didn't pin down, not a property."""
    a = Graph()
    a.edge("src", "t", w=9, w_back=1)        # the trunk: 1 toward src, 9 outward
    a.edge("t", "m1", w=1, w_back=1)
    a.edge("t", "m2", w=1, w_back=1)
    a.edge("src", "m1", w=5, w_back=5)       # a direct, symmetric alternative
    a.edge("src", "m2", w=5, w_back=5)
    r = compare(a, "src", ["m1", "m2"], ecmp=False)
    cv_edges, _ = converging(a, "src", ["m1", "m2"])
    r["trees"]["converging"]["cost_as_used_outward"] = cost_of(a, cv_edges, outward=True)
    r["trees"]["converging"]["cost_it_thought"] = cost_of(a, cv_edges, outward=False)
    return r["trees"]


def on_plan(p, src="hub"):
    """The three constructions on a bed plan's converged topology, single next hop and per-flow ECMP."""
    g = from_plan(p)
    members = [n for n, d in p["nodes"].items() if d["role"] == "child"]
    return {"single_next_hop": compare(g, src, members, ecmp=False)["trees"],
            "per_flow_ecmp": compare(g, src, members, ecmp=True)["trees"]}


if __name__ == "__main__":
    import s4_bed as B
    def show(title, trees):
        print(f"\n== {title} ==")
        for name, m in trees.items():
            if isinstance(m, dict) and "converging" in m:          # a nested {single_next_hop: {trees}} block
                for sub, t in m.items():
                    print(f"  {sub}: {json.dumps(t)}")
            else:
                print(f"  {name:11s} {json.dumps(m)}")

    p = B.plan(20, 5, [{"name": "local", "ip": "127.0.0.1"}], ring=True)
    blk = on_plan(p)
    show("bed S20/E5 ring, single next hop", blk["single_next_hop"])
    show("bed S20/E5 ring, per-flow ECMP", blk["per_flow_ecmp"])
    show("two border routers (ECMP choice)", ecmp_case())
    show("asymmetric trunk (reversed-direction choice)", reversed_case())
