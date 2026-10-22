#!/usr/bin/env python3
"""e2_ref.py -- [DAY_E2_REF_V1] reference costs for Experiment E2 (group distribution tree), independent of the C++
participants. Run: python3 -I e2_ref.py selfcheck [N_PER_KIND] | python3 -I e2_ref.py instances OUT.jsonl [N_PER_KIND]

A graph is a set of links; each link has a cost in each direction (a->b, b->a), equal on a symmetric graph. The group
is a requester (root) and members. Data flows root -> members, so every tree is costed in that direction.

Trees:
  optimum  the minimum Steiner arborescence rooted at the requester spanning the members. Two methods, which must
           agree on every instance or the instance is a reference failure:
             dw    Dreyfus-Wagner dynamic programming over subsets of members (written here)
             enum  every subset X of the other nodes: Edmonds' minimum arborescence (networkx) of the subgraph induced
                   on root + members + X, root forced by dropping its in-edges; the minimum over X
  spt      the shortest-path tree from the requester: each member's shortest root->member path, ties to the
           lowest predecessor; what a requester would build from the topology it reads
  rpt      the reverse-path tree Day's responses form (Ch12 2.8.1, paraphrase): each node's next hop toward the
           requester on its shortest node->root path, ties to the lowest next hop; the members' paths merge where they
           meet; data then flows over each used link in the root->member direction

Per instance: costs, ratio to optimum, and per member the stretch (tree path root->member / shortest root->member).
"""
import itertools, json, random, sys
import networkx as nx

INF = float("inf")

