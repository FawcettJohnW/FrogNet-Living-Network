// test_write_window: [WRITE_WINDOW_V1] a held read ("everything written past id X") must return exactly the rows whose
// current id is past X -- inside the window, beyond it (lapped: the rows answer), and while writers race it -- and no
// reader following its own "after" may ever miss a write. Also: memory must not grow with writes to existing rows.
#include "ram_memory.hpp"
#include <cstdio>
#include <thread>
#include <vector>
#include <map>
#include <set>
#include <atomic>
using namespace ramm;
static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { std::printf("FAIL: " __VA_ARGS__); std::printf("\n"); ++fails; } } while (0)
static std::set<std::pair<std::string, uint64_t>> brute(Memory& m, long long after) {   // every row, then filter
    std::set<std::pair<std::string, uint64_t>> s;
    for (auto& c : m.match("svc", "var", "", -1, 0, 0)) if ((long long)c.id > after) s.insert({c.instance, c.id});
    return s;
}
static std::set<std::pair<std::string, uint64_t>> held(Memory& m, long long after) {
    std::set<std::pair<std::string, uint64_t>> s;
    for (auto& c : m.match("svc", "var", "", after, 0, 0)) s.insert({c.instance, c.id});
    return s;
}
static long rss_kb() { FILE* f = std::fopen("/proc/self/statm", "r"); long a = 0, b = 0; if (f) { if (std::fscanf(f, "%ld %ld", &a, &b) != 2) b = 0; std::fclose(f); } return b * 4; }
int main() {
    Memory m;
    // 1. sequential: 5,000 writes over 300 rows; held reads from many points, inside and beyond the window
    std::vector<uint64_t> ids;
    for (int k = 0; k < 5000; ++k) ids.push_back(m.write("svc", "var", "i" + std::to_string(k % 300), "{}", 1.0));
    for (long long after : {-1LL, 0LL, (long long)ids[0], (long long)ids[100], (long long)ids[3900], (long long)ids[4000],
                            (long long)ids[4500], (long long)ids[4998], (long long)ids[4999]}) {
        if (after < 0) continue;
        auto a = held(m, after), b = brute(m, after);
        CHECK(a == b, "sequential after=%lld: held %zu rows, brute %zu", after, a.size(), b.size());
    }
    // 2. racing: 8 writers x 20,000 writes over 500 rows while 4 readers follow their own "after"; at the end every
    //    reader must have seen every row's final id
    Memory r; std::atomic<bool> done{false}; std::atomic<int> writers{8};
    std::vector<std::thread> th;
    for (int w = 0; w < 8; ++w) th.emplace_back([&, w] {
        for (int k = 0; k < 20000; ++k) r.write("svc", "var", "i" + std::to_string((w * 7919 + k * 31) % 500), "{}", 1.0);
        if (--writers == 0) done = true; });
    std::vector<std::map<std::string, uint64_t>> seen(4);
    for (int q = 0; q < 4; ++q) th.emplace_back([&, q] {
        long long after = 0;
        for (;;) {
            bool last = done.load();
            for (auto& c : r.match("svc", "var", "", after, 0, 0)) {
                auto& e = seen[q][c.instance]; if (c.id > e) e = c.id;
                if ((long long)c.id > after) after = (long long)c.id;
            }
            if (last) break;
            std::this_thread::sleep_for(std::chrono::microseconds(q * 300 + 50));
        } });
    for (auto& t : th) t.join();
    std::map<std::string, uint64_t> final_ids;
    for (auto& c : r.match("svc", "var", "", -1, 0, 0)) final_ids[c.instance] = c.id;
    for (int q = 0; q < 4; ++q) {
        int missed = 0;
        for (auto& kv : final_ids) if (seen[q][kv.first] != kv.second) ++missed;
        CHECK(missed == 0, "racing reader %d missed the final write of %d rows", q, missed);
    }
    // 3. memory: 400,000 more writes to the same 500 rows must not grow the process
    const long before = rss_kb();
    for (int k = 0; k < 400000; ++k) r.write("svc", "var", "i" + std::to_string(k % 500), "{}", 1.0);
    const long grown = rss_kb() - before;
    CHECK(grown < 4096, "400,000 writes to existing rows grew the process by %ld kB", grown);
    std::printf("WRITE-WINDOW %s: %d fail (memory after 400,000 more writes: %+ld kB)\n", fails ? "FAIL" : "PASS", fails, grown);
    return fails ? 1 : 0;
}
