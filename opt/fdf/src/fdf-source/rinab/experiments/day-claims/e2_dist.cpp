// e2_dist.cpp -- [DAY_E2_DIST_V1] Experiment E2 across machines. Everything between the machines goes through the one
// shared memory of the experiment's RAM host; nothing else connects them.
//
//   e2-dist agent HOST:PORT [NAME]       on every machine that runs participants (NAME: default, its first 10.x/IPv4)
//   e2-dist driver HOST:PORT SCENARIOS.jsonl OUTDIR MIN_AGENTS     on one machine (any)
//
// The control DAF "dayctl#1": agents join "agents"; the driver writes the plan as its own record in "plan" (which
// scenario DAF, which agent runs which graph node, every link's cost now); each agent holds a read on "plan", runs the
// nodes it was given and writes its acknowledgement in "acks". In the scenario DAF every node is a member on its own
// session: it writes only its own out-link costs (in "topo"), holds a read on "topo", computes the trees from what it
// read (e2_common.hpp, the same code as the one-machine e2_tree) and writes what it computed as its own record in
// "trees". The driver holds a read on "trees" and checks every node against the reference.
// Timing is on ONE clock, the driver's: from writing a change into the plan to reading the last node's matching tree.
#include "e2_common.hpp"
#include "fdf_version.hpp"
#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <ifaddrs.h>
#include <memory>
#include <mutex>
#include <netinet/in.h>
#include <thread>
#include <unistd.h>
static double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static const std::string C = "dayctl#1";
static std::string links_s(const std::map<int, std::map<int, int>>& L) {
    std::string s = "["; bool f = true; for (auto& [a, m] : L) for (auto& [b, c] : m) { s += (f ? "" : ",") + ("[" + std::to_string(a) + "," + std::to_string(b) + "," + std::to_string(c) + "]"); f = false; } return s + "]"; }
static std::string adj_facts(const std::map<int, int>& out) {
    std::string f = "{\"adj\":{"; bool first = true; for (auto& [b, c] : out) { f += (first ? "" : ",") + q(apn(b)) + ":" + std::to_string(c); first = false; } return f + "}}"; }
static Json must(Json r, const std::string& what) {
    if (!r["ok"].b) { fprintf(stderr, "e2-dist: %s refused: %s %s\n", what.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); fflush(stderr); _exit(2); }
    return r; }
static void control(daf::Client& c, const std::string& me) {   // the control DAF and its names; the first claimant of each wins
    c.call("create_daf", "\"dan\":\"dayctl\",\"daii\":\"1\",\"owner\":\"day-claims\"");
    must(c.call("enroll", "\"daf\":" + q(C) + ",\"apn\":" + q(me)), "enroll " + me + " in " + C);
    for (const char* n : {"agents", "plan", "acks"}) c.call("define_name", "\"daf\":" + q(C) + ",\"name\":" + q(n) + ",\"rule\":\"all\"");
}
static std::string my_ip() {
    std::string best; ifaddrs* ia = nullptr; if (getifaddrs(&ia) != 0) return "agent";
    for (ifaddrs* i = ia; i; i = i->ifa_next) { if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET) continue;
        char b[64]; inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(i->ifa_addr)->sin_addr, b, sizeof b); std::string s = b;
        if (s.rfind("127.", 0) == 0) continue; if (best.empty() || (s.rfind("10.", 0) == 0 && best.rfind("10.", 0) != 0)) best = s; }
    freeifaddrs(ia); return best.empty() ? "agent" : best;
}
// a held read bounded by the step's deadline: the driver must see a missing report as a failed step, not wait forever
static void held(daf::Client& c, const std::string& D, const char* name, uint64_t after, double deadline) {
    const double left = deadline - wall(); if (left <= 0) return;
    try { c.wait(D, "name", name, after, false, "", left); } catch (const std::exception&) {}   // expiry: the loop checks the deadline
}
static uint64_t bytes_of(daf::Client& c) { auto s = c.stats(); return s.bytes_in + s.bytes_out; }

