/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// ram_memory.hpp -- the C++ RAM host's memory. [ROW_LOCKS_V1] John 2026-09-27:
//   "The C++ engine needs row level locking and no more. The locks are multi-reader/single writer."
//   Waits: a condition belongs to the WAIT. a, b and c together identify one storage location (no hierarchy, no
//   root). A wait on one exact location hangs its trigger in a linked list off that location's envelope (the row's
//   metadata, where its write time is). A wait with free coordinates names no location, so it hangs on the shared
//   space itself: a table created when the first such wait is made, keyed by the pattern's FIXED coordinates. A write
//   to a.b.c fires its own envelope's triggers and the space's triggers at a.*.*, *.b.*, *.*.c, a.b.*, a.*.c, *.b.c.
//   Nothing is built until someone waits.
//
// Locks: each row has a std::shared_mutex (readers share it, a writer holds it alone). There is no other lock on the
// memory. Which rows exist, which variables a service has, and the trigger lists are lock-free singly linked lists:
// nodes are appended with a compare-and-swap and never unlinked -- a row that is removed is marked dead and revived
// when its address is written again; a trigger whose waiter has gone is marked free and claimed by the next waiter on
// the same key -- so no reader ever follows freed memory and nothing needs reclamation. A trigger's condition
// variable needs a mutex to sleep on; that mutex belongs to the one waiter using the trigger and guards nothing else.
#pragma once
#include <algorithm>
#include <atomic>
#include <thread>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <vector>

