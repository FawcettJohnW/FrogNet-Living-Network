// test_fanin_data: [HIGH_SPEED_DATA_SOCKET_V1] several large payloads fanned in at once, over a shaped wire.
// The server (ram_server.cpp, fnwp::ServerEngine in its socket loop) runs in this process on SERVER_PORT; the clients
// (frogram::Session, fnwp::ClientEngine inside) connect through RELAY_PORT, a shaped relay (tools/shaped_relay.py) that
// rate-limits and delays every connection. Every transfer is real; only the wire is simulated.
//
//  1. Four writers each write a large bag (their own cell) at the same time, while a fifth session keeps doing small
//     reads and writes on its own cells. Every large write reaches the memory exactly; every small operation keeps
//     completing while the large ones transit, each answered exactly, and their latency stays near the idle latency.
//  2. One session starts reading a very large cell, then a much smaller large cell. The smaller one completes long
//     before the larger one: on the data socket the two are interleaved segment by segment, so B is not queued
//     behind A. Fails on a data socket that sends whole frames (B waits for all of A).
//  3. Afterwards every cell reads back equal to api()'s own answer.
// Usage: test_fanin_data SERVER_PORT RELAY_PORT ENDPOINT [BIG_BYTES]
#include "../src/ram_host.cpp"
#include "test_host.hpp"
#include <atomic>
#include <random>
static int fails = 0;
static void check(bool ok, const std::string& w) { std::cout << (ok ? "PASS " : "FAIL ") << w << "\n"; if (!ok) ++fails; }
static std::string direct(const std::string& m, const std::string& p, const std::string& b) { Reply r; api(m, p, b, false, r); return dump(Json::parse(r.body)); }
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static std::string blob(size_t n, char seed) { std::string s; s.reserve(n); for (size_t i = 0; i < n; ++i) s += char('a' + (i * 7 + seed) % 26); return s; }
static std::string bag_of(const std::string& payload) { return "{\"blob\":\"" + payload + "\"}"; }
static std::string cell(const std::string& var, const std::string& inst, const std::string& bag) {
    return "{\"service\":\"fan\",\"variable\":\"" + var + "\",\"instance\":\"" + inst + "\",\"bag\":" + bag + "}"; }
