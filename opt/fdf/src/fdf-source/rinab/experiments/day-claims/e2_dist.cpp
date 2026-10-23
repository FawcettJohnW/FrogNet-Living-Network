// e2_dist.cpp -- [DAY_E2_DIST_V1] Experiment E2 across machines. Everything between the machines goes through the one
// shared memory of the experiment's RAM host; nothing else connects them.
//
//   e2-dist agent HOST:PORT [NAME]       on every machine that runs participants (NAME: default, its first 10.x/IPv4)
//   e2-dist driver HOST:PORT SCENARIOS.jsonl OUTDIR             on one machine (any)
//
// The control DAF "dayctl#1". Nobody is told how many machines there are [E2D_MEMBERSHIP_IS_DISCOVERED_V1]: an agent
// advertises itself in "agents"; the driver's held read on that family is how it learns who is there. The driver writes
// the plan as its own record in "plan" (which scenario DAF, an epoch, which live agent runs which graph node, every
// link's cost now); every change in who is there makes a new epoch that spreads the nodes over whoever is live --
// a departure at once, an arrival at the next step. Each agent holds a read on "plan", runs the nodes it was given
// (stopping the ones it lost, starting the ones it gained) and writes its acknowledgement in "acks". In the scenario DAF every node is a member on its own
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
static uint64_t bytes_of(daf::Client& c) { auto s = c.stats(); return s.bytes_in + s.bytes_out; }


