// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_ram_apipp.cpp -- [API_PLUS_PLUS_V1] API++ in the platform memory (ram_memory.hpp): update, cas, swap, take,
// take_first and batch, each under real contention on one tuple. -DMEM_BROKEN=1 does the read-modify-write the way an
// application would without API++ (read the row, then write it back) and the update checks must go red.
//
//   test-ram-apipp [threads] [rounds]
#include "ram_memory.hpp"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <set>
#include <string>
#include <thread>
#include <vector>
#ifndef MEM_BROKEN
#define MEM_BROKEN 0
#endif

static int bad = 0;
static void check(bool ok, const char* what, const std::string& detail = "") {
    std::printf("%s %s%s%s\n", ok ? "PASS" : "FAIL", what, detail.empty() ? "" : ": ", detail.c_str());
    if (!ok) bad++;
}
static long long num(const std::string& bag) { auto p = bag.find(':'); return p == std::string::npos ? 0 : atoll(bag.c_str() + p + 1); }
static std::string bag_n(long long n) { return "{\"n\":" + std::to_string(n) + "}"; }

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    const int T = argc > 1 ? atoi(argv[1]) : 4;
    const int R = argc > 2 ? atoi(argv[2]) : 5000;
    std::printf("== API++ in the platform memory, %d threads, %d rounds each%s\n", T, R, MEM_BROKEN ? " (BROKEN on purpose)" : "");
    ramm::Memory M;

    // update: every add lands and every caller sees its own total
    {
        std::vector<std::vector<long long>> totals(T); std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&, t] {
            for (int i = 0; i < R; ++i) {
#if MEM_BROKEN == 1
                auto rows = M.match("s", "ctr", "x", -1, 0, 0);
                long long v = rows.empty() ? 0 : num(rows[0].bag) + 1; if (rows.empty()) v = 1;
                M.write("s", "ctr", "x", bag_n(v), 0); totals[t].push_back(v);
#else
                auto o = M.update("s", "ctr", "x", [](const ramm::RowVal* cur) -> std::optional<std::string> {
                    return bag_n((cur ? num(cur->bag) : 0) + 1); }, 0);
                totals[t].push_back(num(o.bag));
#endif
            } });
        for (auto& x : th) x.join();
        auto rows = M.match("s", "ctr", "x", -1, 0, 0);
        std::set<long long> all; size_t n = 0; for (auto& v : totals) for (long long x : v) { all.insert(x); n++; }
        const long long fin = rows.empty() ? 0 : num(rows[0].bag);
        check(fin == (long long)T * R, "update: every add lands", std::to_string(fin) + " of " + std::to_string((long long)T * R));
        check(all.size() == n, "update: every caller got its own total", std::to_string(n - all.size()) + " repeated");
    }

    // cas: every round, all threads try to replace the same write; exactly one wins
    {
        uint64_t cur = M.write("s", "cas", "x", bag_n(0), 0);
        std::atomic<int> round{-1}, wins{0}, ready{0}; int wrong = 0; std::atomic<uint64_t> expect{cur};
        std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&, t] {
            for (int r = 0; r < R / 10; ++r) {
                while (round.load() != r) { if (round.load() == -2) return; std::this_thread::yield(); }
                auto o = M.cas("s", "cas", "x", expect.load(), bag_n(t), 0);
                if (o.done) wins++;
                ready++;
            } });
        for (int r = 0; r < R / 10; ++r) {
            wins = 0; ready = 0; expect = M.match("s", "cas", "x", -1, 0, 0)[0].id; round = r;
            while (ready.load() < T) std::this_thread::yield();
            if (wins.load() != 1) wrong++;
        }
        round = -2; for (auto& x : th) x.join();
        check(wrong == 0, "cas: exactly one of the racers replaces the write it read", std::to_string(wrong) + " rounds wrong");
        auto absent = M.cas("s", "cas", "new", 0, bag_n(1), 0); auto again = M.cas("s", "cas", "new", 0, bag_n(2), 0);
        check(absent.done && !again.done && again.was.live && num(again.was.bag) == 1, "cas: expect 0 lands only on an absent row");
    }

    // swap: every value swapped in comes out exactly once (as a later swap's `was`, or as the last value)
    {
        std::vector<std::vector<long long>> seen(T); std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&, t] {
            for (int i = 0; i < R; ++i) { auto o = M.swap("s", "swap", "x", bag_n((long long)t * R + i + 1), 0); if (o.was.live) seen[t].push_back(num(o.was.bag)); } });
        for (auto& x : th) x.join();
        std::multiset<long long> out; for (auto& v : seen) for (long long x : v) out.insert(x);
        out.insert(num(M.match("s", "swap", "x", -1, 0, 0)[0].bag));
        bool once = out.size() == size_t(T) * R; for (long long k = 1; k <= (long long)T * R && once; ++k) once = out.count(k) == 1;
        check(once, "swap: every value swapped in is handed back exactly once", std::to_string(out.size()) + " handed back of " + std::to_string(size_t(T) * R));
    }

    // take: racers on one row; exactly one gets it
    {
        std::atomic<int> round{-1}, wins{0}, ready{0}; int wrong = 0; std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&] {
            for (int r = 0; r < R / 10; ++r) {
                while (round.load() != r) { if (round.load() == -2) return; std::this_thread::yield(); }
                if (M.take("s", "take", "x").live) wins++;
                ready++;
            } });
        for (int r = 0; r < R / 10; ++r) {
            wins = 0; ready = 0; M.write("s", "take", "x", bag_n(r), 0); round = r;
            while (ready.load() < T) std::this_thread::yield();
            if (wins.load() != 1) wrong++;
        }
        round = -2; for (auto& x : th) x.join();
        check(wrong == 0, "take: exactly one caller gets the value", std::to_string(wrong) + " rounds wrong");
    }

    // take_first: producers enqueue, consumers pop concurrently; every row popped exactly once; one consumer pops in order
    {
        const int N = R; std::atomic<int> produced{0}; std::vector<std::vector<long long>> got(T);
        std::vector<std::thread> th;
        for (int p = 0; p < 2; ++p) th.emplace_back([&, p] { for (int i = 0; i < N; ++i) { M.write("s", "q", std::to_string(p) + "." + std::to_string(i), bag_n((long long)p * N + i), 0); produced++; } });
        for (int c = 0; c < T; ++c) th.emplace_back([&, c] {
            for (;;) { auto x = M.take_first("s", "q"); if (x.got) { got[c].push_back(num(x.bag)); continue; }
                       if (produced.load() == 2 * N) { if (!M.take_first("s", "q").got) return; } std::this_thread::yield(); } });
        for (auto& x : th) x.join();
        std::multiset<long long> all; for (auto& v : got) for (long long x : v) all.insert(x);
        bool once = all.size() == size_t(2 * N); for (long long k = 0; k < 2LL * N && once; ++k) once = all.count(k) == 1;
        check(once, "take_first: every queued row popped exactly once", std::to_string(all.size()) + " of " + std::to_string(2 * N));
        for (int i = 0; i < 50; ++i) M.write("s", "q1", "e" + std::to_string(i), bag_n(i), 0);
        bool order = true; for (int i = 0; i < 50; ++i) { auto x = M.take_first("s", "q1"); order &= x.got && num(x.bag) == i; }
        check(order && !M.take_first("s", "q1").got, "take_first: one consumer pops in write order, then finds it empty");
    }

    // batch: a family's reader is woken once for the whole batch, and its one read finds every write
    {
        auto cur = M.cursor("s", "b", "", -1); ramm::WakeWord* w = M.wake_word(); ramm::Memory::hook(cur, w);
        const uint32_t n0 = w->n.load();
        std::vector<ramm::Memory::W> ws; for (int i = 0; i < 20; ++i) ws.push_back({"s", "b", "k" + std::to_string(i), bag_n(i)});
        auto ids = M.batch(ws, 0);
        const uint32_t woke = w->n.load() - n0;
        std::vector<ramm::Cell> rows; M.read(cur, rows);
        check(woke == 1, "batch: the family's reader is woken once for 20 writes", std::to_string(woke) + " wakes");
        check(ids.size() == 20 && rows.size() == 20, "batch: its one read finds all 20", std::to_string(rows.size()) + " rows");
        const uint32_t n1 = w->n.load(); for (int i = 0; i < 20; ++i) M.write("s", "b", "k" + std::to_string(i), bag_n(i), 0);
        std::printf("MEASURE the same 20 as plain writes: %u wakes\n", w->n.load() - n1);
    }

    std::printf("RESULT %s: %d failed\n", bad ? "FAIL" : "PASS", bad);
    return bad ? 1 : 0;
}
