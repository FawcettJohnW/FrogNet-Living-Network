// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// room_probe RAM_PORT NAME SESSION TEXT [INVITE_WHO] -- a C++ participant for interop tests: joins the room, invites
// INVITE_WHO to SESSION, sends TEXT in SESSION, waits up to 4 s for someone else's chat line, and prints what it sees:
//   roster: <names>   chat: <from_name>: <text>   (one line each)
#include "comms/room.hpp"
#include <chrono>
#include <iostream>
#include <thread>

int main(int argc, char** argv) {
    if (argc < 5) { std::cerr << "usage: room_probe RAM_PORT NAME SESSION TEXT [INVITE_WHO]\n"; return 2; }
    comms::Room r("127.0.0.1", std::atoi(argv[1]), argv[2]);
    std::string session = argv[3];
    if (argc > 5) r.invite(argv[5], session, "127.0.0.1", 8994);
    r.send_chat(session, argv[4]);
    auto t0 = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - t0 < std::chrono::seconds(4)) {
        bool other = false;
        for (auto& l : r.read_chat(session)) if (l.from_name != argv[2]) other = true;
        if (other) break;
        r.wait_change(r.version(), 500);
    }
    std::cout << "roster:"; for (auto& m : r.roster()) std::cout << " " << m.name; std::cout << "\n";
    for (auto& l : r.read_chat(session)) std::cout << "chat: " << l.from_name << ": " << l.text << "\n";
    return 0;
}