// ================================================================ agent
static std::string utc() { char b[40]; const double t = wall(); time_t s = time_t(t); struct tm g; gmtime_r(&s, &g);
    strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%S", &g); char o[48]; snprintf(o, sizeof o, "%s.%03dZ", b, int((t - double(s)) * 1000)); return o; }
// [DIAG-E2D-LOSS] when a session to the host dies: when (UTC), why, and the route this machine uses to the host right
// then -- the evidence for telling the path from the host from this machine
static std::string g_host_ip;
static std::string route_now() {
    std::string out; FILE* f = popen(("ip route get " + g_host_ip + " 2>&1").c_str(), "r"); if (!f) return "(ip route get: cannot run)";
    char b[512]; while (fgets(b, sizeof b, f)) out += b; pclose(f); while (!out.empty() && out.back() == '\n') out.pop_back(); return out; }
struct Node {
    int id; std::unique_ptr<daf::Client> w, r; std::thread th; std::atomic<bool> stop{false};
    std::mutex mu; std::map<int, int> out; int step = 0; bool ready = false;   // its links as the plan has them, and the step that set them
};
// [E2D_CALL_TIMES_V1] every write this machine makes to the host, timed on this machine's clock: the round trip to the
// host, measured where it is paid. Reported with the agent's acknowledgement (off the critical path).
static std::mutex g_rtt_mu; static std::vector<double> g_rtt;
static Json timed(daf::Client& c, const std::string& fn, const std::string& fields) {
    const double t = wall(); Json r = c.call(fn, fields); const double ms = (wall() - t) * 1000;
    { std::lock_guard<std::mutex> g(g_rtt_mu); g_rtt.push_back(ms); } return r; }
static void write_links(Node* n, const std::string& D) {   // under n->mu; [E2D_STEP_STAMP_V1] the links say which step set them
    std::string f = adj_facts(n->out); f.insert(f.size() - 1, ",\"step\":" + std::to_string(n->step));
    must(timed(*n->w, "join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + f), "join topo"); }
// one graph node: becomes its member, writes what it owns, then computes on every change to what it reads
static void node_loop(Node* n, std::string D, int role, std::string view) {
    try {
        must(n->r->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(view) + ",\"role\":\"observer\""), "enroll " + view);
        // [E2D_NODE_MOVES_V1] a node given to this agent may still be held by the session of the agent that had it. Its
        // name is free when that session leaves the DAF's members: a held read on "members", from the version taken
        // before the attempt, schedules the next attempt. No timer.
        for (;;) {
            if (n->stop) return;
            const uint64_t mrev = uint64_t(n->r->call("resolve", "\"daf\":" + q(D) + ",\"name\":\"members\",\"rule\":\"all\"")["rev"].n);
            Json e = n->w->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn(n->id)));
            if (e["ok"].b) break;
            if (e["error"].s != "EEXIST") throw std::runtime_error("enroll refused: " + e["error"].s + " " + e["why"].s);
            Json wv = n->r->wait(D, "name", "members", mrev); if (!wv["ok"].b) return;
        }
        { std::lock_guard<std::mutex> g(n->mu); write_links(n, D); n->ready = true; }
        if (role) must(n->w->call("join", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"facts\":{\"role\":" + q(role == 1 ? "requester" : "member") + "}"), "join grp");
        // [E2D_HOLD_WHAT_THE_ANSWER_READS_V1] the trees are computed from "topo" AND "grp": one subscription over both
        Json sub = n->r->call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"topo\",\"grp\"]");
        if (!sub["ok"].b) throw std::runtime_error("subscribe refused: " + sub["error"].s + " " + sub["why"].s);
        // [E2D_STATE_IN_THE_WAKE_V1] the subscription's answer carries the state of both names, taken at the version it
        // answers with: no reads. The first answer is the state now; each later one comes when either name changes.
        const std::string id = sub["sub"].s; uint64_t rev = 0; bool first = true;
        while (!n->stop) {
            Json wv = n->r->wait(D, "sub", id, rev, first); first = false;
            if (!wv["ok"].b) break;
            rev = uint64_t(wv["rev"].n);
            const Json& data = wv["rows"].a.at(0)["v"]["data"];
            std::vector<const Json*> topo, grp;
            for (auto& m : data["topo"].a) if (m["live"].b) topo.push_back(&m);
            for (auto& m : data["grp"].a) if (m["live"].b) grp.push_back(&m);
            Trees t = compute_members(topo, grp);
            std::string seen = "{"; { bool f = true; for (const Json* m : topo) { seen += (f ? "" : ",") + q((*m)["apn"].s) + ":" + std::to_string(int((*m)["facts"]["step"].n)); f = false; } } seen += "}";
            timed(*n->w, "join", "\"daf\":" + q(D) + ",\"name\":\"trees\",\"facts\":{\"rev\":" + std::to_string(rev) + ",\"ok\":" + (t.ok ? "true" : "false") +
                  ",\"seen\":" + seen + ",\"rpt\":" + edges_s(t.rpt) + ",\"spt\":" + edges_s(t.spt) + "}");
        }
    } catch (const std::exception& e) {
        if (!n->stop) { fprintf(stderr, "e2-dist agent %s: node %d ended: %s; route to host now: %s\n", utc().c_str(), n->id, e.what(), route_now().c_str()); fflush(stderr); }
    }
}
static void stop_node(Node& n) { n.stop = true; n.r->shutdown(); n.w->shutdown(); if (n.th.joinable()) n.th.join(); }
static int agent_run(const std::string& where, std::string name);
static int agent(const std::string& where, std::string name) {
    g_host_ip = where.substr(0, where.rfind(':'));
    try { return agent_run(where, name); }
    catch (const std::exception& e) {   // [E2D_AGENT_SAYS_WHY_V1] its own session is gone: say when, why and the route; its nodes went with it
        fprintf(stderr, "e2-dist agent %s: session to the host ended: %s; route to host now: %s\n", utc().c_str(), e.what(), route_now().c_str());
        fflush(stderr); _exit(1); }
}
static int agent_run(const std::string& where, std::string name) {
    if (name.empty()) name = my_ip();
    const std::string me = "a-" + name;
    daf::Client c(where); control(c, me);
    must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"agents\",\"facts\":{\"cores\":" + std::to_string(std::thread::hardware_concurrency()) + "}"), "join agents");
    printf("e2-dist agent %s on %s (build %s): advertised; waiting for a plan (a controller writes it: day-claims e2 HOST)\n", me.c_str(), where.c_str(), FDF_BUILD_TAG); fflush(stdout);
    std::string D; std::map<int, std::unique_ptr<Node>> nodes; uint64_t prev = 0; std::string last_key; bool first = true;
    Json psub = c.call("subscribe", "\"daf\":" + q(C) + ",\"names\":[\"plan\"]");
    if (!psub["ok"].b) { fprintf(stderr, "e2-dist agent: subscribe to the plan refused: %s %s\n", psub["error"].s.c_str(), psub["why"].s.c_str()); return 1; }
    const std::string pid = psub["sub"].s;
    auto stop_all = [&] { for (auto& [id, n] : nodes) { n->stop = true; n->r->shutdown(); n->w->shutdown(); } for (auto& [id, n] : nodes) if (n->th.joinable()) n->th.join(); nodes.clear(); D.clear(); };
    for (;;) {
        // [E2D_STATE_IN_THE_WAKE_V1] the plan comes in the held read's answer
        Json wv = c.wait(C, "sub", pid, prev, first); first = false;
        if (!wv["ok"].b) { fprintf(stderr, "e2-dist agent: held read on the plan refused: %s\n", wv["error"].s.c_str()); return 1; }
        prev = uint64_t(wv["rev"].n);
        for (auto& m : wv["rows"].a.at(0)["v"]["data"]["plan"].a) {
            const Json& p = m["facts"]; if (p.type != Json::Obj) continue;
            const std::string key = p["run"].s + "/" + std::to_string(int(p["scn"].n)) + "/" + std::to_string(int(p["step"].n)) + "/" + std::to_string(int(p["epoch"].n)) + (p["end"].b ? "/end" : "");
            if (key == last_key) continue; last_key = key;
            if (p["end"].b) { stop_all(); { std::lock_guard<std::mutex> g(g_rtt_mu); g_rtt.clear(); } printf("e2-dist agent: run %s ended\n", p["run"].s.c_str()); fflush(stdout); continue; }
            std::map<int, std::map<int, int>> L; for (auto& l : p["links"].a) L[int(l.a[0].n)][int(l.a[1].n)] = int(l.a[2].n);
            if (p["D"].s != D) { stop_all(); D = p["D"].s;
                const int sc = int(p["scn"].n); if (sc == 1 || sc % 20 == 0) { printf("e2-dist agent %s: run %s scenario %d\n", utc().c_str(), p["run"].s.c_str(), sc); fflush(stdout); } }
            std::set<int> mine; for (auto& [k, v] : p["assign"].o) if (v.s == me) mine.insert(atoi(k.c_str()));
            for (auto it = nodes.begin(); it != nodes.end();) { if (mine.count(it->first)) { ++it; continue; } stop_node(*it->second); it = nodes.erase(it); }   // given to another agent
            std::set<int> mem; for (auto& x : p["members"].a) mem.insert(int(x.n)); const int root = int(p["root"].n);
            for (int id : mine) {
                auto f = nodes.find(id);
                if (f == nodes.end()) {                           // gained: a new member session for it
                    auto n = std::make_unique<Node>(); n->id = id; n->out = L[id]; n->step = int(p["step"].n);
                    n->w = std::make_unique<daf::Client>(where); n->r = std::make_unique<daf::Client>(where);
                    const int role = id == root ? 1 : mem.count(id) ? 2 : 0;
                    n->th = std::thread(node_loop, n.get(), D, role, apn(id) + "-view-" + name);
                    nodes[id] = std::move(n);
                } else {                                          // kept: it writes its own links if they moved
                    Node& n = *f->second; std::lock_guard<std::mutex> g(n.mu);
                    if (n.out != L[id]) { n.out = L[id]; n.step = int(p["step"].n); if (n.ready) write_links(&n, D); }
                }
            }
            uint64_t b = bytes_of(c); for (auto& [id, n] : nodes) b += bytes_of(*n->w) + bytes_of(*n->r);
            std::vector<double> rt; { std::lock_guard<std::mutex> g(g_rtt_mu); rt = g_rtt; } std::sort(rt.begin(), rt.end());   // over the run so far
            auto rq = [&](double f) { return rt.empty() ? 0.0 : rt[std::min(rt.size() - 1, size_t(f * double(rt.size())))]; };
            char rb[160]; snprintf(rb, sizeof rb, ",\"calls\":%zu,\"rtt_min\":%.2f,\"rtt_p50\":%.2f,\"rtt_p90\":%.2f", rt.size(), rq(0), rq(.5), rq(.9));
            must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"acks\",\"facts\":{\"run\":" + q(p["run"].s) + ",\"scn\":" + std::to_string(int(p["scn"].n)) +
                        ",\"step\":" + std::to_string(int(p["step"].n)) + ",\"epoch\":" + std::to_string(int(p["epoch"].n)) + ",\"nodes\":" + std::to_string(nodes.size()) +
                        ",\"bytes\":" + std::to_string(b) + rb + "}"), "ack");
        }
    }
}

