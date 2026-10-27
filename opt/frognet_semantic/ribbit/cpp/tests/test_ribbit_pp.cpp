// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_ribbit_pp.cpp -- API++ [API_PLUS_PLUS_V2] through real sessions: one process holds the memory and answers
// other sessions on a port; every participant here is its own session to it, as on separate machines. Nothing waits
// by reading the memory again: each check waits on what its held reads were given.
//
//   test-ribbit-pp [PORT]          -DPP_BROKEN=1..5 (ribbit_pp.cpp): each check below goes red
#include "ribbit_pp.hpp"
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <map>
#include <mutex>
#include <set>
#include <thread>

using namespace ribbit;
static int fails = 0;
static void check(bool ok, const std::string& what) { printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str()); fflush(stdout); if (!ok) fails++; }

// what a held read was given, and a wait for it (the test's own lock, not the memory's)
struct Seen {
    std::mutex mu; std::condition_variable cv;
    template <class F> bool until(F done, double s = 20) {
        std::unique_lock<std::mutex> l(mu); return cv.wait_for(l, std::chrono::duration<double>(s), [&] { return done(); });
    }
    template <class F> void add(F f) { { std::lock_guard<std::mutex> l(mu); f(); } cv.notify_all(); }
};

static std::string at;
static std::unique_ptr<Space> session() { return std::make_unique<Space>(at, "pp"); }

static void pubsub() {
    const int W = 4, N = 1000;
    auto sub = session(); Seen s; std::map<std::string, std::vector<uint64_t>> got; uint64_t missed = 0;
    pp::subscribe(*sub, "pp.msg", "t", -1, [&](const pp::Delivery& d) { s.add([&] { got[d.writer].push_back(d.n); }); },
                  [&](const std::string&, const std::string&, uint64_t g) { s.add([&] { missed += g; }); });
    std::vector<std::thread> ts;
    for (int w = 0; w < W; ++w) ts.emplace_back([w] {
        auto me = session(); pp::Publisher p(*me, "pp.msg", "w" + std::to_string(w), {"v"});
        for (int k = 0; k < N; ++k) p.publish("t", {{"v", pyv::Int::of(k)}});
        me->flush(10);
    });
    for (auto& t : ts) t.join();
    s.until([&] { size_t n = 0; for (auto& g : got) n += g.second.size(); return n >= size_t(W * N); });
    bool in_order = got.size() == size_t(W); size_t total = 0;
    for (auto& g : got) { total += g.second.size(); for (size_t k = 0; k < g.second.size(); ++k) if (g.second[k] != k + 1) in_order = false; }
    check(in_order && total == size_t(W * N) && missed == 0,
          "publish/subscribe: 4 writers x 1000 on their own sessions, each writer's 1..1000 in its order (got " + std::to_string(total) +
          ", missed " + std::to_string(missed) + ")");
}

static void pubsub_missed() {
    auto sub = session(); auto pubs = session(); Seen s; std::vector<uint64_t> got; uint64_t missed = 0;
    std::mutex gate; gate.lock();                                    // the reader is held up: the writer's ring moves past it
    bool first = true;
    pp::subscribe(*sub, "pp.miss", "t", -1, [&](const pp::Delivery& d) {
        if (first) { first = false; s.add([&] { got.push_back(d.n); }); std::lock_guard<std::mutex> l(gate); return; }
        s.add([&] { got.push_back(d.n); }); },
        [&](const std::string&, const std::string&, uint64_t g) { s.add([&] { missed += g; }); });
    pp::Publisher p(*pubs, "pp.miss", "w", {"v"}, 8);
    p.publish("t", {{"v", pyv::Int::of(0)}}); pubs->flush(10);
    s.until([&] { return !got.empty(); });
    for (int k = 1; k <= 100; ++k) p.publish("t", {{"v", pyv::Int::of(k)}});
    pubs->flush(10);
    gate.unlock();
    s.until([&] { return !got.empty() && got.back() == 101; });
    check(missed == 92 && got.back() == 101,
          "subscribe says what it missed: a ring of 8 moved 101 publishes past a held-up reader; missed " + std::to_string(missed) + " (92)");
}

