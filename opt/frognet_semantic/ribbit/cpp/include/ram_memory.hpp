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
// Locks: each row has a std::shared_mutex that its WRITER holds; only a write blocks, and only for its one row
// ([LOCKFREE_READS_V1], 2026-10-19). A row's value is one immutable RowVal behind an atomic pointer: a write makes a new
// one, publishes it before its window slot, and retires the old one to epoch reclamation (lf/ebr.hpp); a read loads the
// pointer inside an epoch guard and takes no lock. One read still shares the row lock: the rows walk that continues a
// held read past an id when the window has lapped ([ROW_SCAN_BOUND_V1]) -- it must wait for a write in flight, and the
// writer's lock is what it waits on. There is no other lock on the memory. Which rows exist, which variables a service has, and the trigger lists are lock-free singly linked lists:
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
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <thread>
#include <string>
#include <unordered_map>
#include <vector>
#include "bagarr.hpp"
#include "lf/ebr.hpp"

namespace ramm {

// [DECODE_ONCE_V1] a row read: its bag decoded once (arr) when it was written as fields; bag holds the text only for a
// row written as text. text() is the bag as JSON text either way, for a reader that asks for text.
// [FORWARD_CHANGES_V1] prev: the array this write was a difference against (its writer's previous write of the family);
// changed: the field indices it changed. A reader's encoder that last gave its reader that very array sends only those.
using Changed = ribbit::FwdP;
struct Cell { uint64_t id; std::string service, variable, instance, bag; double updated; ribbit::BagArrP arr;
              ribbit::BagArrP prev = nullptr; Changed changed = nullptr;
              std::string text() const { return arr ? arr->json() : bag; } };

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

// [LOCKFREE_READS_V1] a row's value: immutable once published; replaced whole by the next write
struct RowVal { bool live = false; uint64_t id = 0; std::string bag; double updated = 0;
                ribbit::BagArrP arr;               // [DECODE_ONCE_V1] the bag as decoded, when written as fields
                ribbit::BagArrP prev; Changed changed; };   // [FORWARD_CHANGES_V1]
// A row: one storage location. Its envelope carries its write time and its exact-location triggers.
struct Row {
    std::string service, variable, instance;      // the address, set once
    std::shared_mutex m;                          // THE lock: its writer's (and the bounded rows walk's)
    std::atomic<const RowVal*> val{nullptr};      // read without a lock, inside an epoch guard
    TriggerList triggers;
    Row* next_in_bucket = nullptr;                // set once, before publication
    Row* next_in_variable = nullptr;              // set once, before publication
    std::string pkey[6]; size_t phash[6] = {0, 0, 0, 0, 0, 0};   // [FIRE_KEYS_ONCE_V1] set once, before publication
};

// [WRITE_WINDOW_V1] A variable's recent writes, in the order their ids were taken: a fixed ring of WINDOW slots, so it
// never grows (v0.62's log added a node per write and never freed one -- on a RAM server that stays up, every register
// grew it forever). A writer claims the next position with a compare-and-swap and takes its id inside that claim, so
// ids rise with position; it then fills the slot and publishes it by stamping it with its position. A held read ("what
// was written past id X") reads the window from its newest slot back to X. If the window no longer reaches back to X
// (more than WINDOW writes since), or a slot it needs has been reused, the rows themselves are the answer: every row of
// the variable with an id past X -- the definition, read directly.
static constexpr uint64_t WINDOW = 1024;
struct WindowSlot { std::atomic<uint64_t> pos{~0ull}, id{0}; std::atomic<struct Row*> row{nullptr}; std::atomic<void*> var{nullptr}; };
struct WriteWindow { std::atomic<uint64_t> head{0}; WindowSlot slot[WINDOW]; };
// [FAMILY_HOOKS_V1] A reader of a family is woken by the family's writes and reads them itself, by position, on its
// own thread. A WakeWord is one reader's (one thread waits on it with a futex, for every family it reads); a Hook ties
// it to one family. A write bumps the word of every live hook on its variable and its service and returns: it does not
// know who reads, gives nobody anything, and nothing is queued. Both are insert-only and never freed (a gone reader's
// hook is marked dead and claimed by the next one; a word is released and reused), so a writer never touches freed
// memory; a hook being reassigned may wake the wrong word once -- a spurious wake, which a reader shrugs off.
struct WakeWord { std::atomic<uint32_t> n{0}; std::atomic<bool> in_use{true}; WakeWord* next = nullptr;
    void bump() { n.fetch_add(1, std::memory_order_release); n.notify_all(); } };
struct Hook { std::atomic<WakeWord*> w{nullptr}; std::atomic<bool> live{true}; Hook* next = nullptr; };
struct HookList {
    std::atomic<Hook*> head{nullptr};
    Hook* add(WakeWord* w) {
        for (Hook* h = head.load(std::memory_order_acquire); h; h = h->next) {
            bool f = false;
            if (h->live.compare_exchange_strong(f, true, std::memory_order_acq_rel)) { h->w.store(w, std::memory_order_release); return h; }
        }
        Hook* h = new Hook; h->w.store(w, std::memory_order_relaxed); Hook* o = head.load(std::memory_order_relaxed);
        do { h->next = o; } while (!head.compare_exchange_weak(o, h, std::memory_order_release, std::memory_order_relaxed));
        return h;
    }
    void fire() const {
        for (Hook* h = head.load(std::memory_order_acquire); h; h = h->next)
            if (h->live.load(std::memory_order_acquire)) if (WakeWord* w = h->w.load(std::memory_order_acquire)) w->bump();
    }
};
struct Service;
struct Variable {                                 // the rows one (service, variable) has ever had
    std::string service, variable; std::atomic<Row*> rows{nullptr};
    std::atomic<WriteWindow*> window{nullptr};     // made on the variable's first write or first reader
    std::atomic<Service*> svc{nullptr};            // its service, found once
    HookList hooks;                                // [FAMILY_HOOKS_V1] readers of (service, variable, *)
    Variable* next_in_bucket = nullptr; Variable* next_in_service = nullptr;
};
struct Service { std::string service; std::atomic<Variable*> vars{nullptr};
    std::atomic<WriteWindow*> window{nullptr};     // [FAMILY_HOOKS_V1] the service's writes, in the order they claimed it
    HookList hooks;                                // readers of (service, *, *)
    Service* next_in_bucket = nullptr; };
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
    // A row has one owner, its one writer, whose writes arrive in its own order: the row's lock is the only thing a
    // write waits for, and nothing here decides between writers -- there is never more than one.
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag, double now) {
        return write(s, v, i, bag, now, nullptr);
    }
    // the bag decoded once: stored as it is, its text made only for a reader that asks for text
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, ribbit::BagArrP arr, double now) {
        return write(s, v, i, std::string(), now, std::move(arr));
    }
    // [FORWARD_CHANGES_V1] a write that was a difference: the array it was against and the indices it changed, kept for
    // the readers' encoders
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, ribbit::BagArrP arr, double now,
                   ribbit::BagArrP prev, Changed changed) {
        return write(s, v, i, std::string(), now, std::move(arr), std::move(prev), std::move(changed));
    }
    uint64_t write(const std::string& s, const std::string& v, const std::string& i, const std::string& bag, double now, ribbit::BagArrP arr,
                   ribbit::BagArrP prev = nullptr, Changed changed = nullptr) {
        Row* r = row(s, v, i, true);
        Variable* var = variable(s, v);
        Service* svc = service_of(var);
        WriteWindow* wnd = window_of(var->window);
        WriteWindow* swnd = window_of(svc->window);
        uint64_t id;
        // No order across families: a write takes its variable's next position (its id inside that claim, so a
        // variable's ids rise with its positions) and its service's next position, and is done. [FAMILY_HOOKS_V1]
        { std::unique_lock<std::shared_mutex> w(r->m);
          uint64_t p = wnd->head.load(std::memory_order_acquire);
          for (;;) {
              id = next_id_.fetch_add(1, std::memory_order_relaxed) + 1;   // an id a failed claim drops is a gap, nothing more
              if (wnd->head.compare_exchange_weak(p, p + 1, std::memory_order_acq_rel, std::memory_order_acquire)) break;
          }
          // the row's value first, then its slots: a reader that meets the slot finds the value there
          set_val(r, new RowVal{true, id, bag, now, std::move(arr), std::move(prev), std::move(changed)});
          fill(wnd->slot[p % WINDOW], p, id, r);
          const uint64_t sp = swnd->head.fetch_add(1, std::memory_order_acq_rel);
          fill(swnd->slot[sp % WINDOW], sp, id, r, var); }
        var->hooks.fire(); svc->hooks.fire();          // [FAMILY_HOOKS_V1] its readers' words; they read it themselves
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
                { std::unique_lock<std::shared_mutex> w(r->m);
                  const RowVal* x = r->val.load(std::memory_order_acquire);
                  if (x && x->live && x->id == id) { set_val(r, new RowVal{false, x->id, x->bag, x->updated, x->arr, nullptr, nullptr}); hit = true; } }
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
    static uint64_t row_scan_bound(WriteWindow* wnd, uint64_t* at = nullptr) {
        for (;;) {
            const uint64_t h0 = wnd->head.load(std::memory_order_acquire);
            if (at) *at = h0;
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
    // ---- read: the query's rows, id order. [LOCKFREE_READS_V1] each row's value loaded inside one epoch guard, no lock
    // -- except the bounded rows walk below, which shares each row's lock to wait for a write in flight
    std::vector<Cell> match(const std::string& s, const std::string& v, const std::string& i, long long after, int fresh, double now) const {
        std::vector<Cell> out; const double cutoff = now - fresh;
        ribbit::lf::ebr::Guard eg;
        uint64_t scan_lim = 0; bool bounded = false;    // [ROW_SCAN_BOUND_V1] set when a held read falls back to the rows
        auto take = [&](Row* r) {
            std::shared_lock<std::shared_mutex> g(r->m, std::defer_lock);
            if (bounded) g.lock();                       // only the bounded walk waits for a write in flight
            const RowVal* x = r->val.load(std::memory_order_acquire);
            if (!x || !x->live) return;
            if (!i.empty() && r->instance != i) return;
            if (after >= 0 && (long long)x->id <= after) return;
            if (fresh > 0 && x->updated < cutoff) return;
            out.push_back(Cell{x->id, r->service, r->variable, r->instance, x->bag, x->updated, x->arr, x->prev, x->changed});
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
                for (auto& e : got) {                       // a published slot: its row already holds that value
                    Row* r = e.second; const RowVal* x = r->val.load(std::memory_order_acquire);
                    if (!x || !x->live || x->id != e.first) continue;   // written again since: that write answers for it
                    if (fresh > 0 && x->updated < cutoff) continue;
                    out.push_back(Cell{x->id, r->service, r->variable, r->instance, x->bag, x->updated, x->arr, x->prev, x->changed});
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


    // ---- [FAMILY_HOOKS_V1] reading a family, on the reader's own thread ------------------------------------------
    // A family: (s, v, *) or (s, *, *); an instance, when given, filters. A variable's window is the order of its
    // writes (its ids rise with its positions), and a reader reads it by position: VarCursor, the position it has read
    // to and the newest id it has read there. A service has no order of its own -- its writes to different variables
    // are not ordered against each other -- so its window only says WHICH variables were written: a service reader
    // reads, for each of those, that variable's window from its own VarCursor. A Cursor belongs to its reader's thread.
    struct VarCursor { Variable* var = nullptr; uint64_t pos = 0, last = 0; bool rows_first = false; };
    struct Cursor {
        std::string s, v, instance; HookList* hooks = nullptr;
        VarCursor one;                                      // a variable family
        WriteWindow* sw = nullptr; uint64_t spos = 0;       // a service family: the service's window of hints
        std::unordered_map<Variable*, VarCursor> vars;      //   and each variable's place
        long long after_new = -1;                           //   a variable met for the first time: from this id (-1: all)
        uint64_t recovered = 0, laps = 0;                   // rows read from the rows because a window had moved on
    };
    // from_after < 0: from now on (what is written after this call); else every row with an id past from_after
    Cursor cursor(const std::string& s, const std::string& v, const std::string& i, long long from_after) {
        Cursor c; c.s = s; c.v = v; c.instance = i;
        auto start = [&](Variable* var) {
            VarCursor vc; vc.var = var; WriteWindow* w = window_of(var->window);
            if (from_after < 0) vc.last = row_scan_bound(w, &vc.pos);   // the newest there now, and where it ends
            else { vc.last = uint64_t(from_after); vc.rows_first = true; }
            return vc;
        };
        if (!v.empty()) { Variable* var = variable(s, v); c.hooks = &var->hooks; c.one = start(var); return c; }
        Service* sv = service(s); c.hooks = &sv->hooks; c.sw = window_of(sv->window);
        c.spos = c.sw->head.load(std::memory_order_acquire);   // hints from here; the variables there now start now
        for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service) c.vars[var] = start(var);
        c.after_new = from_after < 0 ? 0 : from_after;       // a variable made after this: all of it is new
        return c;
    }
    // What has been written to the family since the cursor's last read, appended to out: each write once, each
    // variable's in the order written, each row copied under its shared lock. A window is read from the cursor's
    // position and the read stops at a slot still being filled (its writer wakes the reader again when it is done).
    // If a variable's window has moved more than WINDOW past the cursor, the rows answer for what it no longer holds
    // -- every row with an id past the newest the reader has read there, bounded as row_scan_bound says -- and the
    // window is read on from where that bound was taken. Nothing is lost and nothing is read twice.
    void read(Cursor& c, std::vector<Cell>& out) {
        ribbit::lf::ebr::Guard eg;                     // [LOCKFREE_READS_V1]
        if (!c.sw) { read_var(c, c.one, out); return; }
        auto vc_of = [&](Variable* var) -> VarCursor& {
            auto it = c.vars.find(var);
            if (it != c.vars.end()) return it->second;
            VarCursor vc; vc.var = var; vc.last = uint64_t(c.after_new); vc.rows_first = true;   // new since the cursor
            return c.vars.emplace(var, vc).first->second;
        };
        const uint64_t h = c.sw->head.load(std::memory_order_acquire);
        if (h > c.spos + WINDOW) {                           // the hints have moved on: every variable is looked at
            c.laps++; c.spos = h;
            if (Service* sv = services_.find(H(c.s), [&](Service* n) { return n->service == c.s; }))
                for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service) read_var(c, vc_of(var), out);
            return;
        }
        Variable* seen[64]; size_t ns = 0;
        for (; c.spos < h; ++c.spos) {
            WindowSlot& sl = c.sw->slot[c.spos % WINDOW];
            const uint64_t p1 = sl.pos.load(std::memory_order_acquire);
            Variable* var = static_cast<Variable*>(sl.var.load(std::memory_order_acquire));
            const uint64_t p2 = sl.pos.load(std::memory_order_acquire);
            if (p1 != c.spos || p2 != c.spos) {
                if (c.sw->head.load(std::memory_order_acquire) > c.spos + WINDOW) return read(c, out);
                break;                                       // still being filled: its writer wakes us when done
            }
            bool dup = false; for (size_t k = 0; k < ns; ++k) if (seen[k] == var) { dup = true; break; }
            if (dup) continue;
            read_var(c, vc_of(var), out);
            if (ns < 64) seen[ns++] = var;
        }
    }
    // the newest id written to a family now ((s, v, *), or (s, *, *) for v ""): where a reader starts "from now"
    uint64_t newest(const std::string& s, const std::string& v) {
        if (!v.empty()) { Variable* var = vars_.find(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; });
                          WriteWindow* w = var ? var->window.load(std::memory_order_acquire) : nullptr; return w ? row_scan_bound(w) : 0; }
        uint64_t m = 0;
        if (Service* sv = services_.find(H(s), [&](Service* n) { return n->service == s; }))
            for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service)
                if (WriteWindow* w = var->window.load(std::memory_order_acquire)) m = std::max(m, row_scan_bound(w));
        return m;
    }
    // where the family's writes have reached (atomics only: any thread may ask, while the cursor's reader reads it).
    // It moving past what a reader last saw says there may be something new to read. [READERS_IN_PARALLEL_V1]
    static uint64_t head_of(const Cursor& c) {
        if (c.sw) return c.sw->head.load(std::memory_order_acquire);
        WriteWindow* w = c.one.var ? c.one.var->window.load(std::memory_order_acquire) : nullptr;
        return w ? w->head.load(std::memory_order_acquire) : 0;
    }
    // [SEEN_IS_WHAT_WAS_READ_V1] where the cursor's last read got to: a read stops at a slot still being filled, short of
    // head_of(). A reader that takes this, not head_of(), as what it has seen is woken again by that write's own wake.
    static uint64_t read_to(const Cursor& c) { return c.sw ? c.spos : c.one.pos; }
    // a reader's word: one per reading thread, released when it is done (and reused)
    WakeWord* wake_word() {
        for (WakeWord* w = words_.load(std::memory_order_acquire); w; w = w->next) {
            bool f = false; if (w->in_use.compare_exchange_strong(f, true, std::memory_order_acq_rel)) return w;
        }
        WakeWord* w = new WakeWord; WakeWord* o = words_.load(std::memory_order_relaxed);
        do { w->next = o; } while (!words_.compare_exchange_weak(o, w, std::memory_order_release, std::memory_order_relaxed));
        return w;
    }
    static void release(WakeWord* w) { w->in_use.store(false, std::memory_order_release); }
    static Hook* hook(Cursor& c, WakeWord* w) { return c.hooks->add(w); }
    static void unhook(Hook* h) { h->live.store(false, std::memory_order_release); }
private:
    // [LOCKFREE_READS_V1] under the row's write lock: the new value in, the old one to epoch reclamation
    static void set_val(Row* r, const RowVal* nv) {
        const RowVal* old = r->val.exchange(nv, std::memory_order_acq_rel);
        if (old) ribbit::lf::ebr::retire(const_cast<RowVal*>(old));
    }
    static void fill(WindowSlot& sl, uint64_t p, uint64_t id, Row* r, Variable* var = nullptr) {
        sl.pos.store(~0ull, std::memory_order_release);          // being filled
        sl.id.store(id, std::memory_order_relaxed); sl.row.store(r, std::memory_order_relaxed); sl.var.store(var, std::memory_order_relaxed);
        sl.pos.store(p, std::memory_order_release);              // published at position p
    }
    static WriteWindow* window_of(std::atomic<WriteWindow*>& a) {
        WriteWindow* w = a.load(std::memory_order_acquire);
        if (w) return w;
        WriteWindow* fresh = new WriteWindow;
        if (a.compare_exchange_strong(w, fresh, std::memory_order_acq_rel)) return fresh;
        delete fresh; return w;
    }
    Service* service(const std::string& s) {
        return services_.find_or_create(H(s), [&](Service* x) { return x->service == s; },
            [&] { Service* x = new Service; x->service = s; return x; }, [](Service*) {});
    }
    Service* service_of(Variable* var) {
        Service* sv = var->svc.load(std::memory_order_acquire);
        if (!sv) { sv = service(var->service); var->svc.store(sv, std::memory_order_release); }
        return sv;
    }
    std::atomic<WakeWord*> words_{nullptr};
    void read_var(Cursor& c, VarCursor& vc, std::vector<Cell>& out) {
        WriteWindow* w = window_of(vc.var->window);
        if (vc.rows_first || w->head.load(std::memory_order_acquire) > vc.pos + WINDOW) {
            if (!vc.rows_first) c.laps++;
            vc.rows_first = false;
            uint64_t h0 = 0; const uint64_t bound = row_scan_bound(w, &h0);
            std::vector<Cell> got;
            for (Row* r = vc.var->rows.load(std::memory_order_acquire); r; r = r->next_in_variable) {
                std::shared_lock<std::shared_mutex> g(r->m);    // the bounded walk: waits for a write in flight
                const RowVal* x = r->val.load(std::memory_order_acquire);
                if (!x || !x->live || x->id <= vc.last || x->id > bound) continue;
                if (!c.instance.empty() && r->instance != c.instance) continue;
                got.push_back(Cell{x->id, r->service, r->variable, r->instance, x->bag, x->updated, x->arr, x->prev, x->changed});
            }
            std::sort(got.begin(), got.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });
            c.recovered += got.size();
            for (auto& x : got) out.push_back(std::move(x));
            vc.last = std::max(vc.last, bound); vc.pos = h0;
        }
        const uint64_t h = w->head.load(std::memory_order_acquire);
        for (; vc.pos < h; ++vc.pos) {
            WindowSlot& sl = w->slot[vc.pos % WINDOW];
            const uint64_t p1 = sl.pos.load(std::memory_order_acquire);
            const uint64_t id = sl.id.load(std::memory_order_acquire); Row* r = sl.row.load(std::memory_order_acquire);
            const uint64_t p2 = sl.pos.load(std::memory_order_acquire);
            if (p1 != vc.pos || p2 != vc.pos) {
                if (w->head.load(std::memory_order_acquire) > vc.pos + WINDOW) return read_var(c, vc, out);   // lapped while reading
                return;                                       // still being filled
            }
            if (id <= vc.last) continue;                      // read already (from the rows)
            vc.last = id;
            const RowVal* x = r->val.load(std::memory_order_acquire);   // published before its slot: no lock
            if (!x || !x->live || x->id != id) continue;      // written again since: read at its newer slot
            if (!c.instance.empty() && r->instance != c.instance) continue;
            out.push_back(Cell{x->id, r->service, r->variable, r->instance, x->bag, x->updated, x->arr, x->prev, x->changed});
        }
    }
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
            [&] { Row* r = new Row; r->service = s; r->variable = v; r->instance = i;
                  const std::string k6[6] = {pattern_key(s, "", ""), pattern_key("", v, ""), pattern_key("", "", i),
                                             pattern_key(s, v, ""), pattern_key(s, "", i), pattern_key("", v, i)};
                  for (int j = 0; j < 6; ++j) { r->pkey[j] = k6[j]; r->phash[j] = std::hash<std::string>()(k6[j]); }   // [FIRE_KEYS_ONCE_V1]
                  return r; },
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
        for (int j = 0; j < 6; ++j) { const std::string& k = r->pkey[j];   // [FIRE_KEYS_ONCE_V1] made with the row
            if (Pattern* p = patterns_.find(r->phash[j], [&](Pattern* n) { return n->key == k; })) p->triggers.fire(); }
    }
};

}  // namespace ramm
