// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_segment < vectors -- the C++ rate meter and whole-frame ceiling against fnav.py's own _note_sent and
// whole_frame_max (tools/gen_segment.py), then the reassembler's every case.
#include "fnav/segment.hpp"
#include <cmath>
#include <iostream>
#include <sstream>

using namespace fnav;
static long fail = 0;
static void check(bool ok, const std::string& what) { if (!ok && ++fail <= 10) std::cout << "FAIL " << what << "\n"; }

int main() {
    RateMeter m; std::string line; long steps = 0;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::string t; double now, rate; long nb; size_t wmax;
        in >> t >> now >> nb >> rate >> wmax; ++steps;
        m.on_accepted(size_t(nb), now);
        check(std::fabs(m.bytes_per_s() - rate) <= 1e-6 * std::max(1.0, rate), "rate step " + std::to_string(steps) + ": " + std::to_string(m.bytes_per_s()) + " vs " + std::to_string(rate));
        check(whole_frame_max(m.bytes_per_s(), size_t(1) << 18) == wmax, "whole_frame_max step " + std::to_string(steps));
    }
    std::cout << "rate meter and ceiling vs fnav.py: " << steps << " steps, " << fail << " fail\n";

    // reassembler: every case, with the counts it must record
    auto seg = [](uint16_t f, uint16_t i, uint8_t fl, const std::string& s) { return pack_seg(f, i, fl, Bytes(s.begin(), s.end())); };
    Reassembler r; Bytes out;
    check(!r.feed("A", seg(1, 0, 0, "he"), out) && !r.feed("A", seg(1, 1, 0, "ll"), out) && r.feed("A", seg(1, 2, VSEG_LAST, "o"), out)
          && std::string(out.begin(), out.end()) == "hello", "in order completes");
    check(!r.feed("A", seg(2, 0, 0, "x"), out) && !r.feed("A", seg(2, 0, VSEG_ABORT, ""), out) && r.stats.aborted == 1, "abort lets go");
    check(!r.feed("A", seg(3, 1, VSEG_LAST, "y"), out) && r.stats.partial == 1, "frame without piece 0 refused");
    check(!r.feed("A", seg(4, 0, 0, "a"), out) && !r.feed("A", seg(4, 2, VSEG_LAST, "c"), out) && r.stats.gap == 1, "gap discards");
    check(!r.feed("A", seg(5, 0, 0, "a"), out) && !r.feed("A", seg(6, 0, 0, "b"), out) && r.stats.superseded == 1, "newer frame supersedes");
    check(r.feed("A", seg(6, 1, VSEG_LAST, "c"), out) && std::string(out.begin(), out.end()) == "bc", "newer frame completes");
    check(!r.feed("B", seg(7, 0, 0, "p"), out) && r.feed("A", seg(8, 0, VSEG_LAST, "q"), out) && r.feed("B", seg(7, 1, VSEG_LAST, "r"), out)
          && std::string(out.begin(), out.end()) == "pr", "sources are independent");
    check(!r.feed("A", Bytes{1, 2}, out) && r.stats.bad == 1, "short payload counted as bad");
    check(r.stats.done == 4, "4 frames completed, got " + std::to_string(r.stats.done));
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " segmentation: " << fail << " fail\n";
    return fail ? 1 : 0;
}
