// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_ribbit_pp.cpp -- API++ [API_PLUS_PLUS_V2] through real sessions: one process holds the memory and answers
// other sessions on a port; every participant here is its own session to it, as on separate machines. Nothing waits
// by reading the memory again: each check waits on what its held reads were given.
//
//   test-ribbit-pp [PORT]          -DPP_BROKEN=1..5 (ribbit_pp.cpp), -DGONE_BROKEN=1..2 (ram_host.cpp), -DLINGER_BROKEN=1..2 (cellwire.hpp, ram_host.cpp): each check goes red
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
// Every session stays open until the process exits: a session that ends takes what it wrote with it, and the held reads
// on those families are told -- into callbacks whose checks have returned. The one check that ends a session on purpose
// opens its own (gone_with_its_writer).
static std::vector<std::shared_ptr<Space>>* kept = new std::vector<std::shared_ptr<Space>>;
static std::mutex kept_m;
static std::shared_ptr<Space> session() { auto x = std::make_shared<Space>(at, "pp"); std::lock_guard<std::mutex> l(kept_m); kept->push_back(x); return x; }

static void pubsub() {
    const int W = 4, N = 1000;
    auto sub = session(); Seen s; std::map<std::string, std::vector<uint64_t>> got; uint64_t missed = 0;
    pp::subscribe(*sub, "pp.msg", "t", -1, [&](const pp::Delivery& d) { s.add([&] { got[d.writer].push_back(d.n); }); },
                  [&](const std::string&, const std::string&, uint64_t g) { s.add([&] { missed += g; }); });
    // the publishers stay connected until everything is read: a publisher's cells go with its session
    std::vector<std::shared_ptr<Space>> pubs; for (int w = 0; w < W; ++w) pubs.push_back(session());
    std::vector<std::thread> ts;
    for (int w = 0; w < W; ++w) ts.emplace_back([w, me = pubs[w].get()] {
        pp::Publisher p(*me, "pp.msg", "w" + std::to_string(w), {"v"});
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
    // flush says the write has left its writer, not that it has landed: the reader holds a read until it has
    const auto w3row = r->read("pp.last", "t", "w3", 0, 10);
    SpaceRow x; bool ok = !w3row.empty() && pp::latest(*r, "pp.last", "t", x) && x.instance == "w3";
    const auto w1row = r->read("pp.last", "t", "w1", 0, 10);
    pp::offer(*w1, "pp.last", "t", "w1", "{\"v\":4}"); w1->flush(10);
    const bool landed = !w1row.empty() && !r->read("pp.last", "t", "w1", (long long)w1row.front().id, 10).empty();
    SpaceRow y; ok = ok && landed && pp::latest(*r, "pp.last", "t", y) && y.instance == "w1" && y.fields() && y.fields()->num("v") == 4;
    check(ok, "offer/latest: three writers' own cells, the newest wins (w3, then w1 again)");
}

static void ask_answer() {
    const int A = 3, N = 200;
    std::vector<std::shared_ptr<Space>> ss; std::vector<std::unique_ptr<pp::Asker>> as;
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


// [GONE_WITH_ITS_WRITER_V1] a participant's session ends: everything it wrote is gone, and every read held on those
// families is told so, in order -- the answerer learns its asker is gone, the finder that a capability went away
static void gone_with_its_writer() {
    auto ans_s = session(), fin_s = session();
    Seen s; int asked = 0, req_gone = 0, cap_live = 0, cap_gone = 0; std::string gone_asker;
    pp::Answerer ans(*ans_s, "pp.g", [&](const std::string& asker, const SpaceRow& r) {
        s.add([&] { if (r.gone) { req_gone++; gone_asker = asker; } else asked++; }); });
    pp::capable(*fin_s, "pp.gcap", [](const std::string& c) { return c == "resize"; },
                [&](const pp::Capable& c) { s.add([&] { if (c.gone) cap_gone++; else cap_live++; }); });
    {
        auto a = std::make_unique<Space>(at, "pp");     // not kept: its session ends at the end of this block
        pp::Asker asker(*a, "pp.g", "front-a", [](const SpaceRow&) {});
        for (int k = 0; k < 3; ++k) asker.ask(std::to_string(k), "{}");
        pp::announce(*a, "pp.gcap", "resize", "front-a");
        a->flush(10);
        s.until([&] { return asked == 3 && cap_live == 1; });
    }                                                   // its session ends here: the far end sees it close
    s.until([&] { return req_gone == 3 && cap_gone == 1; });
    auto r = session();
    const bool none_left = r->read("pp.g.ask", "front-a").empty() && r->read("pp.gcap", "all").empty();
    check(asked == 3 && req_gone == 3 && gone_asker == "front-a" && cap_gone == 1 && none_left,
          "gone with its writer: an asker's 3 requests and its announcement, its session ended; held reads told: " +
          std::to_string(req_gone) + " requests gone (3), " + std::to_string(cap_gone) + " capability gone (1); left behind: " +
          (none_left ? "none" : "some"));
}


// [LINGER_BY_FAMILY_V1] the writer chooses, per family, what happens to its rows when its session ends: they go at once,
// they stay N seconds and are reaped, or they stay
static void linger_by_family() {
    auto rd = session(); Seen s;
    std::map<std::string, double> gone_at; std::set<std::string> seen_live;
    using clk = std::chrono::steady_clock; clk::time_point ended;
    for (const char* t : {"goes", "stays2", "kept"})
        rd->watch("pp.l", t, (long long)rd->newest("pp.l", t), [&, t = std::string(t)](std::vector<SpaceRow>& rows) {
            for (auto& r : rows) s.add([&] { if (r.gone) gone_at[t] = std::chrono::duration<double>(clk::now() - ended).count(); else seen_live.insert(t); }); });
    {
        auto w = std::make_unique<Space>(at, "pp");     // not kept: its session ends at the end of this block
        w->linger("pp.l", "stays2", 2); w->linger("pp.l", "kept", Space::STAYS);
        for (const char* t : {"goes", "stays2", "kept"}) w->write("pp.l", t, "w", "{\"v\":1}");
        w->flush(10);
        s.until([&] { return seen_live.size() == 3; });
        ended = clk::now();
    }
    s.until([&] { return gone_at.count("goes") && gone_at.count("stays2"); }, 10);
    std::this_thread::sleep_for(std::chrono::milliseconds(1500));   // "kept" must not go
    const double g = gone_at.count("goes") ? gone_at["goes"] : -1, l = gone_at.count("stays2") ? gone_at["stays2"] : -1;
    const bool kept_there = !rd->read("pp.l", "kept").empty() && !gone_at.count("kept");
    char b[160]; snprintf(b, sizeof b, "goes at %.2f s (< 1), stays2 at %.2f s (2..4), kept %s", g, l, kept_there ? "still there" : "GONE");
    check(g >= 0 && g < 1 && l >= 1.9 && l < 4 && kept_there, std::string("linger by family: ") + b);
}

int main(int argc, char** argv) {
    const std::string port = argc > 1 ? argv[1] : "8937";
    Space host("here:127.0.0.1:" + port, "pp");
    at = "127.0.0.1:" + port;
    pubsub(); pubsub_missed(); capabilities(); last_value(); ask_answer(); gone_with_its_writer(); linger_by_family();
    printf("RESULT %s API++ (%d failed)\n", fails ? "FAIL" : "PASS", fails); fflush(stdout);
    _exit(fails ? 1 : 0);   // the held reads' threads are still parked in the memory
}
