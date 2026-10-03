// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_call -- two participants through a real media server. Each must see the other at the other's own size.
// Then Donna's demo throttle goes to 400 kb/s: her bearer must step her upload down by itself, and John must then
// see her at the smaller size.
#include "comms/call.hpp"
#include "media/server.hpp"
#include <iostream>
#include <thread>

using namespace comms;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 12) std::cout << "FAIL " << w << "\n"; }

int main() {
    const int PORT = 19000;
    media::Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    Call donna("127.0.0.1", PORT, "Donna", "S1", std::make_unique<SyntheticSource>(1280, 720));
    Call john("127.0.0.1", PORT, "John", "S1", std::make_unique<SyntheticSource>(640, 360));
    donna.start(); john.start();
    std::this_thread::sleep_for(std::chrono::seconds(6));
    Picture pd, pj;
    bool jd = john.latest("Donna", pd), dj = donna.latest("John", pj);
    check(jd && pd.w == 1280 && pd.h == 720, "John sees Donna at 1280x720: " + std::to_string(pd.w) + "x" + std::to_string(pd.h));
    check(dj && pj.w == 640 && pj.h == 360, "Donna sees John at 640x360: " + std::to_string(pj.w) + "x" + std::to_string(pj.h));
    check(john.stats.v_recv > 60 && donna.stats.v_recv > 60, "both receiving: John " + std::to_string(john.stats.v_recv) + ", Donna " + std::to_string(donna.stats.v_recv) + " frames");
    std::cout << "open call: John sees Donna " << pd.w << "x" << pd.h << " (" << john.stats.v_recv << " frames), Donna sees John "
              << pj.w << "x" << pj.h << " (" << donna.stats.v_recv << " frames); Donna's rung L" << donna.stats.rung << "\n";

    donna.set_throttle(400000);                            // DEMO: a 400 kb/s wire under Donna's uplink
    int lowest = donna.stats.rung;
    for (int s = 0; s < 40 && lowest > 5; ++s) { std::this_thread::sleep_for(std::chrono::seconds(1)); lowest = std::min(lowest, donna.stats.rung.load()); }
    std::this_thread::sleep_for(std::chrono::seconds(3));
    john.latest("Donna", pd);
    check(lowest <= 6, "Donna's bearer stepped her upload down under the 400 kb/s throttle: lowest L" + std::to_string(lowest));
    check(pd.w < 1280, "John now sees Donna smaller: " + std::to_string(pd.w) + "x" + std::to_string(pd.h));
    std::cout << "throttled to 400 kb/s: Donna's rung fell to L" << lowest << ", sending " << donna.stats.send_w << "x" << donna.stats.send_h
              << "; John sees " << pd.w << "x" << pd.h << "; Donna shed " << donna.stats.v_dropped << " frames over the budget\n";
    donna.stop(); john.stop();
    srv.stop(); th.join();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " call through the media server: " << fail << " fail\n";
    return fail ? 1 : 0;
}
