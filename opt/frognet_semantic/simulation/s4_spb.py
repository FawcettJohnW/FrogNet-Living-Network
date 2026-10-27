#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_spb.py - [S4_SPB_V1] the IS-IS modifications IEEE 802.1aq made (RFC 6329 Shortest Path Bridging), as a CONTROL-PLANE
computation measured over the live link-state, since no stack on the bed runs SPB (FRR is L3-only; SPB is enterprise
fabric silicon). What we cannot measure here is the MAC-in-MAC data plane; what we CAN measure -- and what "the
performance of the 802.1 modifications" means -- is the algorithm: one SPF per node yielding every tree.

SPB's SPF is ordinary shortest paths plus a deterministic, SYMMETRIC tie-break among equal-cost paths: the low-PATH-ID
rule. A path's ID is the sorted sequence of the bridge identifiers it traverses, each XOR'd with the ECT-ALGORITHM mask;
the equal-cost path with the lexicographically smallest ID wins. Running it from both ends traverses the same bridges,
so the chosen path is the same both ways (symmetric), and the same trees serve unicast and multicast (congruent). The 16
ECT masks (RFC 6329: 00-80-C2-01..10) XOR different bits of the bridge IDs, so each yields a different-but-deterministic
tree -- up to 16 equal-cost trees for diversity, assigned per VLAN/I-SID. Per-service (S,G) multicast is the sub-tree of
the chosen ECT tree spanning that I-SID's members.

The point is the cost of the modification, not the datapath: we time the plain single-tree SPF (what standard IS-IS
pays) against the full SPB tree set (what the modifications add), on the same machine over the same topology, and report
the ratio -- hardware-independent -- plus reconvergence after a cut, and whether symmetry/congruence actually hold.

