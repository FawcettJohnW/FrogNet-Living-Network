// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_bearer < vectors -- the C++ bearer against fnav.py's own Bearer.sample (tools/gen_bearer.py): the same rung
// after every sample, through clean running, drops, bursts, slow frame rates, audio sheds and backlogs.
#include "fnav/bearer.hpp"
#include <iostream>
#include <sstream>
#include <string>

int main() {
    std::string w; int ceiling, floor; double t0;
    std::cin >> w >> ceiling >> floor >> t0;
    fnav::Bearer b(ceiling, floor, t0);
    std::string t, fs, ft; int backlog, dropped, ashed, want; long n = 0, fail = 0, moves = 0; int last = ceiling;
    while (std::cin >> t >> backlog >> dropped >> fs >> ft >> ashed >> want) {
        ++n;
        std::optional<double> fps, tgt;
        if (fs != "-") fps = std::stod(fs);
        if (ft != "-") tgt = std::stod(ft);
        int got = b.sample(std::stod(t), backlog, dropped, fps, tgt, ashed);
        if (got != last) { ++moves; last = got; }
        if (got != want && ++fail <= 10) std::cout << "FAIL sample " << n << ": C++ L" << got << ", fnav L" << want << "\n";
    }
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " bearer vs fnav.py: " << n << " samples (ceiling L" << ceiling
              << ", floor L" << floor << "), " << moves << " rung changes, " << fail << " fail\n";
    return fail ? 1 : 0;
}
