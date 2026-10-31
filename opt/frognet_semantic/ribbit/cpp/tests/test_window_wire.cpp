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
// test_window_wire.cpp -- [MOVING_WINDOW_REFERENCE_V1] [STRING_SPLICE_V1] [ORIGIN_ONCE_PER_SESSION_V1]
// A client engine and a server engine on one in-process wire: an MQTT-like writer fills a moving window of cells
// (msg.<box>/<n mod W>), interleaved with latest-value cells (retain/<topic>, set and cleared) and a second family.
// Every body the server's executor receives must be the body the client sent, field for field. Prints bytes per write.
#include "fnwp_engine.hpp"
#include <cstdio>
#include <random>
using namespace fnwp;
int main() {
    std::vector<std::string> got;
    ServerEngine srv(std::string(32, 'k'), "/mqtt-api", [&](const std::string&, const std::string&, const std::string& body) {
        got.push_back(body); return Answer{200, "{\"ok\":true,\"id\":" + std::to_string(got.size()) + "}"}; });
    uint64_t out = 0, in = 0;
    ClientEngine cli("127.0.0.1", "127.0.0.1", "/mqtt-api", [&](const std::string& f) { out += f.size(); std::string r = srv.handle(f); in += r.size(); return r; });
    std::mt19937 R(11); const int W = 64; std::vector<std::string> sent; int bad = 0;
    auto b64 = [&](int n) { static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; std::string s; for (int i = 0; i < n; i++) s += A[R() % 64]; return s; };
    uint64_t nA = 0, nB = 0; uint64_t steady_out = 0, steady_n = 0;
    for (int t = 0; t < 20000; t++) {
        const int k = R() % 10; std::string body;
        if (k < 7) {                                       // box A's window: a payload changing in its first characters
            ++nA; const std::string topic = "load/" + std::to_string(R() % 4), client = "lp" + std::to_string(R() % 4);
            body = "{\"service\":\"mqtt\",\"variable\":\"msg.boxA\",\"instance\":\"" + std::to_string(nA % W) + "\",\"bag\":{\"t\":\"" + topic +
                   "\",\"q\":\"" + b64(14) + "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eA==\",\"qos\":1,\"r\":0,\"pr\":\"\",\"e\":0,\"si\":\"" + client +
                   "\",\"sp\":40335,\"o\":0,\"b\":\"boxA\",\"n\":" + std::to_string(nA) + "}}";
        } else if (k < 8) {                                // box B's window, same template, another family
            ++nB;
            body = "{\"service\":\"mqtt\",\"variable\":\"msg.boxB\",\"instance\":\"" + std::to_string(nB % W) + "\",\"bag\":{\"t\":\"x/y\",\"q\":\"" + b64(20) +
                   "\",\"qos\":0,\"r\":0,\"pr\":\"\",\"e\":0,\"si\":\"c9\",\"sp\":1,\"o\":0,\"b\":\"boxB\",\"n\":" + std::to_string(nB) + "}}";
        } else {                                           // latest-value cells: retained set or cleared
            body = "{\"service\":\"mqtt\",\"variable\":\"retain\",\"instance\":\"r/" + std::to_string(R() % 3) + "\",\"bag\":{\"t\":\"r/1\",\"p\":\"" +
                   (R() % 2 ? std::string("") : b64(8)) + "\",\"live\":" + std::to_string(R() % 2) + "}}";
        }
        const uint64_t before = out;
        Answer a = cli.request("POST", "/mqtt-api?op=write", body);
        if (t > 2000 && k < 7) { steady_out += out - before; steady_n++; }
        sent.push_back(body);
        if (a.status != 200) { bad++; if (bad < 5) fprintf(stderr, "status %d at %d\n", a.status, t); }
    }
    if (got.size() != sent.size()) { printf("FAIL: %zu sent, %zu executed\n", sent.size(), got.size()); return 1; }
    for (size_t i = 0; i < sent.size(); i++) {
        auto x = semtpl::detail::json_try(sent[i]), y = semtpl::detail::json_try(got[i]);
        if (!x || !y || !semtpl::python_eq(*x, *y)) { if (bad < 5) fprintf(stderr, "differs at %zu:\n sent %s\n  got %s\n", i, sent[i].c_str(), got[i].c_str()); bad++; }
    }
    const auto st = cli.stats();
    printf("%s: %zu writes, %d wrong; window writes in steady state %.1f B each on the wire; all requests %.1f B out, %.1f B in; full=%llu diff=%llu raw=%llu miss=%llu\n",
           bad ? "FAIL" : "PASS", sent.size(), bad, double(steady_out) / double(steady_n), double(out) / sent.size(), double(in) / sent.size(),
           (unsigned long long)st.full, (unsigned long long)st.diff, (unsigned long long)st.raw, (unsigned long long)st.miss);
    return bad != 0;
}
