// daf_memory.hpp -- [DAF_MEMORY_V1] the DAF region's own memory, under the memory rules (rina_memory.hpp): no lock, no
// write contention by design, immutable values published by exchange or compare-and-swap, held reads parked as
// continuations and answered by whoever writes, epoch reclamation, dead state reclaimed on next touch.
//
// A region is an implementation and its API is how you talk to it (ruling 2026-09-22): this is NOT the generic tuple
// memory with a DAF vocabulary on top. Nothing here is readable except through op=daf, so every read passes the DAF's
// check point.
//
//   records   (variable, instance) -> an immutable value {id, bag}; bag "" is a record that is gone. Ids come from one
//             counter, so a variable's newest change is its highest id. The tables are rmem's: insert-only open
//             addressing of atomic pointers, an instance claiming its slot once by compare-and-swap.
//   writes    put      exchange: a record only its owner writes (a member's own enrollment, its own entry in a name)
//             cas      compare-and-swap from the value the writer read: a decision that others may race (a name taken,
//                      a nonce used, a DAF created, an approval completing). The loser re-reads and sees the winner.
//   durable   a Permanent record is appended to the DAF file and fdatasync'd BEFORE it is published, with its id. Its
//             publication is a compare-and-swap from the value read, so ids rise in publication order; an attempt that
//             loses appends a void line for its id. At start the highest surviving id of each record wins.
//   waits     a held read on a variable (one instance, or all): answered at once if a record there is past `after`;
//             otherwise parked as the request's continuation on the variable's wait list (a lock-free stack). Every
//             write to the variable takes the list and answers what it now can, re-parking the rest. A wait of a
//             session that has ended is dropped at the next touch.
#pragma once
#include "rina_memory.hpp"
#include "door/rina_region.h"
#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace dmem {
using rmem::Val;
inline rmem::Table<rmem::VarSet>& recs() { static auto* t = new rmem::Table<rmem::VarSet>; return *t; }
inline std::atomic<uint64_t>& ids() { static std::atomic<uint64_t> n{0}; return n; }
inline double clock_s() { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return double(t.tv_sec) + double(t.tv_nsec) / 1e9; }

inline rmem::RecSlot* slot(const std::string& v, const std::string& i, bool create) {
    rmem::VarSet* vs = recs().get(v, create);
    if (!vs) { if (create) throw std::runtime_error("daf memory: the variable table is full"); return nullptr; }
    return vs->get(i, create);
}
struct Row { std::string instance; uint64_t id; std::string bag; };   // a copy, safe after the guard

// ---- reads (inside an ebr guard; values are copied out)
inline bool get(const std::string& v, const std::string& i, std::string& bag, uint64_t* id = nullptr) {
    rmem::RecSlot* r = slot(v, i, false); if (!r) return false;
    auto g = rmem::ebr().guard(); const Val* x = r->val.load(std::memory_order_acquire);
    if (!x || x->bag.empty()) return false;
    bag = x->bag; if (id) *id = x->id; return true;
}
inline uint64_t version(const std::string& v, const std::string& i) {        // the record's id, gone or not (0: never)
    rmem::RecSlot* r = slot(v, i, false); if (!r) return 0;
    auto g = rmem::ebr().guard(); const Val* x = r->val.load(std::memory_order_acquire); return x ? x->id : 0;
}
// every record of v past `after` (gone ones too when with_gone); instance "" = all
inline std::vector<Row> rows(const std::string& v, const std::string& i = "", uint64_t after = 0, bool with_gone = false) {
    std::vector<Row> out; rmem::VarSet* vs = recs().get(v, false); if (!vs) return out;
    auto g = rmem::ebr().guard();
    auto take = [&](rmem::RecSlot& r) { const Val* x = r.val.load(std::memory_order_acquire);
        if (x && x->id > after && (with_gone || !x->bag.empty())) out.push_back(Row{r.instance, x->id, x->bag}); };
    if (!i.empty()) { if (rmem::RecSlot* r = vs->get(i, false)) take(*r); } else vs->each(take);
    return out;
}