# ---- graphs
def links_chain(n, rnd):  return [(i, i + 1) for i in range(n - 1)]
def links_star(n, rnd):   return [(0, i) for i in range(1, n)]
def links_ring(n, rnd):   return [(i, (i + 1) % n) for i in range(n)]
def links_branch(n, rnd):
    L = {(rnd.randrange(i), i) for i in range(1, n)}                      # a random tree
    extra = rnd.randint(1, max(1, n // 2))                                # plus extra edges
    while extra:
        a, b = sorted(rnd.sample(range(n), 2))
        if (a, b) not in L: L.add((a, b)); extra -= 1
    return sorted(L)
KINDS = {"chain": links_chain, "star": links_star, "ring": links_ring, "branch": links_branch}

def make(kind, n, seed, asym):
    rnd = random.Random(f"{kind}-{n}-{seed}-{asym}")
    links = KINDS[kind](n, rnd)
    cost = {}
    for a, b in links:
        c = rnd.randint(1, 9); cost[(a, b)] = c; cost[(b, a)] = rnd.randint(1, 9) if asym else c
    nodes = list(range(n))
    root = rnd.choice(nodes)
    k = rnd.randint(1, min(6, n - 1))
    members = sorted(rnd.sample([v for v in nodes if v != root], k))
    return {"kind": kind, "n": n, "seed": seed, "asym": asym, "nodes": nodes, "cost": cost, "root": root, "members": members}

# ---- shortest paths (Dijkstra, ties to the lowest predecessor), in either direction
def dijkstra(nodes, cost, src, reverse=False, high=False):   # high: ties to the HIGHEST predecessor (sensitivity only)
    adj = {v: [] for v in nodes}
    for (a, b), c in cost.items():
        if reverse: adj[b].append((a, c))      # distance FROM v TO src: walk edges backwards
        else: adj[a].append((b, c))
    dist = {v: INF for v in nodes}; pred = {v: None for v in nodes}; dist[src] = 0; done = set()
    while len(done) < len(nodes):
        u = min((v for v in nodes if v not in done), key=lambda v: (dist[v], v))
        if dist[u] == INF: break
        done.add(u)
        for w, c in adj[u]:
            nd = dist[u] + c
            if nd < dist[w] or (nd == dist[w] and pred[w] is not None and (u > pred[w] if high else u < pred[w])): dist[w] = nd; pred[w] = u
    return dist, pred

def tree_cost(g, edges):  return sum(g["cost"][e] for e in edges)   # edges are (parent, child), root->member direction

def spt(g, high=False):
    _, pred = dijkstra(g["nodes"], g["cost"], g["root"], high=high)
    E = set()
    for m in g["members"]:
        v = m
        while v != g["root"]: E.add((pred[v], v)); v = pred[v]
    return E

def rpt(g, high=False):
    # pred here is the next hop toward the root (distance v->root, computed on reversed edges)
    _, nh = dijkstra(g["nodes"], g["cost"], g["root"], reverse=True, high=high)
    E = set()
    for m in g["members"]:
        v = m
        while v != g["root"]: E.add((nh[v], v)); v = nh[v]   # the response goes v -> nh[v]; data goes nh[v] -> v
    return E

def check_tree(g, E):
    """spans root + members, a tree (one parent each, no cycle), only links that exist; True or a reason"""
    par = {}
    for p, c in E:
        if (p, c) not in g["cost"]: return f"no link {p}->{c}"
        if c in par: return f"{c} has two parents"
        par[c] = p
    if g["root"] in par: return "root has a parent"
    for m in g["members"]:
        v, seen = m, set()
        while v != g["root"]:
            if v in seen or v not in par: return f"{m} not reached from root"
            seen.add(v); v = par[v]
    return True

def path_cost(g, E, m):
    par = {c: p for p, c in E}; s = 0; v = m
    while v != g["root"]: s += g["cost"][(par[v], v)]; v = par[v]
    return s

# ---- optimum, method 1: Dreyfus-Wagner (directed: arborescence rooted at the requester)
def dreyfus_wagner(g):
    nodes, T, r = g["nodes"], g["members"], g["root"]
    d = {}
    for u in nodes:
        du, _ = dijkstra(nodes, g["cost"], u); d.update({(u, v): du[v] for v in nodes})
    k = len(T)
    # S as a bitmask over T; dp[S][v] = cheapest arborescence rooted at v spanning T[S]
    dp = [[INF] * len(nodes) for _ in range(1 << k)]
    for i, t in enumerate(T):
        for v in nodes: dp[1 << i][v] = d[(v, t)]
    for S in range(1, 1 << k):
        if S & (S - 1) == 0: continue
        best = [INF] * len(nodes)
        sub = (S - 1) & S
        while sub:
            if sub < (S ^ sub):                                  # each split once
                for v in nodes:
                    c = dp[sub][v] + dp[S ^ sub][v]
                    if c < best[v]: best[v] = c
            sub = (sub - 1) & S
        for v in nodes: dp[S][v] = min(best[u] + d[(v, u)] for u in nodes)
    return dp[(1 << k) - 1][r]

# ---- optimum, method 2: exhaustive over Steiner node sets, Edmonds' algorithm on each
def enumerate_opt(g):
    r, T = g["root"], set(g["members"])
    others = [v for v in g["nodes"] if v != r and v not in T]
    best = INF
    for k in range(len(others) + 1):
        for X in itertools.combinations(others, k):
            keep = {r} | T | set(X)
            # [E2_EDMONDS_BY_MAX_BRANCHING_V1] networkx 3.7's minimum_spanning_arborescence refuses graphs that have one
            # (chain 10 seed 12 asym: root 7, 7->6->5->4->3 and 7->8->9 exist; it says none). maximum_branching is
            # correct there, so: weight' = BIG - cost with BIG > the sum of all costs, so a maximum branching takes as many
            # edges as it can -- a spanning arborescence when one exists -- and among those, the cheapest. Checked: spans.
            BIG = sum(g["cost"].values()) + 1
            H = nx.DiGraph()
            H.add_nodes_from(keep)
            for (a, b), c in g["cost"].items():
                if a in keep and b in keep and b != r: H.add_edge(a, b, w2=BIG - c, cost=c)
            A = nx.maximum_branching(H, attr="w2", preserve_attrs=True)
            if A.number_of_edges() != len(keep) - 1 or not nx.is_arborescence(A): continue   # nothing spans this set
            c = sum(dd["cost"] for _, _, dd in A.edges(data=True))
            best = min(best, c)
    return best

def evaluate(g):
    o1, o2 = dreyfus_wagner(g), enumerate_opt(g)
    out = {"kind": g["kind"], "n": g["n"], "seed": g["seed"], "asym": g["asym"], "root": g["root"], "members": g["members"],
           "links": [[a, b, c] for (a, b), c in sorted(g["cost"].items())], "opt_dw": o1, "opt_enum": o2, "ref_agree": o1 == o2}
    dist, _ = dijkstra(g["nodes"], g["cost"], g["root"])
    for name, fn in (("spt", spt), ("rpt", rpt), ("spt_hi", lambda g: spt(g, True)), ("rpt_hi", lambda g: rpt(g, True))):
        E = fn(g); ok = check_tree(g, E)
        st = [path_cost(g, E, m) / dist[m] for m in g["members"]] if ok is True else []
        out[name] = {"edges": sorted([list(e) for e in E]), "valid": ok is True, "why": None if ok is True else ok,
                     "cost": tree_cost(g, E), "ratio": tree_cost(g, E) / o1 if o1 else None,
                     "stretch_max": max(st) if st else None, "stretch_mean": sum(st) / len(st) if st else None}
    return out

def instances(per_kind):
    for kind in KINDS:
        for asym in (False, True):
            for seed in range(per_kind):
                n = random.Random(f"n-{kind}-{seed}-{asym}").randint(4, 11)
                yield make(kind, n, seed, asym)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selfcheck"
    if mode == "selfcheck":
        per = int(sys.argv[2]) if len(sys.argv) > 2 else 25; bad = 0; count = 0
        # hand-checked cases first: costs worked out by hand, written down before running
        # (1) star, root 0 at the hub, members 1,2,3, costs 1,2,3: every tree is the star, cost 6
        g = {"kind": "hand", "n": 4, "seed": 0, "asym": False, "nodes": [0, 1, 2, 3], "root": 0, "members": [1, 2, 3],
             "cost": {(0, 1): 1, (1, 0): 1, (0, 2): 2, (2, 0): 2, (0, 3): 3, (3, 0): 3}}
        # (2) the SPT/Steiner gap: root 0; members 1,2; links 0-1 4, 0-2 4, 0-3 2, 3-1 2, 3-2 2. Each member's shortest
        #     path ties (direct 4 = via 3 2+2); ties go to the lowest predecessor, 0, so SPT = 0-1 + 0-2 = 8.
        #     Optimum: 0-3, 3-1, 3-2 = 6.
        h = {"kind": "hand", "n": 4, "seed": 1, "asym": False, "nodes": [0, 1, 2, 3], "root": 0, "members": [1, 2],
             "cost": {(0, 1): 4, (1, 0): 4, (0, 2): 4, (2, 0): 4, (0, 3): 2, (3, 0): 2, (3, 1): 2, (1, 3): 2, (3, 2): 2, (2, 3): 2}}
        for gg, want_opt, want_spt in ((g, 6, 6), (h, 6, 8)):
            r = evaluate(gg); ok = r["opt_dw"] == want_opt and r["opt_enum"] == want_opt and r["spt"]["cost"] == want_spt
            print(f"hand case {gg['seed']}: dw {r['opt_dw']} enum {r['opt_enum']} spt {r['spt']['cost']} (want opt {want_opt}, spt {want_spt}) {'ok' if ok else 'FAIL'}")
            bad += not ok
        for g in instances(per):
            r = evaluate(g); count += 1
            if not r["ref_agree"] or not r["spt"]["valid"] or not r["rpt"]["valid"] or r["spt"]["cost"] < r["opt_dw"] or r["rpt"]["cost"] < r["opt_dw"]:
                bad += 1; print("REFERENCE FAILURE", json.dumps(r))
        print(f"selfcheck: {count} generated instances + 2 hand cases; {bad} failures")
        sys.exit(1 if bad else 0)
    if mode == "scenarios":
        # scenarios OUT PER_KIND CHANGES: each instance, then CHANGES link-cost changes (one direction of one link each),
        # with the reference trees after every step, for the C++ participants (e2_tree) to be checked against
        out = sys.argv[2]; per = int(sys.argv[3]); nch = int(sys.argv[4])
        with open(out, "w") as f:
            for g in instances(per):
                rnd = random.Random(f"ch-{g['kind']}-{g['n']}-{g['seed']}-{g['asym']}"); steps = []
                for i in range(nch + 1):
                    if i:
                        (a, b) = rnd.choice(sorted(g["cost"])); c = rnd.randint(1, 9)
                        g["cost"][(a, b)] = c
                        if not g["asym"]: g["cost"][(b, a)] = c
                        change = [a, b, c]
                    else: change = None
                    r = evaluate(g)
                    if not r["ref_agree"]: raise SystemExit("reference failure in a scenario: " + json.dumps(r))
                    steps.append({"change": change, "links": r["links"], "opt": r["opt_dw"], "spt": r["spt"]["edges"], "spt_cost": r["spt"]["cost"],
                                  "rpt": r["rpt"]["edges"], "rpt_cost": r["rpt"]["cost"]})
                f.write(json.dumps({"kind": g["kind"], "n": g["n"], "seed": g["seed"], "asym": g["asym"], "root": g["root"], "members": g["members"], "steps": steps}) + "\n")
        print("wrote", out)
    if mode == "instances":
        out = sys.argv[2]; per = int(sys.argv[3]) if len(sys.argv) > 3 else 25
        with open(out, "w") as f:
            for g in instances(per): f.write(json.dumps(evaluate(g)) + "\n")
        print("wrote", out)
