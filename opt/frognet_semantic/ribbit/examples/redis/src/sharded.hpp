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
// A counter every thread bumps and nobody waits on: one cache line per thread slot, summed on read. The
// global atomics it replaces were the lines every core had to own in turn, once per command.
#pragma once
#include <atomic>
#include <cstdint>
namespace rr {

struct Sharded {
    static const int SLOTS = 64;
    struct alignas(64) Slot { std::atomic<int64_t> v{0}; };
    Slot s[SLOTS];
    static int idx() { static std::atomic<int> next{0}; static thread_local int me = -1; if (me < 0) me = next.fetch_add(1) % SLOTS; return me; }
    void add(int64_t d) { s[idx()].v.fetch_add(d, std::memory_order_relaxed); }
    int64_t load() const { int64_t t = 0; for (auto& x : s) t += x.v.load(std::memory_order_relaxed); return t; }
    void store(int64_t v) { for (auto& x : s) x.v.store(0, std::memory_order_relaxed); if (v) s[0].v.store(v, std::memory_order_relaxed); }
    Sharded& operator++() { add(1); return *this; } void operator++(int) { add(1); }
    Sharded& operator--() { add(-1); return *this; } void operator--(int) { add(-1); }
    Sharded& operator+=(int64_t d) { add(d); return *this; }
    Sharded& operator-=(int64_t d) { add(-d); return *this; }
    Sharded() = default; Sharded(const Sharded& o) { store(o.load()); }
    Sharded& operator=(const Sharded& o) { if (this != &o) store(o.load()); return *this; }
    Sharded& operator=(int64_t v) { store(v); return *this; }
    operator int64_t() const { return load(); }
};

}  // namespace rr
