// e2_tree.cpp -- [DAY_E2_TREE_V1] Experiment E2 (EXPERIMENTS.md): the group distribution tree, derived by every
// participant from shared state, on the DAF region unchanged.
//
// Each node of a scenario graph is a member on its own session. It writes its own measurement of its own out-links
// (a->b, the costs it owns) as its facts in the name "topo", and the group's requester and members join "grp".
// Every participant holds a read on "topo"; on every wake it reads topo and grp and computes, from what it read and
// nothing else, the reverse-path tree (Day's responses, Ch12 2.8.1 paraphrase) and the shortest-path tree. The driver
// changes link costs (each by the session that owns the link) and checks every participant's trees against the
// Python reference (e2_ref.py scenarios: Dreyfus-Wagner and exhaustive Edmonds agree on the optimum of every step).
//
// usage: e2_tree HOST:PORT TAG SCENARIOS.jsonl RAW.jsonl [LIMIT]       Exit 0 only if every correctness check passes.
#include "e2_common.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>
using daf::Json; using daf::q;
using Clock = std::chrono::steady_clock;
static double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static FILE* RAW = nullptr; static std::mutex raw_mu;
static void raw(const std::string& line) { std::lock_guard<std::mutex> g(raw_mu); fprintf(RAW, "%s\n", line.c_str()); fflush(RAW); }

// ---- one participant: a member on its own session; a thread that holds a read on topo and recomputes on every wake
struct Part {
    int id; std::unique_ptr<daf::Client> c;           // the member's session: it writes its own links on it
    std::unique_ptr<daf::Client> r;                    // the same member's reading session (a held read parks a request)
    std::thread th; std::atomic<bool> stop{false};
    std::mutex mu; std::vector<std::pair<double, Trees>> seen;   // every computation: when, what
};
static std::string D;
static std::condition_variable cv; static std::mutex cv_mu;

static int scn_now = 0; static std::atomic<int> died{0};
// [DIAG-E2] every computation is recorded: the reads' revs and reply kinds, the held read's after and answer, and any
// exception that ends the participant (it used to end silently)
static std::string wire_delta(const frogram::Stats& a, const frogram::Stats& b) {
    return "{\"raw\":" + std::to_string(b.raw - a.raw) + ",\"repeat\":" + std::to_string(b.repeat - a.repeat) + ",\"diff\":" + std::to_string(b.diff - a.diff) +
           ",\"full\":" + std::to_string(b.full - a.full) + ",\"same\":" + std::to_string(b.same - a.same) + ",\"rdiff\":" + std::to_string(b.rdiff - a.rdiff) +
           ",\"rraw\":" + std::to_string(b.rraw - a.rraw) + ",\"miss\":" + std::to_string(b.miss - a.miss) + ",\"coalesced\":" + std::to_string(b.coalesced - a.coalesced) + "}"; }
static void participant(Part* p) {
    uint64_t rev = 0; int n = 0; const int scn = scn_now;
    try { while (!p->stop) {
        auto s0 = p->r->stats();
        Json topo = p->r->call("resolve", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\",\"live\":true");
        auto s1 = p->r->stats();
        Json grp = p->r->call("resolve", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"rule\":\"all\",\"live\":true");
        auto s2 = p->r->stats();
        Trees t = compute(topo, grp); const double now = wall();
        { std::lock_guard<std::mutex> g(p->mu); p->seen.push_back({now, t}); }
        { std::lock_guard<std::mutex> g(cv_mu); } cv.notify_all();
        const uint64_t after = std::max(rev, uint64_t(topo["rev"].n)); rev = after;
        if (getenv("E2_TRACE")) raw("{\"k\":\"DIAG-E2\",\"scn\":" + std::to_string(scn) + ",\"node\":" + std::to_string(p->id) + ",\"n\":" + std::to_string(n++) + ",\"t\":" + std::to_string(now) +
            ",\"topo_rev\":" + std::to_string(uint64_t(topo["rev"].n)) + ",\"topo_ok\":" + (topo["ok"].b ? "true" : "false") + ",\"topo_wire\":" + wire_delta(s0, s1) +
            ",\"grp_rev\":" + std::to_string(uint64_t(grp["rev"].n)) + ",\"grp_wire\":" + wire_delta(s1, s2) + ",\"rpt\":" + edges_s(t.rpt) + ",\"wait_after\":" + std::to_string(after) + "}");
        auto s3 = p->r->stats();
        Json w = p->r->wait(D, "name", "topo", rev);
        auto s4 = p->r->stats();
        if (getenv("E2_TRACE")) raw("{\"k\":\"DIAG-E2-WAKE\",\"scn\":" + std::to_string(scn) + ",\"node\":" + std::to_string(p->id) + ",\"t\":" + std::to_string(wall()) + ",\"after\":" + std::to_string(after) +
            ",\"ok\":" + (w["ok"].b ? "true" : "false") + ",\"rev\":" + std::to_string(uint64_t(w["rev"].n)) + ",\"wire\":" + wire_delta(s3, s4) + ",\"error\":" + q(w["error"].s) + "}");
        if (!w["ok"].b) break;
        rev = std::max(rev, uint64_t(w["rev"].n));
    } } catch (const std::exception& e) {
        if (!p->stop) ++died;
        if (!p->stop) raw("{\"k\":\"DIAG-E2-DIED\",\"scn\":" + std::to_string(scn) + ",\"node\":" + std::to_string(p->id) + ",\"t\":" + std::to_string(wall()) + ",\"what\":" + q(e.what()) + "}");
    }
}

