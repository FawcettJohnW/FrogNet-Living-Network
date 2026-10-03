// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_jitter -- [JITTER_IS_THE_LINK_V1]: the jitter setting also delays what a participant RECEIVES. With 300 ms of
// jitter the arrival intervals spread widely, but the delay does not accumulate: as many frames arrive per second as
// with none (a delay that added up would fall further and further behind).
#include "comms/call.hpp"
#include "media/server.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <thread>
#include <vector>

using namespace comms;
using Clock = std::chrono::steady_clock;

struct Run { double spread_ms; uint64_t video; uint64_t audio; };

static Run measure(Call& viewer, int seconds) {
    std::vector<double> gaps;
    uint64_t v0 = viewer.stats.v_recv, a0 = viewer.stats.a_recv, last = v0;
    auto t0 = Clock::now(), lt = t0;
    while (Clock::now() - t0 < std::chrono::seconds(seconds)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
        uint64_t v = viewer.stats.v_recv;
        if (v != last) {
            auto now = Clock::now();
            gaps.push_back(std::chrono::duration<double, std::milli>(now - lt).count());
            lt = now; last = v;
        }
    }
    double mean = 0; for (double g : gaps) mean += g; mean /= std::max<size_t>(1, gaps.size());
    double var = 0; for (double g : gaps) var += (g - mean) * (g - mean); var /= std::max<size_t>(1, gaps.size());
    return {std::sqrt(var), viewer.stats.v_recv - v0, viewer.stats.a_recv - a0};
}

int main() {
    int fail = 0;
    const int PORT = 19060;
    media::Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    Call cam("127.0.0.1", PORT, "Cam", "S1", std::make_unique<SyntheticSource>(320, 240), 24, std::make_unique<ToneSource>(440.0), nullptr);
    Call viewer("127.0.0.1", PORT, "John", "S1", nullptr);
    cam.start(); viewer.start();
    std::this_thread::sleep_for(std::chrono::seconds(3));
    Run calm = measure(viewer, 6);
    viewer.set_jitter(300);
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    Run rough = measure(viewer, 6);
    std::cout << "no jitter:  arrival spread " << calm.spread_ms << " ms; " << calm.video << " video, " << calm.audio << " audio frames in 6 s\n";
    std::cout << "300 ms:     arrival spread " << rough.spread_ms << " ms; " << rough.video << " video, " << rough.audio << " audio frames in 6 s\n";
    if (rough.spread_ms < 3 * calm.spread_ms + 10) { ++fail; std::cout << "FAIL the jitter did not reach what was received\n"; }
    if (rough.video < calm.video * 85 / 100 || rough.audio < calm.audio * 85 / 100) { ++fail; std::cout << "FAIL the delay accumulated (fewer frames arrived)\n"; }
    cam.stop(); viewer.stop(); srv.stop(); th.join();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " jitter on the receive side: " << fail << " fail\n";
    return fail;
}