static void capabilities() {
    auto a = session(), b = session(), f = session();
    pp::announce(*a, "pp.cap", "resize", "A"); pp::announce(*b, "pp.cap", "resize", "B"); pp::announce(*b, "pp.cap", "print", "B");
    a->flush(10); b->flush(10);
    Seen s; std::multiset<std::string> who;
    pp::capable(*f, "pp.cap", [](const std::string& c) { return c == "resize"; }, [&](const pp::Capable& c) { s.add([&] { who.insert(c.who); }); });
    auto c = session(), d = session();
    pp::announce(*c, "pp.cap", "resize", "C"); pp::announce(*d, "pp.cap", "resize", "D"); pp::announce(*d, "pp.cap", "print", "D");
    c->flush(10); d->flush(10);
    s.until([&] { return who.size() >= 4; });
    std::this_thread::sleep_for(std::chrono::milliseconds(300));   // nothing more, and nothing twice
    std::string all; for (auto& w : who) all += w;
    check(all == "ABCD", "capable: \"resize\" announced by A, B before the finder and C, D after; found " + all + " (ABCD, once each)");
}

static void last_value() {
    auto w1 = session(), w2 = session(), w3 = session(), r = session();
    pp::offer(*w1, "pp.last", "t", "w1", "{\"v\":1}"); w1->flush(10);
    pp::offer(*w2, "pp.last", "t", "w2", "{\"v\":2}"); w2->flush(10);
    pp::offer(*w3, "pp.last", "t", "w3", "{\"v\":3}"); w3->flush(10);
    SpaceRow x; bool ok = pp::latest(*r, "pp.last", "t", x) && x.instance == "w3";
    pp::offer(*w1, "pp.last", "t", "w1", "{\"v\":4}"); w1->flush(10);
    SpaceRow y; ok = ok && pp::latest(*r, "pp.last", "t", y) && y.instance == "w1" && y.fields() && y.fields()->num("v") == 4;
    check(ok, "offer/latest: three writers' own cells, the newest wins (w3, then w1 again)");
}

static void ask_answer() {
    const int A = 3, N = 200;
    std::vector<std::unique_ptr<Space>> ss; std::vector<std::unique_ptr<pp::Asker>> as;
    Seen s; std::map<std::string, std::set<std::string>> got; bool wrong = false;
    for (int k = 0; k < A; ++k) {
        ss.push_back(session()); const std::string me = "a" + std::to_string(k);
        as.push_back(std::make_unique<pp::Asker>(*ss.back(), "pp.q", me, [&, me](const SpaceRow& r) {
            auto f = r.fields(); const std::string want = me + ":" + r.instance;
            s.add([&] { if (!f || f->str("echo") != want) wrong = true; got[me].insert(r.instance); }); }));
    }
    for (int k = 0; k < A; ++k) { for (int i = 0; i < N; ++i) as[k]->ask(std::to_string(i), "{}"); ss[k]->flush(10); }   // before any answerer
    auto ans_s = session(); std::unique_ptr<pp::Answerer> ans;
    ans = std::make_unique<pp::Answerer>(*ans_s, "pp.q", [&](const std::string& asker, const SpaceRow& r) {
        ans->answer(asker, r.instance, "{\"echo\":\"" + asker + ":" + r.instance + "\"}"); });
    s.until([&] { size_t n = 0; for (auto& g : got) n += g.second.size(); return n >= size_t(A * N); });
    size_t n = 0; for (auto& g : got) n += g.second.size();
    check(n == size_t(A * N) && !wrong, "ask/answer: 3 askers x 200 asked before the answerer existed; replies to each its own: " + std::to_string(n) + " (600)");
}

int main(int argc, char** argv) {
    const std::string port = argc > 1 ? argv[1] : "8937";
    Space host("here:127.0.0.1:" + port, "pp");
    at = "127.0.0.1:" + port;
    pubsub(); pubsub_missed(); capabilities(); last_value(); ask_answer();
    printf("RESULT %s API++ (%d failed)\n", fails ? "FAIL" : "PASS", fails); fflush(stdout);
    _exit(fails ? 1 : 0);   // the held reads' threads are still parked in the memory
}
