// bench_discovery.cpp -- [DAF_VS_FDF_DISCOVERY_BENCH_V1] "who exists", measured side by side on ONE rina-host:
//   FDF  the existing FD&F discovery: a machine says `node` (its liveness) and `announce`s its facts (op=rina); readers
//        list `participants`; a watcher registers `watch_participants` and holds `wait` on its input space (op=rina_block)
//   DAF  the DAF model: a member `enroll`s and `join`s the whatevercast name "nodes" (op=daf); readers `resolve` it with
//        live members only; a watcher holds `wait` on the name
// Every member is its own FNW1 session, in both. Same N, same facts, same kills, same changes.
// usage: bench_discovery HOST:PORT N KILLS
//   join     per member: FDF node+announce, DAF enroll+join                         (ms, p50/p99, and the whole N)
//   read     1000 reads of who exists, at N members                                (ms, p50/p99)
//   gone     a member's session is closed -> a watcher's next read no longer lists it (ms, p50/p99)
//   change   a member's facts change -> a watcher's next read shows the new value    (ms, p50/p99)
#include "daf_client.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <functional>
#include <memory>
#include <string>
#include <vector>
using daf::Json; using daf::q;
using Clock = std::chrono::steady_clock;
static double ms(Clock::time_point a) { return std::chrono::duration<double, std::milli>(Clock::now() - a).count(); }
static double pct(std::vector<double> v, double p) { if (v.empty()) return -1; std::sort(v.begin(), v.end()); return v[std::min(v.size() - 1, size_t(p * double(v.size())))]; }
static std::unique_ptr<frogram::Session> session(const std::string& where) {
    const size_t c = where.rfind(':'); return std::make_unique<frogram::Session>(where.substr(0, c), atoi(where.c_str() + c + 1), daf::api()); }
static Json rina(frogram::Session& s, const std::string& body) { return Json::parse(s.call_text("POST", daf::api() + "?op=rina", body)); }
static std::string rina_wait(frogram::Session& s, const std::string& proc, uint64_t after) {
    return s.call_text("POST", daf::api() + "?op=rina_block", "{\"op\":\"wait\",\"proc\":" + q(proc) + ",\"after\":" + std::to_string((unsigned long long)after) + "}", 30.0); }
static uint64_t last_seq(const std::string& text, uint64_t after) {          // "E\n<seq>\t<event>\n..."
    uint64_t m = after; size_t p = text.find('\n'); while (p != std::string::npos && p + 1 < text.size()) {
        const uint64_t n = strtoull(text.c_str() + p + 1, nullptr, 10); if (n > m) m = n; p = text.find('\n', p + 1); } return m; }
static std::string facts(int i, int cap) {
    char b[160]; snprintf(b, sizeof b, "{\"subnet\":\"10.%d.%d.0/24\",\"ip\":\"10.%d.%d.1\",\"capacity\":%d}", 60 + i / 250, i % 250, 60 + i / 250, i % 250, cap); return b; }
static void row(const char* what, const std::vector<double>& f, const std::vector<double>& d) {
    printf("  %-34s FDF p50 %8.3f  p99 %8.3f  |  DAF p50 %8.3f  p99 %8.3f   (n=%zu/%zu)\n", what, pct(f, .5), pct(f, .99), pct(d, .5), pct(d, .99), f.size(), d.size()); fflush(stdout); }