namespace ramm {

struct Cell { uint64_t id; std::string service, variable, instance, bag; double updated; };

// A trigger: one waiter's condition. fired is set by a writer and cleared by the waiter.
struct Trigger {
    std::mutex m; std::condition_variable cv; bool fired = false;
    std::atomic<bool> in_use{true};
    Trigger* next = nullptr;                      // set once, before publication
};

// A lock-free list of triggers: push new, or claim one whose waiter has gone.
struct TriggerList {
    std::atomic<Trigger*> head{nullptr};
    Trigger* acquire() {
        for (Trigger* t = head.load(std::memory_order_acquire); t; t = t->next) {
            bool f = false;
            if (t->in_use.compare_exchange_strong(f, true, std::memory_order_acq_rel)) { std::lock_guard<std::mutex> g(t->m); t->fired = false; return t; }
        }
        Trigger* t = new Trigger; Trigger* h = head.load(std::memory_order_relaxed);
        do { t->next = h; } while (!head.compare_exchange_weak(h, t, std::memory_order_release, std::memory_order_relaxed));
        return t;
    }
    void fire() {
        for (Trigger* t = head.load(std::memory_order_acquire); t; t = t->next)
            if (t->in_use.load(std::memory_order_acquire)) { { std::lock_guard<std::mutex> g(t->m); t->fired = true; } t->cv.notify_all(); }
    }
};

// A row: one storage location. Its envelope carries its write time and its exact-location triggers.
struct Row {
    std::string service, variable, instance;      // the address, set once
    std::shared_mutex m;                          // THE lock: many readers or one writer
    bool live = false; uint64_t id = 0; std::string bag; double updated = 0;   // guarded by m
    TriggerList triggers;
    Row* next_in_bucket = nullptr;                // set once, before publication
    Row* next_in_variable = nullptr;              // set once, before publication
};

// [WRITE_WINDOW_V1] A variable's recent writes, in the order their ids were taken: a fixed ring of WINDOW slots, so it
// never grows (v0.62's log added a node per write and never freed one -- on a RAM server that stays up, every register
// grew it forever). A writer claims the next position with a compare-and-swap and takes its id inside that claim, so
// ids rise with position; it then fills the slot and publishes it by stamping it with its position. A held read ("what
// was written past id X") reads the window from its newest slot back to X. If the window no longer reaches back to X
// (more than WINDOW writes since), or a slot it needs has been reused, the rows themselves are the answer: every row of
// the variable with an id past X -- the definition, read directly.
static constexpr uint64_t WINDOW = 1024;
struct WindowSlot { std::atomic<uint64_t> pos{~0ull}, id{0}; std::atomic<struct Row*> row{nullptr}; };
struct WriteWindow { std::atomic<uint64_t> head{0}; WindowSlot slot[WINDOW]; };
struct Variable {                                 // the rows one (service, variable) has ever had
    std::string service, variable; std::atomic<Row*> rows{nullptr};
    std::atomic<WriteWindow*> window{nullptr};     // made on the variable's first write
    Variable* next_in_bucket = nullptr; Variable* next_in_service = nullptr;
};
struct Service { std::string service; std::atomic<Variable*> vars{nullptr}; Service* next_in_bucket = nullptr; };
struct Pattern { std::string key; TriggerList triggers; Pattern* next_in_bucket = nullptr; };

template <class N, size_t BUCKETS>
struct LockFreeTable {                            // insert-only hash of lists; find-or-create with a CAS
    std::atomic<N*> b[BUCKETS] = {};
    template <class Eq, class Make, class Link>
    N* find_or_create(size_t h, Eq eq, Make make, Link link) {
        auto& slot = b[h % BUCKETS];
        for (;;) {
            N* head = slot.load(std::memory_order_acquire);
            for (N* n = head; n; n = n->next_in_bucket) if (eq(n)) return n;
            N* fresh = make(); fresh->next_in_bucket = head;
            if (slot.compare_exchange_strong(head, fresh, std::memory_order_acq_rel)) { link(fresh); return fresh; }
            delete fresh;                          // someone else inserted into this bucket first: look again
        }
    }
    template <class Eq> N* find(size_t h, Eq eq) const {
        for (N* n = b[h % BUCKETS].load(std::memory_order_acquire); n; n = n->next_in_bucket) if (eq(n)) return n;
        return nullptr;
    }
};

inline size_t H(const std::string& a, const std::string& b = "", const std::string& c = "") {
    std::hash<std::string> h; size_t x = h(a); x ^= h(b) + 0x9e3779b97f4a7c15ull + (x << 6) + (x >> 2); x ^= h(c) + 0x9e3779b97f4a7c15ull + (x << 6) + (x >> 2); return x;
}
inline std::string pattern_key(const std::string& s, const std::string& v, const std::string& i) {   // "" = free
    return (s.empty() ? std::string("*") : "=" + s) + '\x1f' + (v.empty() ? std::string("*") : "=" + v) + '\x1f' + (i.empty() ? std::string("*") : "=" + i);
}

class Memory {
public:
    // ---- write: one row's lock, then that row's triggers and the space's matching patterns
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag, double now) {
        Row* r = row(s, v, i, true);
        Variable* var = variable(s, v);
        uint64_t id;
        WriteWindow* wnd = var->window.load(std::memory_order_acquire);
        if (!wnd) {
            WriteWindow* fresh = new WriteWindow;
            if (var->window.compare_exchange_strong(wnd, fresh, std::memory_order_acq_rel)) wnd = fresh; else delete fresh;
        }
        { std::unique_lock<std::shared_mutex> w(r->m);
          uint64_t p = wnd->head.load(std::memory_order_acquire);
          do { id = ++next_id_; } while (!wnd->head.compare_exchange_weak(p, p + 1, std::memory_order_acq_rel, std::memory_order_acquire));
          WindowSlot& sl = wnd->slot[p % WINDOW];
          sl.pos.store(~0ull, std::memory_order_release);          // being filled
          sl.id.store(id, std::memory_order_relaxed); sl.row.store(r, std::memory_order_relaxed);
          sl.pos.store(p, std::memory_order_release);              // published at position p
          r->id = id; r->bag = bag; r->updated = now; r->live = true; }
        fire(r);
        return id;
    }
    // ---- remove by id: rare (no participant calls it); walks the rows, each under its own lock only
    // private_too: a caller inside this process may remove a row of a private ('#') service; the network API may not.
    size_t remove(uint64_t id, bool private_too) {
        for (auto& slot : rows_.b)
            for (Row* r = slot.load(std::memory_order_acquire); r; r = r->next_in_bucket) {
                if (!private_too && !r->service.empty() && r->service[0] == '#') continue;
                bool hit = false;
                { std::unique_lock<std::shared_mutex> w(r->m); if (r->live && r->id == id) { r->live = false; hit = true; } }
                if (hit) { fire(r); return 1; }
            }
        return 0;
    }
    // [ROW_SCAN_BOUND_V1] A held read that falls back to the rows walks them one at a time, each under its own lock, while
    // writers keep writing. Without a bound it could return row Z's new write (id 160) but not row Y's (id 150), which
    // landed on Y after the walk had passed it; the reader then follows "after" = 160 and never sees 150 (Raspberry Pi,
    // test-write-window: "racing reader missed the final write of 13 rows"). The bound: before the walk, take the
    // window's head h0 and the id of the write at position h0-1. Every write at a position below h0 is seen by the walk
    // -- its writer already held the row's lock when h0 was read, so the walk waits for it -- and every write at h0 or
    // later has a larger id (ids rise with position). Reporting only ids up to that one leaves nothing behind a
    // reader's "after": a row rewritten mid-walk is reported by the next read.
    static uint64_t row_scan_bound(WriteWindow* wnd) {
        for (;;) {
            const uint64_t h0 = wnd->head.load(std::memory_order_acquire);
            if (h0 == 0) return 0;
            WindowSlot& sl = wnd->slot[(h0 - 1) % WINDOW];
            for (;;) {
                if (sl.pos.load(std::memory_order_acquire) == h0 - 1) {
                    const uint64_t id = sl.id.load(std::memory_order_acquire);
                    if (sl.pos.load(std::memory_order_acquire) == h0 - 1) return id;
                }
                if (wnd->head.load(std::memory_order_acquire) > h0 - 1 + WINDOW) break;   // lapped: take a new h0
                std::this_thread::yield();               // its writer is between claiming h0-1 and publishing it
            }
        }
    }
    // ---- read: the query's rows (copied under each row's shared lock), id order
    std::vector<Cell> match(const std::string& s, const std::string& v, const std::string& i, long long after, int fresh, double now) const {
        std::vector<Cell> out; const double cutoff = now - fresh;
        uint64_t scan_lim = 0; bool bounded = false;    // [ROW_SCAN_BOUND_V1] set when a held read falls back to the rows
        auto take = [&](Row* r) {
            std::shared_lock<std::shared_mutex> g(r->m);
            if (!r->live) return;
            if (!i.empty() && r->instance != i) return;
            if (after >= 0 && (long long)r->id <= after) return;
            if (fresh > 0 && r->updated < cutoff) return;
            out.push_back(Cell{r->id, r->service, r->variable, r->instance, r->bag, r->updated});
        };
        if (!v.empty() && !i.empty()) { if (Row* r = find_row(s, v, i)) take(r); return out; }
        if (!v.empty() && after >= 0) {                // [WRITE_WINDOW_V1] only what was written past `after`
            Variable* var = vars_.find(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; });
            if (!var) return out;
            WriteWindow* wnd = var->window.load(std::memory_order_acquire);
            if (!wnd) return out;                                   // never written
            const uint64_t h = wnd->head.load(std::memory_order_acquire);
            const uint64_t lo = h > WINDOW ? h - WINDOW : 0;
            std::vector<std::pair<uint64_t, Row*>> got; uint64_t cut = h; bool reached = false, reused = false;
            for (uint64_t p = h; p-- > lo;) {                       // newest to oldest; ids fall along the way
                WindowSlot& sl = wnd->slot[p % WINDOW];
                const uint64_t p1 = sl.pos.load(std::memory_order_acquire);
                const uint64_t id = sl.id.load(std::memory_order_acquire); Row* r = sl.row.load(std::memory_order_acquire);
                const uint64_t p2 = sl.pos.load(std::memory_order_acquire);
                if (p1 != p || p2 != p) {
                    if (wnd->head.load(std::memory_order_acquire) > p + WINDOW) { reused = true; break; }   // lapped
                    cut = p; got.clear(); continue;             // still being filled: nothing at or after it yet
                }
                if ((long long)id <= after) { reached = true; break; }
                if (p < cut) got.emplace_back(id, r);
            }
            if (!reused && (reached || lo == 0)) {
                for (auto& e : got) {
                    std::shared_lock<std::shared_mutex> g(e.second->m);
                    Row* r = e.second;
                    if (!r->live || r->id != e.first) continue;     // written again since: that write answers for it
                    if (fresh > 0 && r->updated < cutoff) continue;
                    out.push_back(Cell{r->id, r->service, r->variable, r->instance, r->bag, r->updated});
                }
                std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });
                return out;
            }
            // the window does not reach back to `after`: the rows themselves (below) answer -- bounded by row_scan_bound
            scan_lim = row_scan_bound(wnd); bounded = true;
        }
        auto each_row = [&](Variable* var) { for (Row* r = var->rows.load(std::memory_order_acquire); r; r = r->next_in_variable) take(r); };
        if (!v.empty()) { if (Variable* var = vars_.find(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; })) each_row(var); }
        else if (Service* sv = services_.find(H(s), [&](Service* n) { return n->service == s; }))
            for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service) each_row(var);
        if (bounded) out.erase(std::remove_if(out.begin(), out.end(), [&](const Cell& c) { return c.id > scan_lim; }), out.end());
        std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });
        return out;
    }
    // ---- a wait: hang a trigger where the query points (the row for an exact address, the space otherwise), then
    // evaluate; re-evaluate every time it fires, until satisfied or the deadline. Registration happens BEFORE the
    // first evaluation, so a write between the two cannot be missed.
    template <class Deadline, class Eval, class Satisfied>
    std::vector<Cell> wait(const std::string& s, const std::string& v, const std::string& i, Deadline deadline, Eval eval, Satisfied ok) {
        TriggerList* list = (!v.empty() && !i.empty()) ? &row(s, v, i, true)->triggers : &pattern(pattern_key(s, v, i))->triggers;
        Trigger* t = list->acquire();
        std::vector<Cell> rows = eval();
        while (!ok(rows)) {
            std::unique_lock<std::mutex> l(t->m);
            if (!t->cv.wait_until(l, deadline, [&] { return t->fired; })) break;
            t->fired = false; l.unlock();
            rows = eval();
        }
        t->in_use.store(false, std::memory_order_release);
        return ok(rows) ? rows : eval();
    }

