// e2_common.hpp -- [DAY_E2_COMMON_V1] the participant's computation for Experiment E2, shared by e2_tree (one machine)
// and e2_dist (distributed): from what a participant read -- every member's own out-link costs ("topo") and the group
// ("grp") -- the reverse-path tree (Day's responses, Ch12 2.8.1 paraphrase) and the shortest-path tree. Ties to the
// lowest predecessor, as e2_ref.py (written separately).
#pragma once
#include "daf_client.hpp"
#include <map>
#include <set>
#include <string>
#include <vector>
using daf::Json; using daf::q;
static inline std::string apn(int v) { char b[16]; snprintf(b, sizeof b, "n%02d", v); return b; }   // zero-padded: text order = number order
static inline int id_of(const std::string& a) { return atoi(a.c_str() + 1); }
using Edges = std::set<std::pair<int, int>>;   // (parent, child), root -> member direction
static std::string edges_s(const Edges& e) { std::string s = "["; bool f = true; for (auto& [p, c] : e) { s += (f ? "" : ",") + ("[" + std::to_string(p) + "," + std::to_string(c) + "]"); f = false; } return s + "]"; }

// ---- the participant's computation: Dijkstra, ties to the lowest predecessor (as e2_ref.py, written separately)
static std::map<int, int> dijkstra_pred(const std::map<int, std::map<int, int>>& out, int src, bool reverse) {
    std::map<int, std::vector<std::pair<int, int>>> adj; std::set<int> nodes{src};
    for (auto& [a, m] : out) { nodes.insert(a); for (auto& [b, c] : m) { nodes.insert(b); if (reverse) adj[b].push_back({a, c}); else adj[a].push_back({b, c}); } }
    const long INF = 1L << 50; std::map<int, long> dist; std::map<int, int> pred; std::set<int> done;
    for (int v : nodes) dist[v] = INF; dist[src] = 0;
    while (done.size() < nodes.size()) {
        int u = -1; for (int v : nodes) if (!done.count(v) && (u < 0 || dist[v] < dist[u] || (dist[v] == dist[u] && v < u))) u = v;
        if (u < 0 || dist[u] >= INF) break; done.insert(u);
        for (auto& [w, c] : adj[u]) { const long nd = dist[u] + c; auto p = pred.find(w);
            if (nd < dist[w] || (nd == dist[w] && p != pred.end() && u < p->second)) { dist[w] = nd; pred[w] = u; } } }
    return pred;
}
struct Trees { Edges spt, rpt; uint64_t rev = 0; bool ok = true; };
static Trees compute(const Json& topo, const Json& grp) {
    Trees t; std::map<int, std::map<int, int>> out; int root = -1; std::vector<int> members;
    for (auto& m : topo["members"].a) for (auto& [nb, c] : m["facts"]["adj"].o) out[id_of(m["apn"].s)][id_of(nb)] = int(c.n);
    for (auto& m : grp["members"].a) { if (m["facts"]["role"].s == "requester") root = id_of(m["apn"].s); else members.push_back(id_of(m["apn"].s)); }
    if (root < 0) { t.ok = false; return t; }
    auto pf = dijkstra_pred(out, root, false), pr = dijkstra_pred(out, root, true);
    for (int m : members) {
        for (int v = m, guard = 0; v != root && guard < 1000; ++guard) { auto p = pf.find(v); if (p == pf.end()) { t.ok = false; break; } t.spt.insert({p->second, v}); v = p->second; }
        for (int v = m, guard = 0; v != root && guard < 1000; ++guard) { auto p = pr.find(v); if (p == pr.end()) { t.ok = false; break; } t.rpt.insert({p->second, v}); v = p->second; }
    }
    t.rev = std::max(uint64_t(topo["rev"].n), uint64_t(grp["rev"].n));
    return t;
}

