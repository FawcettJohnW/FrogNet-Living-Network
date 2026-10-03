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
// bench_semtpl: S3 C++ learn / extract / rebuild on one reply, N rounds; median / p90 microseconds. Input: argv[1] = file
// holding the reply body. Pair with tools/bench_semtpl.py (the same body through John's Python) on the same machine.
#include "semtpl.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <sstream>
int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: bench-semtpl BODYFILE N\n"); return 2; }
    std::ifstream f(argv[1]); std::stringstream ss; ss << f.rdbuf(); std::string body = ss.str(); int n = std::atoi(argv[2]);
    auto now = [] { return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now().time_since_epoch()).count(); };
    std::vector<double> tl, te, tr; semtpl::Dict frag; semcodec::Fields fields;
    for (int i = 0; i < n; ++i) {
        double a = now(); frag = semtpl::learn("json", body, true); double b = now();
        fields = *semtpl::extract(frag, body, true); double c = now();
        semtpl::List vals; for (auto& kv : fields) vals.push_back(kv.second);
        std::string out = semtpl::rebuild_reply(frag, vals); double d = now();
        tl.push_back(b - a); te.push_back(c - b); tr.push_back(d - c);
        if (out.empty()) return 3;
    }
    auto q = [](std::vector<double> v, double p) { std::sort(v.begin(), v.end()); return v[size_t(p * double(v.size() - 1))]; };
    std::printf("c++    %zu B, %zu fields: learn median %.1f us p90 %.1f | extract %.1f / %.1f | rebuild %.1f / %.1f (N=%d)\n",
                body.size(), fields.size(), q(tl, .5), q(tl, .9), q(te, .5), q(te, .9), q(tr, .5), q(tr, .9), n);
}
