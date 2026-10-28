// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_lf_primitives.cpp -- [LF_PRIMITIVES_V1] the in-process lock-free primitives (lf/primitives.hpp) under real contention, one check per
// promise each makes. Built with -DLF_BROKEN=<n> it breaks primitive n on purpose, and that check must go red:
//   1 TakeStack push as a plain store (pushes are lost)       2 update as load-then-store (adds are lost)
//   3 take as load-then-null (two takers get the same value)  4 a writer that reads the registration before it
//     publishes (a waiter registering in between sleeps on)
//   5 QuietSpan::end without the dedupe (a key delivered twice)
//
//   test-lf-primitives [threads] [rounds]
#include "lf/primitives.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <set>
#include <thread>
#include <vector>
#ifndef LF_BROKEN
#define LF_BROKEN 0
#endif
using namespace ribbit::lf;

static int bad = 0;
static void check(bool ok, const char* what, const std::string& detail = "") {
    std::printf("%s %s%s%s\n", ok ? "PASS" : "FAIL", what, detail.empty() ? "" : ": ", detail.c_str());
    if (!ok) bad++;
}

// ---- the broken variants, for the red runs ----------------------------------------------------------------------
template <class T> struct BrokenStack {             // 1: push is a load and a store, not a CAS
    std::atomic<T*> head{nullptr};
    bool push(T* x) { T* h = head.load(); x->next = h; std::this_thread::yield(); head.store(x); return h == nullptr; }
    std::vector<T*> take() { T* l = head.exchange(nullptr); std::vector<T*> o; for (T* x = l; x; x = x->next) o.push_back(x); std::reverse(o.begin(), o.end()); return o; }
};
struct Item { uint64_t producer, seq; Item* next = nullptr; };
#if LF_BROKEN == 1
using Stack = BrokenStack<Item>;
#else
using Stack = TakeStack<Item>;
#endif

