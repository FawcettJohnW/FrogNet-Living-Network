// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only
//
// frogtastic_shm.h -- [FROGTASTIC_SHM_TRANSPORT_V1] the local transport between the Meshtastic firmware and
// FrogTastic-Bridge, the bridge that is on the memory for it.
//
// Two processes, one shared segment. The firmware (GPL-3.0, with Meshtastic's code) calls the mt_ribbit.h functions;
// its shim (front/src/mesh/ribbit/frogtastic_shim.cpp) writes each call into the segment as a record. FrogTastic-Bridge
// (GPL-2.0-only, with the Ribbit platform) takes the record, makes the same call into mt_ribbit.cpp, and writes back what
// it returns. Nothing is kept in the segment: it is a transport, not a copy of the memory. The held reads, the marks and
// the fact queue are where they were, in the RAM and in mt_ribbit.cpp.
//
// Three rings, each one writer process and one reader process:
//   calls    firmware -> bridge   every mt_ribbit.h call, in the order the firmware made them
//   events   bridge -> firmware   what the held reads hand over: packets (mtr_on_packet), fact wakes
//   replies  bridge -> firmware   the answer to a call that returns something, as items then END
// A waiting reader sleeps on a futex word in the segment (no polling); a writer that finds its ring full sleeps on the
// reader's word. Records are length-prefixed byte strings; no structure of either side crosses.
//
// This header is used by both sides, so it is licensed to both: GPL-2.0-only or GPL-3.0-only, at the user's choice.
#pragma once

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <string>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace frogtastic {

constexpr uint32_t MAGIC = 0x464f5254;     // "TROF"
constexpr uint32_t LAYOUT = 1;            // bumped on any change to what follows; the two sides must agree
constexpr uint64_t RING_BYTES = 1u << 20;  // per ring
constexpr uint32_t MAX_RECORD = 64 * 1024; // one record's payload, at most
constexpr const char *DEFAULT_NAME = "/frogtastic-bridge";
constexpr const char *NAME_ENV = "FROGTASTIC_SHM";

// calls (firmware -> bridge); those marked R are answered on replies
enum Op : uint16_t {
    START = 1,        // R  str where, u32 me                      END ret, str bridge version, str front version
    SEND,             //    bytes packet, u32 to, u8 channel
    HOLD_CHANNEL,     //    u8 hash
    HOLD_DM_FOR,      //    u32 node
    MARK,             //    str family, u64 row
    MARK_READ,        // R  str family                             END ret = row
    READ_PAST_MARK,   // R  str family                             PACKET items, END ret = count
    FAMILIES,         // R                                         FAMILY items, END ret = count
    WRITE_FACT,       //    str topic, u32 node, bytes payload
    WRITE_FACT_FOR,   //    str topic, u32 by, u32 subject, bytes payload
    WRITE_FACT_KEPT,  //    str topic, u32 node, bytes payload
    CLEAR_FACT,       //    str topic, u32 node
    HOLD_FACTS,       // R                                         END ret
    TAKE_FACTS,       // R                                         FACT items, END ret = count
    READ_FACTS,       // R  str topic                              CELL items, END ret = count
    READ_CELL,        // R  str topic, u32 node, u64 cap           END ret, bytes payload, u32 t
    // events and reply items (bridge -> firmware)
    EV_PACKET = 100,  //    bytes packet, u64 row, str family      (events: the held read; replies: READ_PAST_MARK)
    EV_FACTS_WAKE,    //    (nothing)
    IT_FAMILY,        //    str family
    IT_FACT,          //    str topic, u32 node, bytes payload, u8 gone, u32 t, u8 live
    IT_CELL,          //    str topic, u32 node, bytes payload, u32 t
    END,              //    i64 ret, then what the call's line above says
};

struct Header {
    uint32_t len; // payload bytes after this header
    uint16_t op;
    uint16_t pad;
    uint64_t id;  // the call's id; its items and END carry it
};

struct alignas(64) Ring {
    std::atomic<uint64_t> head;  // bytes written; only the writer moves it
    std::atomic<uint64_t> tail;  // bytes read; only the reader moves it
    std::atomic<uint32_t> wrote; // futex word: the writer bumps it after each record
    std::atomic<uint32_t> took;  // futex word: the reader bumps it after each record
    uint8_t pad[40];
    uint8_t data[RING_BYTES];
};

struct Segment {
    uint32_t magic;
    uint32_t layout;
    std::atomic<int32_t> bridge_pid;   // the bridge process; 0 until it is serving
    std::atomic<int32_t> firmware_pid; // the firmware attached to it; 0 when none has
    uint32_t pad[12];
    Ring calls;
    Ring events;
    Ring replies;
};

static_assert(std::atomic<uint64_t>::is_always_lock_free, "the rings need lock-free 64-bit atomics");
static_assert(std::atomic<uint32_t>::is_always_lock_free, "the futex words need lock-free 32-bit atomics");

// futex on a word in shared memory (not FUTEX_PRIVATE: the other process waits on it too)
inline void futex_wait_ms(std::atomic<uint32_t> &w, uint32_t seen, long ms)
{
    timespec ts{ms / 1000, (ms % 1000) * 1000000L};
    syscall(SYS_futex, reinterpret_cast<uint32_t *>(&w), FUTEX_WAIT, seen, &ts, nullptr, 0);
}
inline void futex_wake_all(std::atomic<uint32_t> &w)
{
    syscall(SYS_futex, reinterpret_cast<uint32_t *>(&w), FUTEX_WAKE, INT32_MAX, nullptr, nullptr, 0);
}

inline void ring_copy_in(Ring &r, uint64_t at, const void *src, size_t n)
{
    const uint8_t *s = static_cast<const uint8_t *>(src);
    for (size_t i = 0; i < n; i++) r.data[(at + i) % RING_BYTES] = s[i];
}
inline void ring_copy_out(const Ring &r, uint64_t at, void *dst, size_t n)
{
    uint8_t *d = static_cast<uint8_t *>(dst);
    for (size_t i = 0; i < n; i++) d[i] = r.data[(at + i) % RING_BYTES];
}

// Write one record. The caller is the ring's only writer (in-process callers hold one mutex around it). Waits while
// the ring is full; alive() is asked once a second while waiting and false gives up. Returns false when given up or
// the record is too long.
template <class Alive> bool put(Ring &r, uint16_t op, uint64_t id, const std::string &payload, Alive alive)
{
    if (payload.size() > MAX_RECORD) return false;
    Header h{uint32_t(payload.size()), op, 0, id};
    const uint64_t need = sizeof h + payload.size();
    for (;;) {
        uint32_t seen = r.took.load(std::memory_order_acquire);
        uint64_t head = r.head.load(std::memory_order_relaxed);
        if (head + need - r.tail.load(std::memory_order_acquire) <= RING_BYTES) {
            ring_copy_in(r, head, &h, sizeof h);
            ring_copy_in(r, head + sizeof h, payload.data(), payload.size());
            r.head.store(head + need, std::memory_order_release);
            r.wrote.fetch_add(1, std::memory_order_release);
            futex_wake_all(r.wrote);
            return true;
        }
        futex_wait_ms(r.took, seen, 1000);
        if (!alive()) return false;
    }
}

// Read one record, waiting for one. alive() as for put. Returns false when given up.
template <class Alive> bool take(Ring &r, Header &h, std::string &payload, Alive alive)
{
    for (;;) {
        uint32_t seen = r.wrote.load(std::memory_order_acquire);
        uint64_t tail = r.tail.load(std::memory_order_relaxed);
        if (r.head.load(std::memory_order_acquire) != tail) {
            ring_copy_out(r, tail, &h, sizeof h);
            payload.resize(h.len);
            ring_copy_out(r, tail + sizeof h, &payload[0], h.len);
            r.tail.store(tail + sizeof h + h.len, std::memory_order_release);
            r.took.fetch_add(1, std::memory_order_release);
            futex_wake_all(r.took);
            return true;
        }
        futex_wait_ms(r.wrote, seen, 1000);
        if (!alive()) return false;
    }
}

// Drop what a ring holds (the reader starting over: what was written for an earlier reader is not for it).
inline uint64_t drop_all(Ring &r)
{
    uint64_t head = r.head.load(std::memory_order_acquire), tail = r.tail.load(std::memory_order_relaxed);
    r.tail.store(head, std::memory_order_release);
    r.took.fetch_add(1, std::memory_order_release);
    futex_wake_all(r.took);
    return head - tail;
}

// The record payload: little-endian numbers, u32-length-prefixed strings and byte strings.
struct Out {
    std::string b;
    Out &u8(uint8_t v) { b.push_back(char(v)); return *this; }
    Out &u32(uint32_t v) { b.append(reinterpret_cast<const char *>(&v), 4); return *this; }
    Out &u64(uint64_t v) { b.append(reinterpret_cast<const char *>(&v), 8); return *this; }
    Out &i64(int64_t v) { return u64(uint64_t(v)); }
    Out &bytes(const void *p, size_t n) { u32(uint32_t(n)); b.append(static_cast<const char *>(p), n); return *this; }
    Out &str(const char *s) { return bytes(s ? s : "", s ? strlen(s) : 0); }
    Out &str(const std::string &s) { return bytes(s.data(), s.size()); }
};
struct In {
    const std::string &b;
    size_t at = 0;
    bool ok = true;
    explicit In(const std::string &s) : b(s) {}
    bool have(size_t n) { if (at + n > b.size()) ok = false; return ok; }
    uint8_t u8() { if (!have(1)) return 0; return uint8_t(b[at++]); }
    uint32_t u32() { uint32_t v = 0; if (have(4)) { memcpy(&v, b.data() + at, 4); at += 4; } return v; }
    uint64_t u64() { uint64_t v = 0; if (have(8)) { memcpy(&v, b.data() + at, 8); at += 8; } return v; }
    int64_t i64() { return int64_t(u64()); }
    std::string bytes() { uint32_t n = u32(); if (!have(n)) return {}; std::string s = b.substr(at, n); at += n; return s; }
};

} // namespace frogtastic
