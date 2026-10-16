// daf_memory.hpp -- [DAF_MEMORY_V1] [DAF_FROG_V1] the DAF region's own memory, under the memory rules: no lock, no write
// contention, every mutable truth has an owner, contended decisions reduced by read in write order, held reads parked
// as continuations and answered by whoever writes, epoch reclamation, dead state reclaimed on next touch.
//
// A region is an implementation and its API is how you talk to it (ruling 2026-09-22): this is NOT the generic tuple
// memory with a DAF vocabulary on top. Nothing here is readable except through op=daf, so every read passes the check
// point.
//
//   records    (variable, instance) -> an immutable value {id, bag}; bag "" is a record that is gone. Each record has
//              ONE writer -- its owner -- who publishes by exchange. Where several parties have something to say about
//              one thing (a policy, a persistent entry, a designation), each says it in ITS OWN record (the instance
//              names the writer) and a reader folds them: the newest says. Ids come from the memory's one write clock.
//   claims     a decision several may race for (a DAF's name, a member name, a synonym, a nonce, an answer) is a claim
//              log: each claimant appends its claim (append is its own slot; nothing is overwritten) and the log is
//              read in write order as a gap-free prefix. The first claimant wins, and every reader folds the same
//              prefix to the same answer. No compare-and-retry: nobody's write is ever undone or redone.
//   durable    a permanent record is appended to the DAF file with its id and fdatasync'd BEFORE it is published (its
//              one writer publishes in id order). At start the highest id of each record wins.
//   waits      a held read on one or more variables: answered at once if it has something to say; otherwise parked as
//              the request's continuation on each variable's wait list (lock-free stacks). Every write to a variable
//              takes its list and answers what it now can, re-parking the rest; a wait answered through one variable
//              is dropped from the others when next touched. A wait of a session that has ended is dropped likewise.
#pragma once
#include "rina_memory.hpp"
#include "door/rina_region.h"
#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace dmem {
using rmem::Val;
inline double clock_s() { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return double(t.tv_sec) + double(t.tv_nsec) / 1e9; }

// ---- a growable table: insert-only open addressing of atomic pointers; a full table chains one twice its size
// (rmem::Table is fixed at 2^16 keys, which a region that sees sessions come and go for months would fill)
template <class T> class Grow {
public:
    explicit Grow(size_t cap = 1024) : cap_(cap), slot_(new std::atomic<Entry*>[cap]) { for (size_t i = 0; i < cap_; ++i) slot_[i].store(nullptr, std::memory_order_relaxed); }
    T* get(const std::string& key, bool create) {
        const size_t h0 = std::hash<std::string>()(key);
        for (size_t i = 0; i < cap_ / 2; ++i) {                       // half full, then the next table: probes stay short
            std::atomic<Entry*>& s = slot_[(h0 + i) & (cap_ - 1)];
            Entry* e = s.load(std::memory_order_acquire);
            if (!e) {
                if (!create) break;
                Entry* fresh = new Entry{key, new T}; Entry* expect = nullptr;
                if (s.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) return fresh->obj;
                delete fresh->obj; delete fresh; e = expect;
            }
            if (e->key == key) return e->obj;
        }
        Grow* n = next_.load(std::memory_order_acquire);
        if (!n) { if (!create) return nullptr; Grow* fresh = new Grow(cap_ * 2); Grow* expect = nullptr;
            if (next_.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) n = fresh; else { delete fresh; n = expect; } }
        return n->get(key, create);
    }
private:
    struct Entry { std::string key; T* obj; };
    const size_t cap_; std::unique_ptr<std::atomic<Entry*>[]> slot_; std::atomic<Grow*> next_{nullptr};
};

inline Grow<rmem::VarSet>& recs() { static auto* t = new Grow<rmem::VarSet>; return *t; }
inline std::atomic<uint64_t>& ids() { static std::atomic<uint64_t> n{0}; return n; }   // the memory's write clock