// ---- waits
struct Waiter { void* cont; uint64_t after; std::string inst; uint64_t sid; std::function<std::string(const Waiter&)> answer; Waiter* next = nullptr; };
struct WaitList { std::atomic<Waiter*> head{nullptr}; };
inline rmem::Table<WaitList>& waits() { static auto* t = new rmem::Table<WaitList>; return *t; }
// a session that has ended: the record ("ended", <sid>), written once by daf_session_down
inline bool session_ended(uint64_t sid) { std::string b; return get("ended", std::to_string(sid), b, nullptr); }
inline void push_waiter(WaitList* L, Waiter* w) {
    Waiter* h = L->head.load(std::memory_order_acquire);
    do { w->next = h; } while (!L->head.compare_exchange_weak(h, w, std::memory_order_acq_rel, std::memory_order_acquire));
}
// whoever wrote v: answer every parked wait that now has its answer; re-park the rest; drop those whose session ended
inline void wake(const std::string& v) {
    WaitList* L = waits().get(v, false); if (!L || !L->head.load(std::memory_order_acquire)) return;
    Waiter* w = L->head.exchange(nullptr, std::memory_order_acq_rel);
    while (w) { Waiter* n = w->next;
        if (session_ended(w->sid)) { rina_answer(w->cont, "{\"ok\":false,\"error\":\"gone\"}"); delete w; }   // dead state, reclaimed on touch
        else { const std::string a = w->answer(*w); if (!a.empty()) { rina_answer(w->cont, a); delete w; } else push_waiter(L, w); }
        w = n; }
}
// park a wait (the answer function returns "" while there is nothing to say); re-checked after parking, so a write that
// landed between the caller's check and the park is never missed
inline void park(const std::string& v, Waiter* w) {
    WaitList* L = waits().get(v, true); if (!L) throw std::runtime_error("daf memory: the wait table is full");
    push_waiter(L, w);
    wake(v);
}

// ---- writes
inline uint64_t put(const std::string& v, const std::string& i, const std::string& bag) {   // the record's one writer
    rmem::RecSlot* r = slot(v, i, true);
    const uint64_t id = ids().fetch_add(1, std::memory_order_acq_rel) + 1;
    const Val* old = r->val.exchange(new Val{id, bag, clock_s()}, std::memory_order_acq_rel);
    rmem::ebr().retire(old);
    wake(v);
    return id;
}
// a decision others may race: `decide` sees the current bag ("" if none or gone) and returns the new bag, or false to
// write nothing. Retried from a fresh read until it lands or declines. Returns the id written (0: declined).
// `before(id, bag)` runs after the id is taken and before the value is published (the durable append).
inline uint64_t cas(const std::string& v, const std::string& i, const std::function<bool(const std::string& cur, std::string& next)>& decide,
                    const std::function<void(uint64_t, const std::string&)>& before = nullptr,
                    const std::function<void(uint64_t)>& lost = nullptr) {
    rmem::RecSlot* r = slot(v, i, true);
    for (;;) {
        uint64_t won = 0; const Val* old = nullptr;
        {   auto g = rmem::ebr().guard();
            const Val* cur = r->val.load(std::memory_order_acquire);
            std::string next; if (!decide(cur ? cur->bag : std::string(), next)) return 0;
            const uint64_t id = ids().fetch_add(1, std::memory_order_acq_rel) + 1;   // taken AFTER the read: ids rise in publication order
            if (before) before(id, next);
            const Val* fresh = new Val{id, next, clock_s()};
            const Val* expect = cur;
            if (r->val.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) { won = id; old = cur; }
            else { delete fresh; if (lost) lost(id); } }
        if (won) { if (old) rmem::ebr().retire(old); wake(v); return won; }   // outside the guard: retire, then answer waits
    }
}
// replay at start: a value with its original id, no waits to answer yet
inline void restore(const std::string& v, const std::string& i, const std::string& bag, uint64_t id) {
    rmem::RecSlot* r = slot(v, i, true);
    const Val* old = r->val.exchange(new Val{id, bag, clock_s()}, std::memory_order_acq_rel); delete old;
    uint64_t c = ids().load(); while (c < id && !ids().compare_exchange_weak(c, id)) {}
}
}  // namespace dmem
