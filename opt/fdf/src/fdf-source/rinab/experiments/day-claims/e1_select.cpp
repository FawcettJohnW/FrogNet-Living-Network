// e1_select.cpp -- [DAY_E1_SELECT_V1] Experiment E1 (EXPERIMENTS.md): whatevercast selection on the DAF region,
// unchanged. One named group ("svc") resolved by all, one (rank), capability (filter), designated, nearest by reported
// coordinates (the region's rule) and nearest in the network (computed by the asking participant from the adjacency
// facts every member publishes about its own links, in the "topo" name). Membership, capabilities, positions, links
// and sessions change while it runs; after every change each rule is checked against an oracle kept from the writes.
// A held read on the name runs throughout; a second phase changes members concurrently from several threads.
// usage: e1_select HOST:PORT TAG SEED EVENTS RAW.jsonl      Exit 0 only if every correctness check passes.
#include "daf_client.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <random>
#include <set>
#include <string>
#include <thread>
#include <vector>
using daf::Json; using daf::q;
using Clock = std::chrono::steady_clock;
static double ms_since(Clock::time_point t) { return std::chrono::duration<double, std::milli>(Clock::now() - t).count(); }
static double g_session_up = 0; static std::vector<double> g_session_ms;   // when the last member to arrive had its session up (its first write follows)
static double wall() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }

static FILE* RAW = nullptr; static std::mutex raw_mu;
static void raw(const std::string& line) { std::lock_guard<std::mutex> g(raw_mu); fprintf(RAW, "%s\n", line.c_str()); fflush(RAW); }
static int bad = 0, checks = 0;
static void expect(const std::string& what, bool ok, const std::string& detail = "") {
    ++checks; printf("E1  %-78s %s%s%s\n", what.c_str(), ok ? "ok" : "FAIL", detail.empty() ? "" : "  ", detail.c_str()); fflush(stdout);
    if (!ok) ++bad;
}

// ---- the ground truth: what the harness wrote, and nothing else
struct Mem { bool live = false, in_svc = false; int capacity = 0, cpu = 0; bool trauma = false, gpu = false; double x = 0, y = 0;
             std::map<std::string, int> adj; };             // adj: this member's own measurement of each of its links
static std::map<std::string, Mem> T;
static std::string designated;

static std::string svc_facts(const Mem& m) {
    char b[256]; snprintf(b, sizeof b, "{\"capacity\":%d,\"trauma\":%s,\"cpu\":%d,\"gpu\":%s,\"x\":%.3f,\"y\":%.3f}", m.capacity, m.trauma ? "true" : "false", m.cpu, m.gpu ? "true" : "false", m.x, m.y);
    return b; }
static std::string topo_facts(const Mem& m) {
    std::string s = "{\"adj\":{"; bool f = true; for (auto& [n, c] : m.adj) { s += (f ? "" : ",") + q(n) + ":" + std::to_string(c); f = false; } return s + "}}"; }

// ---- the rules, as the oracle computes them from the ground truth
static std::vector<std::string> o_all(bool live) { std::vector<std::string> r; for (auto& [a, m] : T) if (m.in_svc && (!live || m.live)) r.push_back(a); return r; }
static std::string o_best(bool (*ok)(const Mem&), double (*key)(const Mem&)) {
    std::string best; double bk = 0;
    for (auto& [a, m] : T) { if (!m.in_svc || !m.live || !ok(m)) continue; const double k = key(m);
        if (best.empty() || k > bk) { best = a; bk = k; } }                       // map order: ties go to the lowest apn
    return best; }
