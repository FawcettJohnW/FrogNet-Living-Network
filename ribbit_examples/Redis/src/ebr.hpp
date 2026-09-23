// Epoch-based reclamation. No locks: a thread declares itself inside an epoch while it works with raw
// pointers into the memory; a writer that unlinks something retires it, and it is freed only once every
// thread that was inside an older epoch has left. A participant parked in a held read steps out of its
// epoch while it waits, so a long wait never holds anyone's garbage.
#pragma once
#include <atomic>
#include <vector>
#include <cstdint>
#include <functional>

namespace ebr {

static const int MAX_THREADS = 8192;
struct Retired { void* p; void (*del)(void*); };

struct alignas(64) ThreadRec {      // one line per thread: an epoch store must not dirty a neighbour
    std::atomic<uint64_t> epoch{0};        // 0 = inactive; else the epoch it entered (global epoch + 1, so never 0)
    std::atomic<bool> used{false};
    std::vector<Retired> lists[3];         // retired in epoch % 3
    uint64_t counter = 0;
    uint64_t list_epoch[3] = {0, 0, 0};    // the epoch each slot's contents were retired in
};

struct Global {
    std::atomic<uint64_t> epoch{1};
    ThreadRec threads[MAX_THREADS];
    std::atomic<int> high{0};              // highest slot ever used + 1
    // orphaned retire lists from exited threads: a lock-free stack
    struct Orphan { std::vector<Retired> v[3]; std::atomic<Orphan*> next{nullptr}; };
    std::atomic<Orphan*> orphans{nullptr};
};
Global& global();
ThreadRec& me();                            // this thread's record (allocated on first use)

// enter/exit a critical section; nested calls are balanced by depth
void enter();
void exit();
struct Guard { Guard() { enter(); } ~Guard() { exit(); } };
// retire something unlinked from all shared structures; freed when safe
void retire(void* p, void (*del)(void*));
template <class T> void retire(T* p) { retire((void*)p, [](void* q) { delete (T*)q; }); }
// try to advance the epoch and free what can be freed (called by retire, may be called explicitly)
void collect();
void on_thread_exit();

}
