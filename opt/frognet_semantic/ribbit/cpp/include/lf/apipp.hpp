// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lf/apipp.hpp -- API++ [API_PLUS_PLUS_V1]: the lock-free primitives the Redis and MQTT regions each wrote for
// themselves, once, in the platform. Every region builds its memory from these; the platform memory (ram_memory.hpp)
// offers the same operations on tuples, over the wire (API-PLUS-PLUS.md).
//
//   WaitWord     a held read's word: a writer pays only when someone is parked on it
//   QuietSpan    a batch's wakes held on this thread and delivered once each at its end
//   TakeStack    many push, one takes everything at once, oldest first
//   cas / swap / update / take   on one immutable value behind an atomic pointer (epoch-reclaimed)
//
// No lock anywhere. A value behind an atomic pointer is never changed in place: a writer makes a new one and publishes
// it with one compare-and-swap; the one it replaced is retired to epoch reclamation (lf/ebr.hpp), so a reader inside an
// epoch never follows freed memory.
#pragma once
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <climits>
#include <cstdint>
#include <ctime>
#include <vector>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "lf/ebr.hpp"

namespace ribbit { namespace lf {

// ---- futex: sleep on a 32-bit word until it is no longer `seen` -------------------------------------------------
// true: woken or the word had already moved; false: the time ran out. timeout_ns < 0: no time limit.
inline bool futex_wait(std::atomic<uint32_t>& w, uint32_t seen, int64_t timeout_ns = -1) {
    timespec ts, *tp = nullptr;
    if (timeout_ns >= 0) { ts.tv_sec = time_t(timeout_ns / 1000000000); ts.tv_nsec = long(timeout_ns % 1000000000); tp = &ts; }
    long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, seen, tp, nullptr, 0);
    return !(r < 0 && errno == ETIMEDOUT);
}
inline void futex_wake(std::atomic<uint32_t>& w, int n = INT_MAX) {
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, n, nullptr, nullptr, 0);
}

// ---- WaitWord ---------------------------------------------------------------------------------------------------
// A waiter registers (watch: a full-barrier RMW) BEFORE it reads seen() and checks the data; a writer publishes,
// fences, then reads the registration (bump). One of them sees the other: either the writer bumps, or the waiter's
// data check sees the write. Nobody parked means the writer writes no shared line -- the common case, the one that
// scales. On its own cache line: a neighbour's word must not share it.
struct alignas(64) WaitWord {
    std::atomic<uint32_t> gen{0};
    std::atomic<uint32_t> waiters{0};
    void watch() { waiters.fetch_add(1, std::memory_order_seq_cst); }
    void unwatch() { waiters.fetch_sub(1, std::memory_order_seq_cst); }
    uint32_t seen() const { return gen.load(std::memory_order_acquire); }
    // after a write: wake whoever is parked; false (and nothing written) when nobody is
    bool bump() {
        std::atomic_thread_fence(std::memory_order_seq_cst);
        if (waiters.load(std::memory_order_seq_cst) == 0) return false;
        gen.fetch_add(1, std::memory_order_release); futex_wake(gen);
        return true;
    }
    // move the word whether or not anyone is registered (an unblock, a kill)
    void wake() { gen.fetch_add(1, std::memory_order_release); futex_wake(gen); }
    // park until the word moves past `seen` (or the time runs out: false)
    bool wait(uint32_t seen_gen, int64_t timeout_ns = -1) { return futex_wait(gen, seen_gen, timeout_ns); }
};
// watch for the life of a scope
struct Watching {
    WaitWord& w;
    explicit Watching(WaitWord& ww) : w(ww) { w.watch(); }
    ~Watching() { w.unwatch(); }
    Watching(const Watching&) = delete; Watching& operator=(const Watching&) = delete;
};

