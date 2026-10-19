// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// callbench.cpp -- what one region call costs, by itself: N sequential calls of each kind from one thread, and the
// same spread over T threads sharing one session. Measured against a running mqtt-ram.
//
//   callbench HOST PORT [N] [T]
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>
#include "ram_client.hpp"

class C : public ribbit::RamClient { public: std::string vendor_id() const override { return "mqtt"; } };
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "callbench HOST PORT [N] [T]\n"); return 2; }
    int n = argc > 3 ? atoi(argv[3]) : 5000, t = argc > 4 ? atoi(argv[4]) : 4;
    C c; c.connect(argv[1], atoi(argv[2]));
    int f = int(c.call("hello", "{}")["front"].n);
    auto run = [&](const char* what, auto body) {
        double t0 = now(); for (int i = 0; i < n; i++) body(i); double dt = now() - t0;
        printf("%-34s %8d calls  %8.1f us/call  %8.0f calls/s\n", what, n, dt * 1e6 / n, n / dt);
    };
    run("stats (no work)", [&](int) { c.call("stats", "{}"); });
    run("own (one map write)", [&](int i) { c.call("own", "{\"c\":\"cb" + std::to_string(i % 50) + "\",\"f\":" + std::to_string(f) + ",\"v\":4}"); });
    run("pub qos0, no subscribers", [&](int i) {
        c.call("pub", "{\"f\":" + std::to_string(f) + ",\"n\":" + std::to_string(i) + ",\"c\":\"cb\",\"t\":\"bench/x\",\"p\":\"aGVsbG8=\",\"q\":0,\"r\":0,\"pr\":\"\",\"e\":0,\"su\":null,\"sp\":0,\"o\":0}"); });
    c.call("sub", "{\"c\":\"cbs\",\"f\":" + std::to_string(f) + ",\"flt\":\"bench/y\",\"o\":1,\"sid\":0}");
    run("pub qos0, one own subscriber", [&](int i) {
        c.call("pub", "{\"f\":" + std::to_string(f) + ",\"n\":" + std::to_string(i) + ",\"c\":\"cb\",\"t\":\"bench/y\",\"p\":\"aGVsbG8=\",\"q\":0,\"r\":0,\"pr\":\"\",\"e\":0,\"su\":null,\"sp\":0,\"o\":0}"); });
    double t0 = now();
    std::vector<std::thread> th;
    for (int k = 0; k < t; k++) th.emplace_back([&, k] { for (int i = 0; i < n / t; i++)
        c.call("pub", "{\"f\":" + std::to_string(f) + ",\"n\":" + std::to_string(k * n + i) + ",\"c\":\"cb\",\"t\":\"bench/x\",\"p\":\"aGVsbG8=\",\"q\":0,\"r\":0,\"pr\":\"\",\"e\":0,\"su\":null,\"sp\":0,\"o\":0}"); });
    for (auto& x : th) x.join();
    double dt = now() - t0;
    printf("%-34s %8d calls  %8.1f us/call  %8.0f calls/s  (%d threads, one session)\n", "pub qos0 concurrent", n / t * t, dt * 1e6 / (n / t * t), (n / t * t) / dt, t);
    auto st = c.session().stats();
    printf("session: repeat %llu same %llu full %llu diff %llu raw %llu coalesced %llu bytes out %llu in %llu\n",
           (unsigned long long)st.repeat, (unsigned long long)st.same, (unsigned long long)st.full, (unsigned long long)st.diff,
           (unsigned long long)st.raw, (unsigned long long)st.coalesced, (unsigned long long)st.bytes_out, (unsigned long long)st.bytes_in);
    return 0;
}