static bool ok_trauma(const Mem& m) { return m.trauma && m.capacity > 0; }
static double key_capacity(const Mem& m) { return m.capacity; }
static bool ok_capable(const Mem& m) { return m.gpu && m.cpu >= 4; }
static double key_cpu(const Mem& m) { return m.cpu; }
static double PX = 0, PY = 0;
static bool ok_any(const Mem&) { return true; }
static double key_geo(const Mem& m) { return -std::hypot(m.x - PX, m.y - PY); }
static std::string o_designated() { auto it = T.find(designated); return it != T.end() && it->second.in_svc ? designated : ""; }
// nearest in the network: Floyd-Warshall over the live members' own link measurements (a different algorithm from
// the participant's Dijkstra), then the nearest live member of svc other than the asker, ties to the lowest apn
static std::string o_net_nearest(const std::string& asker) {
    std::vector<std::string> v; for (auto& [a, m] : T) if (m.live) v.push_back(a);
    const size_t n = v.size(); const double INF = 1e18; std::vector<std::vector<double>> d(n, std::vector<double>(n, INF));
    std::map<std::string, size_t> ix; for (size_t i = 0; i < n; ++i) { ix[v[i]] = i; d[i][i] = 0; }
    for (size_t i = 0; i < n; ++i) for (auto& [nb, c] : T[v[i]].adj) { auto j = ix.find(nb); if (j != ix.end()) d[i][j->second] = std::min(d[i][j->second], double(c)); }
    for (size_t k = 0; k < n; ++k) for (size_t i = 0; i < n; ++i) for (size_t j = 0; j < n; ++j) if (d[i][k] + d[k][j] < d[i][j]) d[i][j] = d[i][k] + d[k][j];
    auto s = ix.find(asker); if (s == ix.end()) return "";
    std::string best; double bd = INF;
    for (size_t j = 0; j < n; ++j) { const std::string& a = v[j]; if (a == asker || !T[a].in_svc || d[s->second][j] >= INF) continue;
        if (d[s->second][j] < bd) { bd = d[s->second][j]; best = a; } }
    return best;
}

// ---- the participant's side: what it reads, and what it computes from what it reads
static std::string D, obs_where;
static std::vector<std::string> apns(const Json& r) { std::vector<std::string> s; for (auto& m : r["members"].a) s.push_back(m["apn"].s); std::sort(s.begin(), s.end()); return s; }
static std::string join_s(const std::vector<std::string>& v) { std::string s; for (auto& x : v) s += (s.empty() ? "" : ",") + x; return s.empty() ? "-" : s; }
static std::atomic<int> refused{0};
static Json ok_or_say(Json r, const std::string& what) {
    if (!r["ok"].b) { ++refused; fprintf(stderr, "E1  REFUSED %s: %s %s\n", what.c_str(), r["error"].s.c_str(), r["why"].s.c_str()); }
    return r; }
static Json resolve_name(daf::Client& c, const std::string& name, const std::string& extra) {
    return ok_or_say(c.call("resolve", "\"daf\":" + q(D) + ",\"name\":" + q(name) + (extra.empty() ? "" : "," + extra)), "resolve " + name + " " + extra); }
static Json resolve(daf::Client& c, const std::string& extra) { return resolve_name(c, "svc", extra); }
// every session that reads the DAF is a member of it: a non-member gets "not found" for everything (Part 2-2 2.6.3)
static std::unique_ptr<daf::Client> enrolled(const std::string& where, const std::string& apn) {
    auto c = std::make_unique<daf::Client>(where);
    Json e = c->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":\"observer\"");
    if (!e["ok"].b) { fprintf(stderr, "enroll %s refused: %s %s\n", apn.c_str(), e["error"].s.c_str(), e["why"].s.c_str()); exit(2); }
    return c; }
// the asking participant: nearest member of svc by shortest-path cost over the topo facts it reads (Dijkstra)
static std::string p_net_nearest(daf::Client& c, const std::string& self) {
    Json topo = resolve_name(c, "topo", "\"rule\":\"all\",\"live\":true");
    Json svc = resolve(c, "\"rule\":\"all\",\"live\":true");
    std::map<std::string, std::map<std::string, double>> g;
    for (auto& m : topo["members"].a) for (auto& [nb, cost] : m["facts"]["adj"].o) g[m["apn"].s][nb] = cost.n;
    std::map<std::string, double> dist; std::set<std::pair<double, std::string>> pq; dist[self] = 0; pq.insert({0, self});
    while (!pq.empty()) { auto [du, u] = *pq.begin(); pq.erase(pq.begin()); if (du > dist[u]) continue;
        auto gu = g.find(u); if (gu == g.end()) continue;                     // a node that publishes no links ends a path
        for (auto& [w, c] : gu->second) {
            const double nd = du + c; auto it = dist.find(w); if (it == dist.end() || nd < it->second) { if (it != dist.end()) pq.erase({it->second, w}); dist[w] = nd; pq.insert({nd, w}); } } }
    std::string best; double bd = 0;
    for (auto& m : svc["members"].a) { const std::string a = m["apn"].s; if (a == self) continue; auto it = dist.find(a); if (it == dist.end()) continue;
        if (best.empty() || it->second < bd || (it->second == bd && a < best)) { best = a; bd = it->second; } }
    return best;
}

