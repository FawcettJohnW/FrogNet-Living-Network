// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// flood -- one client writes to another as fast as it can, incrementing a
// counter every time; the other pulls with the blocking read as fast as it can.
// How many does the receiver miss?
//
//     flood HOST RAM_PORT [--seconds 5] [--ack] [--rate WRITES_PER_SEC] [--payload BYTES]
//                         [--plane PLANE_PORT] [--json FILE] [--label TEXT]
//
// --payload pads every value to about BYTES, to measure what the path carries.
// Every value carries the writer's clock, so delivery latency is measured per value.
//
// --rate paces the writer instead of letting it run flat out, to find the rate
// at which the reader starts to skip.
//
// Two clients, two sessions. The writer writes ChatServer.<rx>.<tx> = {"n": i}
// and DOES NOT WAIT for an acknowledgement: it sends and sends, held back only
// by TCP when the far end is not keeping up. (--ack makes it wait for each one.)
// Its very last write is acknowledged, so the run knows the far end has taken
// everything before it -- frames on a session are taken in order. The reader sits in ChatServer.<rx>.* with `after` and
// records every n it is handed.
//
// A cell holds ONE value and writing replaces, so a reader that is slower than
// the writer is EXPECTED to skip values: it is always handed the newest, never
// a backlog. What must never happen: a value out of order, a value twice, or
// the reader failing to end on the writer's last value.
//
// With --plane the same race is run on the fast plane (send-or-drop publish,
// block-until-newer read) for comparison.
#include "frogram.hpp"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <thread>
#include <vector>
using namespace frogram;
static double wallclock() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static double pct(std::vector<double>& v, double p) { if (v.empty()) return 0; size_t k = size_t(p * (v.size() - 1)); std::nth_element(v.begin(), v.begin() + k, v.end()); return v[k]; }
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct Result { long sent = 0, got = 0, reads = 0, disorder = 0, dup = 0, biggest_gap = 0, last_sent = -1, last_got = -1; double secs = 0; };

