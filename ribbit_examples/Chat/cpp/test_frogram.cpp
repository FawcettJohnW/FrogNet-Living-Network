// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_frogram HOST RAM_PORT PLANE_PORT [peer]
// The C++ client against a real listener, a real memory and a real plane. With
// `peer`, it also talks to a PYTHON frogchat client through the same memory.
#include "frogram.hpp"
#include <chrono>
#include <cstdio>
#include <thread>
using namespace frogram;
static int fails = 0;
static void ck(const char* name, bool ok) { printf("  %s  %s\n", ok ? "ok  " : "FAIL", name); if (!ok) ++fails; }
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "usage: test_frogram HOST RAM_PORT PLANE_PORT [python-peer-name]\n"); return 2; }
    std::string host = argv[1]; int ram = atoi(argv[2]), plane = atoi(argv[3]);
    std::string tag = std::to_string(long(now() * 1000) % 1000000);
    try {
        Session s(host, ram); Memory m(s);
        uint64_t id = m.write("cpp.test", "v", "i" + tag, "{\"n\":1,\"s\":\"h\\u00e9 \\\"q\\\"\"}");
        auto rows = m.read("cpp.test", "v", "i" + tag);
        ck("C1 write then read returns the bag", rows.size() == 1 && rows[0].id == id && rows[0].bag["n"].n == 1 && rows[0].bag["s"].s == "h\xc3\xa9 \"q\"");
        uint64_t id2 = m.write("cpp.test", "v", "i" + tag, "{\"n\":2}");
        rows = m.read("cpp.test", "v", "i" + tag);
        ck("C2 writing replaces; the cell takes the next id in the memory's order", rows.size() == 1 && rows[0].bag["n"].n == 2 && id2 > id);

        std::vector<Cell> got; double t0 = now(), t1 = 0;
        std::thread w([&] { Session s2(host, ram); Memory m2(s2); got = m2.read("cpp.test", "v", "", int64_t(id2), 8.0); t1 = now(); });
        std::this_thread::sleep_for(std::chrono::milliseconds(600));
        double tw = now(); m.write("cpp.test", "v", "j" + tag, "{\"woke\":true}"); double dw = now() - tw;
        w.join();
        ck("C3 a blocking read on cpp.test.v.* was held, then woke on the write", got.size() == 1 && got[0].bag["woke"].b && t1 - t0 > 0.55 && t1 - t0 < 2.0);
        ck("C3 the write was not held up behind it", dw < 0.5);

        auto before = s.stats(); m.read("cpp.test", "v", "i" + tag); m.read("cpp.test", "v", "i" + tag); auto after = s.stats();
        ck("C4 an unchanged question: REQ_REPEAT out, RESP_SAME back", after.repeat - before.repeat >= 1 && after.same - before.same >= 1);
        before = s.stats(); m.read("cpp.test", "v", "i" + tag); after = s.stats();
        ck("C4 21 bytes out, 21 bytes back", after.bytes_out - before.bytes_out == 21 && after.bytes_in - before.bytes_in == 21);
        for (auto& c : m.read("cpp.test", "v")) m.remove(c.id);
        ck("C5 remove", m.read("cpp.test", "v").empty());
        bool refused = false; try { s.call("GET", "/ram.php?op=nonsense"); } catch (const Refused&) { refused = true; }
        ck("C6 a refusal is thrown as Refused", refused);
        bool unreachable = false; try { Session dead(host, 1); } catch (const Unreachable&) { unreachable = true; }
        ck("C6 no listener is thrown as Unreachable", unreachable);

        Plane a(host, plane), b(host, plane); std::string pre = "cpp" + tag + "/kart/"; uint64_t at = 0;
        a.publish(pre + "1", 10, std::string(80, 'x'));
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto pr = b.read(pre, at);
        ck("C7 plane: a published value is what a reader gets", pr.size() == 1 && pr[0].gen == 10 && pr[0].bytes.size() == 80);
        a.publish(pre + "1", 9, "OLD"); std::this_thread::sleep_for(std::chrono::milliseconds(50));
        uint64_t zero = 0; pr = b.read(pre, zero);
        ck("C7 plane: an older generation cannot replace a newer one", pr.size() == 1 && pr[0].gen == 10);
        std::vector<PlaneRow> woke; t0 = now();
        std::thread r([&] { woke = b.read(pre, at, 5000); t1 = now(); });
        std::this_thread::sleep_for(std::chrono::milliseconds(400)); a.publish(pre + "2", 11, "new"); r.join();
        ck("C8 plane: a blocked read woke on the publish", woke.size() == 1 && woke[0].name == pre + "2" && t1 - t0 > 0.35 && t1 - t0 < 1.5);
        bool never = false; try { uint64_t z = 0; b.read("cpp" + tag + "/nobody/", z); } catch (const std::out_of_range&) { never = true; }
        ck("C8 plane: nothing ever published is said, not returned as empty success", never);

        if (argc > 4) {                          // cross-language, through the memory
            std::string peer = argv[4], me = "Cpp" + tag;
            m.write("ChatServer", peer, me, "{\"from\":" + Json::quote(me) + ",\"to\":[" + Json::quote(peer) + "],\"ts\":" + std::to_string(now()) + ",\"text\":\"hello from C++\"}");
            auto in = m.read("ChatServer", me, "", 0, 8.0);
            ck("C9 a PYTHON client answered a C++ client through the same memory", in.size() == 1 && in[0].bag["text"].s == "hello from python" && in[0].instance == peer);
            uint64_t pat = 0; auto pp = b.read("py/kart/", pat, 4000);
            ck("C9 a PYTHON publish on the plane was read by C++", pp.size() == 1 && pp[0].gen == 77 && pp[0].bytes == "py-body");
            a.publish("cpp/kart/9", 99, "cpp-body");
        }
    } catch (const std::exception& e) { printf("  FAIL  exception: %s\n", e.what()); ++fails; }
    printf("\n%s  (%d failed)\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
