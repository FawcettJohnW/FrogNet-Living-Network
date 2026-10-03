// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_room RAM_PORT -- the Communicator on its shared memory (a real comms-ram): the lobby, chat, and a viewer's
// download cap written as MediaSpeed and applied by the media server to that viewer only.
#include "comms/call.hpp"
#include "comms/room.hpp"
#include "media/speed.hpp"
#include "media/server.hpp"
#include <iostream>
#include <thread>

using namespace comms;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 12) std::cout << "FAIL " << w << "\n"; }

int main(int argc, char** argv) {
    int RAM = argc > 1 ? std::atoi(argv[1]) : 19300, MEDIA = 19310;
    Room donna("127.0.0.1", RAM, "Donna"), john("127.0.0.1", RAM, "John"), julie("127.0.0.1", RAM, "Julie");
    // Others' writes reach a participant's copy through its held read, a few ms behind: wait for them, as the window
    // does (found 2026-10-03: reading immediately raced the held read under load).
    auto until = [](Room& rm, auto cond) {
        auto t0 = std::chrono::steady_clock::now();
        while (!cond() && std::chrono::steady_clock::now() - t0 < std::chrono::seconds(3)) rm.wait_change(rm.version(), 200);
    };
    until(donna, [&] { return donna.roster().size() >= 3; });
    auto r = donna.roster();
    std::vector<std::string> names; for (auto& m : r) names.push_back(m.name);
    std::sort(names.begin(), names.end());
    check(names == std::vector<std::string>({"Donna", "John", "Julie"}), "the lobby lists all three: " + std::to_string(names.size()));
    std::cout << "lobby:"; for (auto& m : r) std::cout << " " << m.name << "(" << m.status << ")"; std::cout << "\n";

    donna.send_chat("S1", "Can you see the cake?");
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    john.send_chat("S1", "Clear as day.");
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    donna.send_chat("S1", "Perfect");
    julie.send_chat("S2", "another call");
    until(julie, [&] { return julie.read_chat("S1").size() >= 3; });
    auto lines = julie.read_chat("S1");
    check(lines.size() == 3 && lines[0].text == "Can you see the cake?" && lines[1].from_name == "John" && lines[2].text == "Perfect",
          "chat in S1: 3 lines in order, S2's line not among them: " + std::to_string(lines.size()));
    for (auto& l : lines) std::cout << "  " << l.from_name << ": " << l.text << "\n";

    media::Server srv("127.0.0.1", MEDIA);
    std::thread th([&] { srv.run(); });
    SpeedPoller poll(srv, "127.0.0.1", RAM);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    Call jc("127.0.0.1", MEDIA, "John", "S1", std::make_unique<SyntheticSource>(1280, 720));
    Call dc("127.0.0.1", MEDIA, "Donna", "S1", nullptr);
    Call uc("127.0.0.1", MEDIA, "Julie", "S1", nullptr);
    jc.start(); dc.start(); uc.start();
    julie.set_media_speed("S1", 120000);                   // written to memory; nothing is sent to the media server
    // Up to 20 s for both: a capped viewer is switched to its transcoded stream at once, but its picture changes only
    // with that stream's first keyframe -- seconds on a loaded single core (found 2026-10-03: the fixed 7 s read a
    // stale picture from before the cap). The server never sends a capped viewer the uncapped stream.
    Picture pd, pu;
    for (auto t0 = std::chrono::steady_clock::now(); std::chrono::steady_clock::now() - t0 < std::chrono::seconds(20);) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        dc.latest("John", pd); uc.latest("John", pu);
        if (pu.w == 160 && pd.w == jc.stats.send_w && pd.w > 160 && std::chrono::steady_clock::now() - t0 > std::chrono::seconds(5)) break;
    }
    // No cap: Donna gets what John is SENDING, passed through -- 1280x720 when the machine keeps up; smaller when a
    // loaded CPU makes John's own bearer step down (correct, and not what this check is about).
    const int sw = jc.stats.send_w, sh = jc.stats.send_h;
    check(pd.w == sw && pd.h == sh && sw > 160, "Donna, no cap: John as sent (" + std::to_string(sw) + "x" + std::to_string(sh) +
          "): " + std::to_string(pd.w) + "x" + std::to_string(pd.h));
    check(pu.w == 160 && pu.h == 120, "Julie, MediaSpeed 120 kb/s in memory: John at 160x120: " + std::to_string(pu.w) + "x" + std::to_string(pu.h));
    std::cout << "MediaSpeed via memory: Donna sees John " << pd.w << "x" << pd.h << ", Julie (120 kb/s) " << pu.w << "x" << pu.h << "\n";
    jc.stop(); dc.stop(); uc.stop(); srv.stop(); th.join();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " lobby, chat and MediaSpeed on comms-ram: " << fail << " fail\n";
    return fail ? 1 : 0;
}