int main(int argc, char** argv) {
    if (argc < 4) { std::cerr << "usage: test_fanin_data SERVER_PORT RELAY_PORT ENDPOINT [BIG_BYTES]\n"; return 2; }
    const int sport = std::atoi(argv[1]), rport = std::atoi(argv[2]); const std::string API = argv[3]; static TestRam host(API); attach(host);
    const size_t BIG = argc > 4 ? size_t(std::atol(argv[4])) : 2000000;
    if (host.start("127.0.0.1:" + std::to_string(sport), true)) { std::cerr << "cannot listen\n"; return 1; }
    auto sess = [&] { return std::make_unique<frogram::Session>("127.0.0.1", rport, API); };
    // ---- idle latency of a small read, through the relay, before anything large is in flight
    auto ctl = sess();
    ctl->call("POST", API + "?op=write", cell("small", "0", "{\"n\":0}"));
    double idle = 1e9; for (int i = 0; i < 20; ++i) { double t0 = now(); ctl->call("GET", API + "?op=read&service=fan&variable=small&instance=0"); idle = std::min(idle, now() - t0); }
    std::cout << "idle small read through the relay: " << int(idle * 1000) << " ms\n";
    // ---- 1. fan-in: four large writes at once, small traffic alongside
    const int W = 4; std::vector<std::string> payload(W); for (int w = 0; w < W; ++w) payload[w] = blob(BIG, char(w));
    std::vector<std::unique_ptr<frogram::Session>> writers; for (int w = 0; w < W; ++w) writers.push_back(sess());
    std::atomic<int> werr{0}; std::atomic<bool> done{false}; std::vector<double> wtime(W, 0);
    std::vector<std::thread> ts;
    const double t_start = now();
    for (int w = 0; w < W; ++w) ts.emplace_back([&, w] {
        try { writers[w]->call("POST", API + "?op=write", cell("big", std::to_string(w), bag_of(payload[w])), 120); wtime[w] = now() - t_start; }
        catch (const std::exception& e) { ++werr; std::cout << "  writer " << w << ": " << e.what() << "\n"; } });
    int small_n = 0, small_bad = 0; double small_max = 0, small_sum = 0;
    std::thread smalls([&] {
        int i = 0;
        while (!done) {
            double t0 = now(); std::string q = API + "?op=read&service=fan&variable=small&instance=0";
            try {
                if (i % 3 == 0) ctl->call("POST", API + "?op=write", cell("small", "0", "{\"n\":" + std::to_string(i) + "}"));
                else { std::string a = dump(ctl->call("GET", q)); if (a != direct("GET", q, "")) ++small_bad; }
            } catch (const std::exception& e) { ++small_bad; std::cout << "  small: " << e.what() << "\n"; }
            double dt = now() - t0; small_max = std::max(small_max, dt); small_sum += dt; ++small_n; ++i;
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        } });
    for (auto& t : ts) t.join();
    done = true; smalls.join();
    const double t_all = now() - t_start;
    check(werr == 0, "fan-in: " + std::to_string(W) + " writers of " + std::to_string(BIG) + " B each completed together (" + std::to_string(int(t_all * 1000)) + " ms, no errors)");
    int wbad = 0; for (int w = 0; w < W; ++w) { std::string q = API + "?op=read&service=fan&variable=big&instance=" + std::to_string(w);
        Json r = ctl->call("GET", q, "", 120); if (r["rows"].size() != 1 || r["rows"][0]["bag"]["blob"].s != payload[w]) ++wbad; }
    check(wbad == 0, "fan-in: every large payload is in the memory byte for byte (" + std::to_string(wbad) + " wrong)");
    check(small_bad == 0 && small_n > 0, "fan-in: " + std::to_string(small_n) + " small operations completed while the large ones transited, every read exact");
    check(small_max < 20 * idle + 0.25, "fan-in: the slowest small operation took " + std::to_string(int(small_max * 1000)) + " ms (mean " +
          std::to_string(int(small_sum / std::max(1, small_n) * 1000)) + " ms; idle " + std::to_string(int(idle * 1000)) + " ms): no waiting behind the large transfers");
    uint64_t dframes = 0; for (auto& w : writers) dframes += w->stats().data_frames;
    check(dframes >= uint64_t(W), "fan-in: the large writes travelled on the data sockets (" + std::to_string(dframes) + " transfers)");
    // ---- 2. a small large transfer is not queued behind a huge one: reads of A (BIG*3) then B (BIG/8) on one session
    auto rd = sess();
    rd->call("POST", API + "?op=write", cell("hol", "A", bag_of(blob(BIG * 3, 'A'))), 120);
    rd->call("POST", API + "?op=write", cell("hol", "B", bag_of(blob(BIG / 8, 'B'))), 120);
    double tA = 0, tB = 0; const double t2 = now(); std::string ansA, ansB;
    std::thread ra([&] { ansA = dump(rd->call("GET", API + "?op=read&service=fan&variable=hol&instance=A", "", 120)); tA = now() - t2; });
    std::this_thread::sleep_for(std::chrono::milliseconds(60));                        // A is under way before B is asked for
    std::thread rb([&] { ansB = dump(rd->call("GET", API + "?op=read&service=fan&variable=hol&instance=B", "", 120)); tB = now() - t2; });
    ra.join(); rb.join();
    check(ansA == direct("GET", API + "?op=read&service=fan&variable=hol&instance=A", "") && ansB == direct("GET", API + "?op=read&service=fan&variable=hol&instance=B", ""),
          "interleave: both answers exact");
    check(tB < tA * 0.5, "interleave: B (" + std::to_string(BIG / 8) + " B) completed at " + std::to_string(int(tB * 1000)) + " ms while A (" +
          std::to_string(BIG * 3) + " B) took " + std::to_string(int(tA * 1000)) + " ms: B was not queued behind A");
    // ---- 3. everything reads back exactly
    int fbad = 0; for (int w = 0; w < W; ++w) { std::string q = API + "?op=read&service=fan&variable=big&instance=" + std::to_string(w); if (dump(ctl->call("GET", q, "", 120)) != direct("GET", q, "")) ++fbad; }
    check(fbad == 0, "afterwards: every large cell reads equal to api()");
    std::cout << (fails ? "RESULT FAIL" : "RESULT PASS") << " fan-in over the shaped wire, endpoint " << API << ", " << BIG << " B payloads (" << fails << " fail)\n";
    for (auto& w : writers) w->shutdown();
    rd->shutdown(); ctl->shutdown();
    std::cout.flush(); std::_Exit(fails ? 1 : 0);
}