int main(int argc, char** argv) {
    if (argc < 5) { fprintf(stderr, "usage: e2_tree HOST:PORT TAG SCENARIOS.jsonl RAW.jsonl [LIMIT]\n"); return 2; }
    const std::string where = argv[1], tag = argv[2]; const int limit = argc > 5 ? atoi(argv[5]) : 1 << 30;
    RAW = fopen(argv[4], "w"); if (!RAW) { perror(argv[4]); return 2; }
    std::ifstream in(argv[3]); std::string line; int idx = 0, bad = 0, checks = 0, steps_total = 0, timeouts = 0;
    std::vector<double> conv_ms; uint64_t bytes_total = 0; int changes_total = 0;
    raw("{\"k\":\"run\",\"exp\":\"E2\",\"host\":" + q(where) + ",\"tag\":" + q(tag) + ",\"scenarios\":" + q(argv[3]) + ",\"t\":" + std::to_string(wall()) + "}");
    while (idx < limit && std::getline(in, line)) {
        Json s = Json::parse(line); ++idx; scn_now = idx;
        if (const char* only = getenv("E2_ONLY")) { const std::string o = std::string(",") + only + ","; if (o.find("," + std::to_string(idx) + ",") == std::string::npos) continue; }
        D = "e2-" + tag + "-" + std::to_string(idx) + "#1";
        daf::Client admin(where);
        admin.call("create_daf", "\"dan\":" + q("e2-" + tag + "-" + std::to_string(idx)) + ",\"daii\":\"1\",\"owner\":\"e2\"");
        admin.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"coordinator\"");
        admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\"");
        admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"rule\":\"all\"");
        const int n = int(s["n"].n), root = int(s["root"].n); std::set<int> mem; for (auto& m : s["members"].a) mem.insert(int(m.n));
        auto& st = s["steps"].a;
        // the links each node owns, as the scenario's step 0 has them
        std::map<int, std::map<int, int>> own; for (auto& l : st[0]["links"].a) own[int(l.a[0].n)][int(l.a[1].n)] = int(l.a[2].n);
        auto adj_facts = [&](int v) { std::string f = "{\"adj\":{"; bool first = true; for (auto& [b, c] : own[v]) { f += (first ? "" : ",") + q(apn(b)) + ":" + std::to_string(c); first = false; } return f + "}}"; };
        std::vector<std::unique_ptr<Part>> P;
        for (int v = 0; v < n; ++v) {
            auto p = std::make_unique<Part>(); p->id = v;
            p->c = std::make_unique<daf::Client>(where); p->r = std::make_unique<daf::Client>(where);
            Json e = p->c->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn(v)));
            Json e2 = p->r->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn(v) + "-view") + ",\"role\":\"observer\"");
            if (!e["ok"].b || !e2["ok"].b) { fprintf(stderr, "enroll refused: %s %s\n", e["why"].s.c_str(), e2["why"].s.c_str()); return 2; }
            p->c->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + adj_facts(v));
            if (v == root || mem.count(v)) p->c->call("join", "\"daf\":" + q(D) + ",\"name\":\"grp\",\"facts\":{\"role\":" + q(v == root ? "requester" : "member") + "}");
            P.push_back(std::move(p));
        }
        for (auto& p : P) p->th = std::thread(participant, p.get());
        uint64_t b0 = 0;   // taken after step 0 converges: the wire figure is the changes' alone

        // every participant's trees equal the reference; returns ms from t_issue to the last participant's first matching
        // computation, or -1 if not all matched within 2 s
        auto await_ref = [&](const Json& ref, double t_issue, int step) -> double {
            Edges want_r, want_s; for (auto& e : ref["rpt"].a) want_r.insert({int(e.a[0].n), int(e.a[1].n)}); for (auto& e : ref["spt"].a) want_s.insert({int(e.a[0].n), int(e.a[1].n)});
            const auto deadline = Clock::now() + std::chrono::seconds(2); double last = 0; bool all = false;
            std::unique_lock<std::mutex> l(cv_mu);
            while (!all) {
                all = true; last = 0;
                for (auto& p : P) { std::lock_guard<std::mutex> g(p->mu); double hit = -1;
                    for (auto& [t, tr] : p->seen) if (t >= t_issue - 1e-9 && tr.ok && tr.rpt == want_r && tr.spt == want_s) { hit = t; break; }
                    if (hit < 0) { all = false; break; } last = std::max(last, hit); }
                if (all) break;
                if (cv.wait_until(l, deadline) == std::cv_status::timeout) break;
            }
            l.unlock();
            // the record: each participant's last computation for this step
            for (auto& p : P) { std::lock_guard<std::mutex> g(p->mu); const Trees& tr = p->seen.back().second;
                const bool ok = tr.ok && tr.rpt == want_r && tr.spt == want_s;
                raw("{\"k\":\"tree\",\"scn\":" + std::to_string(idx) + ",\"step\":" + std::to_string(step) + ",\"node\":" + std::to_string(p->id) + ",\"rev\":" + std::to_string(tr.rev) +
                    ",\"rpt\":" + edges_s(tr.rpt) + ",\"spt\":" + edges_s(tr.spt) + ",\"match\":" + (ok ? "true" : "false") + "}"); }
            return all ? (last - t_issue) * 1000 : -1; };

        double t_issue = 0;
        for (size_t k = 0; k < st.size(); ++k) {
            if (k == 0) t_issue = 0;    // the initial state: any computation counts
            else {
                const Json& ch = st[k]["change"]; const int a = int(ch.a[0].n), b = int(ch.a[1].n), c = int(ch.a[2].n);
                t_issue = wall();
                own[a][b] = c; P[size_t(a)]->c->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + adj_facts(a));   // a owns a->b
                if (!s["asym"].b) { own[b][a] = c; P[size_t(b)]->c->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + adj_facts(b)); }   // b owns b->a
                ++changes_total;
            }
            const double ms = await_ref(st[k], t_issue, int(k)); ++checks; ++steps_total;
            if (ms < 0) { ++bad; ++timeouts; printf("E2  scenario %d (%s n=%d %s seed %d) step %zu: participants did not all reach the reference tree within 2 s\n",
                                     idx, s["kind"].s.c_str(), n, s["asym"].b ? "asym" : "sym", int(s["seed"].n), k); }
            else if (k > 0) conv_ms.push_back(ms);
            if (k == 0) for (auto& p : P) { auto a = p->c->stats(), b = p->r->stats(); b0 += a.bytes_in + a.bytes_out + b.bytes_in + b.bytes_out; }
            raw("{\"k\":\"step\",\"scn\":" + std::to_string(idx) + ",\"kind\":" + q(s["kind"].s) + ",\"n\":" + std::to_string(n) + ",\"asym\":" + (s["asym"].b ? "true" : "false") +
                ",\"seed\":" + std::to_string(int(s["seed"].n)) + ",\"step\":" + std::to_string(k) + ",\"converge_ms\":" + std::to_string(ms) +
                ",\"opt\":" + std::to_string(int(st[k]["opt"].n)) + ",\"rpt_cost\":" + std::to_string(int(st[k]["rpt_cost"].n)) + ",\"spt_cost\":" + std::to_string(int(st[k]["spt_cost"].n)) + "}");
        }
        uint64_t b1 = 0; for (auto& p : P) { auto a = p->c->stats(), b = p->r->stats(); b1 += a.bytes_in + a.bytes_out + b.bytes_in + b.bytes_out; }
        bytes_total += b1 - b0;
        for (auto& p : P) { p->stop = true; p->r->shutdown(); }
        for (auto& p : P) if (p->th.joinable()) p->th.join();
    }
    std::sort(conv_ms.begin(), conv_ms.end());
    auto pct = [&](double f) { return conv_ms.empty() ? 0.0 : conv_ms[std::min(conv_ms.size() - 1, size_t(f * double(conv_ms.size())))]; };
    printf("E2  %d scenarios, %d steps: every participant's trees equal the reference      %s  %d of %d\n", idx, steps_total, bad ? "FAIL" : "ok", checks - bad, checks);
    printf("E2  no participant ended on an error                                        %s  %d ended\n", died ? "FAIL" : "ok", died.load()); if (died) ++bad;
    printf("E2  convergence after a change (all participants): p50 %.2f ms p99 %.2f ms max %.2f ms (n=%zu); %d timeouts\n", pct(.5), pct(.99), conv_ms.empty() ? 0.0 : conv_ms.back(), conv_ms.size(), timeouts);
    printf("E2  wire: %.0f bytes per change, all participants' sessions together (%d changes)\n", changes_total ? double(bytes_total) / changes_total : 0.0, changes_total);
    raw("{\"k\":\"summary\",\"scenarios\":" + std::to_string(idx) + ",\"steps\":" + std::to_string(steps_total) + ",\"bad\":" + std::to_string(bad) + ",\"conv_p50_ms\":" + std::to_string(pct(.5)) +
        ",\"conv_p99_ms\":" + std::to_string(pct(.99)) + ",\"bytes_per_change\":" + std::to_string(changes_total ? double(bytes_total) / changes_total : 0.0) + "}");
    printf("E2  RESULT %s\n", bad ? "FAIL" : "PASS"); fflush(stdout);
    _exit(bad ? 1 : 0);
}
