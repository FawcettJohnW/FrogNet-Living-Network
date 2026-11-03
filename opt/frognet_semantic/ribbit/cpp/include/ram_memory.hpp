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
// ram_memory.hpp -- the C++ RAM host's memory. [FAMILY_LOG_V1] (John 2026-11-03: "There should be no walks, no locks,
// no notifications, and no waiting." "The ids should not be claimed. That created race conditions and violated the
// ownership rule.")
//
// A cell (service, variable, instance) is a Row: an atomic pointer to its current value, an immutable RowVal. Its one
// writer replaces it with a compare-and-swap; nothing is locked. The old value goes to epoch reclamation (lf/ebr.hpp).
//
// Each family -- a variable (s, v, *) and a service (s, *, *) -- has its own log: an append-only array of slots in
// chunks of CH. A write fills the first empty slot after the last filled one with one compare-and-swap, and that CAS is
// the publication: the slot's number IS the write's place in that family. Nothing is numbered before it is visible,
// and there is no counter shared by every writer (the old next_id_ claim, taken before the value landed, let a
// smaller id appear after a larger one had been read: memory-order-check lost 550-996 of 16,000 service writes).
// A writer only CASes the slot right after a filled one, so a log has no holes: slot k filled means every slot below
// k is. A cell's id, as a reader of a family is given it, is its slot there plus one; the same write has one id in its
// variable's log and another in its service's. An `after` is a place in ONE family.
//
// A read past `after` reads that family's slots from there to the first empty one: it costs what it returns. A slot
// whose row has been written again since is passed over (the newer write is further on). No read waits for a write
// in flight, and no read walks rows looking for what changed. A read of a family's current state (no `after`) reads
// that family's rows -- what it asked for -- and gives each an id no later than the log's filled prefix at the read's
// start, so a reader that goes on from the largest id it got misses nothing written meanwhile.
//
// A chunk whose every slot has been passed over is retired whole (its writes all have newer ones); a reader that
// meets a retired chunk goes on at the next. A row written once and never again keeps its chunk.
//
// The one wait is the RAM's blocking read: a reader sleeps on its family's word (lf::WaitWord, a futex) until a write
// to that family moves it; a writer moves it only when somebody is sleeping there. A reader of several families on one
// thread hooks one WakeWord to each ([FAMILY_HOOKS_V1]). Nothing else is woken, fired or told.
//
// When a session ends, the rows it wrote go ([GONE_WITH_ITS_WRITER_V1]): the session's own list of the rows it wrote,
// not a walk of the memory.
#pragma once
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#include "bagarr.hpp"
#include "lf/ebr.hpp"
#include "lf/primitives.hpp"

