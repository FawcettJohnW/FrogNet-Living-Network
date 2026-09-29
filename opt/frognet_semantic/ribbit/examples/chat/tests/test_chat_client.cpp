// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_chat_client.cpp PORT -- chat::Client against a real chat-ram on 127.0.0.1:PORT.
#include "chat_client.hpp"
#include <chrono>
#include <iostream>
#include <thread>
#include <unistd.h>
static int fails = 0;
static void check(bool ok, const std::string& what) { std::cout << (ok ? "PASS " : "FAIL ") << what << "\n"; if (!ok) ++fails; }
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: test-chat-client PORT\n"; return 2; }
    const int port = std::atoi(argv[1]);
    const std::string tag = std::to_string(::getpid());
    const std::string dave = "Dave" + tag, bob = "Bob" + tag, eve = "Eve" + tag;
    chat::Client d, b, e; d.connect("127.0.0.1", port); b.connect("127.0.0.1", port); e.connect("127.0.0.1", port);
    uint64_t bob_at = b.position(bob), dave_at = d.position(dave);
    d.send(bob, dave, "hello");                                                          // G1
    auto m = b.receive(bob, bob_at, 5);
    check(m.size() == 1 && m[0].from == dave && m[0].text == "hello", "G1 what Dave sends is what Bob receives");
    double t0 = now(); std::vector<chat::Message> got;                                   // G2
    std::thread r([&] { got = b.receive(bob, bob_at, 10); });
    std::this_thread::sleep_for(std::chrono::milliseconds(400)); d.send(bob, dave, "late"); r.join();
    double dt = now() - t0;
    check(got.size() == 1 && got[0].text == "late" && dt >= 0.35 && dt < 3, "G2 a receive asked before the send is held open and wakes on it");
    t0 = now(); auto none = b.receive(bob, bob_at, 1.0); dt = now() - t0;               // G3
    check(none.empty() && dt >= 0.9 && dt < 3, "G3 a receive that expires returns nothing after about wait_s");
    std::thread t1([&] { for (int i = 0; i < 10; ++i) d.send(bob, dave, "d" + std::to_string(i)); });   // G5
    std::thread t2([&] { for (int i = 0; i < 10; ++i) b.send(dave, bob, "b" + std::to_string(i)); });
    t1.join(); t2.join();
    std::vector<std::string> atb, atd;
    for (int k = 0; k < 40 && (atb.size() < 10 || atd.size() < 10); ++k) {
        for (auto& x : b.receive(bob, bob_at, 0.5)) atb.push_back(x.text);
        for (auto& x : d.receive(dave, dave_at, 0.5)) atd.push_back(x.text);
    }
    // one cell per sender: a burst from one sender leaves its latest line; what arrives is in write order
    check(!atb.empty() && atb.back() == "d9" && !atd.empty() && atd.back() == "b9", "G5 both directions at once: each side ends on the other's last line");
    uint64_t fresh = b.position(bob);                                                    // G6
    auto again = b.receive(bob, fresh, 0.3);
    check(again.empty(), "G6 a message already in my buffer when I arrive is not new");
    std::thread s1([&] { d.send(bob, dave, "same instant D"); });                        // G7
    std::thread s2([&] { e.send(bob, eve, "same instant E"); });
    s1.join(); s2.join();
    std::vector<chat::Message> both;
    for (int k = 0; k < 10 && both.size() < 2; ++k) for (auto& x : b.receive(bob, fresh, 1)) both.push_back(x);
    check(both.size() == 2 && both[0].from != both[1].from, "G7 two people writing to me at the same instant: neither line is lost");
    bool threw = false;                                                                  // G8
    try { chat::Client x; x.connect("127.0.0.1", 1); } catch (const frogram::Unreachable&) { threw = true; }
    check(threw, "G8 no chat-ram at the address: Unreachable, no fallback");
    std::cout << "RESULT " << (fails ? "FAIL" : "PASS") << " chat client (C++): " << fails << " fail\n";
    return fails ? 1 : 0;
}
