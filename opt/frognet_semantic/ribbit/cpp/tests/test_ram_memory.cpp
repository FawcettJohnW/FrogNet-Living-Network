// test_ram_memory.cpp -- [ROW_LOCKS_V1] oracle for ribbit_cpp/ram_memory.hpp.
//   1. lock gate: the memory's source holds no lock but a row's std::shared_mutex (and a trigger's private mutex)
//   2. concurrent writers on many rows of one variable, concurrent readers, and parked waiters on exact rows, on the
//      variable pattern and on the service pattern -- then:
//        every read returns only cells whose bag is exactly what was written with that id (nothing torn or invented)
//        ids are strictly increasing in write order per row, and globally unique
//        the final state equals a serial replay: each row holds the bag of its highest id
//        no wake is lost: every waiter armed on "after the id I hold" returns a newer cell well before its deadline
#include "ram_memory.hpp"
#include <cstdio>
#include <fstream>
#include <mutex>
#include <random>
#include <regex>
#include <set>
#include <sstream>
#include <thread>
using namespace ramm;
static int fails = 0;
static void check(bool ok, const std::string& what) { std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str()); if (!ok) ++fails; }

int main(int argc, char** argv) {
    const std::string src = argc > 1 ? argv[1] : "ribbit_cpp/ram_memory.hpp";
    { std::ifstream f(src); std::stringstream ss; ss << f.rdbuf(); std::string t = ss.str();
      std::string code; std::istringstream lines(t); std::string ln;
      while (std::getline(lines, ln)) { auto c = ln.find("//"); code += (c == std::string::npos ? ln : ln.substr(0, c)) + "\n"; }
      int shared = 0, plain = 0, other = 0; std::smatch m; std::string s = code;
      std::regex decl(R"(std::(shared_mutex|mutex|recursive_mutex|timed_mutex|shared_timed_mutex|recursive_timed_mutex)\s+([A-Za-z_]\w*)\s*;)");
      for (auto it = std::sregex_iterator(s.begin(), s.end(), decl); it != std::sregex_iterator(); ++it) {
          std::string kind = (*it)[1], name = (*it)[2];
          if (kind == "shared_mutex" && name == "m") ++shared; else if (kind == "mutex" && name == "m") ++plain; else ++other;
      }
      check(shared == 1 && plain == 1 && other == 0,
            "lock gate: the memory declares exactly one row lock (shared_mutex) and one per-trigger mutex, nothing else (shared "
            + std::to_string(shared) + ", trigger " + std::to_string(plain) + ", other " + std::to_string(other) + ")"); }

    Memory mem; const int W = 8, ROWS = 64, N = 400, READERS = 4;
    std::mutex log_m; std::map<uint64_t, std::pair<std::string, std::string>> written;   // id -> (instance, bag): the test's own record
    std::atomic<bool> done{false}; std::atomic<int> bad_reads{0}, reads{0};
    auto bag_of = [](int w, int n) { return "{\"w\":" + std::to_string(w) + ",\"n\":" + std::to_string(n) + "}"; };
    // waiters: exact rows, the variable, the service -- each re-arms "after the newest id it holds"
    std::atomic<int> wakes{0}, missed{0};
    auto waiter = [&](std::string v, std::string i) {
        long long after = 0;
        while (!done.load()) {
            auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
            auto rows = mem.wait("svc", v, i, deadline, [&] { return mem.match("svc", v, i, after, 0, 0); },
                                 [&](const std::vector<Cell>& r) { return !r.empty(); });
            if (rows.empty()) { if (!done.load()) ++missed; continue; }
            ++wakes; for (auto& c : rows) after = std::max<long long>(after, (long long)c.id);
        }
    };
    std::vector<std::thread> ws;
    ws.emplace_back(waiter, "var", ""); ws.emplace_back(waiter, "", ""); ws.emplace_back(waiter, "var", "r7"); ws.emplace_back(waiter, "var", "r63");
    std::vector<std::thread> rs;
    for (int k = 0; k < READERS; ++k) rs.emplace_back([&] {
        while (!done.load()) {
            auto rows = mem.match("svc", "var", "", -1, 0, 0); ++reads;
            std::lock_guard<std::mutex> g(log_m);
            for (auto& c : rows) { auto it = written.find(c.id); if (it == written.end() || it->second.first != c.instance || it->second.second != c.bag) ++bad_reads; }
        }
    });
    std::vector<std::thread> writers;
    std::atomic<int> order_bad{0};
    for (int w = 0; w < W; ++w) writers.emplace_back([&, w] {
        std::map<std::string, uint64_t> last;
        for (int n = 0; n < N; ++n) {
            std::string inst = "r" + std::to_string((w * 7 + n) % ROWS), bag = bag_of(w, n);
            std::lock_guard<std::mutex> g(log_m);                       // the TEST's record is serialized, not the memory
            uint64_t id = mem.write("svc", "var", inst, bag, 1.0);
            if (written.count(id)) ++order_bad;
            written[id] = {inst, bag};
            if (last.count(inst) && id <= last[inst]) ++order_bad;
            last[inst] = id;
        }
    });
    for (auto& t : writers) t.join();
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    done = true;
    for (auto& t : rs) t.join();      // readers first: the release writes below are not in the test's record
    for (int k = 0; k < 4; ++k) mem.write("svc", "var", "r" + std::to_string(k == 2 ? 7 : k == 3 ? 63 : k), "{\"end\":1}", 1.0);   // release every waiter
    for (auto& t : ws) t.join();

    check(bad_reads == 0, "concurrent reads (" + std::to_string(reads.load()) + ") returned only cells exactly as written (" + std::to_string(bad_reads.load()) + " torn or invented)");
    check(order_bad == 0, "ids unique, and strictly increasing per row in write order");
    std::map<std::string, std::pair<uint64_t, std::string>> replay;
    for (auto& kv : written) replay[kv.second.first] = {kv.first, kv.second.second};
    auto final_rows = mem.match("svc", "var", "", -1, 0, 0); int diff = 0;
    for (auto& c : final_rows) if (c.bag != "{\"end\":1}") { auto it = replay.find(c.instance); if (it == replay.end() || it->second.first != c.id || it->second.second != c.bag) ++diff; }
    check(final_rows.size() == ROWS && diff == 0, "final state equals the serial replay: " + std::to_string(final_rows.size()) + " rows, " + std::to_string(diff) + " differ");
    check(missed == 0 && wakes > 0, "no lost wake: " + std::to_string(wakes.load()) + " wakes, " + std::to_string(missed.load()) + " waits ran to their 5 s deadline with writes happening");
    size_t removed = mem.remove(final_rows.front().id, false);
    check(removed == 1 && mem.match("svc", "var", final_rows.front().instance, -1, 0, 0).empty(), "remove by id: the row is gone");
    std::printf("\n%s: %d fail\n", fails ? "RAM-MEMORY FAIL" : "RAM-MEMORY PASS", fails);
    return fails ? 1 : 0;
}