// ---- QuietSpan --------------------------------------------------------------------------------------------------
// While a span is open on this thread, hold(k) keeps k instead of waking it now; end(deliver) calls deliver(k) once
// for each distinct k held, after the whole batch has landed -- a held read wakes once and sees the batch whole.
// K: anything ordered and comparable (a front's number, a (region, variable) pair).
template <class K>
class QuietSpan {
public:
    static void begin() { held() = &store(); store().clear(); }
    static bool open() { return held() != nullptr; }
    // true: held for the end of the span (the caller wakes nothing now); false: no span, wake now
    static bool hold(const K& k) { if (!held()) return false; held()->push_back(k); return true; }
    template <class Deliver> static void end(Deliver deliver) {
        std::vector<K>* h = held(); held() = nullptr;
        if (!h) return;
        std::sort(h->begin(), h->end()); h->erase(std::unique(h->begin(), h->end()), h->end());
        for (const K& k : *h) deliver(k);
        h->clear();
    }
private:
    static std::vector<K>*& held() { static thread_local std::vector<K>* p = nullptr; return p; }
    static std::vector<K>& store() { static thread_local std::vector<K> v; return v; }
};

// ---- TakeStack --------------------------------------------------------------------------------------------------
// Many threads push; whoever takes gets everything pushed so far at once, oldest first, and leaves the stack empty.
// Intrusive: T has a `T* next`. Nothing is lost and nothing is taken twice: push is one compare-and-swap on the head,
// take is one exchange of it.
template <class T>
class TakeStack {
public:
    // true when the stack was empty before this push (the taker may be asleep and need waking)
    bool push(T* x) {
        T* h = head_.load(std::memory_order_relaxed);
        do { x->next = h; } while (!head_.compare_exchange_weak(h, x, std::memory_order_seq_cst, std::memory_order_relaxed));
        return h == nullptr;
    }
    // everything pushed so far, oldest first
    std::vector<T*> take() {
        T* list = head_.exchange(nullptr, std::memory_order_acquire);
        std::vector<T*> out;
        for (T* x = list; x; x = x->next) out.push_back(x);
        std::reverse(out.begin(), out.end());
        return out;
    }
    bool empty() const { return head_.load(std::memory_order_seq_cst) == nullptr; }
private:
    std::atomic<T*> head_{nullptr};
};

// ---- one value behind an atomic pointer -------------------------------------------------------------------------
// Call these inside an epoch (ebr::Guard): the pointers they hand back stay valid until the caller leaves it.
// `retire` is how a replaced value goes (default: epoch reclamation); a structure whose values belong to something
// else (a batch) passes its own.
template <class T> inline void retire_default(const T* p) { if (p) ebr::retire(const_cast<T*>(p)); }

// Publish `desired` only if the slot still holds `expected` (nullptr: only if empty). True iff THIS call replaced it;
// the replaced value is retired. On false, `expected` is what the slot holds now and `desired` is still the caller's.
template <class T, class Retire = void (*)(const T*)>
inline bool cas(std::atomic<const T*>& slot, const T*& expected, const T* desired, Retire retire = retire_default<T>) {
    if (!slot.compare_exchange_strong(expected, desired, std::memory_order_acq_rel, std::memory_order_acquire)) return false;
    retire(expected);
    return true;
}
// Publish `desired` whatever is there; hands back what it replaced (valid inside the caller's epoch; NOT retired --
// the caller reads it, then retires it).
template <class T>
inline const T* swap(std::atomic<const T*>& slot, const T* desired) { return slot.exchange(desired, std::memory_order_acq_rel); }
// Remove the value; exactly one caller gets it (the others get nullptr). Not retired: the caller owns what it took.
template <class T>
inline const T* take(std::atomic<const T*>& slot) { return slot.exchange(nullptr, std::memory_order_acq_rel); }
// Decide-and-replace: `decide(cur)` is shown the live value (nullptr if empty) and returns a new value to put there,
// or nullptr to leave it. The new value lands only on the value it was decided from; if another writer changed the
// slot first, the new value is dropped (`drop`) and `decide` is shown the new live value. Every writer of the slot is
// serialised by it, with no lock. Returns the value this call published, or nullptr if `decide` left it.
template <class T, class Decide, class Drop = void (*)(const T*), class Retire = void (*)(const T*)>
inline const T* update(std::atomic<const T*>& slot, Decide decide,
                       Drop drop = [](const T* p) { delete p; }, Retire retire = retire_default<T>) {
    const T* cur = slot.load(std::memory_order_acquire);
    for (;;) {
        const T* next = decide(cur);
        if (!next) return nullptr;
        if (cas(slot, cur, next, retire)) return next;
        drop(next);                                   // cur is now what another writer put there: decide again
    }
}

}}  // namespace ribbit::lf
