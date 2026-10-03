#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""[ROW_SCAN_BOUND_V1] -- the same fix as ribbit/cpp/include/ram_memory.hpp, applied to this example's copy of the
platform (platform/ribbit-v0.63), which carried the same defect.

A held read whose "after" is further back than the write window walks the rows one at a time while writers keep going.
It could report row Z's new write (id 160) but not row Y's (id 150), which landed on Y after the walk had passed it;
the reader then follows "after" = 160 and never sees 150 (Raspberry Pi 5: "racing reader missed the final write of 13
rows", about 2 runs in 20). The bound: before the walk, take the window's head h0 and the id of the write at h0-1;
report only rows up to that id. Every write below h0 is seen by the walk (its writer held the row's lock when h0 was
read); every later write has a larger id; nothing is left behind a reader's "after".

Usage: apply_row_scan_bound.py <ribbit_cpp dir>   (edits ram_memory.hpp in place; refuses on any anchor that is not
found exactly once)"""
import sys, os
d = sys.argv[1]
PAIRS = [('#include <atomic>\n#include <chrono>\n', '#include <atomic>\n#include <thread>\n#include <chrono>\n'), ("    }\n    // ---- read: the query's rows (copied under each row's shared lock), id order\n", '    }\n    // [ROW_SCAN_BOUND_V1] A held read that falls back to the rows walks them one at a time, each under its own lock, while\n    // writers keep writing. Without a bound it could return row Z\'s new write (id 160) but not row Y\'s (id 150), which\n    // landed on Y after the walk had passed it; the reader then follows "after" = 160 and never sees 150 (Raspberry Pi,\n    // test-write-window: "racing reader missed the final write of 13 rows"). The bound: before the walk, take the\n    // window\'s head h0 and the id of the write at position h0-1. Every write at a position below h0 is seen by the walk\n    // -- its writer already held the row\'s lock when h0 was read, so the walk waits for it -- and every write at h0 or\n    // later has a larger id (ids rise with position). Reporting only ids up to that one leaves nothing behind a\n    // reader\'s "after": a row rewritten mid-walk is reported by the next read.\n    static uint64_t row_scan_bound(WriteWindow* wnd) {\n        for (;;) {\n            const uint64_t h0 = wnd->head.load(std::memory_order_acquire);\n            if (h0 == 0) return 0;\n            WindowSlot& sl = wnd->slot[(h0 - 1) % WINDOW];\n            for (;;) {\n                if (sl.pos.load(std::memory_order_acquire) == h0 - 1) {\n                    const uint64_t id = sl.id.load(std::memory_order_acquire);\n                    if (sl.pos.load(std::memory_order_acquire) == h0 - 1) return id;\n                }\n                if (wnd->head.load(std::memory_order_acquire) > h0 - 1 + WINDOW) break;   // lapped: take a new h0\n                std::this_thread::yield();               // its writer is between claiming h0-1 and publishing it\n            }\n        }\n    }\n    // ---- read: the query\'s rows (copied under each row\'s shared lock), id order\n'), ('        std::vector<Cell> out; const double cutoff = now - fresh;\n        auto take = [&](Row* r) {\n', '        std::vector<Cell> out; const double cutoff = now - fresh;\n        uint64_t scan_lim = 0; bool bounded = false;    // [ROW_SCAN_BOUND_V1] set when a held read falls back to the rows\n        auto take = [&](Row* r) {\n'), ('            }\n            // the window does not reach back to `after`: the rows themselves (below) answer\n        }\n', '            }\n            // the window does not reach back to `after`: the rows themselves (below) answer -- bounded by row_scan_bound\n            scan_lim = row_scan_bound(wnd); bounded = true;\n        }\n'), ('            for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service) each_row(var);\n        std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });\n', '            for (Variable* var = sv->vars.load(std::memory_order_acquire); var; var = var->next_in_service) each_row(var);\n        if (bounded) out.erase(std::remove_if(out.begin(), out.end(), [&](const Cell& c) { return c.id > scan_lim; }), out.end());\n        std::sort(out.begin(), out.end(), [](const Cell& a, const Cell& b) { return a.id < b.id; });\n')]
path = os.path.join(d, "ram_memory.hpp")
s = open(path).read()
for old, new in PAIRS:
    n = s.count(old)
    if n != 1: sys.exit("apply_row_scan_bound: ram_memory.hpp: anchor found %d times: %r" % (n, old[:70]))
    s = s.replace(old, new)
open(path, "w").write(s)
