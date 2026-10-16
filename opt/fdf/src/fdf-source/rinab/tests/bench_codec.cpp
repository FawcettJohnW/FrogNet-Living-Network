// bench_codec.cpp -- [BENCH_CODEC_V1] where a read's time goes in the semantic message cache, with no network and no
// region: a client engine and a server engine wired in one process, the server's answer a DAF `resolve` of N members
// (the shape bench_discovery reads), one member's facts changed between reads. Times each stage of the server's
// encode and the client's decode, and the whole read, against the same answer sent RAW.
//   usage: bench_codec N READS
#include "fnwp_engine.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <algorithm>
using Clock = std::chrono::steady_clock;
static double ms(Clock::time_point t) { return std::chrono::duration<double, std::milli>(Clock::now() - t).count(); }
static double p50(std::vector<double> v) { std::sort(v.begin(), v.end()); return v.empty() ? 0 : v[v.size() / 2]; }
static std::string answer(int n, int changed, int cap) {
    std::string out = "{\"ok\":true,\"name\":\"nodes\",\"rule\":\"all\",\"rev\":" + std::to_string(1000 + cap) + ",\"members\":[";
    for (int i = 0; i < n; ++i) { char b[256];
        snprintf(b, sizeof b, "%s{\"apn\":\"n%d\",\"ref\":\"r%d\",\"live\":true,\"facts\":{\"capacity\":%d,\"ip\":\"10.%d.%d.1\",\"subnet\":\"10.%d.%d.0/24\"}}",
                 i ? "," : "", i, i, i == changed ? cap : 5, 60 + i / 250, i % 250, 60 + i / 250, i % 250);
        out += b; }
    return out + "]}";
}
int main(int argc, char** argv) {
    if (argc != 3) { fprintf(stderr, "usage: bench_codec N READS\n"); return 2; }
    const int N = atoi(argv[1]), R = atoi(argv[2]);
    const std::string api = "/FawcettInnovations.RINA.ram_interface.php", path = api + "?op=daf";
    const std::string req = "{\"fn\":\"resolve\",\"daf\":\"bench#1\",\"name\":\"nodes\",\"live\":true}";
    int k = 0; std::string cur = answer(N, 0, 5);
    fnwp::ServerEngine srv("secret-0123456789", api, [&](const std::string&, const std::string&, const std::string&) { return fnwp::Answer{200, cur}; });
    double srv_ms = 0;
    fnwp::ClientEngine cli("h:1", "o", api, [&](const std::string& f) {
        auto t = Clock::now(); auto w = srv.begin(f); std::string r = w.frame.empty() ? srv.finish(w, fnwp::Answer{200, cur}) : w.frame; srv_ms = ms(t); return r; });
    // the whole read through the engines: server (decode + encode) and client (prepare + apply)
    std::vector<double> whole, server, bytes; std::string kinds;
    for (int i = 0; i < R; ++i) {
        cur = answer(N, (k++ * 37) % N, 6 + i);
        auto t = Clock::now(); auto st0 = cli.stats(); fnwp::Answer a = cli.request("POST", path, req); const double w = ms(t); auto st1 = cli.stats();
        if (a.body != cur && !semtpl::python_eq(*semtpl::detail::json_try(a.body), *semtpl::detail::json_try(cur))) { fprintf(stderr, "read %d: the answer came back different\n sent %.300s\n got  %.300s\n", i, cur.c_str(), a.body.c_str()); return 1; }
        if (i >= 2) { whole.push_back(w); server.push_back(srv_ms); bytes.push_back(double(st1.bytes_in - st0.bytes_in)); }
        kinds += st1.rdiff > st0.rdiff ? 'D' : st1.rraw > st0.rraw ? 'R' : st1.same > st0.same ? 'S' : '?';
    }
    // the server's encode, stage by stage, on the learned template
    uint64_t op = 0; auto tp = fnwp::detail::learn(api, "POST", path, req, answer(N, 0, 5), op);
    if (!tp) { fprintf(stderr, "no template learned for the answer\n"); return 1; }
    std::vector<double> ex, probe, enc, dec; int bytes_differ = 0; semcodec::Dict ref;
    { auto f0 = semtpl::extract(tp->resp_frag, answer(N, 0, 5), true); for (auto& kv : *f0) ref.set(kv.first, kv.second); }
    for (int i = 0; i < R; ++i) {
        const std::string body = answer(N, (i * 37) % N, 7 + i);
        auto t = Clock::now(); auto dyn = semtpl::extract(tp->resp_frag, body, true); ex.push_back(ms(t));
        t = Clock::now(); { pyv::List vals; for (auto& kv : *dyn) vals.push_back(kv.second);
            std::string rb = semtpl::rebuild_reply(tp->resp_frag, vals); bool same = rb == body; if (!same) ++bytes_differ;
            if (!same) { auto x = semtpl::detail::json_try(body), y = semtpl::detail::json_try(rb); same = x && y && semtpl::python_eq(*x, *y); }
            if (!same) { fprintf(stderr, "probe: the template does not hold the answer\n"); return 1; } } probe.push_back(ms(t));
        t = Clock::now(); auto e = semcodec::encode_reply_diff(op, *dyn, &ref, true); enc.push_back(ms(t));
        t = Clock::now(); auto ap = fnwp::apply_resp_diff(e.bytes, tp->resp_frag, &ref); dec.push_back(ms(t));
        if (ap.body != body) { pyv::Obj x = *semtpl::detail::json_try(ap.body), y = *semtpl::detail::json_try(body); if (!semtpl::python_eq(x, y)) { fprintf(stderr, "decode differs\n"); return 1; } }
        ref = e.reference;
    }
    printf("N=%d members, answer %zu B, %d reads (kinds %s)\n", N, cur.size(), R, kinds.substr(0, 12).c_str());
    printf("  whole read through both engines   p50 %8.3f ms   (server part %8.3f)   bytes in %8.0f\n", p50(whole), p50(server), p50(bytes));
    printf("  server: extract (parse answer)    p50 %8.3f ms\n", p50(ex));
    printf("  server: probe (rebuild + compare) p50 %8.3f ms   (rebuilt bytes differed %d/%d: compared by parsing both)\n", p50(probe), bytes_differ, R);
    printf("  server: diff encode               p50 %8.3f ms\n", p50(enc));
    printf("  client: decode + rebuild          p50 %8.3f ms\n", p50(dec));
}