namespace ramm {

// [DECODE_ONCE_V1] a row read: its bag decoded once (arr) when it was written as fields; bag holds the text only for a
// row written as text. text() is the bag as JSON text either way, for a reader that asks for text.
// [FORWARD_CHANGES_V1] prev: the array this write was a difference against (its writer's previous write of the family);
// changed: the field indices it changed. A reader's encoder that last gave its reader that very array sends only those.
using Changed = ribbit::FwdP;
struct Cell { uint64_t id; std::string service, variable, instance, bag; double updated; ribbit::BagArrP arr;
              ribbit::BagArrP prev = nullptr; Changed changed = nullptr;
              bool gone = false;   // [GONE_WITH_ITS_WRITER_V1] the row is gone (its writer is): given to a read past an id, in order
              std::string text() const { return arr ? arr->json() : bag; } };

// [GONE_WITH_ITS_WRITER_V1] the session a write on this thread is made for: set by the host while it serves that session's
// request (WritingFor), 0 for a write this process makes itself. When a session ends, everything it wrote goes with it.
// [LINGER_BY_FAMILY_V1] What happens to a row when its writer's session ends is the writer's choice, made per family
// when it first names the family: it goes at once (LINGER_GOES, the default), it stays that many seconds and is then
// reaped, or it stays (LINGER_STAYS) until it is written again or removed.
constexpr int32_t LINGER_GOES = 0, LINGER_STAYS = -1;
inline thread_local uint64_t t_writer = 0;
inline thread_local int32_t t_linger = LINGER_GOES;
struct WritingFor { uint64_t was; int32_t wl;
                    explicit WritingFor(uint64_t s, int32_t linger = LINGER_GOES) : was(t_writer), wl(t_linger) { t_writer = s; t_linger = linger; }
                    ~WritingFor() { t_writer = was; t_linger = wl; }
                    WritingFor(const WritingFor&) = delete; WritingFor& operator=(const WritingFor&) = delete; };

static constexpr uint64_t CH = 256;               // slots per chunk of a family's log
static constexpr uint64_t WINDOW = CH;            // (the name the tests and the host's diagnostics use)
struct Row; struct Chunk;
// [LOCKFREE_READS_V1] a row's value: immutable once published, except where it landed in the two logs (set by its writer
// just after it lands there; 0 until then)
struct RowVal { bool live = false; std::string bag; double updated = 0;
                ribbit::BagArrP arr;               // [DECODE_ONCE_V1] the bag as decoded, when written as fields
                ribbit::BagArrP prev; Changed changed;   // [FORWARD_CHANGES_V1]
                uint64_t writer = 0;               // [GONE_WITH_ITS_WRITER_V1] the session that wrote it (0: this process)
                int32_t linger = 0;                // [LINGER_BY_FAMILY_V1]
                std::atomic<uint64_t> vid{0}, sid{0};          // its id in its variable's log and in its service's
                std::atomic<Chunk*> vchunk{nullptr}, schunk{nullptr};
                RowVal(bool l, std::string b, double u, ribbit::BagArrP a, ribbit::BagArrP p, Changed c, uint64_t w, int32_t lg)
                    : live(l), bag(std::move(b)), updated(u), arr(std::move(a)), prev(std::move(p)), changed(std::move(c)), writer(w), linger(lg) {} };
struct Variable;
struct Row {
    std::string service, variable, instance;
    std::atomic<const RowVal*> val{nullptr};       // read without a lock, inside an epoch guard
    Variable* var = nullptr;                       // set once, before publication
    std::atomic<uint64_t> listed_by{0};            // the last writer whose list holds it
    Row* next_in_bucket = nullptr;                 // set once, before publication
    Row* next_in_variable = nullptr;               // set once, before publication
};
// ---- a family's log
struct Entry { Row* row; const RowVal* val; };
struct Chunk {
    std::atomic<const Entry*> slot[CH] = {};
    std::atomic<uint32_t> filled{0}, live{0};      // slots filled; of those, the ones not yet passed by a newer write
    std::atomic<bool> dead{false};
    std::atomic<void*>* home = nullptr;            // its place in the directory
    static void free_(void* p) { Chunk* c = static_cast<Chunk*>(p);
        for (auto& s : c->slot) delete s.load(std::memory_order_relaxed);
        delete c; }
};
struct Dir { std::atomic<void*> p[256] = {}; };
inline void* const DEAD = reinterpret_cast<void*>(uintptr_t(1));
struct Log {
    Dir top;                                       // slot k: top[k>>24] -> [k>>16 & 255] -> chunk [k>>8 & 255] -> slot k & 255
    std::atomic<uint64_t> hint{0};                 // every slot below it is filled (it may lag: a writer looks past it)
    ribbit::lf::WaitWord word;                     // the blocking read of this family sleeps here
};
// [FAMILY_HOOKS_V1] A reader of several families on one thread: one WakeWord, hooked to each family. A write moves the
// word of every live hook on its variable and its service. Insert-only lists, never freed (a gone reader's hook is
// marked dead and claimed by the next one); a hook being reassigned may move the wrong word once, which its reader
// takes as a spurious wake.
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
    Log log;
    std::atomic<Service*> svc{nullptr};            // its service, found once
    HookList hooks;                                // [FAMILY_HOOKS_V1] readers of (service, variable, *)
    Variable* next_in_bucket = nullptr; Variable* next_in_service = nullptr;
};
struct Service { std::string service; std::atomic<Variable*> vars{nullptr};
    Log log;
    HookList hooks;                                // readers of (service, *, *)
    Service* next_in_bucket = nullptr; };
