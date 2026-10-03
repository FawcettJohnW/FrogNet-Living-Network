// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_call_audio -- audio and video together through comms-media. Donna sends a 440 Hz tone, John 660 Hz. What each
// one's speaker played is measured at both frequencies: each must hear the other, not themselves.
#include "comms/call.hpp"
#include "media/server.hpp"
#include <cmath>
#include <iostream>
#include <thread>

using namespace comms;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 12) std::cout << "FAIL " << w << "\n"; }

static double goertzel(const Bytes& pcm, double hz) {      // power at hz over the recording
    size_t n = pcm.size() / 2; double k = 2 * std::cos(2 * M_PI * hz / AUDIO_RATE), s1 = 0, s2 = 0;
    for (size_t i = 0; i < n; ++i) {
        double x = int16_t(uint16_t(pcm[2 * i]) | uint16_t(pcm[2 * i + 1]) << 8);
        double s0 = x + k * s1 - s2; s2 = s1; s1 = s0;
    }
    return (s1 * s1 + s2 * s2 - k * s1 * s2) / double(n ? n : 1);
}

int main() {
    const int PORT = 19100;
    media::Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    Call donna("127.0.0.1", PORT, "Donna", "S1", std::make_unique<SyntheticSource>(640, 360), 24,
               std::make_unique<ToneSource>(440), std::make_unique<RecordingSink>());
    Call john("127.0.0.1", PORT, "John", "S1", std::make_unique<SyntheticSource>(640, 360), 24,
              std::make_unique<ToneSource>(660), std::make_unique<RecordingSink>());
    donna.start(); john.start();
    std::this_thread::sleep_for(std::chrono::seconds(5));
    auto* jr = static_cast<RecordingSink*>(john.speaker());
    auto* dr = static_cast<RecordingSink*>(donna.speaker());
    Bytes jp, dp;
    { std::lock_guard<std::mutex> g(jr->mu); jp = jr->data; }
    { std::lock_guard<std::mutex> g(dr->mu); dp = dr->data; }
    double j440 = goertzel(jp, 440), j660 = goertzel(jp, 660), d440 = goertzel(dp, 440), d660 = goertzel(dp, 660);
    check(j440 > 100 * j660, "John hears Donna's 440 Hz and not his own 660 Hz (ratio " + std::to_string(int(j440 / (j660 + 1))) + ")");
    check(d660 > 100 * d440, "Donna hears John's 660 Hz and not her own 440 Hz (ratio " + std::to_string(int(d660 / (d440 + 1))) + ")");
    check(john.stats.a_recv > 200 && donna.stats.a_recv > 200, "audio frames received: John " + std::to_string(john.stats.a_recv) + ", Donna " + std::to_string(donna.stats.a_recv));
    check(john.stats.a_refused == 0 && donna.stats.a_refused == 0, "no audio refused");
    check(john.stats.v_recv > 60 && donna.stats.v_recv > 60, "video flowing alongside: John " + std::to_string(john.stats.v_recv) + ", Donna " + std::to_string(donna.stats.v_recv));
    std::cout << "John's speaker: 440 Hz " << int(10 * std::log10(j440 + 1)) << " dB, 660 Hz " << int(10 * std::log10(j660 + 1)) << " dB; "
              << "Donna's speaker: 660 Hz " << int(10 * std::log10(d660 + 1)) << " dB, 440 Hz " << int(10 * std::log10(d440 + 1)) << " dB\n"
              << "audio received: John " << john.stats.a_recv << ", Donna " << donna.stats.a_recv << " (5 s = 250 blocks); "
              << "mixer pads " << john.mixer().pads << "/" << donna.mixer().pads << ", trims " << john.mixer().trims << "/" << donna.mixer().trims
              << "; video received " << john.stats.v_recv << "/" << donna.stats.v_recv << "\n";
    donna.stop(); john.stop(); srv.stop(); th.join();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " audio and video through the media server: " << fail << " fail\n";
    return fail ? 1 : 0;
}
