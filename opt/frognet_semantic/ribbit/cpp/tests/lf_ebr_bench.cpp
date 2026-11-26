// the RAM host's reclaimer under load: T threads, each takes a guard, reads a shared pointer, and one time in 64
// replaces it and retires the old one. Reports nanoseconds per guard and whether everything retired was freed.
#include "lf/ebr.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>
#include <vector>
using namespace ribbit::lf;
static std::atomic<long> live{0};
struct Obj { long v; explicit Obj(long x) : v(x) { live.fetch_add(1, std::memory_order_relaxed); } ~Obj() { v = -1; live.fetch_sub(1, std::memory_order_relaxed); } };
int main(int argc, char** argv) {
    const int T = argc > 1 ? atoi(argv[1]) : 4; const long N = argc > 2 ? atol(argv[2]) : 2000000;
    std::atomic<Obj*> slot[16]; for (auto& s : slot) s.store(new Obj(1));
    std::atomic<long> bad{0};
    auto t0 = std::chrono::steady_clock::now();
    std::vector<std::thread> th;
    for (int k = 0; k < T; ++k) th.emplace_back([&, k] {
        for (long i = 0; i < N; ++i) {
            ebr::Guard g; auto& s = slot[(k + i) & 15];
            Obj* o = s.load(std::memory_order_acquire);
            if (o->v < 0) bad.fetch_add(1);
            if ((i & 63) == 0) { Obj* n = new Obj(o->v + 1); Obj* was = s.exchange(n); ebr::retire(was); }
        }
        ebr::on_thread_exit(); });
    for (auto& x : th) x.join();
    const double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    for (int k = 0; k < 2000 && live.load() > 16; ++k) { ebr::Guard g; ebr::collect(); }
    for (int k = 0; k < 64; ++k) ebr::collect();
    printf("%d thread(s): %.1f ns a guard; read after free: %ld; objects alive at the end: %ld (16 slots)\n", T, s * 1e9 / (double(N) * T), bad.load(), live.load());
    return bad.load() != 0;
}
