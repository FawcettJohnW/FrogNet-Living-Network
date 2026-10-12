// rina_memory.hpp -- [RINA_MEMORY_V1] the RINA region's own memory: the shape of what RINA does, under the memory rules
// (no lock, no write contention, immutable values published by compare-and-swap or exchange, held reads on wait words
// that wake only when someone waits, epoch reclamation, dead state reclaimed on next touch).
//
// A region is an implementation and its API is how you talk to it (ruling 2026-09-22): this is NOT the generic tuple
// memory with a RINA vocabulary on top. What RINA needs:
//
//   InputSpace  one per program: everything addressed to it (flow requests, verdicts, SDUs, its end) in the order it was
//               addressed. Many producers, ONE consumer (the program's held wait). An event is immutable once published;
//               its number is its place. Segments of SEG slots are appended as needed and freed (epoch reclamation) once
//               the program has read past them. The program's wait sleeps on the space's wait word.
//   FlowDir     one per flow direction (fid, receiving program): the SDUs in flight, by sequence number, in a ring the
//               credit bounds (an SDU is overwritten only once its receiver has taken it, which the credit guarantees),
//               so a later reference (an echo) finds the very bytes; the receiver's contiguous taken count; the credit
//               word the sender sleeps on when it is RING ahead.
//
// Both are found through an insert-only open-addressing table of atomic pointers (keys never move; a dead program's
// space is emptied on its session's end and its slot reused only by the same key).
#pragma once
#include <atomic>
#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <linux/futex.h>
#include <memory>
#include <set>
#include <string>
#include <sys/syscall.h>
#include <unistd.h>
#include <vector>
#include "ebr.hpp"

namespace rmem {

// ---- wait words --------------------------------------------------------------------------------------------------------
struct WaitWord { std::atomic<uint32_t> gen{0}; std::atomic<uint32_t> waiters{0}; };
inline void bump(WaitWord& w) {                          // a writer: wake only if someone is waiting here
    w.gen.fetch_add(1, std::memory_order_acq_rel);
    if (w.waiters.load(std::memory_order_acquire))
        syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w.gen), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0);
}
inline void sleep_on(WaitWord& w, uint32_t seen, double secs) {   // a reader: sleep while the word is still `seen`
    w.waiters.fetch_add(1, std::memory_order_acq_rel);
    if (w.gen.load(std::memory_order_acquire) == seen) {
        if (secs > 1e9) secs = 1e9;
        timespec ts; ts.tv_sec = time_t(secs); ts.tv_nsec = long((secs - double(ts.tv_sec)) * 1e9);
        syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w.gen), FUTEX_WAIT_PRIVATE, seen, &ts, nullptr, 0);
    }
    w.waiters.fetch_sub(1, std::memory_order_acq_rel);
}

inline ribbit::Ebr& ebr() { static ribbit::Ebr* e = new ribbit::Ebr; return *e; }

// ---- a parked held read: the continuation of a request that will be answered when its answer exists --------------------
struct Parked { void* cont; uint64_t arg; std::string who; };   // [DEFERRED_ANSWER_V1] arg: the wait's `after`, the credit's `have`

