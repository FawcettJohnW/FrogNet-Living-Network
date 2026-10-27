#!/usr/bin/env python3
"""e2_compare.py OUTDIR -- [DAY_E2_COMPARE_V1] Day's construction against FrogNet's against the exact optimum, on the
same graphs: same members, source, directed link costs and topology snapshot for every construction.

Constructions (data flows source -> members; every cost is paid in the data direction):
  day/dest      Day's converging Create Flow Responses (Ch12 2.8.1, paraphrase), routing assumption R1: every node has ONE
                next hop toward the requester (destination-based), equal costs broken the same way at every node.
  day/member    Same, routing assumption R2: each member's response picks its own way among equal-cost next hops
                (per-flow/ECMP-like). The union of the paths need not be a tree.
  spt           A tree the requester computes from the shared topology: its own shortest path to each member (policy).
  frog/h=...    FrogNet shared RAM as it actually moves data: the source writes once into the RAM host; each member's
                held read brings it one copy from the host. A link carries one copy per member whose read crosses it
                (plus the write if it is on the source->host path). Host at the source, at the best node (the "center",
                E3's question), at the worst node.
  opt           The exact minimum Steiner arborescence rooted at the source (Dreyfus-Wagner; cross-checked by exhaustive
                Edmonds in e2_ref.py on every instance).

Measures, per construction:
  cost          sum over links of link cost x copies of the one message that cross it
  reach         members that receive at least one copy; dup = extra copies delivered to members
  delay         worst source->member cost of the first copy; stretch = that / the member's shortest-path cost
  build         construction cost in hop-messages (model): day: a request to each member along the requester's
                shortest path + the response back along the response path; frog: each member's subscribe (to the host and
                its answer back) + the source's write; spt/opt: not built by messages (computed from shared state)
Near-optimal: cost <= 1.10 x opt. Every ratio is reported, not only the verdict.
"""
import collections, itertools, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2_ref                                                  # the references: Dijkstra, Dreyfus-Wagner, instances
import networkx as nx
INF = float("inf")
NEAR = 1.10

def dist_all(g):
    D = {}
    for u in g["nodes"]:
        du, _ = e2_ref.dijkstra(g["nodes"], g["cost"], u); D.update({(u, v): du[v] for v in g["nodes"]})
    return D

def hops_path(pred, src, dst):
    p = [dst]
    while p[-1] != src:
        q = pred.get(p[-1]);
        if q is None: return None
        p.append(q)
    return list(reversed(p))

# ---- Day's construction: response paths member -> requester, data on the reverse links
def day_paths(g, D, mode):
    r = g["root"]; out = {}
    for m in g["members"]:
        if D[(m, r)] == INF: out[m] = None; continue
        path = [m]; v = m
        while v != r:
            cands = sorted(u for (a, u), c in g["cost"].items() if a == v and c + D[(u, r)] == D[(v, r)])
            if not cands: path = None; break
            v = cands[0] if mode == "dest" else cands[(m * 7919 + v * 104729) % len(cands)]
            path.append(v)
        out[m] = path
    return out

def union_metrics(g, D, data_edges, delivered_paths):
    """data_edges: set of (u, v) the message is forwarded on (a node forwards every copy it receives on each out-edge).
    Copies on (u, v) = number of distinct source->u paths in the union. Returns cost, reach, dup, delay, stretch."""
    r = g["root"]; out = collections.defaultdict(list)
    for u, v in data_edges: out[u].append(v)
    # the union is acyclic here (costs > 0, every edge moves away from the requester along shortest paths); count paths
    order = sorted(g["nodes"], key=lambda v: D[(r, v)] if D[(r, v)] < INF else INF)
    paths = collections.Counter({r: 1}); first = {r: 0.0}
    indeg = collections.Counter(v for _, v in data_edges)
    q = [r]; seen = set()
    # Kahn over the union reachable from r
    reach_union = set([r]); stack = [r]
    while stack:
        u = stack.pop()
        for v in out[u]:
            if v not in reach_union: reach_union.add(v); stack.append(v)
    indeg = collections.Counter(v for u, v in data_edges if u in reach_union)
    ready = [r]; topo = []
    while ready:
        u = ready.pop(); topo.append(u)
        for v in out[u]:
            indeg[v] -= 1
            if indeg[v] == 0: ready.append(v)
    if len(topo) != len(reach_union): return {"cyclic": True}
    cost = 0.0
    for u in topo:
        for v in out[u]:
            cost += g["cost"][(u, v)] * paths[u]; paths[v] += paths[u]
            t = first[u] + g["cost"][(u, v)]
            if v not in first or t < first[v]: first[v] = t
    reach = sum(1 for m in g["members"] if paths[m] > 0); dup = sum(max(0, paths[m] - 1) for m in g["members"])
    delays = [first[m] for m in g["members"] if m in first]
    stretch = [first[m] / D[(r, m)] for m in g["members"] if m in first and D[(r, m)] > 0]
    return {"cost": cost, "reach": reach, "dup": dup, "delay": max(delays) if delays else INF, "stretch": max(stretch) if stretch else INF}

