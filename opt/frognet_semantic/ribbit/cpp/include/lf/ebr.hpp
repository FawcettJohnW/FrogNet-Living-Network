// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lf/ebr.hpp -- [LF_PLATFORM_V1] the Redis region's epoch reclamation, moved into the platform (qualified there on x86
// and on the Pi 5's ARM memory model) so every region builds its lock-free memory from one platform.
// Epoch-based reclamation. No locks: a thread declares itself inside an epoch while it works with raw
// pointers into the memory; a writer that unlinks something retires it, and it is freed only once every
// thread that was inside an older epoch has left. A participant parked in a held read steps out of its
// epoch while it waits, so a long wait never holds anyone's garbage.
//
// [NOBODY_IS_ASKED_V1] (John, 2026-11-25: "Why is it walking the threads. That is messaging style." "There should be no
// walks. No acks, no copying things around.") This kept a record for each thread in a fixed array, and to move the
// epoch read every record up to the highest ever used to ask what epoch its thread was in; each thread kept its own
// retired lists and handed them to whoever came next when it ended. Now a thread counts itself into the epoch it
// enters and out of it when it leaves: there is one count per epoch (three, used in turn), no record of a thread,
// nothing to walk, and one retired list per epoch that nobody hands to anybody.
//   entering   read the epoch, count into it, read the epoch again; not the same: count out and enter again.
//   retiring   done while entered: onto the list of the epoch as it is then.
//   the epoch moves from e to e+1 when nobody is counted into e-1. Whoever moves it is itself counted into e, so it
//              cannot move again under them, and frees the list of e-1: what is on it was unlinked before epoch e
//              began, and nobody who entered before then is still in.
//   who moves it: whoever notices -- every 64th retire of a thread, the last out of an epoch that has ended, collect().
#pragma once
#include <atomic>
#include <vector>
#include <cstdint>
#include <functional>
namespace ribbit { namespace lf {

namespace ebr {

struct Retired { void* p; void (*del)(void*); Retired* next; };

struct alignas(64) Of {                 // one epoch in three: who is in it, what was retired in it
    std::atomic<int64_t> in{0};
    std::atomic<Retired*> retired{nullptr};
};
struct Global {
    std::atomic<uint64_t> epoch{3};
    Of of[3];
    std::atomic<int> high{0};              // no thread has a record any more; kept, at 0, for what reports it
};
Global& global();

// enter/exit a critical section; nested calls are balanced by depth
void enter();
void exit();
struct Guard { Guard() { enter(); } ~Guard() { exit(); } };
// retire something unlinked from all shared structures; freed when safe
void retire(void* p, void (*del)(void*));
template <class T> void retire(T* p) { retire((void*)p, [](void* q) { delete (T*)q; }); }
// try to advance the epoch and free what can be freed (called by retire, may be called explicitly)
void collect();
void on_thread_exit();                      // nothing is kept for a thread: nothing to do. Kept for its callers.
// what reclamation has done: retired and not yet freed, freed, collects tried, epochs advanced
struct Stats { std::atomic<long> pending{0}, freed{0}, collects{0}, advances{0}; };
Stats& stats();

}

} }  // namespace ribbit::lf