// ================================================================ agent
struct Node {
    int id; std::unique_ptr<daf::Client> w, r; std::map<int, int> out; std::thread th; std::atomic<bool> stop{false};
};
static void node_loop(Node* n, std::string D) {
    // [E2D_HOLD_WHAT_THE_ANSWER_READS_V1] a node's trees are computed from "topo" AND "grp", so its held read covers both:
    // one subscription over the two names. Holding "topo" alone left a node that computed before the requester joined
    // "grp" with "no tree" for good (seen on 3 machines' worth of agents; one machine had hidden it: all joined first).
    try {
        Json sub = n->r->call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"topo\",\"grp\"]");
        if (!sub["ok"].b) throw std::runtime_error("subscribe refused: " + sub["error"].s + " " + sub["why"].s);
        const std::string id = sub["sub"].s; uint64_t rev = uint64_t(sub["rev"].n);
        while (!n->stop) {
            Json topo = n->r->call("resolve", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\",\"live\":true");
            Json grp = n->r->call("resolve", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"rule\":\"all\",\"live\":true");
            if (!topo["ok"].b || !grp["ok"].b) throw std::runtime_error("read refused: " + topo["error"].s + grp["error"].s);
            Trees t = compute(topo, grp);
            if (!t.ok) {   // [DIAG-E2D-NODE] what this node read when it could not make a tree, and the rev it will hold after
                std::string tm, gm; for (auto& m : topo["members"].a) tm += m["apn"].s + " "; for (auto& m : grp["members"].a) gm += m["apn"].s + "=" + m["facts"]["role"].s + " ";
                fprintf(stderr, "[DIAG-E2D-NODE] node %d D %s no tree: topo rev %llu [%s] grp rev %llu [%s] held rev before %llu\n", n->id, D.c_str(),
                        (unsigned long long)topo["rev"].n, tm.c_str(), (unsigned long long)grp["rev"].n, gm.c_str(), (unsigned long long)rev); fflush(stderr); }
            n->w->call("join", "\"daf\":" + q(D) + ",\"name\":\"trees\",\"facts\":{\"rev\":" + std::to_string(t.rev) + ",\"ok\":" + (t.ok ? "true" : "false") +
                       ",\"rpt\":" + edges_s(t.rpt) + ",\"spt\":" + edges_s(t.spt) + "}");
            // [E2D_VERSION_BEFORE_READS_V1] hold after the version taken BEFORE the reads (the subscription's, or the last
            // wake's), never after a rev a read returned: topo was read, then grp; a member's links written between the
            // two have an id below grp's rev, and holding after grp's rev never woke for them (scenario 85, node 3)
            Json wv = n->r->wait(D, "sub", id, rev);
            if (!wv["ok"].b) break;
            rev = uint64_t(wv["rev"].n);
        }
    } catch (const std::exception& e) {
        if (!n->stop) { fprintf(stderr, "e2-dist agent: node %d ended: %s\n", n->id, e.what()); fflush(stderr); }
    }
}
static int agent(const std::string& where, std::string name) {
    if (name.empty()) name = my_ip();
    const std::string me = "a-" + name;
    daf::Client c(where); control(c, me);
    must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"agents\",\"facts\":{\"cores\":" + std::to_string(std::thread::hardware_concurrency()) + "}"), "join agents");
    printf("e2-dist agent %s on %s (build %s): waiting for a plan\n", me.c_str(), where.c_str(), FDF_BUILD_TAG); fflush(stdout);
    std::string D; std::vector<std::unique_ptr<Node>> nodes; uint64_t prev = 0; std::string last_key;
    auto teardown = [&] { for (auto& n : nodes) { n->stop = true; n->r->shutdown(); } for (auto& n : nodes) if (n->th.joinable()) n->th.join(); nodes.clear(); D.clear(); };
    for (;;) {
        Json pl = c.call("resolve", "\"daf\":" + q(C) + ",\"name\":\"plan\",\"rule\":\"all\"");
        const uint64_t rev = uint64_t(pl["rev"].n);
        for (auto& m : pl["members"].a) {
            const Json& p = m["facts"]; if (p.type != Json::Obj) continue;
            const std::string key = p["run"].s + "/" + std::to_string(int(p["scn"].n)) + "/" + std::to_string(int(p["step"].n)) + (p["end"].b ? "/end" : "");
            if (key == last_key) continue; last_key = key;
            if (p["end"].b) { teardown(); printf("e2-dist agent: run %s ended\n", p["run"].s.c_str()); fflush(stdout); continue; }
            std::map<int, std::map<int, int>> L; for (auto& l : p["links"].a) L[int(l.a[0].n)][int(l.a[1].n)] = int(l.a[2].n);
            if (p["D"].s != D) {                                  // a new scenario: this machine's nodes, fresh sessions
                teardown(); D = p["D"].s; std::set<int> mem; for (auto& x : p["members"].a) mem.insert(int(x.n)); const int root = int(p["root"].n);
                for (auto& [k, v] : p["assign"].o) { if (v.s != me) continue; const int id = atoi(k.c_str());
                    auto n = std::make_unique<Node>(); n->id = id; n->w = std::make_unique<daf::Client>(where); n->r = std::make_unique<daf::Client>(where);
                    must(n->w->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn(id))), "enroll " + apn(id));
                    must(n->r->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn(id) + "-view") + ",\"role\":\"observer\""), "enroll view");
                    n->out = L[id]; must(n->w->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + adj_facts(n->out)), "join topo");
                    if (id == root || mem.count(id)) must(n->w->call("join", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"facts\":{\"role\":" + q(id == root ? "requester" : "member") + "}"), "join grp");
                    nodes.push_back(std::move(n)); }
                for (auto& n : nodes) n->th = std::thread(node_loop, n.get(), D);
            } else {                                              // a change: each node writes its own links if they moved
                for (auto& n : nodes) if (L[n->id] != n->out) { n->out = L[n->id]; must(n->w->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + adj_facts(n->out)), "join topo"); }
            }
            uint64_t b = bytes_of(c); for (auto& n : nodes) b += bytes_of(*n->w) + bytes_of(*n->r);
            must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"acks\",\"facts\":{\"run\":" + q(p["run"].s) + ",\"scn\":" + std::to_string(int(p["scn"].n)) +
                        ",\"step\":" + std::to_string(int(p["step"].n)) + ",\"nodes\":" + std::to_string(nodes.size()) + ",\"bytes\":" + std::to_string(b) + "}"), "ack");
        }
        prev = std::max(prev, rev);
        Json wv = c.wait(C, "name", "plan", prev); if (!wv["ok"].b) { fprintf(stderr, "e2-dist agent: held read on plan refused: %s\n", wv["error"].s.c_str()); return 1; }
        prev = std::max(prev, uint64_t(wv["rev"].n));
    }
}