private:
    std::atomic<uint64_t> next_id_{0};
    LockFreeTable<Row, 1 << 16> rows_;
    LockFreeTable<Variable, 1 << 14> vars_;
    LockFreeTable<Service, 1 << 10> services_;
    LockFreeTable<Pattern, 1 << 12> patterns_;

    Row* find_row(const std::string& s, const std::string& v, const std::string& i) const {
        return rows_.find(H(s, v, i), [&](Row* n) { return n->service == s && n->variable == v && n->instance == i; });
    }
    Row* row(const std::string& s, const std::string& v, const std::string& i, bool create) {
        if (!create) return find_row(s, v, i);
        return rows_.find_or_create(H(s, v, i), [&](Row* n) { return n->service == s && n->variable == v && n->instance == i; },
            [&] { Row* r = new Row; r->service = s; r->variable = v; r->instance = i; return r; },
            [&](Row* r) {                         // a new row joins its variable's list (and the variable its service's)
                Variable* var = vars_.find_or_create(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; },
                    [&] { Variable* n = new Variable; n->service = s; n->variable = v; return n; },
                    [&](Variable* n) {
                        Service* sv = services_.find_or_create(H(s), [&](Service* x) { return x->service == s; },
                            [&] { Service* x = new Service; x->service = s; return x; }, [](Service*) {});
                        Variable* h = sv->vars.load(std::memory_order_relaxed);
                        do { n->next_in_service = h; } while (!sv->vars.compare_exchange_weak(h, n, std::memory_order_release, std::memory_order_relaxed));
                    });
                Row* h = var->rows.load(std::memory_order_relaxed);
                do { r->next_in_variable = h; } while (!var->rows.compare_exchange_weak(h, r, std::memory_order_release, std::memory_order_relaxed));
            });
    }
    Variable* variable(const std::string& s, const std::string& v) {
        return vars_.find_or_create(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; },
            [&] { Variable* n = new Variable; n->service = s; n->variable = v; return n; },
            [&](Variable* n) {
                Service* sv = services_.find_or_create(H(s), [&](Service* x) { return x->service == s; },
                    [&] { Service* x = new Service; x->service = s; return x; }, [](Service*) {});
                Variable* h = sv->vars.load(std::memory_order_relaxed);
                do { n->next_in_service = h; } while (!sv->vars.compare_exchange_weak(h, n, std::memory_order_release, std::memory_order_relaxed));
            });
    }
    Pattern* pattern(const std::string& key) {
        return patterns_.find_or_create(std::hash<std::string>()(key), [&](Pattern* n) { return n->key == key; },
            [&] { Pattern* p = new Pattern; p->key = key; return p; }, [](Pattern*) {});
    }
    void fire(Row* r) {
        r->triggers.fire();
        const std::string& s = r->service; const std::string& v = r->variable; const std::string& i = r->instance;
        const std::string keys[6] = {pattern_key(s, "", ""), pattern_key("", v, ""), pattern_key("", "", i),
                                     pattern_key(s, v, ""), pattern_key(s, "", i), pattern_key("", v, i)};
        for (const auto& k : keys)
            if (Pattern* p = patterns_.find(std::hash<std::string>()(k), [&](Pattern* n) { return n->key == k; })) p->triggers.fire();
    }
};

}  // namespace ramm