int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: bench_discovery HOST:PORT N KILLS\n"); return 2; }
    const std::string where = argv[1]; const int N = atoi(argv[2]), K = atoi(argv[3]);
    const std::string tag = std::to_string(getpid()), D = "bench-" + tag + "#1";
    printf("who exists, N=%d members, %d kills, %d changes -- times in ms\n", N, K, K);

    // ---- join
    std::vector<std::unique_ptr<frogram::Session>> fm; std::vector<std::unique_ptr<daf::Client>> dm; std::vector<double> jf, jd;
    for (int i = 0; i < N; ++i) {
        auto s = session(where); const std::string ns = "bench" + tag + "-" + std::to_string(i);
        auto t = Clock::now();
        Json a = rina(*s, "{\"op\":\"node\",\"ns\":" + q(ns) + ",\"devs\":\"\"}");
        Json b = rina(*s, "{\"op\":\"announce\",\"ns\":" + q(ns) + ",\"name\":" + q(ns) + ",\"cap\":\"{}\",\"ips\":[],\"facts\":" + q(facts(i, 5)) + "}");
        jf.push_back(ms(t));
        if (!a["ok"].b || !b["ok"].b) { printf("FDF join %d refused: %s %s / %s %s\n", i, a["error"].s.c_str(), a["why"].s.c_str(), b["error"].s.c_str(), b["why"].s.c_str()); return 1; }
        fm.push_back(std::move(s)); }
    daf::Client admin(where);
    admin.call("create_daf", "\"dan\":" + q("bench-" + tag) + ",\"daii\":\"1\",\"owner\":\"bench\"");
    admin.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"admin\",\"role\":\"admin\"");
    admin.call("define_name", "\"daf\":" + q(D) + ",\"name\":\"nodes\",\"rule\":\"all\"");
    for (int i = 0; i < N; ++i) {
        auto c = std::make_unique<daf::Client>(where); const std::string apn = "n" + std::to_string(i);
        auto t = Clock::now();
        Json a = c->call("enroll", "\"daf\":" + q(D) + ",\"apn\":" + q(apn) + ",\"role\":\"node\",\"facts\":" + facts(i, 5));
        Json b = c->call("join", "\"daf\":" + q(D) + ",\"name\":\"nodes\",\"facts\":" + facts(i, 5));
        jd.push_back(ms(t));
        if (!a["ok"].b || !b["ok"].b) { printf("DAF join %d refused: %s / %s\n", i, a["why"].s.c_str(), b["why"].s.c_str()); return 1; }
        dm.push_back(std::move(c)); }
    double sf = 0, sd = 0; for (double x : jf) sf += x; for (double x : jd) sd += x;
    row("join, per member", jf, jd);
    printf("  %-34s FDF %8.1f ms total            |  DAF %8.1f ms total\n", "join, all N", sf, sd);

    // ---- read
    auto rf = session(where); daf::Client rd(where); rd.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"reader\",\"role\":\"observer\"");
    auto fdf_live = [&](const Json& p, const std::string& ns, int* count) {
        bool there = false; int n = 0;
        for (auto& x : p["participants"].a) {
            if (!x["alive"].b || x["ns"].s.compare(0, 5 + tag.size(), "bench" + tag) != 0) { continue; }
            ++n;
            if (x["ns"].s == ns) { there = true; }
        }
        if (count) { *count = n; }
        return there; };
    std::vector<double> rf_t, rd_t; int nf = 0, nd = 0;
    for (int i = 0; i < 1000; ++i) { auto t = Clock::now(); Json p = rina(*rf, "{\"op\":\"participants\"}"); rf_t.push_back(ms(t)); fdf_live(p, "", &nf); }
    for (int i = 0; i < 1000; ++i) { auto t = Clock::now(); Json r = rd.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"nodes\",\"live\":true"); rd_t.push_back(ms(t)); nd = int(r["members"].a.size()); }
    row("read who exists (1000 reads)", rf_t, rd_t);
    printf("  %-34s FDF %d alive listed              |  DAF %d live listed\n", "  (both see all N?)", nf, nd);

    // ---- watchers: FDF a proc's input space; DAF a held read on the name, each on a session of its own
    auto fw = session(where); rina(*fw, "{\"op\":\"watch_participants\",\"proc\":" + q("bw" + tag) + "}");
    uint64_t fafter = 0;
    daf::Client dwc(where); Json dwe = dwc.call("enroll", "\"daf\":" + q(D) + ",\"apn\":\"watcher\",\"role\":\"observer\"");
    daf::Client dw(where); dw.call("connect", "\"daf\":" + q(D) + ",\"ae_name\":\"watch\",\"objects\":[\"*\"],\"apn\":\"watcher\",\"token\":" + q(dwe["token"].s));
    uint64_t drev = uint64_t(rd.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"nodes\"")["rev"].n);
    // until the watcher's read shows `done`: wait, then read; again if it does not yet
    auto fdf_until = [&](const std::function<bool(const Json&)>& done) { for (;;) { fafter = last_seq(rina_wait(*fw, "bw" + tag, fafter), fafter);
        if (done(rina(*rf, "{\"op\":\"participants\"}"))) return; } };
    auto daf_until = [&](const std::function<bool(const Json&)>& done) { for (;;) { Json w = dw.wait(D, "name", "nodes", drev); drev = uint64_t(w["rev"].n);
        if (done(rd.call("resolve", "\"daf\":" + q(D) + ",\"name\":\"nodes\",\"live\":true"))) return; } };

    // ---- change
    std::vector<double> cf, cd;
    for (int k = 0; k < K; ++k) {
        const int i = k; const std::string ns = "bench" + tag + "-" + std::to_string(i), apn = "n" + std::to_string(i); const int cap = 100 + k;
        auto t = Clock::now();
        rina(*fm[size_t(i)], "{\"op\":\"announce\",\"ns\":" + q(ns) + ",\"name\":" + q(ns) + ",\"cap\":\"{}\",\"ips\":[],\"facts\":" + q(facts(i, cap)) + "}");
        fdf_until([&](const Json& p) { for (auto& x : p["participants"].a) if (x["ns"].s == ns) return x["facts"]["capacity"].n == cap; return false; });
        cf.push_back(ms(t));
        t = Clock::now();
        dm[size_t(i)]->call("join", "\"daf\":" + q(D) + ",\"name\":\"nodes\",\"facts\":" + facts(i, cap));
        daf_until([&](const Json& r) { for (auto& x : r["members"].a) if (x["apn"].s == apn) return x["facts"]["capacity"].n == cap; return false; });
        cd.push_back(ms(t)); }
    row("change -> watcher's read shows it", cf, cd);

    // ---- gone
    std::vector<double> gf, gd;
    for (int k = 0; k < K; ++k) {
        const int i = N - 1 - k; const std::string ns = "bench" + tag + "-" + std::to_string(i), apn = "n" + std::to_string(i);
        auto t = Clock::now(); fm[size_t(i)].reset();
        fdf_until([&](const Json& p) { return !fdf_live(p, ns, nullptr); });
        gf.push_back(ms(t));
        t = Clock::now(); dm[size_t(i)].reset();
        daf_until([&](const Json& r) { for (auto& x : r["members"].a) if (x["apn"].s == apn) return false; return true; });
        gd.push_back(ms(t)); }
    row("session closed -> watcher sees gone", gf, gd);
    return 0;
}