// ---- an input space ----------------------------------------------------------------------------------------------------
struct Event { uint64_t seq; std::string bag; };          // immutable once published
struct Segment {
    static constexpr uint64_t SEG = 1024;
    explicit Segment(uint64_t b) : base(b) { for (auto& s : slot) s.store(nullptr, std::memory_order_relaxed); }
    ~Segment() { for (auto& s : slot) delete s.load(std::memory_order_relaxed); }
    const uint64_t base;                                  // the first event number this segment holds
    std::atomic<const Event*> slot[SEG];
    std::atomic<Segment*> next{nullptr};
};
class InputSpace {
public:
    InputSpace() { Segment* s = new Segment(1); head_.store(s); tail_.store(s); }
    ~InputSpace() { for (Segment* s = head_.load(); s;) { Segment* n = s->next.load(); delete s; s = n; } }
    // any producer: the event gets the next number; published whole into its slot; the consumer is woken
    uint64_t push(std::string bag) {
        auto g = ebr().guard();
        const uint64_t n = next_.fetch_add(1, std::memory_order_acq_rel);
        Segment* s = seg_for(n);
        s->slot[n - s->base].store(new Event{n, std::move(bag)}, std::memory_order_release);
        bump(ww_);
        return n;
    }
    // the one consumer: every event numbered after `after`, up to the first not yet published (a prefix: never a gap);
    // segments wholly at or below `after` (already read) are freed
    template <class F> size_t read_after(uint64_t after, F each) {
        auto g = ebr().guard();
        release_below(after + 1);
        size_t k = 0;
        for (uint64_t n = after + 1;; ++n) {
            Segment* s = find(n); if (!s) break;
            const Event* e = s->slot[n - s->base].load(std::memory_order_acquire);
            if (!e) break;
            each(*e); ++k;
        }
        return k;
    }
    // [DEFERRED_ANSWER_V1] the program's one held wait, parked: whoever pushes next takes it (exactly once) and answers it
    void park(Parked* p) { Parked* old = parked_.exchange(p, std::memory_order_acq_rel); delete old; }
    Parked* take() { return parked_.exchange(nullptr, std::memory_order_acq_rel); }
    bool any_after(uint64_t after) { auto g = ebr().guard(); Segment* s = find(after + 1); return s && s->slot[after + 1 - s->base].load(std::memory_order_acquire); }
    uint32_t word() const { return ww_.gen.load(std::memory_order_acquire); }
    void sleep(uint32_t seen, double secs) { sleep_on(ww_, seen, secs); }
    void end() { gone_.store(true, std::memory_order_release); bump(ww_); }
    bool gone() const { return gone_.load(std::memory_order_acquire); }
    uint64_t numbered() const { return next_.load(std::memory_order_acquire) - 1; }   // events numbered so far (a debugging aid)
    uint64_t head_base() const { return head_.load(std::memory_order_acquire)->base; }
private:
    Segment* seg_for(uint64_t n) {                       // the segment holding n, appending segments as needed
        Segment* s = tail_.load(std::memory_order_acquire);
        if (n < s->base) s = head_.load(std::memory_order_acquire);   // an older one (rare: a slow producer)
        for (;;) {
            if (n < s->base + Segment::SEG) return s;
            Segment* nx = s->next.load(std::memory_order_acquire);
            if (!nx) { Segment* fresh = new Segment(s->base + Segment::SEG); Segment* expect = nullptr;
                if (s->next.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) nx = fresh; else { delete fresh; nx = expect; }
                Segment* t = s; tail_.compare_exchange_strong(t, nx, std::memory_order_acq_rel); }
            s = nx;
        }
    }
    Segment* find(uint64_t n) {                           // the segment holding n, or null if not appended yet
        for (Segment* s = head_.load(std::memory_order_acquire); s; s = s->next.load(std::memory_order_acquire))
            if (n < s->base + Segment::SEG) return n >= s->base ? s : nullptr;
        return nullptr;
    }
    void release_below(uint64_t n) {                      // the consumer only: free what it has read
        for (;;) {
            Segment* h = head_.load(std::memory_order_acquire); Segment* nx = h->next.load(std::memory_order_acquire);
            if (!nx || h->base + Segment::SEG > n) return;      // keep the segment holding n, and always one
            if (head_.compare_exchange_strong(h, nx, std::memory_order_acq_rel)) ebr().retire(h);
        }
    }
    std::atomic<uint64_t> next_{1};
    std::atomic<Segment*> head_{nullptr}, tail_{nullptr};
    std::atomic<Parked*> parked_{nullptr};
    WaitWord ww_; std::atomic<bool> gone_{false};
};

// ---- a flow direction --------------------------------------------------------------------------------------------------
struct Sdu { uint64_t q; std::string d; };               // immutable once published
class FlowDir {
public:
    static constexpr uint64_t RING = 4096;               // = the library's RING: the credit window
    FlowDir() { for (auto& s : ring_) s.store(nullptr, std::memory_order_relaxed); }
    ~FlowDir() { for (auto& s : ring_) delete s.load(std::memory_order_relaxed); }
    void keep(uint64_t q, const std::string& d) {        // the flow's one sender: the SDU, where a reference can find it
        const Sdu* old = ring_[q % RING].exchange(new Sdu{q, d}, std::memory_order_acq_rel);
        if (old) ebr().retire(old);
    }
    bool find(uint64_t q, std::string& d) {              // a reference: the very SDU q, or false if since overwritten
        auto g = ebr().guard();
        const Sdu* s = ring_[q % RING].load(std::memory_order_acquire);
        if (!s || s->q != q) return false;
        d = s->d; return true;
    }
    // the receiver's wait only: SDUs handed over; returns the contiguous count, and whether a quarter was crossed
    uint64_t taken(const std::vector<uint64_t>& qs, bool& crossed) {
        const uint64_t before = next_;
        for (uint64_t q : qs) if (q >= next_) above_.insert(q);
        while (!above_.empty() && *above_.begin() == next_) { above_.erase(above_.begin()); ++next_; }
        crossed = next_ / (RING / 4) != before / (RING / 4);
        if (crossed) { credit_.store(next_, std::memory_order_release); bump(cw_); }
        return next_;
    }
    // the sender: the credit past `have`, sleeping on the credit word until there is some (or the receiver is gone)
    bool credit_after(uint64_t have, uint64_t& n) {
        for (;;) {
            const uint32_t seen = cw_.gen.load(std::memory_order_acquire);
            if (gone_.load(std::memory_order_acquire)) return false;
            n = credit_.load(std::memory_order_acquire);
            if (n > have) return true;
            sleep_on(cw_, seen, 1e9);
        }
    }
    void end() { gone_.store(true, std::memory_order_release); bump(cw_); }
    // [CREDIT_IS_STATE_V1] the receiver's own truth: how many SDUs of this flow it has handed to its program (contiguous)
    void publish_taken(uint64_t n) { uint64_t c = credit_.load(); while (n > c && !credit_.compare_exchange_weak(c, n)) {} bump(cw_); }
    bool gone() const { return gone_.load(std::memory_order_acquire); }
    uint64_t credit() const { return credit_.load(std::memory_order_acquire); }
    // [DEFERRED_ANSWER_V1] the sender's credit wait, parked: taken (exactly once) when credit passes its `have`
    void park(Parked* p) { Parked* old = parked_.exchange(p, std::memory_order_acq_rel); delete old; }
    Parked* take() { return parked_.exchange(nullptr, std::memory_order_acq_rel); }
    std::string state() const { return "taken=" + std::to_string(next_) + " above=" + std::to_string(above_.size()) + " credit=" + std::to_string(credit_.load()) + (gone_.load() ? " gone" : ""); }
private:
    std::atomic<const Sdu*> ring_[RING];
    uint64_t next_ = 0; std::set<uint64_t> above_;       // the receiver's wait alone touches these
    std::atomic<uint64_t> credit_{0}; WaitWord cw_; std::atomic<bool> gone_{false};
    std::atomic<Parked*> parked_{nullptr};
};

