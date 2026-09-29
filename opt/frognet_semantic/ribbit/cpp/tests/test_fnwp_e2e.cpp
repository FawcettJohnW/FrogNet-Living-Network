// test_fnwp_e2e: the C++ FNWP client engine against the C++ FNWP server engine, the RAM op being ram_server.cpp's own
// api() (compiled in). Every answer the client returns must equal, byte for byte, the answer api() gives directly for the
// same request at that moment. Workload: writes, one-member rewrites, new shapes, removes, repeated reads. Then the storm
// case: 500 cells, one member of one cell rewritten, the same read 200 times -- bytes against the reduced wire.
#define main ram_server_main
#include "../src/ram_host.cpp"
#include "test_host.hpp"
#undef main
#include "fnwp_engine.hpp"
#include <random>
static int fails = 0;
// [INSTANCE_REFERENCES_V1] a read answered RAW is justified only when that location has no previous answer, or its answer
// changed shape against that same location's previous answer (the RAM-answer handler cannot hold it).
static bool genuine_shape_change(const std::map<std::string, std::string>& prev, const std::string& q, const std::string& cur) {
    auto it = prev.find(q); if (it == prev.end()) return true;
    return !ramrows::extract(cur, ramrows::learn(it->second)).has_value();
}
static void check(bool ok, const std::string& w) { std::cout << (ok ? "PASS " : "FAIL ") << w << "\n"; if (!ok) ++fails; }
static fnwp::Answer direct(const std::string& m, const std::string& p, const std::string& b) { Reply r; api(m, p, b, false, r); return {r.status, r.body}; }
int main(int argc, char** argv) {
    unsigned seed = argc > 1 ? unsigned(std::stoul(argv[1])) : 1; std::mt19937 rng(seed);
    // [RAM_INTERFACE_IS_THE_VENDORS_V1] the RAM interface endpoint, as the region's deployment names it (argv[2])
    if (argc < 3) { std::cerr << "usage: test_fnwp_e2e SEED ENDPOINT   (e.g. /ram.php, /Vendor.Product.ram_interface.php)\n"; return 2; }
    const std::string API = argv[2]; static TestRam host(API); attach(host);
    std::cout << "endpoint " << API << "\n";
    fnwp::ServerEngine srv(std::string(32, 'k'), API, direct);
    fnwp::ClientEngine cli("10.9.9.9", "10.1.1.1", API, [&](const std::string& f) { return srv.handle(f); });
    auto bagtxt = [&](int shape, int v) {
        std::string b = "{\"n\":" + std::to_string(v) + ",\"s\":\"x" + std::to_string(v % 7) + "\"";
        if (shape % 3 == 1) b += ",\"nested\":{\"a\":" + std::to_string(v % 5) + ",\"t\":\"é\"}";
        if (shape % 3 == 2) b += ",\"list\":[1,\"q\",2.5],\"f\":" + std::to_string(v) + ".25";
        return b + "}"; };
    std::vector<int> shape(24); for (auto& s : shape) s = int(rng() % 3);
    std::vector<std::string> ids(24);
    const std::vector<std::string> reads = {API + "?op=read&service=e2e&variable=v0", API + "?op=read&service=e2e&variable=v1",
                                            API + "?op=read&service=e2e", API + "?op=read&service=e2e&variable=v2&instance=i2"};
    int answers = 0, mism = 0, read_raw = 0, read_genuine = 0, read_same_evicted = 0; std::map<std::string, std::string> prev_answer;
    auto via = [&](const std::string& m, const std::string& p, const std::string& b) {
        try { fnwp::Answer x = cli.request(m, p, b); ++answers; return x; }
        catch (const std::exception& e) { std::cout << "EXCEPTION on " << m << " " << p << " body=" << b.substr(0, 80) << ": " << e.what() << std::endl; throw; } };
    for (int step = 0; step < 400; ++step) {
        int a = int(rng() % 24); std::string var = "v" + std::to_string(a % 3), inst = "i" + std::to_string(a);
        int r = int(rng() % 100);
        if (r < 70 || ids[a].empty()) {
            if (r >= 60 && r < 70) shape[a] = int(rng() % 3);
            std::string body = "{\"service\":\"e2e\",\"variable\":\"" + var + "\",\"instance\":\"" + inst + "\",\"bag\":" + bagtxt(shape[a], int(rng() % 1000)) + "}";
            fnwp::Answer w = via("POST", API + "?op=write", body);
            frogram::Json j = frogram::Json::parse(w.body); if (!j["ok"].b) { check(false, "write via the engine: " + w.body); break; }
            ids[a] = std::to_string((long long)j["id"].n);
        } else { via("DELETE", API + "?op=remove&id=" + ids[a], ""); ids[a].clear(); }
        for (auto& q : reads) {
            auto b4 = cli.stats(); fnwp::Answer x = via("GET", q, ""); auto af = cli.stats(); fnwp::Answer d = direct("GET", q, "");
            bool wr = (af.raw - b4.raw) + (af.rraw - b4.rraw) > 0, gen = genuine_shape_change(prev_answer, q, d.body);
            // a RAW that follows a RESP_SAME naming a body the client's SAME cache no longer holds is the SAME-miss recovery
            // ([SAME_MISS_AUTORETRY_V1] in the proxy): counted by name, never folded into "justified"
            bool same_evicted = wr && !gen && (af.same - b4.same) > 0 && (af.raw - b4.raw) > 0;
            if (wr && !same_evicted) ++read_raw;
            if (same_evicted) ++read_same_evicted;
            if (gen) ++read_genuine;
            if (wr && !gen && std::getenv("WHY")) std::cout << "UNJUSTIFIED step " << step << " " << q << " req_raw=" << (af.raw - b4.raw)
                << " resp_raw=" << (af.rraw - b4.rraw) << " miss=" << (af.miss - b4.miss) << " same=" << (af.same - b4.same) << "\n";
            prev_answer[q] = d.body; if (x.body != d.body || x.status != d.status) { if (++mism <= 3) std::cout
              << "MISMATCH step " << step << " " << q << "\n"; } }
    }
    auto st = cli.stats();
    check(mism == 0, "workload: " + std::to_string(answers) + " answers through the engines, every read equal to api()'s own (" + std::to_string(mism) + " differ)");
    std::cout << "frames: REQ_RAW " << st.raw << " REQ_FULL " << st.full << " REQ_DIFF " << st.diff << " REQ_REPEAT " << st.repeat << " | RESP_SAME " << st.same
              << " RESP_DIFF " << st.rdiff << " RESP_RAW " << st.rraw << " REQ_MISS " << st.miss << " | bytes out " << st.bytes_out << " in " << st.bytes_in << "\n";
    check(st.rdiff > 0 && st.same > 0, "the engines spoke semantic FNWP (RESP_DIFF and RESP_SAME seen)");
    std::cout << "reads answered RAW " << read_raw << ", reads whose location changed shape or was new " << read_genuine
              << ", SAME-miss recoveries (body evicted from the 256-entry SAME cache) " << read_same_evicted << "\n";
    check(read_raw == read_genuine,
          "[INSTANCE_REFERENCES_V1] a read goes RAW only for a new location or a real shape change (" +
          std::to_string(read_raw) + " RAW, " + std::to_string(read_genuine) + " justified)");
    // storm case
    fnwp::ServerEngine srv2(std::string(32, 'k'), API, direct);
    fnwp::ClientEngine c2("10.9.9.8", "10.1.1.1", API, [&](const std::string& f) { return srv2.handle(f); });
    for (int i = 0; i < 500; ++i) direct("POST", API + "?op=write"
              , "{\"service\":\"storm\",\"variable\":\"grid\",\"instance\":\"c" + std::to_string(i) + "\",\"bag\":{\"n\":" + std::to_string(i) + ",\"s\":\"cell-"
        + std::to_string(i) + "-padding-padding\"}}");
    const std::string q = API + "?op=read&service=storm&variable=grid";
    c2.request("GET", q, ""); c2.request("GET", q, "");
    auto s0 = c2.stats(); uint64_t raw_bytes = 0; int bad = 0;
    for (int r = 0; r < 200; ++r) {
        direct("POST", API + "?op=write"
              , "{\"service\":\"storm\",\"variable\":\"grid\",\"instance\":\"c" + std::to_string(r % 500) + "\",\"bag\":{\"n\":" + std::to_string(100000 + r) + ",\"s\":\"cell-"
        + std::to_string(r % 500) + "-padding-padding\"}}");
        fnwp::Answer x = c2.request("GET", q, ""); fnwp::Answer d = direct("GET", q, ""); if (x.body != d.body) ++bad; raw_bytes += d.body.size();
    }
    auto s1 = c2.stats();
    check(bad == 0, "storm: 200 reads of 500 cells after a one-member rewrite, every answer equal to api()'s");
    std::cout << "storm: bytes in " << (s1.bytes_in - s0.bytes_in) << " for 200 changed answers of ~" << raw_bytes / 200 << " B each (reduced wire: " << raw_bytes
              << " B); RESP_DIFF " << (s1.rdiff - s0.rdiff) << ", bytes out " << (s1.bytes_out - s0.bytes_out) << "\n";
    check((s1.bytes_in - s0.bytes_in) * 50 < raw_bytes, "storm: under 2% of the reduced wire's bytes");
    std::cout << "RESULT " << (fails ? "FAIL" : "PASS") << " fnwp e2e (seed " << seed << ", " << fails << " fail)\n"; return fails ? 1 : 0;
}