usage: s4_spb.py            # the demo: the S20/E5 ring bed topology, measured
"""
import json
import os
import sys
import time
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_tree as T                                            # noqa: E402  (Graph, dijkstra, exact)

# The 16 ECT-ALGORITHMs of RFC 6329 (00-80-C2-01..10) each XOR a distinct mask into the bridge identifiers before the
# low-PATH-ID tie-break, so each picks a different equal-cost tree. The real BridgeIDs are 8 bytes and the mask reorders
# them across their width; to get the same reordering on our ids we spread the algorithm's low byte (01..10) across the
# id width (x 0x01010101). ECT-ALGORITHM 01 (mask 0) is the default (no reordering). [RFC6329_ECT_V1]
ECT_MASKS = [0] + [zlib.crc32(("ect-%d" % i).encode()) & 0xFFFFFFFF for i in range(1, 16)]   # 0 = default (lowest bridge id); 15 spread masks that reorder ties across the id width


def bridgeid(name):
    """A stable 32-bit bridge identifier per node (the IS-IS SysID an SPB bridge would carry), well spread across its
    width (CRC32 of the name) so the ECT masks actually reorder ties. Deterministic, so the tie-break is reproducible."""
    return zlib.crc32(name.encode()) & 0xFFFFFFFF


def spb_tree(g, src, mask, bid):
    """One SPB shortest-path tree from src under one ECT mask, by the low-PATH-ID tie-break done INLINE in the SPF (the
    way an SPB bridge does it, O(Dijkstra) -- not by enumerating paths). Each node's label is (cost, path-id), path-id
    being the sorted sequence of the bridge ids XOR mask along the path from src; a node keeps the equal-cost path whose
    path-id is lexicographically smallest. Under one metric per hop (IS-IS metric-style wide, as on the bed) equal cost
    means equal hop count, so the comparison is well-formed. Returns (directed tree edges src->...->node, parent map).
    Both ends traverse the same bridges, so the chosen path is the same both ways -- symmetric and congruent."""
    import heapq
    start = (0, (bid(src) ^ mask,))
    label = {src: start}
    parent = {}
    pq = [(0, start[1], src)]
    while pq:
        c, pid, u = heapq.heappop(pq)
        if (c, pid) > label.get(u, (float("inf"),)):
            continue
        for v in g.adj.get(u, ()):
            nc = c + g.cost[(u, v)]
            npid = tuple(sorted(pid + (bid(v) ^ mask,)))
            if (nc, npid) < label.get(v, (float("inf"), ())):
                label[v] = (nc, npid)
                parent[v] = u
                heapq.heappush(pq, (nc, npid, v))
    edges = {(parent[v], v) for v in parent}
    return edges, parent


def _path(dst, parent, src):
    p = [dst]
    while p[-1] != src and p[-1] in parent:
        p.append(parent[p[-1]])
    return p[::-1]


def ect_set(g, src, bid, masks=ECT_MASKS):
    """Up to 16 equal-cost trees from src, one per ECT algorithm."""
    return {m: spb_tree(g, src, m, bid)[0] for m in masks}


def isid_multicast(g, src, members, mask, bid):
    """A per-service (S,G) tree: the sub-tree of src's ECT tree that spans the I-SID's members (tandem replication at
    the forks). Congruent with unicast by construction -- same tree, pruned."""
    _edges, parent = spb_tree(g, src, mask, bid)
    keep = set()
    for m in members:
        p = _path(m, parent, src)
        for a, b in zip(p, p[1:]):
            keep.add((a, b))
    return keep


# ---- measurements ------------------------------------------------------------------------------------------------

def _time(fn, iters):
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    return (time.perf_counter() - t0) / iters * 1e3           # ms per call


def measure(g, bid=bridgeid, masks=ECT_MASKS, iters=50):
    nodes = sorted(g.nodes)
    # baseline: plain IS-IS SPF -- one shortest-path tree per node (Dijkstra), no tie-break set
    def plain():
        for s in nodes:
            g.dijkstra(s)
    # the modification: the full SPB tree set -- every ECT tree from every node
    def spb_one():                                             # one SPB tree per node (default ECT) -- the tie-break cost
        for s in nodes:
            spb_tree(g, s, masks[0], bid)
    def spb_all():                                             # all 16 ECT trees per node -- the full diversity set
        for s in nodes:
            ect_set(g, s, bid, masks)
    t_plain = _time(plain, iters)
    t_one = _time(spb_one, max(1, iters // 2))
    t_spb = _time(spb_all, max(1, iters // 5))
    # reconvergence: recompute the SPB set after a cut (drop one hub link), timed
    import copy
    g2 = copy.deepcopy(g)
    cut = next(((u, v) for (u, v) in list(g2.cost) if u == "hub" or v == "hub"), None)
    if cut:
        for e in [cut, (cut[1], cut[0])]:
            g2.cost.pop(e, None)
            g2.adj.get(e[0], set()).discard(e[1])
    t_recon = _time(lambda: [ect_set(g2, s, bid, masks) for s in sorted(g2.nodes)], max(1, iters // 5))
    # symmetry: under the default ECT, is the src->dst path the reverse of dst->src?
    m0 = masks[0]
    par = {s: spb_tree(g, s, m0, bid)[1] for s in nodes}
    sym = tot = 0
    for a in nodes:
        for b in nodes:
            if a < b and b in par[a] and a in par[b]:
                tot += 1
                sym += (_path(b, par[a], a) == _path(a, par[b], b)[::-1])
    # ECT diversity: distinct trees across the 16 masks, from a representative source (the hub)
    src0 = "hub" if "hub" in g.nodes else nodes[0]
    distinct = len({frozenset(spb_tree(g, src0, m, bid)[0]) for m in masks})
    # congruence: a per-I-SID multicast tree's edges are a subset of the unicast ECT tree -- true by construction
    members = [n for n in nodes if n != src0][:8]
    uni = spb_tree(g, src0, m0, bid)[0]
    mc = isid_multicast(g, src0, members, m0, bid)
    congruent = mc <= uni
    return {
        "nodes": len(nodes),
        "compute_ms": {
            "plain_isis_spf": round(t_plain, 3),
            "spb_one_tree": round(t_one, 3),
            "spb_full_ect_set": round(t_spb, 3),
            "spb_one_over_plain_x": round(t_one / t_plain, 2) if t_plain else None,
            "spb_full_over_plain_x": round(t_spb / t_plain, 2) if t_plain else None,
            "note": "plain=1 shortest-path tree/node; spb_one=1 SPB tree/node (tie-break cost); spb_full=all 16 ECT "
                    "trees/node; same machine, same LSDB. The full set only buys diversity where ties exist.",
        },
        "reconverge_ms_after_cut": round(t_recon, 3),
        "ect_trees": len(masks),
        "ect_distinct_trees_from_hub": distinct,
        "path_symmetric": f"{sym}/{tot}",
        "unicast_multicast_congruent": congruent,
    }


def grid(n):
    """An n x n mesh: every interior node has equal-cost alternatives, so the ECT tie-break actually fires and the 16
    algorithms diverge -- the topology where SPB's multi-tree diversity is real (unlike the hub bed)."""
    g = T.Graph()
    def nm(r, c):
        return f"g{r}_{c}"
    for r in range(n):
        for c in range(n):
            if c + 1 < n:
                g.edge(nm(r, c), nm(r, c + 1), 1)
            if r + 1 < n:
                g.edge(nm(r, c), nm(r + 1, c), 1)
    return g


if __name__ == "__main__":
    import s4_bed as B
    p = B.plan(20, 5, [{"name": "local", "ip": "127.0.0.1"}], ring=True)
    print("== bed S20/E5 ring (hub-and-spoke: no equal-cost ties) ==")
    print(json.dumps(measure(T.from_plan(p)), indent=1))
    print("\n== 5x5 mesh (equal-cost ties everywhere: where ECT diversity is real) ==")
    print(json.dumps(measure(grid(5)), indent=1))