// [GONE_WITH_ITS_WRITER_V1] a session's own list of the rows it wrote
struct WRow { Row* r; WRow* next; };
struct Writer { uint64_t id = 0; std::atomic<WRow*> rows{nullptr}; Writer* next_in_bucket = nullptr; };

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

class Memory {
public:
    // ---- write: the row's value replaced with one CAS, then its place in its variable's log and its service's
    // A row has one owner, its one writer, whose writes arrive in its own order: nothing here decides between writers.
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
        ribbit::lf::ebr::Guard eg;
        return land(row(s, v, i), true, bag, now, std::move(arr), std::move(prev), std::move(changed), t_writer, t_linger,
                    [](const RowVal*) { return true; });
    }
    // ---- [GONE_WITH_ITS_WRITER_V1] a session has ended: every row it wrote last is gone. Each removal takes its place in
    // its families' logs as a write does, so a read held on the family is given it, in order, as gone. Reads the
    // session's own list of the rows it wrote. Returns the lingers (seconds) of the rows it left: the host reaps each at
    // the session's end plus that many seconds.
    std::vector<int32_t> gone_with(uint64_t writer, double now, size_t* removed = nullptr) {
        std::vector<int32_t> later; size_t n = 0;
        if (!writer) return later;
        ribbit::lf::ebr::Guard eg;
        Writer* wr = writers_.find(writer, [&](Writer* x) { return x->id == writer; });
        for (WRow* e = wr ? wr->rows.load(std::memory_order_acquire) : nullptr; e; e = e->next) {
            Row* r = e->r;
            if (!mine(r, writer)) continue;
            int32_t left = LINGER_GOES;
            if (land(r, false, std::string(), now, nullptr, nullptr, nullptr, writer, LINGER_GOES, [&](const RowVal* x) {
                    if (!x || !x->live || x->writer != writer) return false;
                    if (x->linger != LINGER_GOES) { left = x->linger; return false; }
                    return true; })) n++;
            if (left > 0 && std::find(later.begin(), later.end(), left) == later.end()) later.push_back(left);
        }
        if (wr && later.empty()) drop_list(wr);       // nothing lingers: the list is done with
        if (removed) *removed = n;
        return later;
    }
    // [LINGER_BY_FAMILY_V1] the reaper: rows a session that ended at `ended` left to linger, whose time has come --
    // still that session's (written again since by someone else: theirs now, not reaped). Each goes as gone_with's do.
    size_t reap(uint64_t writer, double ended, double now) {
        size_t n = 0;
        if (!writer) return 0;
        ribbit::lf::ebr::Guard eg;
        Writer* wr = writers_.find(writer, [&](Writer* x) { return x->id == writer; });
        for (WRow* e = wr ? wr->rows.load(std::memory_order_acquire) : nullptr; e; e = e->next)
            if (mine(e->r, writer) && land(e->r, false, std::string(), now, nullptr, nullptr, nullptr, writer, LINGER_GOES, [&](const RowVal* x) {
                    return x && x->live && x->writer == writer && x->linger > 0 && ended + x->linger <= now; })) n++;
        return n;
    }
    // ---- remove one cell: the write whose id in its variable's log is `id` (s, v, id), if that is still the cell's
    // current value -- found at that slot, not looked for. private_too: a caller inside this process may remove a row of
    // a private ('#') service; the network API may not. It goes as a removal does: in order, in its families' logs.
    size_t remove(const std::string& s, const std::string& v, uint64_t id, bool private_too) {
        if (!private_too && !s.empty() && s[0] == '#') return 0;
        if (id == 0) return 0;
        ribbit::lf::ebr::Guard eg;
        const Variable* var = find_var(s, v); if (!var) return 0;
        Chunk* c = chunk(var->log, id - 1, false);
        if (!c || dead(c)) return 0;
        const Entry* e = c->slot[(id - 1) & (CH - 1)].load(std::memory_order_acquire); if (!e) return 0;
        const RowVal* cur = e->row->val.load(std::memory_order_acquire);
        if (!cur || !cur->live || cur->vid.load(std::memory_order_acquire) != id) return 0;
        return land(e->row, false, std::string(), cur->updated, nullptr, nullptr, nullptr, cur->writer, LINGER_GOES,
                    [cur](const RowVal* x) { return x == cur; }) ? 1 : 0;
    }

    // ---- read: the query's rows, id order. [LOCKFREE_READS_V1] inside one epoch guard, no lock, no wait.
    //   v and i given: that cell (past `after`: only if its id in its variable is past it)
    //   after >= 0: the family's log from `after` -- the variable's when v is given, else the service's (i filters)
    //   after < 0: the family's rows as they are now (the live ones), each id no later than the log's prefix
    std::vector<Cell> match(const std::string& s, const std::string& v, const std::string& i, long long after, int fresh, double now) const {
        std::vector<Cell> out; const double cutoff = now - fresh;
        ribbit::lf::ebr::Guard eg;
        if (!v.empty() && !i.empty()) {
            Row* r = find_row(s, v, i); if (!r) return out;
            const RowVal* x = r->val.load(std::memory_order_acquire);
            if (!x || (!x->live && after < 0)) return out;
            const uint64_t id = x->vid.load(std::memory_order_acquire);
            if (after >= 0 && (id == 0 || (long long)id <= after)) return out;   // 0: still landing in its log
            if (fresh > 0 && x->updated < cutoff) return out;
            out.push_back(cell(r, x, id ? id : prefix(r->var->log)));
            return out;
        }
        const Variable* var = v.empty() ? nullptr : find_var(s, v);
        const Service* sv = v.empty() ? find_svc(s) : nullptr;
        if (!var && !sv) return out;
        const Log& L = var ? var->log : sv->log; const bool svc = !var;
        if (after >= 0) {
            uint64_t pos = uint64_t(after);
            read_log(L, svc, pos, i, out, fresh > 0 ? &cutoff : nullptr);
            return out;
        }
        const uint64_t h0 = prefix(L);
        auto take = [&](Row* r) {
            const RowVal* x = r->val.load(std::memory_order_acquire);
            if (!x || !x->live) return;
            if (!i.empty() && r->instance != i) return;
            if (fresh > 0 && x->updated < cutoff) return;
            const uint64_t id = (svc ? x->sid : x->vid).load(std::memory_order_acquire);
            out.push_back(cell(r, x, id && id <= h0 ? id : h0));
        };
        if (var) for (Row* r = var->rows.load(std::memory_order_acquire); r; r = r->next_in_variable) take(r);
        else for (Variable* x = sv->vars.load(std::memory_order_acquire); x; x = x->next_in_service)
                 for (Row* r = x->rows.load(std::memory_order_acquire); r; r = r->next_in_variable) take(r);
        std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });
        return out;
    }
    // ---- the blocking read: evaluate; until satisfied or the deadline, sleep on the family's word and evaluate again.
    // The family: the variable's when v is given (one cell's too), else the service's. The word is watched before the
    // first evaluation, so a write between the two moves it.
    template <class Deadline, class Eval, class Satisfied>
    std::vector<Cell> wait(const std::string& s, const std::string& v, const std::string& i, Deadline deadline, Eval eval, Satisfied ok) {
        (void)i;
        Log& L = v.empty() ? service(s)->log : variable(s, v)->log;
        ribbit::lf::Watching w(L.word);
        uint32_t seen = L.word.seen();
        std::vector<Cell> rows = eval();
        while (!ok(rows)) {
            const auto left = std::chrono::duration_cast<std::chrono::nanoseconds>(deadline - std::chrono::steady_clock::now()).count();
            if (left <= 0) break;
            L.word.wait(seen, left);
            seen = L.word.seen();
            rows = eval();
        }
        return rows;
    }

    // ---- [FAMILY_HOOKS_V1] reading a family, on the reader's own thread ------------------------------------------
    // A Cursor is a place in one family's log; a read takes what is there from that place on. It belongs to its
    // reader's thread.
    struct Cursor {
        std::string s, v, instance; HookList* hooks = nullptr;
        const Log* log = nullptr; bool svc = false; uint64_t pos = 0;
        uint64_t recovered = 0, laps = 0;           // (no window to lap: kept for the host's diagnostics, always 0)
    };
    // from_after < 0: from now on (what is written after this call); else every write in the family past from_after
    Cursor cursor(const std::string& s, const std::string& v, const std::string& i, long long from_after) {
        Cursor c; c.s = s; c.v = v; c.instance = i;
        if (!v.empty()) { Variable* var = variable(s, v); c.hooks = &var->hooks; c.log = &var->log; }
        else { Service* sv = service(s); c.hooks = &sv->hooks; c.log = &sv->log; c.svc = true; }
        c.pos = from_after < 0 ? prefix(*c.log) : uint64_t(from_after);
        return c;
    }
    // What has been written to the family since the cursor's last read, appended to out, in the order it landed there.
    void read(Cursor& c, std::vector<Cell>& out) {
        ribbit::lf::ebr::Guard eg;
        read_log(*c.log, c.svc, c.pos, c.instance, out, nullptr);
    }
    // the newest id in a family now ((s, v, *), or (s, *, *) for v ""): where a reader starts "from now"
    uint64_t newest(const std::string& s, const std::string& v) {
        if (!v.empty()) { const Variable* var = find_var(s, v); return var ? prefix(var->log) : 0; }
        const Service* sv = find_svc(s); return sv ? prefix(sv->log) : 0;
    }
    // where the family's writes have reached (an atomic: any thread may ask, while the cursor's reader reads it).
    // It moving past what a reader last saw says there may be something new to read. [READERS_IN_PARALLEL_V1]
    static uint64_t head_of(const Cursor& c) { return c.log ? c.log->hint.load(std::memory_order_acquire) : 0; }
    // [SEEN_IS_WHAT_WAS_READ_V1] where the cursor's last read got to
    static uint64_t read_to(const Cursor& c) { return c.pos; }
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
    static Cell cell(const Row* r, const RowVal* x, uint64_t id) {
        return Cell{id, r->service, r->variable, r->instance, x->bag, x->updated, x->arr, x->prev, x->changed, !x->live};
    }
    // ---- the log
    // the chunk holding slot k; create: make the path to it. nullptr: never made (nothing there yet); DEAD: retired.
    static Chunk* chunk(const Log& L, uint64_t k, bool create) {
        std::atomic<void*>* a = const_cast<std::atomic<void*>*>(&L.top.p[(k >> 24) & 255]);
        for (int lv = 0; lv < 2; ++lv) {
            void* d = a->load(std::memory_order_acquire);
            if (!d) { if (!create) return nullptr;
                      Dir* fresh = new Dir; void* exp = nullptr;
                      if (a->compare_exchange_strong(exp, fresh, std::memory_order_acq_rel)) d = fresh; else { delete fresh; d = exp; } }
            a = &static_cast<Dir*>(d)->p[(k >> (lv == 0 ? 16 : 8)) & 255];
        }
        void* c = a->load(std::memory_order_acquire);
        if (c == DEAD) return static_cast<Chunk*>(DEAD);
        if (!c) { if (!create) return nullptr;
                  Chunk* fresh = new Chunk; fresh->home = a; void* exp = nullptr;
                  if (a->compare_exchange_strong(exp, fresh, std::memory_order_acq_rel)) c = fresh; else { delete fresh; c = exp; } }
        return static_cast<Chunk*>(c);
    }
    static bool dead(const Chunk* c) { return c == static_cast<const Chunk*>(DEAD); }
    // the first empty slot after the last filled one, filled with one CAS: its number is the write's place
    static uint64_t append(Log& L, Row* r, const RowVal* v, Chunk** in) {
        Entry* e = new Entry{r, v};
        uint64_t k = L.hint.load(std::memory_order_acquire);
        for (;;) {
            Chunk* c = chunk(L, k, true);
            if (dead(c)) { k = (k | (CH - 1)) + 1; continue; }
            std::atomic<const Entry*>& s = c->slot[k & (CH - 1)];
            if (s.load(std::memory_order_acquire)) { ++k; continue; }
            c->live.fetch_add(1, std::memory_order_acq_rel);
            const Entry* exp = nullptr;
            if (s.compare_exchange_strong(exp, e, std::memory_order_acq_rel)) {
                c->filled.fetch_add(1, std::memory_order_acq_rel);
                uint64_t h = L.hint.load(std::memory_order_relaxed);
                while (h < k + 1 && !L.hint.compare_exchange_weak(h, k + 1, std::memory_order_acq_rel)) {}
                *in = c;
                maybe_retire(c);
                return k;
            }
            c->live.fetch_sub(1, std::memory_order_acq_rel); ++k;
        }
    }
    // a slot passed by a newer write of its row: its chunk retires when every slot in it has been
    static void passed(Chunk* c) { if (c) { c->live.fetch_sub(1, std::memory_order_acq_rel); maybe_retire(c); } }
    static void maybe_retire(Chunk* c) {
        if (c->filled.load(std::memory_order_acquire) == CH && c->live.load(std::memory_order_acquire) == 0
            && !c->dead.exchange(true, std::memory_order_acq_rel)) {
            c->home->store(DEAD, std::memory_order_release);
            ribbit::lf::ebr::retire(c, &Chunk::free_);
        }
    }
    // the log's filled prefix: every slot below it is filled
    static uint64_t prefix(const Log& L) {
        uint64_t k = L.hint.load(std::memory_order_acquire);
        for (;;) {
            Chunk* c = chunk(L, k, false);
            if (!c) return k;
            if (dead(c)) { k = (k | (CH - 1)) + 1; continue; }
            if (!c->slot[k & (CH - 1)].load(std::memory_order_acquire)) return k;
            ++k;
        }
    }
    // the family's writes from slot pos to the first empty slot; pos moves past what was read
    static void read_log(const Log& L, bool svc, uint64_t& pos, const std::string& inst, std::vector<Cell>& out, const double* cutoff) {
        for (;;) {
            Chunk* c = chunk(L, pos, false);
            if (!c) return;
            if (dead(c)) { pos = (pos | (CH - 1)) + 1; continue; }
            const Entry* e = c->slot[pos & (CH - 1)].load(std::memory_order_acquire);
            if (!e) return;
            const uint64_t id = ++pos;
            const RowVal* cur = e->row->val.load(std::memory_order_acquire);
            if (cur && (svc ? cur->sid : cur->vid).load(std::memory_order_acquire) > id) continue;   // a newer write of it is further on
            if (!inst.empty() && e->row->instance != inst) continue;
            const RowVal* x = e->val;
            if (cutoff && x->updated < *cutoff) continue;
            out.push_back(cell(e->row, x, id));
        }
    }
    // A row's next value takes its place: one CAS on the row (ok(current) false: nothing lands), then its slot in its
    // variable's log and in its service's, then the value it replaced is let go (its slots passed, it to reclamation),
    // then the families' words move for whoever sleeps on them. The value is let go only after the new one has its
    // places, so a reader that meets the old value's slot while the new one is still landing still has it to read.
    template <class Ok>
    uint64_t land(Row* r, bool live, const std::string& bag, double now, ribbit::BagArrP arr, ribbit::BagArrP prev, Changed changed,
                  uint64_t writer, int32_t linger, Ok ok) {
        Variable* var = r->var;
        Service* svc = service_of(var);
        RowVal* nv = new RowVal(live, bag, now, std::move(arr), std::move(prev), std::move(changed), writer, linger);
        const RowVal* old = r->val.load(std::memory_order_acquire);
        for (;;) {
            if (!ok(old)) { delete nv; return 0; }
            if (r->val.compare_exchange_weak(old, nv, std::memory_order_acq_rel, std::memory_order_acquire)) break;
        }
        Chunk* vc = nullptr; Chunk* sc = nullptr;
        const uint64_t vk = append(var->log, r, nv, &vc); nv->vchunk.store(vc, std::memory_order_relaxed); nv->vid.store(vk + 1, std::memory_order_release);
        const uint64_t sk = append(svc->log, r, nv, &sc); nv->schunk.store(sc, std::memory_order_relaxed); nv->sid.store(sk + 1, std::memory_order_release);
        if (old) { passed(old->vchunk.load(std::memory_order_acquire)); passed(old->schunk.load(std::memory_order_acquire));
                   ribbit::lf::ebr::retire(const_cast<RowVal*>(old)); }
        if (writer && r->listed_by.load(std::memory_order_acquire) != writer) {
            r->listed_by.store(writer, std::memory_order_release);
            Writer* wr = writers_.find_or_create(writer, [&](Writer* x) { return x->id == writer; },
                                                 [&] { Writer* x = new Writer; x->id = writer; return x; }, [](Writer*) {});
            WRow* n = new WRow{r, wr->rows.load(std::memory_order_relaxed)};
            while (!wr->rows.compare_exchange_weak(n->next, n, std::memory_order_release, std::memory_order_relaxed)) {}
        }
        var->log.word.bump(); svc->log.word.bump();
        var->hooks.fire(); svc->hooks.fire();          // [FAMILY_HOOKS_V1]
        return vk + 1;
    }
    void drop_list(Writer* wr) {
        WRow* e = wr->rows.exchange(nullptr, std::memory_order_acq_rel);
        while (e) { WRow* n = e->next; ribbit::lf::ebr::retire(e); e = n; }
    }
    // the row's value, read without a lock (inside an epoch guard): live and written by that session -- checked again
    // in the CAS before anything lands
    static bool mine(Row* r, uint64_t writer) {
        const RowVal* x = r->val.load(std::memory_order_acquire); return x && x->live && x->writer == writer;
    }
    Service* service_of(Variable* var) {
        Service* sv = var->svc.load(std::memory_order_acquire);
        if (!sv) { sv = service(var->service); var->svc.store(sv, std::memory_order_release); }
        return sv;
    }
    std::atomic<WakeWord*> words_{nullptr};
    LockFreeTable<Row, 1 << 16> rows_;
    LockFreeTable<Variable, 1 << 14> vars_;
    LockFreeTable<Service, 1 << 10> services_;
    LockFreeTable<Writer, 1 << 12> writers_;

    Row* find_row(const std::string& s, const std::string& v, const std::string& i) const {
        return rows_.find(H(s, v, i), [&](Row* n) { return n->service == s && n->variable == v && n->instance == i; });
    }
    const Variable* find_var(const std::string& s, const std::string& v) const {
        return vars_.find(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; });
    }
    const Service* find_svc(const std::string& s) const {
        return services_.find(H(s), [&](Service* n) { return n->service == s; });
    }
    Row* row(const std::string& s, const std::string& v, const std::string& i) {
        Variable* var = variable(s, v);
        return rows_.find_or_create(H(s, v, i), [&](Row* n) { return n->service == s && n->variable == v && n->instance == i; },
            [&] { Row* r = new Row; r->service = s; r->variable = v; r->instance = i; r->var = var; return r; },
            [&](Row* r) {                         // a new row joins its variable's list
                Row* h = var->rows.load(std::memory_order_relaxed);
                do { r->next_in_variable = h; } while (!var->rows.compare_exchange_weak(h, r, std::memory_order_release, std::memory_order_relaxed));
            });
    }
    Service* service(const std::string& s) {
        return services_.find_or_create(H(s), [&](Service* x) { return x->service == s; },
            [&] { Service* x = new Service; x->service = s; return x; }, [](Service*) {});
    }
    Variable* variable(const std::string& s, const std::string& v) {
        return vars_.find_or_create(H(s, v), [&](Variable* n) { return n->service == s && n->variable == v; },
            [&] { Variable* n = new Variable; n->service = s; n->variable = v; return n; },
            [&](Variable* n) {
                Service* sv = service(s);
                Variable* h = sv->vars.load(std::memory_order_relaxed);
                do { n->next_in_service = h; } while (!sv->vars.compare_exchange_weak(h, n, std::memory_order_release, std::memory_order_relaxed));
            });
    }
};

}  // namespace ramm
