// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_lisper_client.cpp PORT -- lisper::Client against a real lisper-ram on 127.0.0.1:PORT.
#include "lisper_client.hpp"
#include <iostream>
static int fails = 0;
static void check(bool ok, const std::string& what) { std::cout << (ok ? "PASS " : "FAIL ") << what << "\n"; if (!ok) ++fails; }
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: test-lisper-client PORT\n"; return 2; }
    lisper::Client c; c.connect("127.0.0.1", std::atoi(argv[1]));
    c.add_site("0", "198.18.0.0/16", "", true, 1, "client-test-key");
    check(c.resolve("0", "198.18.7.7") == "[]", "C1 nothing registered: the lookup resolves to nothing");
    c.operation("registration.put", "{\"iid\":\"0\",\"prefix\":\"198.18.7.0/24\",\"group\":\"\",\"rlocs\":[{\"address\":\"192.0.2.7\",\"priority\":1,\"weight\":100}],\"ttl\":3}");
    const std::string r = c.resolve("0", "198.18.7.7");
    check(r.find("198.18.7.0/24") != std::string::npos && r.find("192.0.2.7") != std::string::npos, "C2 registered: the lookup resolves to it (" + r.substr(0, 80) + ")");
    bool refused = false;
    try { c.operation("no.such.operation", "{}"); } catch (const frogram::Refused&) { refused = true; }
    check(refused, "C3 an operation the region does not have: Refused, carrying the host's error");
    bool threw = false;
    try { lisper::Client x; x.connect("127.0.0.1", 1); } catch (const frogram::Unreachable&) { threw = true; }
    check(threw, "C4 no lisper-ram at the address: Unreachable, no fallback");
    c.delete_site("0", "198.18.0.0/16", "");
    std::cout << "RESULT " << (fails ? "FAIL" : "PASS") << " lisper client (C++): " << fails << " fail\n";
    return fails ? 1 : 0;
}
