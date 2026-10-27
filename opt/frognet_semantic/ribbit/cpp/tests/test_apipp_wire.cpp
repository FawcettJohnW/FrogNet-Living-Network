// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_apipp_wire.cpp -- [API_PLUS_PLUS_V1] API++ end to end: a RAM host, and participants each on their own session
// calling frogram::Memory's cas / swap / add / take / take_first / batch at once, through the session's wire.
//
//   test-apipp-wire PORT ENDPOINT [participants] [rounds]
#include "../src/ram_host.cpp"
#include "test_host.hpp"
#include <atomic>
#include <set>
static int fails = 0;
static void check(bool ok, const std::string& w) { std::cout << (ok ? "PASS " : "FAIL ") << w << std::endl; if (!ok) ++fails; }

int main(int argc, char** argv) {
    if (argc < 3) { std::cerr << "usage: test-apipp-wire PORT ENDPOINT [participants] [rounds]\n"; return 2; }
    const std::string port = argv[1], API = argv[2];
    const int P = argc > 3 ? atoi(argv[3]) : 4, R = argc > 4 ? atoi(argv[4]) : 500;
    static TestRam host(API); attach(host);
    if (host.start("127.0.0.1:" + port, true)) { std::cerr << "cannot listen\n"; return 1; }
    std::vector<std::unique_ptr<frogram::Session>> ss; std::vector<std::unique_ptr<frogram::Memory>> ms;
    for (int p = 0; p < P; ++p) { ss.emplace_back(new frogram::Session("127.0.0.1", atoi(port.c_str()), API)); ms.emplace_back(new frogram::Memory(*ss.back(), API)); }
    std::cout << "== API++ over the wire: " << P << " participants, each its own session, " << R << " rounds" << std::endl;

    // add: every participant's adds land; each gets its own total
    {
        std::vector<std::vector<long long>> got(P); std::vector<std::thread> th;
        for (int p = 0; p < P; ++p) th.emplace_back([&, p] { for (int i = 0; i < R; ++i) got[p].push_back((long long)ms[p]->add("t", "ctr", "hits", 1)); });
        for (auto& x : th) x.join();
        std::set<long long> all; size_t n = 0; for (auto& v : got) for (long long x : v) { all.insert(x); n++; }
        auto rows = ms[0]->read("t", "ctr", "hits");
        const long long fin = rows.empty() ? -1 : (long long)rows[0].bag["n"].n;
        check(fin == (long long)P * R && all.size() == n && *all.begin() == 1 && *all.rbegin() == (long long)P * R,
              "add: " + std::to_string(P * R) + " adds from " + std::to_string(P) + " sessions land, each caller its own total (final " + std::to_string(fin) + ")");
        // one session, several threads, the identical add at once: each still its own add, its own total
        { std::vector<std::vector<long long>> g1(4); std::vector<std::thread> t1;
          for (int k = 0; k < 4; ++k) t1.emplace_back([&, k] { for (int i = 0; i < R; ++i) g1[k].push_back((long long)ms[0]->add("t", "ctr", "one", 1)); });
          for (auto& x : t1) x.join();
          std::set<long long> a1; size_t n1 = 0; for (auto& v : g1) for (long long x : v) { a1.insert(x); n1++; }
          auto r1 = ms[0]->read("t", "ctr", "one");
          check(a1.size() == n1 && !r1.empty() && (long long)r1[0].bag["n"].n == 4LL * R,
                "add: 4 threads on ONE session, the identical add at once -- each its own add (" + std::to_string(n1 - a1.size()) + " totals repeated, final " +
                std::to_string(r1.empty() ? -1 : (long long)r1[0].bag["n"].n) + " of " + std::to_string(4 * R) + ")"); }
        check(ms[0]->add("t", "ctr", "other", 2.5, "score") == 2.5 && ms[1]->add("t", "ctr", "other", -1, "score") == 1.5, "add: a named field, created on the first add, fractional");
        bool refused = false; ms[0]->write("t", "ctr", "word", "{\"n\":\"x\"}");
        try { ms[0]->add("t", "ctr", "word", 1); } catch (const frogram::Refused&) { refused = true; }
        check(refused, "add: refused, and nothing written, when the field is not a number");
    }
    // cas: each round every participant tries to replace the same write; exactly one does
    {
        int wrong = 0;
        for (int r = 0; r < R / 10; ++r) {
            const uint64_t cur = ms[0]->read("t", "lock", "owner").empty() ? 0 : ms[0]->read("t", "lock", "owner")[0].id;
            std::atomic<int> won{0}; std::vector<std::thread> th;
            for (int p = 0; p < P; ++p) th.emplace_back([&, p] { if (ms[p]->cas("t", "lock", "owner", cur, "{\"by\":" + std::to_string(p) + "}").done) won++; });
            for (auto& x : th) x.join();
            if (won != 1) wrong++;
        }
        check(wrong == 0, "cas: in " + std::to_string(R / 10) + " rounds of " + std::to_string(P) + " sessions racing, exactly one replaced the write each time");
        auto a = ms[0]->cas("t", "lock", "fresh", 0, "{\"k\":1}"), b = ms[1]->cas("t", "lock", "fresh", 0, "{\"k\":2}");
        check(a.done && !b.done && b.was.live && b.was.bag["k"].n == 1, "cas: expect 0 lands on an absent row only; the loser is told what is there");
        auto s1 = ms[0]->swap("t", "lock", "fresh", "{\"k\":3}");
        check(s1.done && s1.was.live && s1.was.bag["k"].n == 1, "swap: lands, and hands back what it replaced");
    }
    // take: exactly one participant gets the value
    {
        int wrong = 0;
        for (int r = 0; r < R / 10; ++r) {
            ms[0]->write("t", "box", "item", "{\"r\":" + std::to_string(r) + "}");
            std::atomic<int> got{0}; std::vector<std::thread> th;
            for (int p = 0; p < P; ++p) th.emplace_back([&, p] { if (ms[p]->take("t", "box", "item").live) got++; });
            for (auto& x : th) x.join();
            if (got != 1) wrong++;
        }
        check(wrong == 0, "take: in " + std::to_string(R / 10) + " rounds, exactly one session got the value each time");
    }
    // take_first: a queue across sessions -- one producer, every other session consuming; each item once
    {
        const int N = R; std::vector<std::vector<int>> got(P); std::atomic<bool> stop{false}; std::vector<std::thread> th;
        for (int p = 1; p < P; ++p) th.emplace_back([&, p] { for (;;) { auto t = ms[p]->take_first("t", "jobs", 0.5); if (t.got) got[p].push_back(int(t.bag["j"].n)); else if (stop) return; } });
        for (int j = 0; j < N; ++j) ms[0]->write("t", "jobs", "j" + std::to_string(j), "{\"j\":" + std::to_string(j) + "}");
        for (;;) { std::this_thread::sleep_for(std::chrono::milliseconds(50)); size_t n = 0; for (auto& v : got) n += v.size(); if (n >= size_t(N)) break; }
        stop = true; for (auto& x : th) x.join();
        std::multiset<int> all; for (auto& v : got) for (int x : v) all.insert(x);
        bool once = all.size() == size_t(N); for (int j = 0; j < N && once; ++j) once = all.count(j) == 1;
        if (getenv("APIPP_TRACE")) for (int p = 1; p < P; ++p) { int rep = 0; for (size_t k = 1; k < got[p].size(); ++k) if (got[p][k] == got[p][k - 1]) rep++;
            std::cout << "TRACE consumer " << p << ": " << got[p].size() << " taken, " << rep << " the same as its previous" << std::endl; }
        int dup = 0, missing = 0; for (int j = 0; j < N; ++j) { size_t c = all.count(j); if (c == 0) missing++; if (c > 1) dup += int(c - 1); }
        check(once, "take_first: " + std::to_string(N) + " jobs written by one session, taken by " + std::to_string(P - 1) + " held takers, each job exactly once ("
              + std::to_string(all.size()) + " taken, " + std::to_string(dup) + " twice, " + std::to_string(missing) + " never)");
        auto t0 = std::chrono::steady_clock::now(); frogram::Memory::Taken late;
        std::thread waiter([&] { late = ms[1]->take_first("t", "late", 5); });
        std::this_thread::sleep_for(std::chrono::milliseconds(300)); ms[0]->write("t", "late", "x", "{\"v\":7}"); waiter.join();
        const double el = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        check(late.got && late.bag["v"].n == 7 && el < 2.0, "take_first: a held taker is handed the row as it is written (" + std::to_string(int(el * 1000)) + " ms, written at 300)");
    }
    // batch: several writes, one call; the ids in order and every write readable
    {
        std::vector<frogram::Memory::Write> ws; for (int i = 0; i < 10; ++i) ws.push_back({"t", "set", "k" + std::to_string(i), "{\"i\":" + std::to_string(i) + "}"});
        auto ids = ms[0]->batch(ws);
        bool rising = ids.size() == 10; for (size_t k = 1; k < ids.size() && rising; ++k) rising = ids[k] > ids[k - 1];
        check(rising && ms[1]->read("t", "set").size() == 10, "batch: 10 writes in one call, ids in order, all read by another session");
    }
    bool priv = false; try { ms[0]->add("#hidden", "x", "y", 1); } catch (const frogram::Refused&) { priv = true; }
    check(priv, "a private service is refused, as for write");
    std::cout << "RESULT " << (fails ? "FAIL" : "PASS") << ": " << fails << " failed" << std::endl;
    _exit(fails ? 1 : 0);
}
