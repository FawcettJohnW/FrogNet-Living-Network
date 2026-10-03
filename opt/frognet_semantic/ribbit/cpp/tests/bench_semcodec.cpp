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
// S2 performance: ns per operation for the semantic codec, single thread, in memory. 9 batches per operation; prints
// median, min and max of the per-batch ns/op. tools/bench_semcodec_python.py times the same operations on core/codec.py.
#include "semcodec.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <functional>

using namespace semcodec;
static volatile uint64_t sink;

static void bench(const char* name, long n, const std::function<uint64_t()>& f) {
    std::vector<double> r;
    for (int b = 0; b < 9; ++b) {
        uint64_t acc = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (long i = 0; i < n; ++i) acc += f();
        auto t1 = std::chrono::steady_clock::now();
        sink = sink + acc;
        r.push_back(std::chrono::duration<double, std::nano>(t1 - t0).count() / double(n));
    }
    std::sort(r.begin(), r.end());
    std::printf("%-34s median %10.1f ns/op  min %10.1f  max %10.1f  (9 x %ld)\n", name, r[4], r[0], r[8], n);
}

int main() {
    // five dynamic fields typical of a JSON API request: id, name, flag, score, tags
    Fields f5 = {{"id", Int::of(123456)}, {"name", Str{"alice@example.com"}}, {"active", true}, {"score", 98.25},
                 {"tags", List{Str{"a"}, Str{"b"}, Int::of(3)}}};
    Fields f5b = f5; f5b[0].second = Int::of(123457);
    Dict ref; for (auto& p : f5) ref.set(p.first, p.second);
    Dict big; for (int i = 0; i < 40; ++i) big.set("key" + std::to_string(i), Str{"value number " + std::to_string(i)});
    Fields fj = {{"doc", big}};
    std::string blob4k; for (int i = 0; i < 256; ++i) blob4k += "row " + std::to_string(i % 16) + " status=ok;";
    Fields fc = {{"body", Str{blob4k.substr(0, 4000)}}};
    std::vector<std::string> names = {"id", "name", "active", "score", "tags"}, dn = {"doc"}, cn = {"body"};
    std::string p5 = encode_reply(7, f5), pd = encode_reply_diff(7, f5b, &ref).bytes, pj = encode_reply(7, fj);
    std::string pc = encode_reply(7, fc);
    bench("encode_reply 5 fields", 300000, [&] { return encode_reply(7, f5).size(); });
    bench("encode_reply_diff 5, identical", 300000, [&] { return uint64_t(encode_reply_diff(7, f5, &ref).identical); });
    bench("encode_reply_diff 5, one changed", 300000, [&] { return encode_reply_diff(7, f5b, &ref).bytes.size(); });
    bench("encode_reply JSON dict 40 keys", 100000, [&] { return encode_reply(7, fj).size(); });
    bench("encode_reply 4000 B str, LZ4", 50000, [&] { return encode_reply(7, fc).size(); });
    bench("decode_reply 5 fields", 300000, [&] { return decode_reply(p5, names, nullptr).size(); });
    bench("decode_reply diff + reference", 300000, [&] { return decode_reply(pd, names, &ref).size(); });
    bench("decode_reply JSON dict 40 keys", 100000, [&] { return decode_reply(pj, dn, nullptr).size(); });
    bench("decode_reply 4000 B str, LZ4", 50000, [&] { return decode_reply(pc, cn, nullptr).size(); });
    std::printf("sizes: 5 fields %zu B, diff %zu B, JSON dict %zu B, 4000 B str compressed to %zu B\n",
                p5.size(), pd.size(), pj.size(), pc.size());
    return 0;
}