// ---- the tables: insert-only open addressing, atomic pointers --------------------------------------------------------
template <class T> class Table {
public:
    static constexpr size_t N = 1 << 16;
    Table() { for (auto& s : slot_) s.store(nullptr, std::memory_order_relaxed); }
    T* get(const std::string& key, bool create) {        // the object for key (made once, by whoever is first)
        const size_t h0 = std::hash<std::string>()(key);
        for (size_t i = 0; i < N; ++i) {
            std::atomic<Entry*>& s = slot_[(h0 + i) & (N - 1)];
            Entry* e = s.load(std::memory_order_acquire);
            if (!e) {
                if (!create) return nullptr;
                Entry* fresh = new Entry{key, new T}; Entry* expect = nullptr;
                if (s.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) return fresh->obj;
                delete fresh->obj; delete fresh; e = expect;
            }
            if (e->key == key) return e->obj;
        }
        return nullptr;                                   // full: the caller says so
    }
private:
    struct Entry { std::string key; T* obj; };
    std::atomic<Entry*> slot_[N];
};

inline Table<InputSpace>& spaces() { static Table<InputSpace>* t = new Table<InputSpace>; return *t; }
inline Table<FlowDir>& flows() { static Table<FlowDir>* t = new Table<FlowDir>; return *t; }

}  // namespace rmem

// ---- control records --------------------------------------------------------------------------------------------------
// [RINA_RECORDS_V1] the region's control state -- IPC processes, DIF policy, registrations, flow records, holders, a
// program's liveness, sessions -- as records: (variable, instance) -> an immutable value, published by exchange. A
// variable's records live in an open-addressing table of slots (an instance claims its slot once, by compare-and-swap);
// a full table chains a table twice its size. Reading a variable walks its tables; reading one record finds its slot.
namespace rmem {
struct Val { uint64_t id; std::string bag; double updated; };
struct RecSlot { std::string instance; std::atomic<const Val*> val{nullptr}; };
class VarSet {
public:
    explicit VarSet(size_t cap = 64) : cap_(cap), slot_(new std::atomic<RecSlot*>[cap]) { for (size_t i = 0; i < cap_; ++i) slot_[i].store(nullptr, std::memory_order_relaxed); }
    ~VarSet() { for (size_t i = 0; i < cap_; ++i) if (RecSlot* r = slot_[i].load()) { delete r->val.load(); delete r; } delete next_.load(); }
    RecSlot* get(const std::string& inst, bool create) {
        const size_t h0 = std::hash<std::string>()(inst);
        for (size_t i = 0; i < cap_; ++i) {
            std::atomic<RecSlot*>& s = slot_[(h0 + i) % cap_];
            RecSlot* r = s.load(std::memory_order_acquire);
            if (!r) {
                if (!create) break;
                RecSlot* fresh = new RecSlot; fresh->instance = inst; RecSlot* expect = nullptr;
                if (s.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) return fresh;
                delete fresh; r = expect;
            }
            if (r->instance == inst) return r;
        }
        VarSet* n = next_.load(std::memory_order_acquire);   // full (or not here): the next, larger table
        if (!n) { if (!create) return nullptr; VarSet* fresh = new VarSet(cap_ * 2); VarSet* expect = nullptr;
            if (next_.compare_exchange_strong(expect, fresh, std::memory_order_acq_rel)) n = fresh; else { delete fresh; n = expect; } }
        return n->get(inst, create);
    }
    template <class F> void each(F f) {
        for (size_t i = 0; i < cap_; ++i) if (RecSlot* r = slot_[i].load(std::memory_order_acquire)) f(*r);
        if (VarSet* n = next_.load(std::memory_order_acquire)) n->each(f);
    }
private:
    const size_t cap_; std::unique_ptr<std::atomic<RecSlot*>[]> slot_; std::atomic<VarSet*> next_{nullptr};
};
inline Table<VarSet>& records() { static Table<VarSet>* t = new Table<VarSet>; return *t; }
inline std::atomic<uint64_t>& record_ids() { static std::atomic<uint64_t> n{0}; return n; }
}  // namespace rmem