static void report(const char* what, const Result& r) {
    long missed = r.sent - r.got;
    printf("\n%s\n", what);
    printf("  writer   sent %ld in %.2f s  = %.0f writes/s\n", r.sent, r.secs, r.sent / r.secs);
    printf("  reader   received %ld in %ld reads  = %.0f reads/s\n", r.got, r.reads, r.reads / r.secs);
    printf("  MISSED   %ld of %ld  (%.1f%%)   biggest jump: %ld\n", missed, r.sent, r.sent ? 100.0 * missed / r.sent : 0.0, r.biggest_gap);
    printf("  out of order: %ld    delivered twice: %ld    ended on the writer's last value: %s (%ld / %ld)\n",
           r.disorder, r.dup, r.last_got == r.last_sent ? "yes" : "NO", r.last_got, r.last_sent);
}

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: flood HOST RAM_PORT [--seconds N] [--plane PORT]\n"); return 2; }
    std::string host = argv[1]; int ram = atoi(argv[2]), plane_port = 0; double seconds = 5, rate = 0; bool ack = false; long payload = 0; std::string json_path, label;
    for (int i = 3; i < argc; ++i) if (!strcmp(argv[i], "--ack")) { ack = true; for (int k = i; k + 1 < argc; ++k) argv[k] = argv[k + 1]; --argc; break; }
    for (int i = 3; i + 1 < argc; i += 2) { if (!strcmp(argv[i], "--seconds")) seconds = atof(argv[i + 1]); else if (!strcmp(argv[i], "--plane")) plane_port = atoi(argv[i + 1]); else if (!strcmp(argv[i], "--rate")) rate = atof(argv[i + 1]); else if (!strcmp(argv[i], "--payload")) payload = atol(argv[i + 1]);
        else if (!strcmp(argv[i], "--json")) json_path = argv[i + 1]; else if (!strcmp(argv[i], "--label")) label = argv[i + 1]; }
    std::string tag = std::to_string(long(now() * 1000) % 1000000), rx = "Rx" + tag, tx = "Tx" + tag;
    int rc = 0;
    try {
        {   // ---------------- the memory
            Session ws(host, ram), rs(host, ram); Memory wm(ws), rm(rs); Result r; std::atomic<bool> done(false);
            std::vector<double> lat, acklat; std::string pad(size_t(payload > 0 ? payload : 0), 'x'); double started = wallclock();
            std::thread reader([&] {
                int64_t after = 0; long prev = -1;
                while (true) {
                    auto rows = rm.read("ChatServer", rx, "", after, 2.0); ++r.reads;
                    double tnow = wallclock();
                    for (auto& c : rows) { after = std::max<int64_t>(after, int64_t(c.id)); long n = long(c.bag["n"].n); lat.push_back(tnow - c.bag["t"].n);
                        if (n == prev) ++r.dup; else if (n < prev) ++r.disorder; else { if (prev >= 0) r.biggest_gap = std::max(r.biggest_gap, n - prev); ++r.got; prev = n; r.last_got = n; } }
                    if (done && (r.last_got == r.last_sent || rows.empty())) break;
                }
            });
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
            double t0 = now(); long i = 0;
            while (now() - t0 < seconds) {
                char tb[48]; snprintf(tb, sizeof tb, "%.6f", wallclock());
                std::string bag = "{\"n\":" + std::to_string(i) + ",\"t\":" + tb + (payload > 0 ? ",\"pad\":\"" + pad + "\"" : "") + "}";
                if (ack) { double a = now(); wm.write("ChatServer", rx, tx, bag); acklat.push_back(now() - a); } else wm.write_nowait("ChatServer", rx, tx, bag);
                r.last_sent = i; ++i;
                if (rate > 0) { double due = t0 + i / rate, left = due - now(); if (left > 0) std::this_thread::sleep_for(std::chrono::duration<double>(left)); }
            }
            r.secs = now() - t0;
            { char tb[48]; snprintf(tb, sizeof tb, "%.6f", wallclock()); wm.write("ChatServer", rx, tx, "{\"n\":" + std::to_string(i) + ",\"t\":" + tb + "}"); } r.last_sent = i; ++i;   // acknowledged: everything before it has landed
            r.sent = i; double drained = now() - t0; done = true; reader.join();
            printf("\n(%s; refused posts: %llu; far end had taken everything %.2f s after the writer stopped)", ack ? "every write acknowledged" : "writes NOT waited for",
                   (unsigned long long)ws.stats().posts_refused, drained - r.secs);
            report("THE MEMORY  (ChatServer.<rx>.<tx>)", r);
            double l50 = pct(lat, .5) * 1e3, l99 = pct(lat, .99) * 1e3, a50 = pct(acklat, .5) * 1e3, a99 = pct(acklat, .99) * 1e3, amin = acklat.empty() ? 0 : *std::min_element(acklat.begin(), acklat.end()) * 1e3;
            printf("  delivery latency: median %.2f ms  p99 %.2f ms", l50, l99); if (ack) printf("     write acknowledgement: min %.2f  median %.2f  p99 %.2f ms", amin, a50, a99); printf("\n");
            if (r.disorder || r.dup || r.last_got != r.last_sent) rc = 1;
            for (auto& c : rm.read("ChatServer", rx)) rm.remove(c.id);
            if (!json_path.empty()) { FILE* f = fopen(json_path.c_str(), "w"); if (!f) { fprintf(stderr, "cannot write %s\n", json_path.c_str()); return 1; }
                fprintf(f, "{\"tool\":\"flood\",\"label\":%s,\"host\":%s,\"port\":%d,\"started_epoch\":%.3f,\"mode\":\"latest\",\"ack\":%s,\"rate\":%.1f,\"payload_bytes\":%ld,\"seconds\":%.3f,"
                           "\"writes\":%ld,\"writes_per_s\":%.1f,\"reads\":%ld,\"reads_per_s\":%.1f,\"skipped\":%ld,\"out_of_order\":%ld,\"delivered_twice\":%ld,\"ended_on_last\":%s,"
                           "\"delivery_ms\":{\"p50\":%.3f,\"p99\":%.3f},\"ack_ms\":{\"min\":%.3f,\"p50\":%.3f,\"p99\":%.3f},\"pass\":%s}\n",
                        Json::quote(label).c_str(), Json::quote(host).c_str(), ram, started, ack ? "true" : "false", rate, payload, r.secs, r.sent, r.sent / r.secs, r.reads, r.reads / r.secs,
                        r.sent - r.got, r.disorder, r.dup, r.last_got == r.last_sent ? "true" : "false", l50, l99, amin, a50, a99, rc ? "false" : "true"); fclose(f); }
        }
        if (plane_port) { // ---------------- the fast plane
            Plane wp(host, plane_port), rp(host, plane_port); Result r; std::atomic<bool> done(false); std::string name = "flood" + tag + "/k";
            std::thread reader([&] {
                uint64_t after = 0; long prev = -1;
                while (true) {
                    auto rows = rp.read(name, after, 500); ++r.reads;
                    for (auto& row : rows) { long n = long(row.gen);
                        if (n == prev) ++r.dup; else if (n < prev) ++r.disorder; else { if (prev >= 0) r.biggest_gap = std::max(r.biggest_gap, n - prev); ++r.got; prev = n; r.last_got = n; } }
                    if (done && (r.last_got == r.last_sent || rows.empty())) break;
                }
            });
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
            double t0 = now(); long i = 1, shed = 0; std::string body(80, 'k');
            while (now() - t0 < seconds) { if (wp.publish(name, uint64_t(i), body)) r.last_sent = i; else ++shed; ++i; }
            r.sent = i - 1 - shed; r.secs = now() - t0; done = true; reader.join();
            report("THE FAST PLANE  (publish is send-or-drop, not acknowledged; 80-byte body)", r);
            printf("  shed by the publisher before leaving the machine: %ld\n", shed);
            if (r.disorder || r.dup || r.last_got != r.last_sent) rc = 1;
        }
    } catch (const std::exception& e) { fprintf(stderr, "FAILED: %s\n", e.what()); return 1; }
    printf("\n%s\n", rc ? "FAIL: order, duplication or the last value" : "PASS: skips only; never out of order, never twice, always ends on the last value");
    return rc;
}
