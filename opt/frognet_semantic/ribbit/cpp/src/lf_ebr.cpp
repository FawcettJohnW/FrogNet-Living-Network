// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lf_ebr.cpp -- see include/lf/ebr.hpp
#include "lf/ebr.hpp"
#include <cstdlib>
namespace ribbit { namespace lf {

namespace ebr {

Stats& stats() { static Stats* s = new Stats(); return *s; }
Global& global() { static Global* g = new Global(); return *g; }

struct Local { int depth = 0; uint64_t at = 0; uint64_t retires = 0; };
static thread_local Local tl;

static uint64_t count_in(Global& g) {
    for (;;) {
        const uint64_t e = g.epoch.load(std::memory_order_seq_cst);
        g.of[e % 3].in.fetch_add(1, std::memory_order_seq_cst);
        if (g.epoch.load(std::memory_order_seq_cst) == e) return e;
        g.of[e % 3].in.fetch_sub(1, std::memory_order_seq_cst);
    }
}
static bool something_retired(Global& g) {
    return g.of[0].retired.load(std::memory_order_acquire) || g.of[1].retired.load(std::memory_order_acquire) || g.of[2].retired.load(std::memory_order_acquire);
}
static void free_list(Retired* list) {
    long n = 0;
    while (list) { Retired* r = list; list = list->next; r->del(r->p); delete r; ++n; }
    if (n) { stats().pending.fetch_sub(n, std::memory_order_relaxed); stats().freed.fetch_add(n, std::memory_order_relaxed); }
}
// The epoch moves from e to e+1 when nobody is counted into e-1. The mover is counted into e while it frees the list
// of e-1, so the epoch cannot reach e+2 -- which uses that same list -- under it.
static void move(Global& g) {
    stats().collects.fetch_add(1, std::memory_order_relaxed);
    for (;;) {
        const uint64_t e = count_in(g); bool moved = false;
        if (g.of[(e + 2) % 3].in.load(std::memory_order_seq_cst) == 0) {
            uint64_t was = e;
            if (g.epoch.compare_exchange_strong(was, e + 1, std::memory_order_seq_cst)) {
                moved = true; stats().advances.fetch_add(1, std::memory_order_relaxed);
                free_list(g.of[(e + 2) % 3].retired.exchange(nullptr, std::memory_order_acq_rel));
            }
        }
        const bool last = g.of[e % 3].in.fetch_sub(1, std::memory_order_seq_cst) == 1;
        if (!(moved && last && something_retired(g))) return;       // someone still in will notice when it leaves
    }
}

void enter() {
    if (tl.depth++ > 0) return;
    tl.at = count_in(global());
}
void exit() {
    if (--tl.depth > 0) return;
    Global& g = global(); const uint64_t e = tl.at;
    if (g.of[e % 3].in.fetch_sub(1, std::memory_order_seq_cst) == 1                // the last out of an epoch that has ended
        && g.epoch.load(std::memory_order_seq_cst) != e && something_retired(g)) move(g);
}

void collect() { move(global()); }

void retire(void* p, void (*del)(void*)) {
    Global& g = global();
    Retired* n = new Retired{p, del, nullptr};
    enter();                                                          // counted in while it is put on a list
    std::atomic<Retired*>& list = g.of[g.epoch.load(std::memory_order_seq_cst) % 3].retired;
    Retired* head = list.load(std::memory_order_relaxed);
    do { n->next = head; } while (!list.compare_exchange_weak(head, n, std::memory_order_release, std::memory_order_relaxed));
    stats().pending.fetch_add(1, std::memory_order_relaxed);
    const bool mine = ++tl.retires % 64 == 0;
    exit();
    if (mine) move(g);
}

void on_thread_exit() {}

}

} }  // namespace ribbit::lf