def day(g, D, mode):
    P = day_paths(g, D, mode); E = set(); missing_link = 0
    for m, p in P.items():
        if p is None: continue
        for a, b in zip(p, p[1:]):                               # the response goes a -> b; the data comes back b -> a
            if (b, a) in g["cost"]: E.add((b, a))
            else: missing_link += 1
    res = union_metrics(g, D, E, P)
    parents = collections.Counter(v for _, v in E); res["tree"] = all(c <= 1 for c in parents.values()) and not res.get("cyclic")
    res["one_way_breaks"] = missing_link
    r = g["root"]; build = 0
    for m, p in P.items():
        _, pred = e2_ref.dijkstra(g["nodes"], g["cost"], r); fp = hops_path(pred, r, m)
        build += (len(fp) - 1 if fp else 0) + (len(p) - 1 if p else 0)
    res["build"] = build; return res

def spt(g, D):
    E = e2_ref.spt(g); res = union_metrics(g, D, E, None); res["tree"] = True; res["build"] = 0; return res

def opt_tree(g):
    """the minimum Steiner arborescence's edges (exhaustive Edmonds, as e2_ref's method 2, keeping the edges)"""
    r, T = g["root"], set(g["members"]); others = [v for v in g["nodes"] if v != r and v not in T]
    best, bestE = INF, None; BIG = sum(g["cost"].values()) + 1
    for k in range(len(others) + 1):
        for X in itertools.combinations(others, k):
            keep = {r} | T | set(X); H = nx.DiGraph(); H.add_nodes_from(keep)
            for (a, b), c in g["cost"].items():
                if a in keep and b in keep and b != r: H.add_edge(a, b, w2=BIG - c, cost=c)
            A = nx.maximum_branching(H, attr="w2", preserve_attrs=True)
            if A.number_of_edges() != len(keep) - 1 or not nx.is_arborescence(A): continue
            c = sum(d["cost"] for _, _, d in A.edges(data=True))
            if c < best: best, bestE = c, set(A.edges())
    return best, bestE

def opt(g, D):
    c, E = opt_tree(g)
    # prune Steiner leaves that are not members (they cost nothing in the optimum, but keep the tree minimal)
    res = union_metrics(g, D, E, None); res["tree"] = True; res["build"] = 0; return res

def frog(g, D, h):
    """the source writes once to host h; every member's held read brings one copy from h"""
    r = g["root"]
    if D[(r, h)] == INF: return {"cost": INF, "reach": 0, "dup": 0, "delay": INF, "stretch": INF, "tree": False, "build": INF}
    copies = collections.Counter()
    _, pr = e2_ref.dijkstra(g["nodes"], g["cost"], r); p = hops_path(pr, r, h)
    for a, b in zip(p, p[1:]): copies[(a, b)] += 1
    _, ph = e2_ref.dijkstra(g["nodes"], g["cost"], h); build = 2 * (len(p) - 1); reach = 0; delays = []; stretch = []
    for m in g["members"]:
        if D[(h, m)] == INF: continue
        pm = hops_path(ph, h, m); reach += 1
        for a, b in zip(pm, pm[1:]): copies[(a, b)] += 1
        build += 2 * (len(pm) - 1)
        delays.append(D[(r, h)] + D[(h, m)]); stretch.append((D[(r, h)] + D[(h, m)]) / D[(r, m)] if D[(r, m)] > 0 else 1.0)
    cost = sum(g["cost"][e] * n for e, n in copies.items())
    return {"cost": cost, "reach": reach, "dup": 0, "delay": max(delays) if delays else INF, "stretch": max(stretch) if stretch else INF,
            "tree": False, "build": build, "max_copies_on_a_link": max(copies.values()) if copies else 0}

def evaluate(g):
    D = dist_all(g); o = opt(g, D)
    res = {"opt": o, "day/dest": day(g, D, "dest"), "day/member": day(g, D, "member"), "spt": spt(g, D)}
    fr = {h: frog(g, D, h) for h in g["nodes"]}
    finite = {h: v for h, v in fr.items() if v["reach"] == len(g["members"])}
    hb = min(finite, key=lambda h: (finite[h]["cost"], h)); hw = max(finite, key=lambda h: (finite[h]["cost"], -h))
    res["frog/h=source"] = fr[g["root"]]; res["frog/h=best"] = dict(fr[hb], host=hb); res["frog/h=worst"] = dict(fr[hw], host=hw)
    for k, v in res.items():   # a cost ratio only for a construction that reached every member: a partial delivery costs less
        v["ratio"] = v["cost"] / o["cost"] if o["cost"] and v["cost"] < INF and v["reach"] == len(g["members"]) else INF
    e2_ref_cost = e2_ref.dreyfus_wagner(g)
    if abs(e2_ref_cost - o["cost"]) > 1e-9: raise SystemExit(f"reference disagreement: DW {e2_ref_cost} vs Edmonds tree {o['cost']}")
    return res

