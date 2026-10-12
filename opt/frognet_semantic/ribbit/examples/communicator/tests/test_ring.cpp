// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_ring RAM_PORT -- [RINGS_ONLY_WHILE_OFFERED_V1] [LEAVE_WITHDRAWS_THE_OFFER_V1] on a real comms-ram: a call rings
// the callee while the caller offers it, and stops ringing when the caller hangs up -- though the invitation row stays.
#include "comms/room.hpp"
#include <iostream>
#include <thread>

using namespace comms;
static long fail = 0;
static void check(bool ok, const std::string& w) { std::cout << (ok ? "ok   " : "FAIL ") << w << "\n"; if (!ok) ++fail; }

int main(int argc, char** argv) {
    int RAM = argc > 1 ? std::atoi(argv[1]) : 19320;
    Room phone("127.0.0.1", RAM, "Phone"), win("127.0.0.1", RAM, "Windows");
    auto until = [](Room& rm, auto cond) {
        auto t0 = std::chrono::steady_clock::now();
        while (!cond() && std::chrono::steady_clock::now() - t0 < std::chrono::seconds(5)) rm.wait_change(rm.version(), 200);
        return cond();
    };
    check(win.ringing().empty(), "nothing rings before a call");
    // the phone calls Windows, as both apps do: offer, invite, join
    phone.offer_call("S-ring-1", "127.0.0.1", 8994); phone.invite("Windows", "S-ring-1", "127.0.0.1", 8994);
    phone.join_call("S-ring-1", "127.0.0.1", 8994);
    check(until(win, [&] { return win.ringing().size() == 1; }), "Windows rings while the phone offers the call");
    if (!win.ringing().empty()) check(win.ringing()[0].offered_by == "Phone", "it says who is calling: " + win.ringing()[0].offered_by);
    // the phone hangs up before Windows answers
    phone.leave_call("S-ring-1");
    check(until(win, [&] { return win.ringing().empty(); }), "Windows stops ringing when the phone hangs up");
    check(win.invitations().size() == 1, "the invitation row is still there (it is not what rings): " + std::to_string(win.invitations().size()));
    // a second call rings again: a new session, not the old one
    phone.offer_call("S-ring-2", "127.0.0.1", 8994); phone.invite("Windows", "S-ring-2", "127.0.0.1", 8994);
    check(until(win, [&] { auto r = win.ringing(); return r.size() == 1 && r[0].session == "S-ring-2"; }), "the next call rings, and only it");
    phone.leave_call("S-ring-2");
    // Windows hanging up withdraws its own offer too (it never did: every session it started stayed offered)
    win.offer_call("S-ring-3", "127.0.0.1", 8994); win.invite("Phone", "S-ring-3", "127.0.0.1", 8994);
    check(until(phone, [&] { return phone.ringing().size() == 1; }), "the phone rings when Windows calls");
    win.leave_call("S-ring-3");
    check(until(phone, [&] { return phone.ringing().empty(); }), "the phone stops ringing when Windows hangs up");
    bool offered = false; for (auto& o : phone.offers()) if (o.session == "S-ring-3") offered = true;
    check(!offered, "Windows no longer offers the session it hung up");
    std::cout << (fail ? "test_ring: FAIL\n" : "test_ring: PASS\n");
    return fail ? 1 : 0;
}