struct Num { int64_t v; };

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    const int T = argc > 1 ? atoi(argv[1]) : int(std::max(4u, std::thread::hardware_concurrency()));
    const int R = argc > 2 ? atoi(argv[2]) : 20000;
    std::printf("== lock-free primitives, %d threads, %d rounds each%s\n", T, R, LF_BROKEN ? " (BROKEN on purpose)" : "");

    // 1. TakeStack: every push taken exactly once, each producer's pushes in its own order
    {
        Stack st; std::atomic<int> done{0}; std::vector<std::vector<uint64_t>> got(T);
        size_t taken = 0, dup = 0, order = 0; std::set<std::pair<uint64_t, uint64_t>> seen;
        std::vector<std::thread> th;
        for (int p = 0; p < T; ++p) th.emplace_back([&, p] { for (int i = 0; i < R; ++i) st.push(new Item{uint64_t(p), uint64_t(i)}); done++; });
        std::vector<uint64_t> last(T, 0); std::vector<bool> any(T, false);
        auto drain = [&] { for (Item* x : st.take()) {
            taken++; if (!seen.insert({x->producer, x->seq}).second) dup++;
            if (any[x->producer] && x->seq <= last[x->producer]) order++;
            any[x->producer] = true; last[x->producer] = x->seq; delete x; } };
        while (done.load() < T) drain();
        for (auto& t : th) t.join();
        drain();
        check(taken == size_t(T) * R && dup == 0, "TakeStack: every push taken exactly once",
              std::to_string(taken) + " of " + std::to_string(size_t(T) * R) + ", " + std::to_string(dup) + " twice");
        check(order == 0, "TakeStack: each producer's pushes come out in the order it pushed them", std::to_string(order) + " out of order");
    }

    // 2. update: T threads each add 1, R times, to one immutable counter; every add lands, and every caller saw its own total
    {
        std::atomic<const Num*> slot{new Num{0}}; std::vector<std::vector<int64_t>> totals(T);
        std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&, t] {
            for (int i = 0; i < R; ++i) {
                ebr::Guard g;
#if LF_BROKEN == 2
                const Num* cur = slot.load(); const Num* nx = new Num{cur->v + 1}; std::this_thread::yield();
                const Num* old = slot.exchange(nx); retire_default(old); totals[t].push_back(nx->v);
#else
                const Num* nx = update(slot, [](const Num* cur) { return new Num{(cur ? cur->v : 0) + 1}; });
                totals[t].push_back(nx->v);
#endif
            } });
        for (auto& t : th) t.join();
        std::set<int64_t> all; size_t n = 0; for (auto& v : totals) for (int64_t x : v) { all.insert(x); n++; }
        ebr::Guard g;
        check(slot.load()->v == int64_t(T) * R, "update: every add lands", std::to_string(slot.load()->v) + " of " + std::to_string(int64_t(T) * R));
        check(all.size() == n, "update: every caller got its own total (no two the same)", std::to_string(n - all.size()) + " repeated");
    }

    // 3. take: T threads race to take one value, R times; exactly one gets it each time
    {
        std::atomic<const Num*> slot{nullptr}; std::atomic<int> winners{0}, round{-1}, ready{0};
        int wrong = 0;
        std::vector<std::thread> th;
        for (int t = 0; t < T; ++t) th.emplace_back([&] {
            for (int r = 0; r < R / 10; ++r) {
                while (round.load() != r) { if (round.load() == -2) return; std::this_thread::yield(); }
#if LF_BROKEN == 3
                const Num* p = slot.load(); std::this_thread::yield(); if (p) slot.store(nullptr);
#else
                const Num* p = take(slot);
#endif
                if (p) winners++;
                ready++;
            } });
        for (int r = 0; r < R / 10; ++r) {
            winners = 0; slot.store(new Num{r}); ready = 0; round = r;
            while (ready.load() < T) std::this_thread::yield();
            if (winners.load() != 1) wrong++;
        }
        round = -2; for (auto& t : th) t.join();
        check(wrong == 0, "take: exactly one caller gets the value", std::to_string(wrong) + " of " + std::to_string(R / 10) + " rounds wrong");
    }

    // 4. WaitWord: a waiter that registered before checking is always woken by a writer's bump; a bump with nobody
    //    registered writes nothing
    {
        // One write at a time, each to a waiter that is either parked or on its way to parking: the writer waits until
        // the waiter has taken write i before it makes write i+1, and the waiter does its own work (unregistered)
        // between reads, as a reader does.
        WaitWord w; std::atomic<int> data{0}, consumed{0}; std::atomic<int> stuck{0};
        const int N = R / 10;
        std::thread waiter([&] {
            for (int i = 1; i <= N; ++i) {
                for (;;) {
                    w.watch(); uint32_t s = w.seen();
                    if (data.load(std::memory_order_seq_cst) >= i) { w.unwatch(); break; }
                    if (!w.wait(s, 50000000)) { stuck++; w.unwatch(); break; }    // 50 ms asleep with the write there: lost
                    w.unwatch();
                }
                consumed.store(i, std::memory_order_seq_cst);
                std::this_thread::yield();                                      // its own work, not registered
            } });
        for (int i = 1; i <= N; ++i) {
            while (consumed.load(std::memory_order_seq_cst) < i - 1) std::this_thread::yield();
#if LF_BROKEN == 4
            // the registration read before the data is published: a waiter registering in between is never woken
            const bool parked = w.waiters.load(std::memory_order_seq_cst) != 0;
            std::this_thread::yield();                     // a preemption right here
            data.store(i, std::memory_order_seq_cst);
            if (parked) { w.gen.fetch_add(1); futex_wake(w.gen); }
#else
            data.store(i, std::memory_order_seq_cst);
            w.bump();
#endif
        }
        waiter.join();
        check(stuck == 0, "WaitWord: no waiter sleeps through a write it registered for", std::to_string(stuck.load()) + " lost wake-ups");
        WaitWord q; uint32_t g0 = q.seen(); bool woke = q.bump();
        check(!woke && q.seen() == g0, "WaitWord: a bump with nobody parked writes nothing");
    }

    // 5. QuietSpan: inside a span nothing is woken; at its end each distinct key is delivered once
    {
        std::vector<int> delivered;
        QuietSpan<int>::begin();
        bool held = true; for (int k : {3, 1, 3, 2, 1, 3}) held &= QuietSpan<int>::hold(k);
#if LF_BROKEN == 5
        // no dedupe: every hold delivered
        delivered = {3, 1, 3, 2, 1, 3}; QuietSpan<int>::end([](int) {});
#else
        QuietSpan<int>::end([&](int k) { delivered.push_back(k); });
#endif
        check(held && delivered == std::vector<int>({1, 2, 3}), "QuietSpan: each key held, then delivered once at the end");
        check(!QuietSpan<int>::hold(9), "QuietSpan: outside a span nothing is held (the caller wakes now)");
    }

    std::printf("RESULT %s: %d failed\n", bad ? "FAIL" : "PASS", bad);
    return bad ? 1 : 0;
}