CONS = ["day/dest", "day/member", "spt", "frog/h=source", "frog/h=best", "frog/h=worst", "opt"]

# ---- counterexample families
def g_of(nodes, links, root, members, sym=True):
    cost = {}
    for a, b, c, *back in links:
        cost[(a, b)] = c
        if back: cost[(b, a)] = back[0]
        elif sym: cost[(b, a)] = c
    return {"kind": "family", "n": len(nodes), "seed": 0, "asym": not sym, "nodes": list(nodes), "cost": cost, "root": root, "members": list(members)}

def fam_trunk(k, Dd=10, T=9, e=2):
    """cheap shared trunk vs individually shortest: r-m_i direct Dd; r-h T; h-m_i e. Dd < T+e, so every member's own
    shortest path is the direct link; T + k*e < k*Dd, so the shared trunk is cheaper for the group."""
    nodes = list(range(k + 2)); L = [(0, 1, T)] + [(0, 2 + i, Dd) for i in range(k)] + [(1, 2 + i, e) for i in range(k)]
    return g_of(nodes, L, 0, range(2, k + 2))

def fam_asym(k):
    """asymmetric costs: each member has two ways to the requester. Way A is cheap toward the requester (1+1) but dear
    away from it (9+9); way B is the reverse (5+5 toward, 2+2 away). Responses take A; data then pays A's dear side."""
    nodes = [0]; L = []; nid = 1; mem = []
    for i in range(k):
        a, b, m = nid, nid + 1, nid + 2; nid += 3; nodes += [a, b, m]; mem.append(m)
        L += [(0, a, 9, 1), (a, m, 9, 1), (0, b, 2, 5), (b, m, 2, 5)]          # (from, to, cost from->to, cost to->from)
    return g_of(nodes, L, 0, mem, sym=False)

def fam_ties(k):
    """equal-cost alternatives: r reaches z through x or y at equal cost; k members hang off z. Under R1 z has one next
    hop; under R2 members choose independently and z can receive the message from both x and y."""
    nodes = list(range(4 + k)); L = [(0, 1, 3), (0, 2, 3), (1, 3, 3), (2, 3, 3)] + [(3, 4 + i, 1) for i in range(k)]
    return g_of(nodes, L, 0, range(4, 4 + k))

def fam_oneway(seed):
    """a branching graph where some links run one way only: a response can reach the requester over a link that data
    cannot come back over"""
    g = e2_ref.make("branch", 9, seed, True); rnd = random.Random(f"oneway-{seed}")
    for (a, b) in sorted(g["cost"]):
        if (a, b) in g["cost"] and (b, a) in g["cost"] and rnd.random() < 0.3: del g["cost"][(a, b)]
    g["kind"] = "oneway"; return g

def strongly_ok(g):
    H = nx.DiGraph(); H.add_nodes_from(g["nodes"]); H.add_edges_from(g["cost"]); return nx.is_strongly_connected(H)

def row(name, res, g):
    return {c: {k: (None if (isinstance(v, float) and v == INF) else v) for k, v in res[c].items()} for c in CONS} | {"case": name, "members": len(g["members"])}

def main():
    out = sys.argv[1]; os.makedirs(out, exist_ok=True); rows = []
    # 1. the standard instances (the same 1,200 graphs as E2's references), every construction
    for g in e2_ref.instances(150):
        rows.append(row(f"{g['kind']}/{'asym' if g['asym'] else 'sym'}/n{g['n']}/s{g['seed']}", evaluate(g), g) | {"set": "standard", "kind": g["kind"], "asym": g["asym"]})
    # 2. source position: 30 branching graphs, every node as the source
    for seed in range(30):
        base = e2_ref.make("branch", 8, seed, seed % 2 == 1)
        for r in base["nodes"]:
            g = dict(base); g["root"] = r; g["members"] = [m for m in base["members"] if m != r] or [v for v in base["nodes"] if v != r][:2]
            rows.append(row(f"source/s{seed}/r{r}", evaluate(g), g) | {"set": "source", "kind": "branch", "asym": base["asym"]})
    # 3. counterexample families
    for k in range(1, 9): rows.append(row(f"trunk/k{k}", evaluate(fam_trunk(k)), fam_trunk(k)) | {"set": "trunk", "k": k})
    for k in range(1, 5): rows.append(row(f"asym/k{k}", evaluate(fam_asym(k)), fam_asym(k)) | {"set": "asym", "k": k})
    for k in range(1, 7): rows.append(row(f"ties/k{k}", evaluate(fam_ties(k)), fam_ties(k)) | {"set": "ties", "k": k})
    n1 = 0
    for seed in range(400):
        g = fam_oneway(seed)
        if not strongly_ok(g): continue
        rows.append(row(f"oneway/s{seed}", evaluate(g), g) | {"set": "oneway"}); n1 += 1
        if n1 >= 100: break
    with open(os.path.join(out, "compare.jsonl"), "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    print("wrote", len(rows), "rows to", os.path.join(out, "compare.jsonl"))

if __name__ == "__main__":
    main()
