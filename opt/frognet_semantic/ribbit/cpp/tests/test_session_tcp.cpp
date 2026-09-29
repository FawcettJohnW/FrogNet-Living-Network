// test_session_tcp: frogram::Session (fnwp::ClientEngine inside) against ram_server.cpp (fnwp::ServerEngine inside its
// socket loop) over real TCP on loopback, the server in this process so its own api() is the ground truth.
//  1. sequential: writes, one-member rewrites, new shapes, removes, reads; every read equals api() at that moment
//  2. concurrent: writer threads, reader threads and a parked held read on one session; every answer is a state the
//     cell actually held, the held read wakes with the write it waited for, and the final reads equal api()
//  3. the data socket: a reply too large for the return socket crosses the data socket while small reads keep
//     completing; every answer exact
// Usage: test_session_tcp PORT ENDPOINT
#include "../src/ram_host.cpp"
#include "test_host.hpp"
#include <atomic>
#include <random>
static int fails = 0;
static void check(bool ok, const std::string& w) { std::cout << (ok ? "PASS " : "FAIL ") << w << "\n"; if (!ok) ++fails; }
static std::string direct(const std::string& m, const std::string& p, const std::string& b) { Reply r; api(m, p, b, false, r); return dump(Json::parse(r.body)); }
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int main(int argc, char** argv) {
    if (argc < 3) { std::cerr << "usage: test_session_tcp PORT ENDPOINT\n"; return 2; }
    const std::string port = argv[1], API = argv[2]; static TestRam host(API); attach(host);
    if (host.start("127.0.0.1:" + port, true)) { std::cerr << "cannot listen\n"; return 1; }
    frogram::Session s("127.0.0.1", std::atoi(port.c_str()), API);
    auto via = [&](const std::string& m, const std::string& p, const std::string& b) { return dump(s.call(m, p, b)); };
    // ---- 1. sequential
    std::mt19937 rng(7); int bad = 0, reads = 0;
    std::vector<std::string> ids(24);
    auto bag = [&](int shape, int v) { std::string b = "{\"n\":" + std::to_string(v) + ",\"s\":\"x" + std::to_string(v % 7) + "\"";
        if (shape % 3 == 1) b += ",\"nested\":{\"a\":" + std::to_string(v % 5) + "}";
        if (shape % 3 == 2) b += ",\"list\":[1,2.5]";
        return b + "}"; };
    std::vector<int> shape(24); for (auto& x : shape) x = int(rng() % 3);
    for (int i = 0; i < 1500; ++i) {
        int a = int(rng() % 24); int op = int(rng() % 10);
        if (op < 4) { if (rng() % 5 == 0) shape[a] = int(rng() % 3);
            Json w = s.call("POST", API + "?op=write", "{\"service\":\"t\",\"variable\":\"v" + std::to_string(a % 4) + "\",\"instance\":\"i" + std::to_string(a) +
                            "\",\"bag\":" + bag(shape[a], int(rng() % 50)) + "}");
            ids[a] = std::to_string(uint64_t(w["id"].n)); }
        else if (op == 4 && !ids[a].empty()) { s.call("DELETE", API + "?op=remove&id=" + ids[a]); ids[a].clear(); }
        else { std::string q = API + "?op=read&service=t&variable=v" + std::to_string(a % 4) + (rng() % 2 ? "&instance=i" + std::to_string(a) : "");
            ++reads; if (via("GET", q, "") != direct("GET", q, "")) ++bad; }
    }
    auto st = s.stats();
    check(bad == 0, "sequential: " + std::to_string(reads) + " reads over TCP, every answer equal to api()'s (" + std::to_string(bad) + " differ)");
    std::cout << "  frames: RAW " << st.raw << " FULL " << st.full << " DIFF " << st.diff << " REPEAT " << st.repeat << " | SAME " << st.same
              << " RDIFF " << st.rdiff << " RRAW " << st.rraw << " MISS " << st.miss << " | bytes out " << st.bytes_out << " in " << st.bytes_in << "\n";
    check(st.same > 0 && st.rdiff > 0 && st.full + st.diff > 0, "sequential: the full contract is on the wire (FULL/DIFF, SAME, RESP_DIFF all seen)");
    // ---- 2. concurrent
    std::atomic<int> cbad{0}, cerr{0}; std::atomic<bool> go{true};
    s.call("POST", API + "?op=write", "{\"service\":\"c\",\"variable\":\"held\",\"instance\":\"x\",\"bag\":{\"k\":0}}");
    Json first = s.call("GET", API + "?op=read&service=c&variable=held"); uint64_t after = uint64_t(first["rows"][0]["id"].n);
    std::atomic<bool> woke{false}; std::string woke_with;
    std::thread held([&] { try { Json r = s.call("GET", API + "?op=read&service=c&variable=held&after=" + std::to_string(after) + "&wait_s=20", "", 30);
                                 woke_with = dump(r["rows"][0]["bag"]); woke = true; } catch (const std::exception& e) { ++cerr; std::cout << "  held: " << e.what() << "\n"; } });
    std::vector<std::thread> ts;
    for (int t = 0; t < 4; ++t) ts.emplace_back([&, t] {                      // each writer owns its own cell: one writer per cell
        for (int i = 0; i < 300 && go; ++i) { try { s.call("POST", API + "?op=write", "{\"service\":\"c\",\"variable\":\"w\",\"instance\":\"" + std::to_string(t) +
                                                          "\",\"bag\":{\"t\":" + std::to_string(t) + ",\"i\":" + std::to_string(i) + "}}"); } catch (...) { ++cerr; } } });
    for (int t = 0; t < 4; ++t) ts.emplace_back([&, t] {                      // readers: each answer must be a state the cell held
        for (int i = 0; i < 300 && go; ++i) { try { Json r = s.call("GET", API + "?op=read&service=c&variable=w&instance=" + std::to_string((t + 1) % 4));
            if (r["rows"].size() == 1 && int(r["rows"][0]["bag"]["t"].n) != (t + 1) % 4) ++cbad; } catch (...) { ++cerr; } } });
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    s.call("POST", API + "?op=write", "{\"service\":\"c\",\"variable\":\"held\",\"instance\":\"x\",\"bag\":{\"k\":1}}");
    for (auto& t : ts) t.join();
    held.join();
    check(cerr == 0, "concurrent: 4 writers x 300, 4 readers x 300 and a held read on one session, no errors (" + std::to_string(cerr) + ")");
    check(cbad == 0, "concurrent: every read answered with a state its cell held (" + std::to_string(cbad) + " wrong)");
    check(woke && woke_with == "{\"k\":1}", "concurrent: the held read woke with the write it waited for (" + woke_with + ")");
    int fbad = 0; for (int t = 0; t < 4; ++t) { std::string q = API + "?op=read&service=c&variable=w&instance=" + std::to_string(t); if (via("GET", q, "") != direct("GET", q, "")) ++fbad; }
    check(fbad == 0, "concurrent: afterwards every cell reads equal to api()");
    // ---- 3. the data socket
    std::string big = "{\"blob\":\"";
    for (int i = 0; i < 400000; ++i) big += char('a' + i % 26);
    big += "\"}";
    auto d0 = s.stats(); std::atomic<bool> bigdone{false}; std::string bigans; double small_max = 0; int small_n = 0, small_bad = 0;
    s.call("POST", API + "?op=write", "{\"service\":\"d\",\"variable\":\"big\",\"instance\":\"0\",\"bag\":" + big + "}");
    std::thread tb([&] { for (int i = 0; i < 3; ++i) bigans = via("GET", API + "?op=read&service=d&variable=big", ""); bigdone = true; });
    while (!bigdone) { double t0 = now(); std::string q = API + "?op=read&service=c&variable=w&instance=0"; std::string a = via("GET", q, "");
                       small_max = std::max(small_max, now() - t0); ++small_n; if (a != direct("GET", q, "")) ++small_bad; }
    tb.join(); auto d1 = s.stats();
    check(bigans == direct("GET", API + "?op=read&service=d&variable=big", ""), "data socket: the 400 kB answer is exact");
    check(d1.data_frames > d0.data_frames, "data socket: the large reply crossed the data socket (" + std::to_string(d1.data_frames - d0.data_frames) +
          " frames, " + std::to_string(d1.data_bytes - d0.data_bytes) + " B)");
    check(small_bad == 0 && small_n > 0, "data socket: " + std::to_string(small_n) + " small reads completed while it transited, every one exact; slowest " +
          std::to_string(int(small_max * 1000)) + " ms");
    std::cout << (fails ? "RESULT FAIL" : "RESULT PASS") << " session over TCP, endpoint " << API << " (" << fails << " fail)\n";
    s.shutdown(); std::cout.flush(); std::_Exit(fails ? 1 : 0);
}
