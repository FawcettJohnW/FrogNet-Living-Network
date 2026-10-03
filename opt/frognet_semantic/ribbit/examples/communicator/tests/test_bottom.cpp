// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_bottom -- [BOTTOM_RUNG_SHRINKS_V1]: under a narrow uplink the sender's picture gets SMALLER instead of
// disappearing ("video is not gone; it is smaller"), and when the wire opens again it grows back and climbs.
// Found 2026-10-01 on the Pi: at 128 kb/s the sender went audio only and never came back.
#include "comms/call.hpp"
#include "media/server.hpp"
#include <iostream>
#include <thread>

using namespace comms;
int main() {
    long fail = 0;
    const int PORT = 19050;
    media::Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    Call donna("127.0.0.1", PORT, "Donna", "S1", std::make_unique<SyntheticSource>(1280, 720));
    Call john("127.0.0.1", PORT, "John", "S1", nullptr);
    donna.start(); john.start();
    std::this_thread::sleep_for(std::chrono::seconds(4));
    donna.set_throttle(128000);                                   // a 128 kb/s uplink
    int smallest = 1 << 30; uint64_t audio_only_s = 0;
    for (int s = 0; s < 40; ++s) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        int w = donna.stats.send_w;
        if (w == 0) ++audio_only_s; else smallest = std::min(smallest, w);
        if (s % 5 == 4) std::cout << "  t=" << s + 1 << "s at 128 kb/s: sending " << (w ? std::to_string(w) + "x" + std::to_string(donna.stats.send_h.load()) : std::string("audio only")) << "\n";
    }
    Picture p; bool got = john.latest("Donna", p);
    std::cout << "at 128 kb/s: smallest picture sent " << smallest << " wide; audio only for " << audio_only_s << " of 40 s; John receiving "
              << (got ? std::to_string(p.w) + "x" + std::to_string(p.h) : std::string("nothing")) << "\n";
    if (smallest > 640 || donna.stats.send_w == 0) { ++fail; std::cout << "FAIL at 128 kb/s the sender should still send a small picture\n"; }
    donna.set_throttle(0);                                       // the wire opens
    int largest = 0;
    for (int s = 0; s < 90 && largest < 640; ++s) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        largest = std::max(largest, donna.stats.send_w.load());
        if (s % 10 == 9) std::cout << "  t=" << s + 1 << "s unthrottled: sending " << donna.stats.send_w << "x" << donna.stats.send_h << " (L" << donna.stats.rung << ")\n";
    }
    std::cout << "unthrottled: grew back to " << largest << " wide\n";
    if (largest < 640) { ++fail; std::cout << "FAIL the picture did not grow back to 640x360 within 90 s\n"; }
    donna.stop(); john.stop(); srv.stop(); th.join();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " the bottom walk: " << fail << " fail\n";
    return fail ? 1 : 0;
}
