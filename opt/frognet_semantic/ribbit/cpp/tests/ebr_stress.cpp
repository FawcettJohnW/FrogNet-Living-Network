// many threads, each new: guard, read a slot's object, replace it, retire the old one. Under AddressSanitizer a
// freed object that is still read aborts the run; at the end everything retired must have been freed.
#include "ebr.hpp"
#include <atomic>
#include <cstdio>
#include <thread>
#include <vector>
static std::atomic<long> live{0};
struct Obj { long v[4]; Obj(long x) { for (auto& e : v) e = x; live.fetch_add(1); } ~Obj() { v[0] = -1; live.fetch_sub(1); } };
int main(int argc, char** argv) {
    const int rounds = argc > 1 ? atoi(argv[1]) : 40, per = 200;
    ribbit::Ebr e; std::atomic<const Obj*> slot[8];
    for (auto& s : slot) s.store(new Obj(1));
    std::atomic<long> bad{0}, ops{0};
    for (int r = 0; r < rounds; ++r) {
        std::vector<std::thread> t;
        for (int k = 0; k < per; ++k) t.emplace_back([&, k] {
            for (int i = 0; i < 300; ++i) {
                auto g = e.guard(); auto& s = slot[(k + i) & 7];
                const Obj* o = g.protect(s);
                if (o->v[0] != o->v[3] || o->v[0] < 0) bad.fetch_add(1);
                if ((i & 3) == 0) { const Obj* n = new Obj(o->v[0] + 1); const Obj* was = s.exchange(n); e.retire(was); }
                ops.fetch_add(1);
            } });
        for (auto& x : t) x.join();
    }
    e.drain();
    const long at_end = live.load(); for (auto& s : slot) delete s.load();
    printf("ebr stress: %ld operations by %d threads, each new; objects read after their free: %ld; objects alive at the end: %ld (8 slots)\n", ops.load(), rounds * per, bad.load(), at_end);
    return bad.load() || at_end != 8;
}