inline rmem::RecSlot* slot(const std::string& v, const std::string& i, bool create) {
    rmem::VarSet* vs = recs().get(v, create); if (!vs) return nullptr;
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
// every record of v past `after` (gone ones too when with_gone); instance "" = all
inline std::vector<Row> rows(const std::string& v, const std::string& i = "", uint64_t after = 0, bool with_gone = false) {
    std::vector<Row> out; rmem::VarSet* vs = recs().get(v, false); if (!vs) return out;
    auto g = rmem::ebr().guard();
    auto take = [&](rmem::RecSlot& r) { const Val* x = r.val.load(std::memory_order_acquire);
        if (x && x->id > after && (with_gone || !x->bag.empty())) out.push_back(Row{r.instance, x->id, x->bag}); };
    if (!i.empty()) { if (rmem::RecSlot* r = vs->get(i, false)) take(*r); } else vs->each(take);
    return out;
}
// the newest change to a variable, gone records included (0: never written)
inline uint64_t newest(const std::string& v) { uint64_t m = 0; for (auto& r : rows(v, "", 0, true)) if (r.id > m) m = r.id; return m; }

// ---- waits
struct WaitState { void* cont; uint64_t sid; std::atomic<bool> done{false}; };
struct Waiter { std::shared_ptr<WaitState> st; std::function<std::string()> answer; Waiter* next = nullptr; };
struct WaitList { std::atomic<Waiter*> head{nullptr}; };
inline Grow<WaitList>& waits() { static auto* t = new Grow<WaitList>; return *t; }
inline bool session_ended(uint64_t sid) { std::string b; return get("ended", std::to_string(sid), b, nullptr); }
inline void push_waiter(WaitList* L, Waiter* w) {
    Waiter* h = L->head.load(std::memory_order_acquire);
    do { w->next = h; } while (!L->head.compare_exchange_weak(h, w, std::memory_order_acq_rel, std::memory_order_acquire));
}
// whoever wrote v: answer every parked wait that now has its answer; re-park the rest; drop those answered elsewhere
// and those whose session ended
inline void wake(const std::string& v) {
    WaitList* L = waits().get(v, false); if (!L || !L->head.load(std::memory_order_acquire)) return;
    Waiter* w = L->head.exchange(nullptr, std::memory_order_acq_rel);
    while (w) { Waiter* n = w->next;
        if (w->st->done.load(std::memory_order_acquire)) delete w;                       // answered through another variable
        else if (session_ended(w->st->sid)) { if (!w->st->done.exchange(true)) rina_answer(w->st->cont, "{\"ok\":false,\"error\":\"gone\"}"); delete w; }
        else { const std::string a = w->answer();
            if (!a.empty()) { if (!w->st->done.exchange(true)) rina_answer(w->st->cont, a); delete w; }
            else push_waiter(L, w); }
        w = n; }
}
// park a wait on every variable that can change its answer (answer() says "" while there is nothing to say); re-checked
// after parking, so a write that landed between the caller's check and the park is never missed
inline void park(const std::vector<std::string>& vars, void* cont, uint64_t sid, const std::function<std::string()>& answer) {
    auto st = std::make_shared<WaitState>(); st->cont = cont; st->sid = sid;
    for (auto& v : vars) { WaitList* L = waits().get(v, true); push_waiter(L, new Waiter{st, answer}); }
    for (auto& v : vars) wake(v);
}

// ---- writes: by the record's owner
inline uint64_t put(const std::string& v, const std::string& i, const std::string& bag, const std::function<void(uint64_t)>& before = nullptr) {
    rmem::RecSlot* r = slot(v, i, true);
    const uint64_t id = ids().fetch_add(1, std::memory_order_acq_rel) + 1;
    if (before) before(id);                                     // the durable append, before anyone can read it
    const Val* old = r->val.exchange(new Val{id, bag, clock_s()}, std::memory_order_acq_rel);
    rmem::ebr().retire(old);
    wake(v);
    return id;
}
// replay at start: a value with its original id, no waits to answer yet
inline void restore(const std::string& v, const std::string& i, const std::string& bag, uint64_t id) {
    rmem::RecSlot* r = slot(v, i, true);
    const Val* old = r->val.exchange(new Val{id, bag, clock_s()}, std::memory_order_acq_rel); delete old;
    uint64_t c = ids().load(); while (c < id && !ids().compare_exchange_weak(c, id)) {}
}

// ---- claims: append-only, read in write order as a gap-free prefix
struct Claim { uint64_t seq; std::string bag; };
class ClaimLog {
public:
    ClaimLog() { Seg* s = new Seg(1); head_.store(s); tail_.store(s); }
    // append a claim: its place in the log is its number, 1 = the first claimant
    uint64_t append(std::string bag) {
        const uint64_t n = next_.fetch_add(1, std::memory_order_acq_rel);
        Seg* s = seg_for(n);
        s->slot[n - s->base].store(new Claim{n, std::move(bag)}, std::memory_order_release);
        rmem::bump(ww_);
        return n;
    }
    // claims 1..upto, in order; a claim numbered but still being placed is waited for (on the log's wait word)
    template <class F> void prefix(uint64_t upto, F each) {
        for (uint64_t k = 1; k <= upto; ++k) {
            const Claim* c = nullptr;
            for (;;) { const uint32_t seen = ww_.gen.load(std::memory_order_acquire);
                Seg* s = find(k); if (s) c = s->slot[k - s->base].load(std::memory_order_acquire);
                if (c) break;
                rmem::sleep_on(ww_, seen, 0.01); }
            each(*c);
        }
    }
    uint64_t count() const { return next_.load(std::memory_order_acquire) - 1; }
private:
    static constexpr uint64_t SEG = 256;
    struct Seg { explicit Seg(uint64_t b) : base(b) { for (auto& x : slot) x.store(nullptr, std::memory_order_relaxed); }
                 const uint64_t base; std::atomic<const Claim*> slot[SEG]; std::atomic<Seg*> next{nullptr}; };
    Seg* seg_for(uint64_t n) {
        Seg* s = tail_.load(std::memory_order_acquire); if (n < s->base) s = head_.load(std::memory_order_acquire);
        for (;;) { if (n < s->base + SEG) return s;
            Seg* nx = s->next.load(std::memory_order_acquire);
            if (!nx) { Seg* fresh = new Seg(s->base + SEG); Seg* expect = nullptr;
                if (s->next.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) nx = fresh; else { delete fresh; nx = expect; }
                Seg* t = s; tail_.compare_exchange_strong(t, nx, std::memory_order_acq_rel); }
            s = nx; }
    }
    Seg* find(uint64_t n) { for (Seg* s = head_.load(std::memory_order_acquire); s; s = s->next.load(std::memory_order_acquire)) if (n < s->base + SEG) return n >= s->base ? s : nullptr; return nullptr; }
    std::atomic<uint64_t> next_{1}; std::atomic<Seg*> head_{nullptr}, tail_{nullptr}; rmem::WaitWord ww_;
};
inline Grow<ClaimLog>& claims() { static auto* t = new Grow<ClaimLog>; return *t; }
inline ClaimLog& claim_log(const std::string& key) { return *claims().get(key, true); }
}  // namespace dmem
