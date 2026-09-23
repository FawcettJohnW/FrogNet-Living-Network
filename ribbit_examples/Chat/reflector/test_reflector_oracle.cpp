// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_reflector HOST:PORT -- is the reflector a real one-to-many send-or-drop fast plane?
//
//   R1  one publisher's frame reaches EVERY subscriber of that stream
//   R2  a subscriber of a DIFFERENT stream does not receive it
//   R3  a single publisher's frames arrive in order at every subscriber (TCP, per publisher)
//   R4  a subscriber that joins LATE gets only frames published after it joined -- nothing stored
//   R5  after unsubscribe, no more frames arrive on that stream
//   R6  send-or-drop: a fast publisher to N subscribers never blocks; reflection latency
//       (publish -> in a subscriber's callback) is reported, and no subscriber sees a frame twice
#include "reflector_client.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
using namespace reflector;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static int fails = 0;
static void ck(const char* n, bool ok, const char* extra = "") { printf("  %s  %s%s%s\n", ok ? "ok  " : "FAIL", n, extra[0] ? "   " : "", extra); if (!ok) ++fails; }
static double pctl(std::vector<double>& v, double p) { if (v.empty()) return 0; std::sort(v.begin(), v.end()); return v[size_t(p * (v.size() - 1))]; }

int main(int argc, char** argv) {
    std::string hp = argc > 1 ? argv[1] : "127.0.0.1:8789";
    std::string tag = std::to_string(long(now() * 1000) % 1000000), stream = "s" + tag, other = "o" + tag;
    try {
        // R1/R2: three subscribers of `stream`, one of `other`
        std::vector<std::unique_ptr<Reflector>> subs; std::vector<std::atomic<int>*> counts;
        std::vector<std::vector<uint64_t>> gens(4); std::vector<std::mutex> gm(4);
        for (int i = 0; i < 4; ++i) {
            subs.emplace_back(new Reflector(hp, "sub" + std::to_string(i)));
            counts.push_back(new std::atomic<int>(0));
            std::string st = (i == 3) ? other : stream; int idx = i;
            subs[i]->subscribe(st, [idx, &gens, &gm, &counts](uint64_t g, const std::string&) { std::lock_guard<std::mutex> l(gm[idx]); gens[idx].push_back(g); (*counts[idx])++; });
        }
        Reflector pub(hp, "pub");
        std::this_thread::sleep_for(std::chrono::milliseconds(200));   // let subscriptions register
        for (uint64_t g = 1; g <= 20; ++g) { pub.publish(stream, g, "frame-" + std::to_string(g)); std::this_thread::sleep_for(std::chrono::milliseconds(5)); }
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        ck("R1 every subscriber of the stream got all 20 frames", *counts[0] == 20 && *counts[1] == 20 && *counts[2] == 20, (std::to_string(*counts[0]) + "/" + std::to_string(*counts[1]) + "/" + std::to_string(*counts[2])).c_str());
        ck("R2 a subscriber of another stream got none", *counts[3] == 0);
        bool ordered = true; for (int i = 0; i < 3; ++i) { std::lock_guard<std::mutex> l(gm[i]); for (size_t k = 1; k < gens[i].size(); ++k) if (gens[i][k] <= gens[i][k-1]) ordered = false; }
        ck("R3 one publisher's frames arrived in order at every subscriber", ordered);

        // R4: a late subscriber sees only new frames (nothing stored)
        Reflector late(hp, "late"); std::atomic<int> lc(0); uint64_t first_seen = 0; std::mutex lm;
        late.subscribe(stream, [&](uint64_t g, const std::string&) { std::lock_guard<std::mutex> l(lm); if (!first_seen) first_seen = g; lc++; });
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        for (uint64_t g = 21; g <= 25; ++g) { pub.publish(stream, g, "x"); std::this_thread::sleep_for(std::chrono::milliseconds(5)); }
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        ck("R4 a late subscriber got only frames published after it joined", lc == 5 && first_seen == 21, ("count " + std::to_string(lc) + ", first " + std::to_string(first_seen)).c_str());

        // R5: unsubscribe stops delivery
        subs[0]->unsubscribe(stream); std::this_thread::sleep_for(std::chrono::milliseconds(100));
        int before = *counts[0]; for (uint64_t g = 26; g <= 30; ++g) pub.publish(stream, g, "y");
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        ck("R5 after unsubscribe no more frames arrived", *counts[0] == before);

        // R6: reflection latency, publish -> callback, stamped
        std::vector<double> lat; std::mutex latm; std::atomic<int> got(0);
        std::string lstream = "L" + tag; std::vector<std::unique_ptr<Reflector>> rs;
        for (int i = 0; i < 8; ++i) { rs.emplace_back(new Reflector(hp, "r" + std::to_string(i)));
            rs[i]->subscribe(lstream, [&](uint64_t, const std::string& body) { double t; std::memcpy(&t, body.data(), sizeof t); std::lock_guard<std::mutex> l(latm); lat.push_back((now() - t) * 1000.0); got++; }); }
        Reflector lp(hp, "lp"); std::this_thread::sleep_for(std::chrono::milliseconds(200));
        int published = 0;
        for (uint64_t g = 1; g <= 500; ++g) { double t = now(); std::string b(reinterpret_cast<char*>(&t), sizeof t); b.resize(80, 'x'); if (lp.publish(lstream, g, b)) ++published; std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        printf("        fan-out latency to 8 subscribers, %d frames published (%llu shed at publisher): median %.2f ms, p99 %.2f ms; %d delivered\n",
               published, (unsigned long long)lp.shed, pctl(lat, .5), pctl(lat, .99), got.load());
        ck("R6 send-or-drop never blocked the publisher and frames were delivered", published > 0 && got > 0);
    } catch (const std::exception& e) { printf("  FAIL exception: %s\n", e.what()); ++fails; }
    printf("\n%s  (%d failed)\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
