// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_room_watch RAM_PORT -- the room's copy of the region is kept by a held read ([HOLD_THE_READ_V1]):
// a chat line and a new participant reach the other rooms in milliseconds, and an idle room costs almost nothing.
#include "comms/room.hpp"
#include <chrono>
#include <iostream>
#include <thread>

using namespace comms;
using Clock = std::chrono::steady_clock;

int main(int argc, char** argv) {
    int port = argc > 1 ? std::atoi(argv[1]) : 19700;
    long fail = 0;
    Room john("127.0.0.1", port, "John"), donna("127.0.0.1", port, "Donna");
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    // a chat line, Donna -> John's copy
    for (int i = 0; i < 5; ++i) {
        uint64_t v = john.version();
        auto t0 = Clock::now();
        donna.send_chat("S1", "line " + std::to_string(i));
        bool seen = false;
        while (!seen && Clock::now() - t0 < std::chrono::seconds(3)) {
            john.wait_change(v, 3000); v = john.version();
            for (auto& l : john.read_chat("S1")) if (l.text == "line " + std::to_string(i)) seen = true;
        }
        double ms = std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
        std::cout << "chat line " << i << ": in John's copy after " << ms << " ms\n";
        if (!seen || ms > 500) { ++fail; std::cout << "FAIL chat line " << i << "\n"; }
    }
    // a new participant appears in both copies
    auto t0 = Clock::now();
    Room julie("127.0.0.1", port, "Julie");
    bool j = false, d = false;
    while ((!j || !d) && Clock::now() - t0 < std::chrono::seconds(3)) {
        for (auto& m : john.roster()) j |= m.name == "Julie";
        for (auto& m : donna.roster()) d |= m.name == "Julie";
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    double ms = std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
    std::cout << "Julie joined: in John's and Donna's lobby after " << ms << " ms (including her own connect)\n";
    if (!j || !d) { ++fail; std::cout << "FAIL Julie not seen\n"; }
    // idle: 20 s with nobody doing anything (heartbeats only); comms-ram logs each session's request count at close
    std::this_thread::sleep_for(std::chrono::seconds(20));
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " held-read room copy: " << fail << " fail\n";
    return fail ? 1 : 0;
}
