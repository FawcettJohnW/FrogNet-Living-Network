// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_row_scan_bound: [ROW_SCAN_BOUND_V1] a held read that falls back to the rows (its "after" is further back than
// the write window reaches) must not report a write while missing an earlier one that landed on a row the walk had
// already passed. Readers sleep long enough between reads that every read takes the row path; writers rewrite thousands
// of rows meanwhile. Each reader follows its own "after"; at the end every reader must have seen every row's final id.
// Before the bound this missed writes in every run on x86 at these defaults (20,000 rows) -- the defect test-write-window shows
// only on ARM, about 2 runs in 20.
#include "ram_memory.hpp"
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>
#include <map>
#include <atomic>
using namespace ramm;
int main(int argc, char** argv) {
    const int ROWS = argc > 1 ? std::atoi(argv[1]) : 20000, PER = argc > 2 ? std::atoi(argv[2]) : 40000;
    const int SLEEP_US = argc > 3 ? std::atoi(argv[3]) : 5000, WRITERS = 6, READERS = 4;
    Memory r; std::atomic<bool> done{false}; std::atomic<int> left{WRITERS};
    for (int k = 0; k < ROWS; ++k) r.write("svc", "var", "i" + std::to_string(k), "{}", 1.0);
    std::vector<std::thread> th;
    for (int w = 0; w < WRITERS; ++w) th.emplace_back([&, w] {
        for (int k = 0; k < PER; ++k) r.write("svc", "var", "i" + std::to_string((w * 7919 + k * 131) % ROWS), "{}", 1.0);
        if (--left == 0) done = true; });
    std::vector<std::map<std::string, uint64_t>> seen(READERS);
    for (int q = 0; q < READERS; ++q) th.emplace_back([&, q] {
        long long after = 0;
        for (;;) {
            bool last = done.load();
            for (auto& c : r.match("svc", "var", "", after, 0, 0)) {
                auto& e = seen[q][c.instance]; if (c.id > e) e = c.id;
                if ((long long)c.id > after) after = (long long)c.id;
            }
            if (last) break;
            std::this_thread::sleep_for(std::chrono::microseconds(SLEEP_US + q * 400));
        } });
    for (auto& t : th) t.join();
    std::map<std::string, uint64_t> fin; for (auto& c : r.match("svc", "var", "", -1, 0, 0)) fin[c.instance] = c.id;
    int bad = 0;
    for (int q = 0; q < READERS; ++q) {
        int m = 0; for (auto& kv : fin) if (seen[q][kv.first] != kv.second) ++m;
        if (m) { std::printf("FAIL: reader %d missed the final write of %d rows\n", q, m); ++bad; }
    }
    std::printf("ROW-SCAN-BOUND %s: %d fail (%d rows, %d writers x %d writes, readers on the row path)\n",
                bad ? "FAIL" : "PASS", bad, ROWS, WRITERS, PER);
    return bad ? 1 : 0;
}