// ================================================================ driver
static std::vector<std::string> live_agents(daf::Client& c, uint64_t* rev = nullptr) {
    Json a = c.call("resolve", "\"daf\":" + q(C) + ",\"name\":\"agents\",\"rule\":\"all\",\"live\":true");
    std::vector<std::string> v; for (auto& m : a["members"].a) v.push_back(m["apn"].s); std::sort(v.begin(), v.end());
    if (rev) *rev = uint64_t(a["rev"].n); return v;
}
static int driver(const std::string& where, const std::string& scen, const std::string& outdir) {
    char run[64]; snprintf(run, sizeof run, "%lx-%d", (long)time(nullptr), getpid());
    FILE* RAW = fopen((outdir + "/raw.jsonl").c_str(), "w"); if (!RAW) { perror((outdir + "/raw.jsonl").c_str()); return 2; }
    auto raw = [&](const std::string& l) { fprintf(RAW, "%s\n", l.c_str()); fflush(RAW); };
    daf::Client c(where); control(c, "driver-" + std::string(run));
    // [E2D_MEMBERSHIP_IS_DISCOVERED_V1] the run starts when the first agent has advertised; nobody says how many
    uint64_t arev = 0; std::vector<std::string> agents = live_agents(c, &arev);
    if (agents.empty()) { printf("e2-dist driver: no agent has advertised yet; holding a read on agents\n"); fflush(stdout); }
    while (agents.empty()) { c.wait(C, "name", "agents", arev); agents = live_agents(c, &arev); }
    auto names = [](const std::vector<std::string>& v) { std::string s; for (auto& a : v) s += (s.empty() ? "" : " ") + a; return s; };
    printf("e2-dist driver: run %s, build %s, host %s; agents now: %s\n", run, FDF_BUILD_TAG, where.c_str(), names(agents).c_str()); fflush(stdout);
    raw("{\"k\":\"run\",\"exp\":\"E2-dist\",\"run\":" + q(run) + ",\"build\":" + q(FDF_BUILD_TAG) + ",\"host\":" + q(where) + ",\"t\":" + std::to_string(wall()) + "}");
    std::ifstream in(scen); std::string line; int idx = 0, bad = 0, checks = 0, epoch = 0, replans = 0; std::vector<double> conv; uint64_t bytes_changes = 0; int changes = 0;
    std::map<std::string, uint64_t> agent_bytes; std::set<std::string> ever(agents.begin(), agents.end()); std::map<size_t, int> steps_by_count;
    while (std::getline(in, line)) {
        Json s = Json::parse(line); ++idx;
        const std::string dan = "e2d-" + std::string(run) + "-" + std::to_string(idx), D = dan + "#1";
        must(c.call("create_daf", "\"dan\":" + q(dan) + ",\"daii\":\"1\",\"owner\":\"e2d\""), "create " + dan);
        daf::Client dc(where); must(dc.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"coordinator\""), "enroll coordinator");
        for (const char* n : {"topo", "grp", "trees"}) must(dc.call("define_name", "\"daf\":" + q(D) + ",\"name\":" + q(n) + ",\"rule\":\"all\""), "define");
        Json ts = must(dc.call("subscribe", "\"daf\":" + q(D) + ",\"names\":[\"trees\"]"), "subscribe trees"); const std::string tsub = ts["sub"].s; uint64_t trev = 0;
        const int n = int(s["n"].n), root = int(s["root"].n);
        std::string members = "["; { bool f = true; for (auto& m : s["members"].a) { members += (f ? "" : ",") + std::to_string(int(m.n)); f = false; } } members += "]";
        std::map<int, std::map<int, int>> L; for (auto& l : s["steps"].a[0]["links"].a) L[int(l.a[0].n)][int(l.a[1].n)] = int(l.a[2].n);
        auto& st = s["steps"].a;
        for (size_t k = 0; k < st.size(); ++k) {
            if (k > 0) { const Json& ch = st[k]["change"]; L[int(ch.a[0].n)][int(ch.a[1].n)] = int(ch.a[2].n); if (!s["asym"].b) L[int(ch.a[1].n)][int(ch.a[0].n)] = int(ch.a[2].n); }
            Edges want_r, want_s; for (auto& e : st[k]["rpt"].a) want_r.insert({int(e.a[0].n), int(e.a[1].n)}); for (auto& e : st[k]["spt"].a) want_s.insert({int(e.a[0].n), int(e.a[1].n)});
            // an arrival is picked up here, at the step; a departure below, at once
            { auto now_agents = live_agents(c, &arev); if (now_agents != agents && !now_agents.empty()) { agents = now_agents; ++epoch; ++replans; for (auto& a : agents) ever.insert(a); } }
            const double t0 = wall(), deadline = t0 + 15;
            // the writers of this step: their links must be in what a node read before its tree counts ([E2D_STEP_STAMP_V1])
            std::vector<int> writers; if (k > 0) { writers.push_back(int(st[k]["change"].a[0].n)); if (!s["asym"].b) writers.push_back(int(st[k]["change"].a[1].n)); }
            auto publish = [&] {
                std::string assign = "{"; for (int v = 0; v < n; ++v) assign += (v ? "," : "") + q(std::to_string(v)) + ":" + q(agents[size_t(v) % agents.size()]); assign += "}";
                must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"plan\",\"facts\":{\"run\":" + q(run) + ",\"scn\":" + std::to_string(idx) + ",\"step\":" + std::to_string(k) +
                            ",\"epoch\":" + std::to_string(epoch) + ",\"D\":" + q(D) + ",\"root\":" + std::to_string(root) + ",\"members\":" + members + ",\"assign\":" + assign +
                            ",\"links\":" + links_s(L) + "}"), "write plan"); };
            auto departed = [&]() -> bool {   // a departure: re-plan this step at once, over whoever is still there
                auto now_agents = live_agents(c, &arev); bool gone = false; for (auto& a : agents) if (!std::count(now_agents.begin(), now_agents.end(), a)) gone = true;
                if (!gone || now_agents.empty()) return false;
                printf("e2-dist driver %s: agents now %s; re-planning scenario %d step %zu\n", utc().c_str(), names(now_agents).c_str(), idx, k); fflush(stdout);
                agents = now_agents; ++epoch; ++replans; publish(); return true; };
            publish();
            double last = -1; std::map<int, bool> match; std::string last_trees; bool first = true;
            while (wall() < deadline) {
                // [E2D_STATE_IN_THE_WAKE_V1] every node's report arrives in the held read's answer
                const double left = deadline - wall(); Json wv;
                try { wv = dc.wait(D, "sub", tsub, trev, first, "", left); } catch (const std::exception&) { break; }   // the step's bound
                first = false; if (!wv["ok"].b) break;
                trev = uint64_t(wv["rev"].n); const double now = wall(); const Json& rows = wv["rows"].a.at(0)["v"]["data"]["trees"];
                match.clear(); int present = 0; last_trees.clear();
                for (auto& m : rows.a) { if (!m["live"].b) continue; const Json& f = m["facts"]; const int id = id_of(m["apn"].s); ++present;
                    bool fresh = true; for (int w : writers) { const Json& sv = f["seen"][apn(w)]; if (sv.type != Json::Num || int(sv.n) < int(k)) fresh = false; }
                    Edges r, sp; for (auto& e : f["rpt"].a) r.insert({int(e.a[0].n), int(e.a[1].n)}); for (auto& e : f["spt"].a) sp.insert({int(e.a[0].n), int(e.a[1].n)});
                    match[id] = f["ok"].b && fresh && r == want_r && sp == want_s;
                    last_trees += (last_trees.empty() ? "" : ",") + ("{\"node\":" + std::to_string(id) + ",\"ok\":" + (f["ok"].b ? "true" : "false") + ",\"fresh\":" + (fresh ? "true" : "false") +
                                  ",\"rpt\":" + edges_s(r) + ",\"spt\":" + edges_s(sp) + "}"); }
                int ok = 0; for (int v = 0; v < n; ++v) ok += match.count(v) && match[v];
                if (ok == n) { last = now; break; }
                if (present < n) departed();   // a node's report is gone: is its agent?
            }
            ++checks; ++steps_by_count[agents.size()];
            std::string missing; for (int v = 0; v < n; ++v) if (!match.count(v) || !match[v]) missing += " " + std::to_string(v) + "@" + agents[size_t(v) % agents.size()];
            if (last < 0) { ++bad; printf("E2D  scenario %d (%s n=%d %s seed %d) step %zu: not every node reached the reference within 15 s; agents %zu; missing:%s\n",
                                    idx, s["kind"].s.c_str(), n, s["asym"].b ? "asym" : "sym", int(s["seed"].n), k, agents.size(), missing.c_str()); fflush(stdout);
                raw("{\"k\":\"DIAG-E2D\",\"scn\":" + std::to_string(idx) + ",\"step\":" + std::to_string(k) +
                    ",\"want_rpt\":" + edges_s(want_r) + ",\"trees\":[" + last_trees + "]}"); }
            else if (k > 0) conv.push_back((last - t0) * 1000);
            if (k > 0) ++changes;
            raw("{\"k\":\"step\",\"scn\":" + std::to_string(idx) + ",\"kind\":" + q(s["kind"].s) + ",\"n\":" + std::to_string(n) + ",\"asym\":" + (s["asym"].b ? "true" : "false") +
                ",\"step\":" + std::to_string(k) + ",\"epoch\":" + std::to_string(epoch) + ",\"agents\":" + q(names(agents)) + ",\"converge_ms\":" + std::to_string(last < 0 ? -1.0 : (last - t0) * 1000) +
                ",\"missing\":" + q(missing) + "}");
        }
        if (idx % 20 == 0) { printf("e2-dist driver: %d scenarios done, %d failed steps, agents now: %s\n", idx, bad, names(agents).c_str()); fflush(stdout); }
    }
    // the agents' own measurements: each acknowledgement carries that machine's call times to the host over the run so far,
    // on its own clock, and its sessions' bytes ([E2D_CALL_TIMES_V1])
    std::string rtt_lines; uint64_t bytes_total = 0;
    { Json a = c.call("resolve", "\"daf\":" + q(C) + ",\"name\":\"acks\",\"rule\":\"all\"");
      for (auto& m : a["members"].a) { const Json& f = m["facts"]; if (f["run"].s != run) continue; bytes_total += uint64_t(f["bytes"].n);
          char b[200]; snprintf(b, sizeof b, "E2D  %-18s round trip to the host, its own clock: min %6.1f ms  p50 %6.1f ms  p90 %6.1f ms  (%d calls)\n",
                                m["apn"].s.c_str(), f["rtt_min"].n, f["rtt_p50"].n, f["rtt_p90"].n, int(f["calls"].n)); rtt_lines += b;
          char r2[256]; snprintf(r2, sizeof r2, ",\"calls\":%d,\"rtt_min\":%.2f,\"rtt_p50\":%.2f,\"rtt_p90\":%.2f,\"bytes\":%llu}", int(f["calls"].n), f["rtt_min"].n, f["rtt_p50"].n, f["rtt_p90"].n, (unsigned long long)f["bytes"].n);
          raw("{\"k\":\"agent\",\"apn\":" + q(m["apn"].s) + r2); } }
    bytes_changes = bytes_total;
    must(c.call("join", "\"daf\":" + q(C) + ",\"name\":\"plan\",\"facts\":{\"run\":" + q(run) + ",\"epoch\":" + std::to_string(epoch) + ",\"end\":true}"), "end the run");
    std::sort(conv.begin(), conv.end()); auto pct = [&](double f) { return conv.empty() ? 0.0 : conv[std::min(conv.size() - 1, size_t(f * double(conv.size())))]; };
    std::string sb; for (auto& [cnt, nst] : steps_by_count) sb += " " + std::to_string(nst) + " steps on " + std::to_string(cnt) + (cnt == 1 ? " agent;" : " agents;");
    printf("E2D  agents seen: %s; re-plans %d;%s\n", names(std::vector<std::string>(ever.begin(), ever.end())).c_str(), replans, sb.c_str());
    printf("E2D  %d scenarios, %d steps: every node's trees equal the reference   %s  %d of %d\n", idx, checks, bad ? "FAIL" : "ok", checks - bad, checks);
    printf("E2D  convergence, driver's clock (plan written -> last node's matching tree read): p50 %.2f ms p99 %.2f ms max %.2f ms (n=%zu)\n", pct(.5), pct(.99), conv.empty() ? 0.0 : conv.back(), conv.size());
    printf("%s", rtt_lines.c_str());
    printf("E2D  wire: %.0f bytes per step, all agents' sessions together, setup included (%d steps)\n", checks ? double(bytes_changes) / checks : 0.0, checks);
    printf("E2D  RESULT %s\n", bad ? "FAIL" : "PASS"); fflush(stdout);
    raw("{\"k\":\"summary\",\"bad\":" + std::to_string(bad) + ",\"steps\":" + std::to_string(checks) + ",\"replans\":" + std::to_string(replans) + ",\"conv_p50_ms\":" + std::to_string(pct(.5)) + ",\"conv_p99_ms\":" + std::to_string(pct(.99)) + "}");
    fclose(RAW); return bad ? 1 : 0;
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "agent" && argc >= 3) return agent(argv[2], argc > 3 ? argv[3] : "");
    if (mode == "driver" && argc >= 5) return driver(argv[2], argv[3], argv[4]);
    fprintf(stderr, "usage: e2-dist agent HOST:PORT [NAME] | e2-dist driver HOST:PORT SCENARIOS.jsonl OUTDIR\n"); return 2;
}
