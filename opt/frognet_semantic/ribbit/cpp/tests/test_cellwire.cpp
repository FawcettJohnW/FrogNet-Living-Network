// test_cellwire.cpp -- [CELL_WRITE_V1] writes encoded on several threads at once; at the "memory" each chain (a writer's
// writes of a family) is its own job, run on a pool of four, every chain at the same time. Every bag stored must be the
// bag its writer wrote, every cell must hold its last writer's newest write, and a difference whose reference the memory
// does not have must be refused (an error), never waited for. Four writers share one family (a box's publish window, one
// counter as a box's fronts number it), plus latest-value cells. Frames arrive as one connection carries them: each
// writer's in its own order, the writers interleaved at random. Prints bytes per write.
#include "cellwire.hpp"
#include <algorithm>
#include <condition_variable>
#include <cstdio>
#include <deque>
#include <map>
#include <mutex>
#include <random>
#include <thread>
using namespace cellwire;
static std::string b64(std::mt19937& R, int n) { static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; std::string s; for (int i = 0; i < n; i++) s += A[R() % 64]; return s; }
struct Out { std::string frame, sv, var, inst, bag; int t; };
int main() {
    Encoder enc; const std::string M = "FNW1"; const int T = 4, N = 5000, W = 16384;
    std::atomic<uint64_t> seq{0};
    std::vector<std::vector<Out>> outs(T);
    std::vector<std::thread> th;
    for (int t = 0; t < T; t++) th.emplace_back([&, t] {
        std::mt19937 R(100 + t);
        for (int k = 0; k < N; k++) {
            Out o; o.sv = "mqtt"; o.t = t;
            if (R() % 10) {
                const uint64_t n = ++seq; o.var = "msg.boxA"; o.inst = std::to_string(n % W);
                o.bag = "{\"b\":\"boxA\",\"e\":0,\"n\":" + std::to_string(n) + ",\"o\":0,\"p\":\"\",\"pr\":\"\",\"prq\":\"\",\"q\":\"" + b64(R, 22) +
                        "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eA==\",\"qos\":1,\"r\":0,\"si\":\"pub" + std::to_string(t) +
                        "\",\"sp\":" + std::to_string(40000 + t) + ",\"t\":\"load/" + std::to_string(t) + "\"}";
            } else {
                o.var = "retain"; o.inst = "r/" + std::to_string(R() % 3);   // shared cells: every writer writes them
                o.bag = "{\"live\":" + std::to_string(R() % 2) + ",\"p\":\"" + (R() % 2 ? std::string() : b64(R, 8)) + "\",\"t\":\"r/1\"}";
            }
            o.frame = enc.encode(M, o.sv, o.var, o.inst, o.bag);
            outs[t].push_back(std::move(o));
        }
    });
    for (auto& x : th) x.join();
    std::vector<const Out*> all; std::vector<size_t> at(T, 0); std::mt19937 S(7); size_t bytes = 0, win = 0, win_bytes = 0;
    while (all.size() < size_t(T * N)) { int t = int(S() % T); if (at[t] < outs[t].size()) { const Out* o = &outs[t][at[t]++]; all.push_back(o);
        bytes += o->frame.size(); if (o->var == "msg.boxA") { win++; win_bytes += o->frame.size(); } } }
    struct Fam { std::string sv, var; Chain chain; };
    std::map<std::pair<uint64_t, uint64_t>, Fam*> fams;          // the reader's alone, as on the memory's reader
    std::mutex mem_m; struct CellV { uint64_t wid = 0; uint32_t c = 0; const Out* who = nullptr; }; std::map<std::string, CellV> mem;
    std::atomic<int> bad{0}, refused{0}; std::atomic<size_t> done{0};
    std::mutex qm; std::condition_variable qcv; std::deque<std::function<void()>> q;   // the test's pool
    auto sched = [&](std::function<void()> f) { { std::lock_guard<std::mutex> g(qm); q.push_back(std::move(f)); } qcv.notify_one(); };
    std::vector<std::thread> js; std::atomic<bool> fin{false};
    for (int k = 0; k < 4; k++) js.emplace_back([&] { for (;;) { std::function<void()> f;
        { std::unique_lock<std::mutex> g(qm); qcv.wait_for(g, std::chrono::milliseconds(20), [&] { return !q.empty() || fin.load(); });
          if (q.empty()) { if (fin.load()) return; continue; } f = std::move(q.front()); q.pop_front(); }
        f(); } });
    auto step = [&](const Out* o, Frame& x, Fam* nm) {
        Chain& ch = nm->chain;
        const Ref* ref = (ch.last && ch.last_c + 1 == x.counter) ? ch.last.get() : nullptr;
        if (x.flags & F_NAMES) { nm->sv = x.sv; nm->var = x.var; }
        if (!(x.flags & F_KEYS) && !ref) { refused++; ch.last.reset(); ch.last_c = x.counter; done++; return; }   // an error, not a wait
        ribbit::BagArrP ba; auto nr = decode(x, ref, ba); const std::string bag = ba->json(); ch.last = std::move(nr); ch.last_c = x.counter;
        auto a = semtpl::detail::json_try(bag), b = semtpl::detail::json_try(o->bag);
        if (!a || !b || !semtpl::python_eq(*a, *b) || x.inst != o->inst || nm->var != o->var) { if (bad < 5) fprintf(stderr, "differs:\n wrote %s %s\n  got %s %s\n", o->inst.c_str(), o->bag.c_str(), x.inst.c_str(), bag.c_str()); bad++; }
        { std::lock_guard<std::mutex> g(mem_m);
          CellV& cv = mem[nm->var + "|" + x.inst]; cv.c = x.counter; cv.who = o; }   // one owner, its writes in its order: stored as they come
        done++;
    };
    for (const Out* o : all) {                                  // the reader: parse, append to its chain
        auto x = std::make_shared<Frame>(parse(o->frame, 5));
        Fam*& nm = fams[{x->slot, x->fam}]; if (!nm) nm = new Fam{};
        Fam* f = nm; f->chain.post([&, o, x, f] { step(o, *x, f); }, sched);
    }
    while (done.load() < all.size()) std::this_thread::sleep_for(std::chrono::milliseconds(5));
    fin = true; for (auto& j : js) j.join();
    int stale = 0;
    for (auto& kv : mem) { const Out* who = kv.second.who; const Out* newest = nullptr;
        for (auto& o : outs[who->t]) if (o.var + "|" + o.inst == kv.first) newest = &o;
        if (newest != who) { bool other = false; for (int t = 0; t < T; t++) if (t != who->t) for (auto& o : outs[t]) if (o.var + "|" + o.inst == kv.first) other = true;
                             if (!other) stale++; } }
    { Encoder e2; std::string f1 = e2.encode(M, "s", "v", "1", "{\"a\":1,\"b\":\"x\"}"), f2 = e2.encode(M, "s", "v", "2", "{\"a\":2,\"b\":\"x\"}");
      Frame x2 = parse(f2, 5); Chain c; const Ref* ref = (c.last && c.last_c + 1 == x2.counter) ? c.last.get() : nullptr;
      if ((x2.flags & F_KEYS) || ref) { printf("FAIL: a lone difference was not seen as one\n"); return 1; }
      e2.refused(x2.slot, x2.fam); std::string f3 = e2.encode(M, "s", "v", "3", "{\"a\":3,\"b\":\"x\"}");
      if (!(parse(f3, 5).flags & F_KEYS)) { printf("FAIL: after a refusal the chain did not start again whole\n"); return 1; } }
    if (stale) { printf("FAIL: %d cells hold an older write of the writer that wrote them last\n", stale); return 1; }
    {   // a chain whose bag changes its keys from one write to the next (a Redis piece is "q", the rest of it "p")
        Encoder e3; Chain c3; const char* bs[] = {"{\"k\":\"P\",\"n\":2,\"q\":\"QUJD\"}", "{\"k\":\"c\",\"n\":3,\"p\":\"tail\"}", "{\"k\":\"c\",\"n\":4,\"q\":\"REVG\"}", "{\"k\":\"c\",\"n\":5,\"q\":\"R0hJ\"}"};
        int k = 0;
        for (const char* b : bs) { std::string f = e3.encode(M, "redis", "cmd", "x/" + std::to_string(++k), b); Frame x = parse(f, 5);
            const Ref* ref = (c3.last && c3.last_c + 1 == x.counter) ? c3.last.get() : nullptr; ribbit::BagArrP oa; auto r = decode(x, ref, oa); const std::string out = oa->json(); c3.last = std::move(r); c3.last_c = x.counter;
            auto a = semtpl::detail::json_try(out), bb = semtpl::detail::json_try(std::string(b));
            if (!a || !bb || !semtpl::python_eq(*a, *bb)) { printf("FAIL: a change of keys within a chain: wrote %s got %s\n", b, out.c_str()); return 1; } }
    }
    printf("%s: %zu writes from %d threads, %d wrong, %d refused; window writes %.1f B each, all writes %.1f B\n",
           bad.load() || refused.load() ? "FAIL" : "PASS", all.size(), T, bad.load(), refused.load(), double(win_bytes) / win, double(bytes) / all.size());
    return bad.load() != 0 || refused.load() != 0;
}