struct Sample { double ms; uint64_t out, in; };
static std::vector<Sample> lat_resolve;
static int stale = 0, wrong = 0;

// every rule, checked against the oracle; returns the number of mismatches
static int check_all(daf::Client& obs, daf::Client& asker, const std::string& asker_apn, int ev, const std::string& evname) {
    int miss = 0; std::string detail;
    // [DIAG-E1] each resolve keeps its request, its answer's text and the client's wire counters for that call, so a
    // mismatch can be told apart: the region's answer (a fresh session asks again, with no cache) or the transport's
    std::string last_body, last_text, last_wire;
    auto timed = [&](const std::string& extra) { auto s0 = obs.stats(); const auto t = Clock::now();
        last_body = "{\"fn\":\"resolve\",\"daf\":" + q(D) + ",\"name\":\"svc\"," + extra + "}";
        last_text = obs.raw(last_body); const double m = ms_since(t); Json r = ok_or_say(Json::parse(last_text), "resolve svc " + extra);
        auto s1 = obs.stats(); lat_resolve.push_back({m, s1.bytes_out - s0.bytes_out, s1.bytes_in - s0.bytes_in});
        last_wire = "{\"raw\":" + std::to_string(s1.raw - s0.raw) + ",\"repeat\":" + std::to_string(s1.repeat - s0.repeat) + ",\"same\":" + std::to_string(s1.same - s0.same) +
                    ",\"miss\":" + std::to_string(s1.miss - s0.miss) + ",\"full\":" + std::to_string(s1.full - s0.full) + ",\"diff\":" + std::to_string(s1.diff - s0.diff) +
                    ",\"rdiff\":" + std::to_string(s1.rdiff - s0.rdiff) + ",\"rraw\":" + std::to_string(s1.rraw - s0.rraw) + ",\"coalesced\":" + std::to_string(s1.coalesced - s0.coalesced) + "}";
        if (getenv("E1_TRACE_CALLS")) raw("{\"k\":\"call\",\"ev\":" + std::to_string(ev) + ",\"request\":" + last_body + ",\"wire\":" + last_wire + ",\"answer\":" + last_text + "}");
        return r; };
    auto cmp = [&](const char* rule, const std::string& got, const std::string& want) {
        const bool ok = got == want; if (!ok) { ++miss; detail += std::string(" ") + rule + ":got=" + got + ",want=" + want;
            if (std::string(rule) != "nearest_net") {
                const std::string again = obs.raw(last_body);
                auto fp = enrolled(obs_where, "diag-" + std::to_string(ev) + "-" + rule); const std::string fresh = fp->raw(last_body);
                raw("{\"k\":\"DIAG-E1\",\"ev\":" + std::to_string(ev) + ",\"rule\":" + q(rule) + ",\"request\":" + last_body + ",\"answer\":" + last_text +
                    ",\"wire\":" + last_wire + ",\"same_session_again\":" + again + ",\"fresh_session\":" + fresh + "}"); } }
        raw("{\"k\":\"check\",\"ev\":" + std::to_string(ev) + ",\"event\":" + q(evname) + ",\"rule\":" + q(rule) + ",\"got\":" + q(got) + ",\"want\":" + q(want) + ",\"ok\":" + (ok ? "true" : "false") + "}"); };
    cmp("all", join_s(apns(timed("\"rule\":\"all\""))), join_s(o_all(false)));
    cmp("all_live", join_s(apns(timed("\"rule\":\"all\",\"live\":true"))), join_s(o_all(true)));
    cmp("one_capacity", join_s(apns(timed("\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}"))),
        join_s(o_best(ok_trauma, key_capacity).empty() ? std::vector<std::string>{} : std::vector<std::string>{o_best(ok_trauma, key_capacity)}));
    cmp("capability", join_s(apns(timed("\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"gpu\",\"v\":true},{\"f\":\"cpu\",\"op\":\">=\",\"v\":4}],\"rank\":{\"f\":\"cpu\"}"))),
        join_s(o_best(ok_capable, key_cpu).empty() ? std::vector<std::string>{} : std::vector<std::string>{o_best(ok_capable, key_cpu)}));
    cmp("designated", join_s(apns(timed("\"rule\":\"designated\""))), join_s(o_designated().empty() ? std::vector<std::string>{} : std::vector<std::string>{o_designated()}));
    char nb[160]; snprintf(nb, sizeof nb, "\"rule\":\"nearest\",\"live\":true,\"near\":{\"x\":%.3f,\"y\":%.3f}", PX, PY);
    cmp("nearest_geo", join_s(apns(timed(nb))), join_s(o_best(ok_any, key_geo).empty() ? std::vector<std::string>{} : std::vector<std::string>{o_best(ok_any, key_geo)}));
    const std::string pn = p_net_nearest(asker, asker_apn), on = o_net_nearest(asker_apn);
    cmp("nearest_net", pn.empty() ? "-" : pn, on.empty() ? "-" : on);
    if (miss) { ++stale; printf("    after event %d (%s):%s\n", ev, evname.c_str(), detail.c_str()); }
    return miss;
}

