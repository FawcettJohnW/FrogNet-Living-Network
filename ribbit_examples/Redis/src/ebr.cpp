#include "ebr.hpp"
#include "memory.hpp"
#include <cstdlib>

namespace ebr {

Global& global() { static Global* g = new Global(); return *g; }

struct Local { ThreadRec* rec = nullptr; int depth = 0; ~Local() { if (rec) on_thread_exit(); } };
static thread_local Local tl;

ThreadRec& me() {
    if (tl.rec) return *tl.rec;
    Global& g = global();
    for (int i = 0; i < MAX_THREADS; i++) {
        bool f = false;
        if (g.threads[i].used.compare_exchange_strong(f, true)) {
            tl.rec = &g.threads[i];
            int h = g.high.load(); while (h < i + 1 && !g.high.compare_exchange_weak(h, i + 1)) {}
            return *tl.rec;
        }
    }
    abort();   // more threads than slots: a configuration error, not something to paper over
}

void enter() {
    if (tl.depth++ > 0) return;
    ThreadRec& r = me();
    r.epoch.store(global().epoch.load(std::memory_order_acquire), std::memory_order_release);
}
void exit() {
    if (--tl.depth > 0) return;
    me().epoch.store(0, std::memory_order_release);
}

static void free_list(std::vector<Retired>& v) { long n = (long)v.size(); for (auto& r : v) r.del(r.p); v.clear(); if (n) { memstats().retired_pending.fetch_sub(n, std::memory_order_relaxed); memstats().retired_freed.fetch_add(n, std::memory_order_relaxed); } }

// The epoch can advance when no active thread is still in an older epoch. Then everything retired two
// epochs ago cannot be referenced by anyone and is freed.
void collect() {
    Global& g = global();
    memstats().collects.fetch_add(1, std::memory_order_relaxed);
    uint64_t e = g.epoch.load(std::memory_order_acquire);
    int high = g.high.load(std::memory_order_acquire);
    for (int i = 0; i < high; i++) {
        uint64_t te = g.threads[i].epoch.load(std::memory_order_acquire);
        if (te != 0 && te != e) return;              // someone is still in an older epoch
    }
    if (!g.epoch.compare_exchange_strong(e, e + 1)) return;
    memstats().epoch_advances.fetch_add(1, std::memory_order_relaxed);
    // epoch e+1 begun: lists from (e+1) % 3 == (e-2) % 3 are unreachable
    ThreadRec& r = me();
    if (r.list_epoch[(e + 1) % 3] + 2 <= e + 1) free_list(r.lists[(e + 1) % 3]);
    // orphans: take the stack and free the same generation, re-push the rest
    Global::Orphan* o = g.orphans.exchange(nullptr);
    while (o) {
        Global::Orphan* nx = o->next.load();
        free_list(o->v[(e + 1) % 3]);
        bool empty = o->v[0].empty() && o->v[1].empty() && o->v[2].empty();
        if (empty) delete o;
        else { Global::Orphan* head = g.orphans.load(); do { o->next.store(head); } while (!g.orphans.compare_exchange_weak(head, o)); }
        o = nx;
    }
}

// Every retiring thread frees its own garbage as soon as the epoch has moved past it -- not only the thread
// that happened to advance the epoch. With several writers, whoever advances would otherwise free only its own
// lists and the others' would wait for their own next successful advance.
void retire(void* p, void (*del)(void*)) {
    ThreadRec& r = me();
    uint64_t e = global().epoch.load(std::memory_order_acquire);
    // each slot is stamped with the epoch its contents were retired in; anything stamped <= e-2 is free
    for (int s = 0; s < 3; s++) if (!r.lists[s].empty() && r.list_epoch[s] + 2 <= e) free_list(r.lists[s]);
    if (r.list_epoch[e % 3] != e) { if (!r.lists[e % 3].empty()) free_list(r.lists[e % 3]); r.list_epoch[e % 3] = e; }   // stale contents were from <= e-3
    r.lists[e % 3].push_back(Retired{p, del});
    memstats().retired_pending.fetch_add(1, std::memory_order_relaxed);
    if (++r.counter % 64 == 0) collect();
}

void on_thread_exit() {
    if (!tl.rec) return;
    Global& g = global();
    ThreadRec& r = *tl.rec;
    auto* o = new Global::Orphan();
    for (int i = 0; i < 3; i++) o->v[i].swap(r.lists[i]);
    Global::Orphan* head = g.orphans.load(); do { o->next.store(head); } while (!g.orphans.compare_exchange_weak(head, o));
    r.epoch.store(0); r.counter = 0;
    r.used.store(false);
    tl.rec = nullptr;
}

}