// ================================================================ driver
static int driver(const std::string& where, const std::string& scen, const std::string& outdir, int min_agents) {
    char run[64]; snprintf(run, sizeof run, "%lx-%d", (long)time(nullptr), getpid());
    FILE* RAW = fopen((outdir + "/raw.jsonl").c_str(), "w"); if (!RAW) { perror((outdir + "/raw.jsonl").c_str()); return 2; }
    auto raw = [&](const std::string& l) { fprintf(RAW, "%s\n", l.c_str()); fflush(RAW); };
    daf::Client c(where); control(c, "driver-" + std::string(run));
    // the agents: wait until MIN_AGENTS are live
    std::vector<std::string> agents; uint64_t arev = 0;
    for (;;) { Json a = c.call("resolve", "\"daf\":" + q(C) + ",\"name\":\"agents\",\"rule\":\"all\",\"live\":true"); agents.clear();
        for (auto& m : a["members"].a) agents.push_back(m["apn"].s); std::sort(agents.begin(), agents.end());
        if (int(agents.size()) >= min_agents) break;
        printf("e2-dist driver: %zu of %d agents present; waiting\n", agents.size(), min_agents); fflush(stdout);
        arev = std::max(arev, uint64_t(a["rev"].n)); c.wait(C, "name", "agents", arev); }
    std::string al; for (auto& a : agents) al += (al.empty() ? "" : " ") + a;
    printf("e2-dist driver: run %s, build %s, host %s, agents: %s\n", run, FDF_BUILD_TAG, where.c_str(), al.c_str()); fflush(stdout);
    raw("{\"k\":\"run\",\"exp\":\"E2-dist\",\"run\":" + q(run) + ",\"build\":" + q(FDF_BUILD_TAG) + ",\"host\":" + q(where) + ",\"agents\":" + std::to_string(agents.size()) + ",\"t\":" + std::to_string(wall()) + "}");
    std::ifstream in(scen); std::string line; int idx = 0, bad = 0, checks = 0; std::vector<double> conv; uint64_t bytes_changes = 0; int changes = 0;
    std::map<std::string, uint64_t> agent_bytes;
    while (std::getline(in, line)) {
        Json s = Json::parse(line); ++idx;
        const std::string dan = "e2d-" + std::string(run) + "-" + std::to_string(idx), D = dan + "#1";
        must(c.call("create_daf", "\"dan\":" + q(dan) + ",\"daii\":\"1\",\"owner\":\"e2d\""), "create " + dan);
        daf::Client dc(where); must(dc.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"coordinator\""), "enroll coordinator");
        for (const char* n : {"topo", "grp", "trees"}) must(dc.call("define_name", "\"daf\":" + q(D) + ",\"name\":" + q(n) + ",\"rule\":\"all\""), "define");
        const int n = int(s["n"].n), root = int(s["root"].n);
        std::string assign = "{"; for (int v = 0; v < n; ++v) assign += (v ? "," : "") + q(std::to_string(v)) + ":" + q(agents[size_t(v) % agents.size()]); assign += "}";
        std::string members = "["; { bool f = true; for (auto& m : s["members"].a) { members += (f ? "" : ",") + std::to_string(int(m.n)); f = false; } } members += "]";
        std::map<int, std::map<int, int>> L; for (auto& l : s["steps"].a[0]["links"].a) L[int(l.a[0].n)][int(l.a[1].n)] = int(l.a[2].n);
        auto& st = s["steps"].a;
        for (size_t k = 0; k < st.size(); ++k) {
            if (k > 0) { const Json& ch = st[k]["change"]; L[int(ch.a[0].n)][int(ch.a[1].n)] = int(ch.a[2].n); if (!s["asym"].b) L[int(ch.a[1].n)][int(ch.a[0].n)] = int(ch.a[2].n); }
            Edges want_r, want_s; for (auto& e : st[k]["rpt"].a) want_r.insert({int(e.a[0].n), int(e.a[1].n)}); for (auto& e : st[k]["spt"].a) want_s.insert({int(e.a[0].n), int(e.a[1].n)});
            const double t0 = wall();
            must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"plan\",\"facts\":{\"run\":" + q(run) + ",\"scn\":" + std::to_string(idx) + ",\"step\":" + std::to_string(k) +
                        ",\"D\":" + q(D) + ",\"root\":" + std::to_string(root) + ",\"members\":" + members + ",\"assign\":" + assign + ",\"links\":" + links_s(L) + "}"), "write plan");
            // every agent acknowledges (its nodes have written what they own); then every node's tree, at or past that state
            std::set<std::string> acked; uint64_t kr = 0; const double deadline = t0 + 10; std::map<std::string, uint64_t> step_bytes;
            while (acked.size() < agents.size() && wall() < deadline) {
                Json a = c.call("resolve", "\"daf\":" + q(C) + ",\"name\":\"acks\",\"rule\":\"all\"");
                for (auto& m : a["members"].a) { const Json& f = m["facts"]; if (f["run"].s == run && int(f["scn"].n) == idx && int(f["step"].n) == int(k)) { acked.insert(m["apn"].s); step_bytes[m["apn"].s] = uint64_t(f["bytes"].n); } }
                if (acked.size() >= agents.size()) break; kr = std::max(kr, uint64_t(a["rev"].n)); held(c, C, "acks", kr, deadline); }
            const uint64_t topo_rev = uint64_t(dc.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\"")["rev"].n);
            double last = -1; uint64_t tr = 0; std::map<int, bool> match; std::string last_trees;
            while (wall() < deadline) {
                const std::string tt = dc.raw("{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"trees\",\"rule\":\"all\",\"live\":true}"); Json t = Json::parse(tt); last_trees = tt; const double now = wall();
                for (auto& m : t["members"].a) { const Json& f = m["facts"]; const int id = id_of(m["apn"].s);
                    Edges r, sp; for (auto& e : f["rpt"].a) r.insert({int(e.a[0].n), int(e.a[1].n)}); for (auto& e : f["spt"].a) sp.insert({int(e.a[0].n), int(e.a[1].n)});
                    match[id] = f["ok"].b && uint64_t(f["rev"].n) >= topo_rev && r == want_r && sp == want_s; }
                int ok = 0; for (int v = 0; v < n; ++v) ok += match.count(v) && match[v];
                if (ok == n) { last = now; break; }
                tr = std::max(tr, uint64_t(t["rev"].n)); held(dc, D, "trees", tr, deadline);
            }
            ++checks; std::string missing; for (int v = 0; v < n; ++v) if (!match.count(v) || !match[v]) missing += " " + std::to_string(v) + "@" + agents[size_t(v) % agents.size()];
            if (last < 0) { ++bad; printf("E2D  scenario %d (%s n=%d %s seed %d) step %zu: not every node reached the reference within 10 s; acks %zu of %zu; missing:%s\n",
                                    idx, s["kind"].s.c_str(), n, s["asym"].b ? "asym" : "sym", int(s["seed"].n), k, acked.size(), agents.size(), missing.c_str()); fflush(stdout); }
            else if (k > 0) conv.push_back((last - t0) * 1000);
            if (last < 0)   // [DIAG-E2D] what each node last published, against what was expected, and the state it had to reach
                raw("{\"k\":\"DIAG-E2D\",\"scn\":" + std::to_string(idx) + ",\"step\":" + std::to_string(k) + ",\"topo_rev\":" + std::to_string(topo_rev) +
                    ",\"want_rpt\":" + edges_s(want_r) + ",\"want_spt\":" + edges_s(want_s) + ",\"trees\":" + (last_trees.empty() ? "null" : last_trees) +
                    ",\"topo\":" + dc.raw("{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\"}") + "}");
            if (k > 0) { ++changes; for (auto& [a, b] : step_bytes) { if (agent_bytes.count(a)) bytes_changes += b - agent_bytes[a]; } }
            for (auto& [a, b] : step_bytes) agent_bytes[a] = b;
            raw("{\"k\":\"step\",\"scn\":" + std::to_string(idx) + ",\"kind\":" + q(s["kind"].s) + ",\"n\":" + std::to_string(n) + ",\"asym\":" + (s["asym"].b ? "true" : "false") +
                ",\"step\":" + std::to_string(k) + ",\"acks\":" + std::to_string(acked.size()) + ",\"converge_ms\":" + std::to_string(last < 0 ? -1.0 : (last - t0) * 1000) + ",\"missing\":" + q(missing) + "}");
        }
        if (idx % 20 == 0) { printf("e2-dist driver: %d scenarios done, %d failed steps\n", idx, bad); fflush(stdout); }
    }
    must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"plan\",\"facts\":{\"run\":" + q(run) + ",\"end\":true}"), "end the run");
    std::sort(conv.begin(), conv.end()); auto pct = [&](double f) { return conv.empty() ? 0.0 : conv[std::min(conv.size() - 1, size_t(f * double(conv.size())))]; };
    printf("E2D  %d scenarios, %d steps, %zu agents: every node's trees equal the reference   %s  %d of %d\n", idx, checks, agents.size(), bad ? "FAIL" : "ok", checks - bad, checks);
    printf("E2D  convergence, driver's clock (plan written -> last node's matching tree read): p50 %.2f ms p99 %.2f ms max %.2f ms (n=%zu)\n", pct(.5), pct(.99), conv.empty() ? 0.0 : conv.back(), conv.size());
    printf("E2D  wire: %.0f bytes per change, all agents' sessions together (%d changes)\n", changes ? double(bytes_changes) / changes : 0.0, changes);
    printf("E2D  RESULT %s\n", bad ? "FAIL" : "PASS"); fflush(stdout);
    raw("{\"k\":\"summary\",\"bad\":" + std::to_string(bad) + ",\"steps\":" + std::to_string(checks) + ",\"conv_p50_ms\":" + std::to_string(pct(.5)) + ",\"conv_p99_ms\":" + std::to_string(pct(.99)) + "}");
    fclose(RAW); return bad ? 1 : 0;
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "agent" && argc >= 3) return agent(argv[2], argc > 3 ? argv[3] : "");
    if (mode == "driver" && argc >= 6) return driver(argv[2], argv[3], argv[4], atoi(argv[5]));
    fprintf(stderr, "usage: e2-dist agent HOST:PORT [NAME] | e2-dist driver HOST:PORT SCENARIOS.jsonl OUTDIR MIN_AGENTS\n"); return 2;
}