// ---- the held read on the name, throughout
struct Wake { double t; uint64_t rev; std::string one; };
static std::vector<Wake> wakes; static std::mutex wake_mu; static std::atomic<bool> stop_watch{false};
static void watcher(const std::string& where, const std::string& name) {
    auto wp = enrolled(where, "watcher"); daf::Client& w = *wp; uint64_t rev = 0;
    try { Json r = resolve_name(w, name, "\"rule\":\"all\""); rev = uint64_t(r["rev"].n);
        for (;;) { rev = w.watch(D, name, rev); const double t = wall();
            Json o = resolve_name(w, "svc", "\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}");
            std::lock_guard<std::mutex> g(wake_mu); wakes.push_back({t, rev, join_s(apns(o))}); if (stop_watch) return; } }
    catch (const std::exception&) {}                        // the session was shut down: the read ends
}

int main(int argc, char** argv) {
    if (argc < 6) { fprintf(stderr, "usage: e1_select HOST:PORT TAG SEED EVENTS RAW.jsonl\n"); return 2; }
    const std::string where = argv[1], tag = argv[2]; const unsigned seed = unsigned(atoi(argv[3])); const int EVENTS = atoi(argv[4]);
    RAW = fopen(argv[5], "w"); if (!RAW) { perror(argv[5]); return 2; }
    std::mt19937 rng(seed); auto R = [&](int lo, int hi) { return std::uniform_int_distribution<int>(lo, hi)(rng); };
    D = "e1-" + tag + "#1";
    printf("E1 whatevercast selection: host %s, DAF %s, seed %u, %d events\n", where.c_str(), D.c_str(), seed, EVENTS);
    raw("{\"k\":\"run\",\"exp\":\"E1\",\"host\":" + q(where) + ",\"daf\":" + q(D) + ",\"seed\":" + std::to_string(seed) + ",\"events\":" + std::to_string(EVENTS) + ",\"t\":" + std::to_string(wall()) + "}");

    daf::Client admin(where);
    Json cd = admin.call("create_daf", "\"dan\":" + q("e1-" + tag) + ",\"daii\":\"1\",\"owner\":\"e1\"");
    if (!cd["ok"].b) { printf("create_daf refused: %s %s\n", cd["error"].s.c_str(), cd["why"].s.c_str()); return 2; }
    admin.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"coordinator\",\"role\":\"coordinator\"");
    Json d1 = admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"one\",\"lifetime\":\"session\"");
    Json d2 = admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"rule\":\"all\",\"lifetime\":\"session\"");
    Json d3 = admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"lead\",\"rule\":\"designated\",\"lifetime\":\"session\"");
    expect("names defined: svc (one), topo (all), lead (designated)", d1["ok"].b && d2["ok"].b && d3["ok"].b);

    // ---- 12 members, each on its own session; a connected random graph of their links (each end measures its own)
    const int N = 12; std::vector<std::string> A; for (int i = 0; i < N; ++i) { char b[8]; snprintf(b, sizeof b, "m%02d", i); A.push_back(b); }
    for (int i = 1; i < N; ++i) { const int j = R(0, i - 1), c = R(1, 9); T[A[i]].adj[A[j]] = c; T[A[j]].adj[A[i]] = c; }
    for (int e = 0; e < N / 2; ++e) { const int i = R(0, N - 1), j = R(0, N - 1); if (i == j || T[A[i]].adj.count(A[j])) continue; const int c = R(1, 9); T[A[i]].adj[A[j]] = c; T[A[j]].adj[A[i]] = c; }
    std::map<std::string, std::unique_ptr<daf::Client>> S;
    auto bring_up = [&](const std::string& a) {
        // [DIAG-E1-REVIVE] each step of a member's arrival timed: session, enroll, each join (wall seconds)
        const double t0 = wall(); S[a] = std::make_unique<daf::Client>(where); const double t1 = wall(); g_session_up = t1; g_session_ms.push_back((t1 - t0) * 1000); Mem& m = T[a];
        Json e = S[a]->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(a)); const double t2 = wall();
        if (!e["ok"].b) { printf("enroll %s refused: %s %s\n", a.c_str(), e["error"].s.c_str(), e["why"].s.c_str()); exit(2); }
        m.live = true;
        S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + topo_facts(m));
        S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"lead\",\"facts\":{}");
        const double t3 = wall();
        if (m.in_svc) S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(m) + ",\"ref\":" + q("ref-" + a));
        const double t4 = wall();
        raw("{\"k\":\"DIAG-E1-REVIVE\",\"apn\":" + q(a) + ",\"in_svc\":" + (m.in_svc ? "true" : "false") + ",\"t0\":" + std::to_string(t0) +
            ",\"session_ms\":" + std::to_string((t1 - t0) * 1000) + ",\"enroll_ms\":" + std::to_string((t2 - t1) * 1000) +
            ",\"topo_lead_ms\":" + std::to_string((t3 - t2) * 1000) + ",\"svc_join_ms\":" + std::to_string((t4 - t3) * 1000) + "}");
    };
    for (auto& a : A) { Mem& m = T[a]; m.in_svc = R(0, 3) != 0; m.capacity = R(0, 20); m.trauma = R(0, 1); m.cpu = R(1, 8); m.gpu = R(0, 1);
                        m.x = R(0, 1000) / 10.0; m.y = R(0, 1000) / 10.0; bring_up(a); }
    const std::string ASKER = A[0];                             // the participant that computes network-nearest; never killed
    designated = A[R(1, N - 1)]; admin.call("designate", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"apn\":" + q(designated));
    obs_where = where; auto obsp = enrolled(where, "observer"); daf::Client& obs = *obsp;
    PX = R(0, 1000) / 10.0; PY = R(0, 1000) / 10.0;
    expect("initial state: every rule matches the oracle", check_all(obs, *S[ASKER], ASKER, 0, "initial") == 0);

    std::thread W(watcher, where, "svc");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // ---- serial changes during operation; each is complete when its call returns
    struct EvRec { int ev; std::string kind; double t_issue, t_done; uint64_t rev; bool touches_svc; };
    std::vector<EvRec> evs; std::vector<double> depart_ms; int depart_missed = 0;
    for (int ev = 1; ev <= EVENTS; ++ev) {
        std::vector<std::string> livev; for (auto& [a, m] : T) if (m.live) livev.push_back(a);
        std::vector<std::string> deadv; for (auto& [a, m] : T) if (!m.live) deadv.push_back(a);
        const std::string a = livev[size_t(R(0, int(livev.size()) - 1))]; Mem& m = T[a];
        int k = R(0, 99); std::string kind; bool svc_touch = false;
        double t_issue = wall();   // change->wake runs from here: the watcher can be woken before the writer's reply lands
        if (k < 30) { kind = "capability"; if (!m.in_svc) { m.in_svc = true; kind = "join"; }
            m.capacity = R(0, 20); m.trauma = R(0, 1); m.cpu = R(1, 8); m.gpu = R(0, 1);
            S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(m) + ",\"ref\":" + q("ref-" + a)); svc_touch = true; }
        else if (k < 40) { kind = "move"; m.x = R(0, 1000) / 10.0; m.y = R(0, 1000) / 10.0; if (m.in_svc) { S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(m) + ",\"ref\":" + q("ref-" + a)); svc_touch = true; } }
        else if (k < 52) { if (m.in_svc) { kind = "unjoin"; m.in_svc = false; S[a]->call("unjoin", "\"daf\":" + q(D) + ",\"name\":\"svc\""); }
                           else { kind = "join"; m.in_svc = true; S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(m) + ",\"ref\":" + q("ref-" + a)); }
                           svc_touch = true; }
        else if (k < 70) { kind = "link"; if (m.adj.empty()) continue; auto it = m.adj.begin(); std::advance(it, R(0, int(m.adj.size()) - 1)); const std::string b = it->first;
            const int c = R(1, 9); m.adj[b] = c; T[b].adj[a] = c;
            S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + topo_facts(m));
            if (T[b].live) S[b]->call("join", "\"daf\":" + q(D) + ",\"name\":\"topo\",\"facts\":" + topo_facts(T[b])); }
        else if (k < 78) { kind = "designate"; designated = A[size_t(R(0, N - 1))]; admin.call("designate", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"apn\":" + q(designated)); }
        else if (k < 86 && a != ASKER) { kind = "kill"; const bool was_svc = m.in_svc;
            // the session ends; the member is gone when the region hears the close. Observed by a held read, not polled.
            // the follower is a member on its own session, made before the kill; the lag runs from the shutdown to the
            // answer that no longer lists the member
            auto wdp = enrolled(where, "depart-" + std::to_string(ev)); daf::Client& watch_d = *wdp;
            uint64_t rev = uint64_t(resolve_name(watch_d, "members", "\"rule\":\"all\"")["rev"].n);
            std::atomic<bool> gone{false}; std::atomic<double> lag{0};
            const auto t0 = Clock::now();
            S[a]->shutdown(); S.erase(a);
            std::thread follow([&] { try { for (;;) {
                Json r = resolve_name(watch_d, "members", "\"rule\":\"all\"");
                auto v = apns(r); if (std::find(v.begin(), v.end(), a) == v.end()) { lag = ms_since(t0); gone = true; return; }
                rev = std::max(rev, uint64_t(r["rev"].n)); watch_d.wait(D, "name", "members", rev); } } catch (...) {} });
            for (int i = 0; i < 500 && !gone; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(2));
            if (!gone) watch_d.shutdown();                       // not gone within a second: end the read, count it missed
            follow.join();
            wdp.reset();                                         // its session ends: it leaves
            if (gone) depart_ms.push_back(lag.load()); else ++depart_missed;
            m.live = false; m.in_svc = false; (void)was_svc; svc_touch = true; }   // a session name's entry ends with the session
        else if (!deadv.empty()) { kind = "revive"; const std::string d = deadv[size_t(R(0, int(deadv.size()) - 1))]; T[d].in_svc = R(0, 1); bring_up(d); svc_touch = true; }
        else { kind = "move"; m.x = R(0, 1000) / 10.0; m.y = R(0, 1000) / 10.0; if (m.in_svc) { S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(m) + ",\"ref\":" + q("ref-" + a)); svc_touch = true; } }
        if (kind == "revive") t_issue = g_session_up;   // a revive's change is its enroll; session setup is measured apart (DIAG-E1-REVIVE)
        const double t_done = wall();
        PX = R(0, 1000) / 10.0; PY = R(0, 1000) / 10.0;
        Json rv = obs.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"rule\":\"all\"");
        evs.push_back({ev, kind, t_issue, t_done, uint64_t(rv["rev"].n), svc_touch});
        raw("{\"k\":\"event\",\"ev\":" + std::to_string(ev) + ",\"kind\":" + q(kind) + ",\"apn\":" + q(a) + ",\"t_issue\":" + std::to_string(t_issue) + ",\"t\":" + std::to_string(t_done) + ",\"rev\":" + std::to_string(uint64_t(rv["rev"].n)) + "}");
        wrong += check_all(obs, *S[ASKER], ASKER, ev, kind);
    }
    expect("serial changes: every rule matches the oracle after every change (" + std::to_string(EVENTS) + " changes x 7 rules)", stale == 0, std::to_string(wrong) + " mismatches in " + std::to_string(stale) + " events");
    std::sort(depart_ms.begin(), depart_ms.end());
    expect("a killed member leaves the name (seen by a held read)", depart_missed == 0, std::to_string(depart_ms.size()) + " kills" + (depart_ms.empty() ? std::string() : ", lag " + std::to_string(depart_ms.front()).substr(0, 5) + ".." + std::to_string(depart_ms.back()).substr(0, 5) + " ms"));

    // ---- the held read: every change to svc's set is followed by a wake at or past its rev, and the last wake is right
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    std::vector<double> wake_ms; int missed = 0;
    { std::lock_guard<std::mutex> g(wake_mu);
      for (auto& e : evs) { if (!e.touches_svc) continue; bool hit = false;
          for (auto& w : wakes) if (w.rev >= e.rev) { if (e.kind != "kill") wake_ms.push_back((w.t - e.t_issue) * 1000); hit = true; break; }   // a kill has its own lag (depart)
          if (!hit) ++missed; }
      const std::string want = o_best(ok_trauma, key_capacity).empty() ? "-" : o_best(ok_trauma, key_capacity);
      expect("held read on svc: a wake at or past every change's rev", missed == 0, std::to_string(wakes.size()) + " wakes for " + std::to_string(evs.size()) + " changes, missed " + std::to_string(missed));
      expect("held read on svc: the answer after the last wake is the current one", !wakes.empty() && wakes.back().one == want, (wakes.empty() ? std::string("none") : wakes.back().one) + " vs " + want);
      for (auto& w : wakes) raw("{\"k\":\"wake\",\"t\":" + std::to_string(w.t) + ",\"rev\":" + std::to_string(w.rev) + ",\"one\":" + q(w.one) + "}"); }

    // ---- a change of designation is a change of the name's resolution: does a held read on the name hear it?
    { auto wlp = enrolled(where, "lead-watcher"); daf::Client& wl = *wlp; Json r = resolve_name(wl, "lead", "\"rule\":\"designated\""); const uint64_t rev0 = uint64_t(r["rev"].n);
      std::atomic<bool> woke{false}; std::thread t([&] { try { wl.wait(D, "name", "lead", rev0); woke = true; } catch (...) {} });
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      const std::string cur = r["members"].a.empty() ? "" : r["members"].a[0]["apn"].s;
      std::string to; for (auto& [a, m] : T) if (m.live && a != cur) { to = a; break; }
      admin.call("designate", "\"daf\":" + q(D) + ",\"name\":\"lead\",\"apn\":" + q(to));
      Json after = resolve_name(admin, "lead", "");
      for (int i = 0; i < 100 && !woke; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(10));
      const bool resolved_new = !after["members"].a.empty() && after["members"].a[0]["apn"].s == to;
      expect("designation changed: resolution follows it at once", resolved_new, to);
      expect("designation changed: a held read on the name wakes", woke.load(), woke ? "woke" : "no wake within 1 s; resolve rev " + std::to_string(rev0) + " -> " + std::to_string(uint64_t(after["rev"].n)));
      raw("{\"k\":\"designation_wake\",\"woke\":" + std::string(woke ? "true" : "false") + ",\"rev_before\":" + std::to_string(rev0) + ",\"rev_after\":" + std::to_string(uint64_t(after["rev"].n)) + "}");
      wl.shutdown(); t.join(); }

    // ---- concurrent: four writers change their own members' capabilities while two readers resolve; nothing is lost
    { std::vector<std::string> mine; for (auto& [a, m] : T) if (m.live && a != ASKER) mine.push_back(a);
      std::atomic<bool> go{true}; std::atomic<int> ops{0}; std::vector<std::thread> ws; std::mutex tmu;
      const int WR = 4;
      for (int w = 0; w < WR; ++w) ws.emplace_back([&, w] { std::mt19937 r2(seed * 31 + unsigned(w));
          while (go) { for (size_t i = size_t(w); i < mine.size(); i += WR) { const std::string& a = mine[i];
              Mem nm; { std::lock_guard<std::mutex> g(tmu); nm = T[a]; }
              nm.in_svc = true; nm.capacity = std::uniform_int_distribution<int>(0, 20)(r2); nm.trauma = r2() & 1; nm.cpu = std::uniform_int_distribution<int>(1, 8)(r2); nm.gpu = r2() & 1;
              S[a]->call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":" + svc_facts(nm) + ",\"ref\":" + q("ref-" + a));
              { std::lock_guard<std::mutex> g(tmu); T[a] = nm; } ++ops; } } });
      std::atomic<int> reads{0}, inconsistent{0};
      std::vector<std::thread> rs;
      for (int r = 0; r < 2; ++r) rs.emplace_back([&, r] { auto cp = enrolled(where, "reader-" + std::to_string(r)); daf::Client& c = *cp;
          while (go) { Json o = resolve_name(c, "svc", "\"rule\":\"one\",\"live\":true,\"filter\":[{\"f\":\"trauma\",\"v\":true},{\"f\":\"capacity\",\"op\":\">\",\"v\":0}],\"rank\":{\"f\":\"capacity\"}");
              ++reads; for (auto& m : o["members"].a) if (!(m["facts"]["trauma"].b && m["facts"]["capacity"].n > 0)) ++inconsistent; } });
      std::this_thread::sleep_for(std::chrono::seconds(3)); go = false; for (auto& t : ws) t.join(); for (auto& t : rs) t.join();
      const int miss = check_all(obs, *S[ASKER], ASKER, EVENTS + 1, "after-concurrent");
      expect("concurrent writers: final state matches the last write of every member (no lost operation)", miss == 0, std::to_string(ops.load()) + " writes, " + std::to_string(reads.load()) + " resolves in 3 s");
      expect("concurrent readers: every selected member satisfies the rule on the facts returned with it", inconsistent == 0, std::to_string(inconsistent.load()) + " violations");
      raw("{\"k\":\"concurrent\",\"writes\":" + std::to_string(ops.load()) + ",\"resolves\":" + std::to_string(reads.load()) + ",\"violations\":" + std::to_string(inconsistent.load()) + ",\"final_mismatch\":" + std::to_string(miss) + "}"); }

    stop_watch = true; admin.call("join", "\"daf\":" + q(D) + ",\"name\":\"svc\",\"facts\":{}");   // one more change wakes the watcher to stop
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); W.detach();

    // ---- speed, after correctness
    auto pct = [](std::vector<double> v, double p) { if (v.empty()) return 0.0; std::sort(v.begin(), v.end()); return v[std::min(v.size() - 1, size_t(p * double(v.size())))]; };
    std::vector<double> lr; uint64_t bo = 0, bi = 0; for (auto& s : lat_resolve) { lr.push_back(s.ms); bo += s.out; bi += s.in; }
    { std::vector<double> v = g_session_ms; size_t slow = 0; for (double x : v) if (x > 10) ++slow;
      printf("E1  session setup (3 sockets + HELLOs): p50 %.2f ms max %.2f ms; %zu of %zu over 10 ms\n", pct(v, .5), pct(v, 1.0), slow, v.size()); }
    printf("E1  speed: resolve p50 %.3f ms p99 %.3f ms (n=%zu); bytes/resolve out %.0f in %.0f; change->wake p50 %.2f ms p99 %.2f ms (n=%zu); depart p50 %.1f ms\n",
           pct(lr, .5), pct(lr, .99), lr.size(), lr.empty() ? 0.0 : double(bo) / double(lr.size()), lr.empty() ? 0.0 : double(bi) / double(lr.size()), pct(wake_ms, .5), pct(wake_ms, .99), wake_ms.size(), pct(depart_ms, .5));
    raw("{\"k\":\"speed\",\"resolve_p50_ms\":" + std::to_string(pct(lr, .5)) + ",\"resolve_p99_ms\":" + std::to_string(pct(lr, .99)) + ",\"n\":" + std::to_string(lr.size()) +
        ",\"bytes_out_per_resolve\":" + std::to_string(lr.empty() ? 0.0 : double(bo) / double(lr.size())) + ",\"bytes_in_per_resolve\":" + std::to_string(lr.empty() ? 0.0 : double(bi) / double(lr.size())) +
        ",\"wake_p50_ms\":" + std::to_string(pct(wake_ms, .5)) + ",\"wake_p99_ms\":" + std::to_string(pct(wake_ms, .99)) + ",\"depart_p50_ms\":" + std::to_string(pct(depart_ms, .5)) + "}");
    expect("no resolve was refused", refused == 0, std::to_string(refused.load()) + " refused");
    printf("E1  RESULT %s (%d of %d checks)\n", bad ? "FAIL" : "PASS", checks - bad, checks);
    fclose(RAW); fflush(stdout); _exit(bad ? 1 : 0);
}
