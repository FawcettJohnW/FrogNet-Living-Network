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
// [READS_AT_THE_FRONT_V1] the host's half: holders, the change counter, the feeds (coherence.hpp says why).
// Nothing here takes a lock: the holder index is the memory's own lock-free map, a feed is a lock-free stack drained
// whole by its one reader, and a reader with nothing to read sleeps on a futex word.
#include "coherence.hpp"
#include "memory.hpp"
#include <atomic>
#include <chrono>
#include <unordered_set>
#include <algorithm>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <ctime>
namespace rr::coh {
namespace {
std::atomic<uint64_t> seq{0};
// [HOLDERS_ARE_A_FILTER_V1] which fronts have read a key: one 64-bit mask per slot of a fixed array, the slot chosen by a
// hash of (db, key). Two keys in one slot share a mask: a front then hears of changes to a key it never read, and
// ignores them. A superset is all correctness needs; no allocation, no index walk, one cache line per read and write.
constexpr size_t HOLD = size_t(1) << 20;
std::atomic<uint64_t>* holders = [] { auto* h = new std::atomic<uint64_t>[HOLD]; for (size_t i = 0; i < HOLD; i++) h[i].store(0, std::memory_order_relaxed); return h; }();
std::atomic<uint64_t>& holder(int db, const std::string& key) { return holders[(std::hash<std::string>{}(key) ^ (size_t(db) * 0x9E3779B97F4A7C15ull)) & (HOLD - 1)]; }

struct Node { Inval v; Node* next = nullptr; };
struct Feed {
    std::atomic<bool> used{false};
    std::atomic<Node*> head{nullptr};
    std::atomic<uint32_t> word{0}, waiters{0};
    std::atomic<int64_t> polled_ms{0};
};
Feed feeds[FEEDS];
constexpr int64_t DEAD_MS = 10000;                            // a front that has not read its feed this long is gone

int64_t now_ms() { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
void drain_free(Feed& f) { Node* n = f.head.exchange(nullptr, std::memory_order_acq_rel); while (n) { Node* x = n->next; delete n; n = x; } }
void push(int i, const Inval& v) {
    Feed& f = feeds[i];
    if (!f.used.load(std::memory_order_acquire)) return;
    if (now_ms() - f.polled_ms.load(std::memory_order_relaxed) > DEAD_MS) {     // its front is gone: so is its feed
        bool t = true; if (f.used.compare_exchange_strong(t, false)) drain_free(f);
        return;
    }
    Node* n = new Node{v, nullptr};
    Node* h = f.head.load(std::memory_order_acquire);
    do { n->next = h; } while (!f.head.compare_exchange_weak(h, n, std::memory_order_acq_rel, std::memory_order_acquire));
    std::atomic_thread_fence(std::memory_order_seq_cst);
    if (f.waiters.load(std::memory_order_seq_cst)) {
        f.word.fetch_add(1, std::memory_order_release);
        syscall(SYS_futex, reinterpret_cast<uint32_t*>(&f.word), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
    }
}
thread_local int tl_front = -1;
thread_local std::vector<Inval>* tl_mine = nullptr;

void on_change(Region& R, const std::string* var) {
    if (R.dbi < 0) return;                                    // the claims and batches regions: nobody reads them
    std::atomic_thread_fence(std::memory_order_seq_cst);      // the change is visible before the holders are read
    if (!var) {                                               // the whole db: every live front drops it
        Inval v; v.seq = seq.fetch_add(1, std::memory_order_seq_cst) + 1; v.db = R.dbi; v.flush = true;
        for (int i = 0; i < FEEDS; i++) push(i, v);
        if (tl_front >= 0 && tl_mine) tl_mine->push_back(v);
        return;
    }
    uint64_t m = holder(R.dbi, *var).load(std::memory_order_seq_cst);
    if (!m) return;                                           // no front ever read it
    Inval v; v.seq = seq.fetch_add(1, std::memory_order_seq_cst) + 1; v.db = R.dbi; v.key = *var;
    for (int i = 0; i < FEEDS; i++) if (m >> i & 1) push(i, v);
    if (tl_front >= 0 && tl_mine && (m >> tl_front & 1)) tl_mine->push_back(v);
}
}  // namespace

void install() { Region::on_change = &on_change; }

uint64_t register_read(int front, int db, const std::string& key) {
    holder(db, key).fetch_or(uint64_t(1) << front, std::memory_order_seq_cst);
    std::atomic_thread_fence(std::memory_order_seq_cst);
    return seq.load(std::memory_order_seq_cst);
}
void begin_item(int front, std::vector<Inval>* mine) { tl_front = front; tl_mine = mine; }
void end_item() { tl_front = -1; tl_mine = nullptr; }

int feed_open() {
    for (int pass = 0; pass < 2; pass++)
        for (int i = 0; i < FEEDS; i++) {
            Feed& f = feeds[i];
            bool used = f.used.load(std::memory_order_acquire);
            if (used && (pass == 0 || now_ms() - f.polled_ms.load() <= DEAD_MS)) continue;
            bool e = used;                                    // free, or (second pass) held by a front that is gone
            f.polled_ms.store(now_ms());
            if (!f.used.compare_exchange_strong(e, true)) continue;
            drain_free(f);                                    // a new front holds nothing: what was queued is not its
            return i;
        }
    return -1;
}
void feed_take(int i, std::vector<Inval>& out, int wait_ms) {
    Feed& f = feeds[i];
    f.polled_ms.store(now_ms(), std::memory_order_relaxed);
    Node* n = f.head.exchange(nullptr, std::memory_order_acq_rel);
    if (!n && wait_ms > 0) {
        f.waiters.fetch_add(1, std::memory_order_seq_cst);
        uint32_t seen = f.word.load(std::memory_order_seq_cst);
        n = f.head.exchange(nullptr, std::memory_order_acq_rel);
        if (!n) {
            struct timespec ts{wait_ms / 1000, (wait_ms % 1000) * 1000000L};
            syscall(SYS_futex, reinterpret_cast<uint32_t*>(&f.word), FUTEX_WAIT_PRIVATE, seen, &ts, nullptr, 0);
            n = f.head.exchange(nullptr, std::memory_order_acq_rel);
        }
        f.waiters.fetch_sub(1, std::memory_order_seq_cst);
        f.polled_ms.store(now_ms(), std::memory_order_relaxed);
    }
    size_t at = out.size();
    while (n) { Node* x = n->next; out.push_back(std::move(n->v)); delete n; n = x; }
    std::reverse(out.begin() + (long)at, out.end());          // the stack is newest first
}
bool cacheable(const std::string& l, size_t argc) {
    static const std::unordered_set<std::string> reads = {
        "get", "strlen", "getrange", "substr", "type", "getbit", "bitcount", "bitpos",
        "hget", "hmget", "hgetall", "hlen", "hexists", "hkeys", "hvals", "hstrlen",
        "lrange", "llen", "lindex", "lpos",
        "scard", "sismember", "smismember", "smembers",
        "zscore", "zmscore", "zcard", "zrange", "zrevrange", "zrangebyscore", "zrevrangebyscore", "zrangebylex",
        "zrevrangebylex", "zrank", "zrevrank", "zcount", "zlexcount",
        "xlen", "xrange", "xrevrange"};
    if (argc < 2) return false;
    if (l == "exists") return argc == 2;                      // EXISTS of one key; more keys are more keys
    return reads.count(l) != 0;
}
}  // namespace rr::coh
