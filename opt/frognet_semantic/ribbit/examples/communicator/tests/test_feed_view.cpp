// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_feed_view RAM_PORT FEED_NAME -- requirement 1 from a viewer's seat: the unattended feed is in the lobby as
// "*<name>", its call is offered, the viewer joins it and receives the picture.
#include "comms/call.hpp"
#include "comms/room.hpp"
#include <iostream>
#include <thread>

using namespace comms;
int main(int argc, char** argv) {
    if (argc < 3) { std::cout << "usage: test_feed_view RAM_PORT FEED_NAME\n"; return 2; }
    int ram = std::atoi(argv[1]); std::string feed = argv[2]; long fail = 0;
    Room me("127.0.0.1", ram, "Donna");
    if (argc > 3 && std::string(argv[3]) == "offline") {   // after the feed stopped: it says so in the lobby
        for (auto& m : me.roster()) if (m.name == "*" + feed) {
            std::cout << "after stopping: *" << feed << " is " << m.status << "\n";
            return m.status == "offline" ? 0 : 1;
        }
        std::cout << "after stopping: *" << feed << " is gone from the lobby\n";
        return 0;
    }
    std::vector<Member> roster;
    for (int i = 0; i < 20; ++i) { roster = me.roster(); bool f = false; for (auto& m : roster) f |= m.name == "*" + feed; if (f) break; std::this_thread::sleep_for(std::chrono::milliseconds(250)); }
    const Member* fm = nullptr; for (auto& m : roster) if (m.name == "*" + feed) fm = &m;
    if (!fm || fm->status != "online") { ++fail; std::cout << "FAIL the feed is not in the lobby as *" << feed << "\n"; }
    else std::cout << "lobby: *" << feed << " (" << fm->status << ", caps " << fm->caps << ")\n";
    // the call the feed itself is on (its CallsJoined row), offered at a media server
    std::vector<std::string> on = me.calls_of(feed);
    const Offer* op = nullptr;
    std::vector<Offer> offers = me.offers();
    for (auto& x : offers) for (auto& s : on) if (x.session == s) op = &x;
    if (!op) { ++fail; std::cout << "FAIL no call of the feed's is offered (" << on.size() << " joined, " << offers.size() << " offered)\n"; return 1; }
    const Offer& o = *op;
    std::cout << "offered: session " << o.session << " at " << o.media_host << ":" << o.media_port << "\n";
    me.join_call(o.session, o.media_host, o.media_port);
    Call c(o.media_host, o.media_port, "Donna", o.session, nullptr);
    c.start();
    std::this_thread::sleep_for(std::chrono::seconds(5));
    Picture p;
    bool got = c.latest(feed, p);
    if (!got || p.w != 1280 || p.h != 720 || c.stats.v_recv < 60) { ++fail; std::cout << "FAIL the feed's picture: " << p.w << "x" << p.h << ", " << c.stats.v_recv << " frames\n"; }
    else std::cout << "joined: " << feed << " at " << p.w << "x" << p.h << ", " << c.stats.v_recv << " frames in 5 s\n";
    c.stop();
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " the unattended feed, from a viewer's seat: " << fail << " fail\n";
    return fail ? 1 : 0;
}
