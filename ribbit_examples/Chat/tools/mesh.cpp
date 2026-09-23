// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mesh -- N clients, every one cross-posting to every other one at the same
// time, every one pulling its own buffer with the blocking read.
//
//   mesh HOST PORT [--clients 10] [--seconds 5] [--every | --latest] [--ack]
//                  [--rate PER_PAIR_PER_SEC] [--server-pid PID] [--json FILE] [--label TEXT]
//
// --every   (default) PURE CONTENTION FOR THE MEMORY. Every message is its own
//           cell, ChatServer.<to>.<from>:<n>, so nothing is ever replaced and the
//           memory must store and hand back every single one. Each reader checks
//           every sequence number from every writer: exactly once, in order, none
//           missing. ONE DROPPED SEQUENCE NUMBER ANYWHERE IS A FAILURE. Readers
//           remove what they have consumed (not waited for), so deletes contend too.
// --latest  one cell per pair, ChatServer.<to>.<from>; writing replaces, so a slow
//           reader skips by design. Skips are reported, not failed.
// Writers wait for each acknowledgement with --ack; otherwise they do not wait.
//
// Every message carries the writer's wall clock, so delivery latency (written ->
// in the reader's hands) is measured per message. All clients run in this one
// process, so one clock is used for both ends.
#include "frogram.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <thread>
#include <unistd.h>
using namespace frogram;
static double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static double wallclock() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static double proc_cpu(int pid) { std::ifstream f("/proc/" + std::to_string(pid) + "/stat"); std::string s; std::getline(f, s); size_t p = s.rfind(')'); if (p == std::string::npos) return -1;
    std::istringstream in(s.substr(p + 2)); std::string tok; double ut = 0, st = 0; for (int i = 3; i <= 15 && (in >> tok); ++i) { if (i == 14) ut = atof(tok.c_str()); if (i == 15) st = atof(tok.c_str()); } return (ut + st) / sysconf(_SC_CLK_TCK); }
static long proc_field(int pid, const char* key) { std::ifstream f("/proc/" + std::to_string(pid) + "/status"); std::string l; while (std::getline(f, l)) if (!l.compare(0, strlen(key), key)) return atol(l.c_str() + strlen(key) + 1); return -1; }
static double pct(std::vector<double>& v, double p) { if (v.empty()) return 0; size_t k = size_t(p * (v.size() - 1)); std::nth_element(v.begin(), v.begin() + k, v.end()); return v[k]; }

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: mesh HOST PORT [--clients N] [--seconds S] [--every|--latest] [--ack] [--rate R] [--server-pid PID] [--json FILE] [--label TEXT]\n"); return 2; }
    std::string host = argv[1], json_path, label; int port = atoi(argv[2]), N = 10, spid = 0; double seconds = 5, rate = 0; bool ack = false, every = true;
    for (int i = 3; i < argc; ++i) { std::string a = argv[i];
        if (a == "--ack") ack = true; else if (a == "--every") every = true; else if (a == "--latest") every = false;
        else if (i + 1 < argc) { if (a == "--clients") N = atoi(argv[++i]); else if (a == "--seconds") seconds = atof(argv[++i]); else if (a == "--rate") rate = atof(argv[++i]);
            else if (a == "--server-pid") spid = atoi(argv[++i]); else if (a == "--json") json_path = argv[++i]; else if (a == "--label") label = argv[++i]; } }
    std::string tag = std::to_string(long(wallclock() * 1000) % 100000);
    std::vector<std::string> name(N); for (int i = 0; i < N; ++i) name[i] = "c" + tag + "_" + std::to_string(i);
    std::vector<std::vector<long>> sent(N, std::vector<long>(N, 0)), got(N, std::vector<long>(N, 0)), last(N, std::vector<long>(N, -1));
    std::vector<long> disorder(N, 0), dup(N, 0), gaps(N, 0), reads(N, 0); std::vector<std::vector<double>> lat(N), acklat(N);
    std::atomic<bool> done(false); std::atomic<int> failed(0);
    std::vector<std::unique_ptr<Session>> ws(N), rs(N);
    try { for (int i = 0; i < N; ++i) { ws[i].reset(new Session(host, port)); rs[i].reset(new Session(host, port)); } }
    catch (const std::exception& e) { fprintf(stderr, "cannot connect %d clients: %s\n", N, e.what()); return 1; }

    std::vector<std::thread> th;
    for (int j = 0; j < N; ++j) th.emplace_back([&, j] {          // reader j
        try { Memory m(*rs[j]); int64_t after = 0; std::vector<long> prev(N, -1); int idle = 0;
            for (;;) { auto rows = m.read("ChatServer", name[j], "", after, 1.0); ++reads[j]; double tnow = wallclock();
                for (auto& c : rows) { after = std::max<int64_t>(after, int64_t(c.id));
                    size_t us = c.instance.rfind('_'), colon = c.instance.find(':', us); int i = atoi(c.instance.c_str() + us + 1); long n = long(c.bag["n"].n);
                    (void)colon; lat[j].push_back(tnow - c.bag["t"].n);
                    if (n == prev[i]) ++dup[j]; else if (n < prev[i]) ++disorder[j]; else { if (n != prev[i] + 1) gaps[j] += n - prev[i] - 1; prev[i] = n; ++got[i][j]; last[i][j] = n; }
                    if (every) rs[j]->post("DELETE", "/ram.php?op=remove&id=" + std::to_string(c.id)); }
                if (done) { bool all = true; for (int i = 0; i < N; ++i) if (i != j && last[i][j] != sent[i][j] - 1) all = false; idle = rows.empty() ? idle + 1 : 0; if (all || idle >= 3) break; } }
        } catch (const std::exception& e) { fprintf(stderr, "reader %d: %s\n", j, e.what()); ++failed; } });
    std::this_thread::sleep_for(std::chrono::milliseconds(400));
    double cpu0 = spid ? proc_cpu(spid) : 0, t0 = now(), started = wallclock();
    std::vector<std::thread> wt;
    for (int i = 0; i < N; ++i) wt.emplace_back([&, i] {           // writer i
        try { Memory m(*ws[i]); long k = 0; double start = now(); char buf[96];
            auto put = [&](int j, bool wait) { snprintf(buf, sizeof buf, "{\"n\":%ld,\"t\":%.6f}", sent[i][j], wallclock());
                std::string inst = every ? name[i] + ":" + std::to_string(sent[i][j]) : name[i];
                if (wait) { double a = now(); m.write("ChatServer", name[j], inst, buf); acklat[i].push_back(now() - a); } else m.write_nowait("ChatServer", name[j], inst, buf);
                ++sent[i][j]; };
            while (now() - start < seconds) { for (int j = 0; j < N; ++j) if (j != i) { put(j, ack); ++k; }
                if (rate > 0) { double due = start + (double(k) / (N - 1)) / rate, left = due - now(); if (left > 0) std::this_thread::sleep_for(std::chrono::duration<double>(left)); } }
            for (int j = 0; j < N; ++j) if (j != i) put(j, true);                 // acknowledged: everything before it has landed
        } catch (const std::exception& e) { fprintf(stderr, "writer %d: %s\n", i, e.what()); ++failed; } });
    for (auto& t : wt) t.join();
    double wall = now() - t0, cpu1 = spid ? proc_cpu(spid) : 0; long rss = spid ? proc_field(spid, "VmRSS:") : -1, thr = spid ? proc_field(spid, "Threads:") : -1;
    done = true; for (auto& t : th) t.join(); double drain = now() - t0 - wall;

    long S = 0, G = 0, R = 0, D = 0, U = 0, GAP = 0, bad_last = 0, wmin = 1L << 60, wmax = 0, refused = 0; std::vector<double> L, A;
    for (int i = 0; i < N; ++i) { long wi = 0; for (int j = 0; j < N; ++j) if (i != j) { S += sent[i][j]; G += got[i][j]; wi += sent[i][j]; if (last[i][j] != sent[i][j] - 1) ++bad_last; } wmin = std::min(wmin, wi); wmax = std::max(wmax, wi);
        refused += long(ws[i]->stats().posts_refused + rs[i]->stats().posts_refused); A.insert(A.end(), acklat[i].begin(), acklat[i].end()); }
    for (int j = 0; j < N; ++j) { R += reads[j]; D += disorder[j]; U += dup[j]; GAP += gaps[j]; L.insert(L.end(), lat[j].begin(), lat[j].end()); }
    double l50 = pct(L, .5) * 1e3, l99 = pct(L, .99) * 1e3, lmax = L.empty() ? 0 : *std::max_element(L.begin(), L.end()) * 1e3, a50 = pct(A, .5) * 1e3, a99 = pct(A, .99) * 1e3;
    bool ok = !failed && !D && !U && !bad_last && !refused && (!every || (GAP == 0 && S == G));
    printf("%s: %d clients, %d pairs, %s, %s%s, %.2f s (+%.2f s until the last reader had everything)\n", every ? "EVERY (contention; nothing may be dropped)" : "LATEST (one cell per pair; skipping is by design)",
           N, N * (N - 1), ack ? "acknowledged writes" : "writes not waited for", rate > 0 ? "paced " : "flat out", rate > 0 ? (std::to_string(int(rate)) + "/s per pair").c_str() : "", wall, drain);
    printf("  writes    %ld = %.0f/s aggregate, %.0f/s per client (least %ld, most %ld)\n", S, S / wall, S / wall / N, wmin, wmax);
    printf("  reads     %ld = %.0f/s aggregate; %.2f cells per read\n", R, R / wall, R ? double(G) / R : 0.0);
    printf("  DROPPED   %ld sequence numbers of %ld%s\n", S - G, S, every ? ((S - G) ? "   <-- FAIL" : "") : "   (skipped by design)");
    printf("  all %d pairs: out of order %ld   delivered twice %ld   gaps seen %ld   pairs not ending on the last value %ld   refused %ld\n", N * (N - 1), D, U, GAP, bad_last, refused);
    printf("  delivery latency (written -> in the reader's hands): median %.2f ms   p99 %.2f ms   max %.2f ms\n", l50, l99, lmax);
    if (ack) printf("  write acknowledgement latency: median %.2f ms   p99 %.2f ms\n", a50, a99);
    if (spid) printf("  server    %.0f%% of one core; %.1f us CPU per write; RSS %.1f MiB; %ld threads\n", 100 * (cpu1 - cpu0) / wall, S ? 1e6 * (cpu1 - cpu0) / S : 0.0, rss / 1024.0, thr);
    printf("  %s\n", ok ? "PASS" : "FAIL");
    if (!json_path.empty()) { FILE* f = fopen(json_path.c_str(), "w"); if (!f) { fprintf(stderr, "cannot write %s\n", json_path.c_str()); return 1; }
        fprintf(f, "{\"tool\":\"mesh\",\"label\":%s,\"host\":%s,\"port\":%d,\"started_epoch\":%.3f,\"mode\":\"%s\",\"ack\":%s,\"rate_per_pair\":%.1f,\"clients\":%d,\"pairs\":%d,"
                   "\"seconds\":%.3f,\"drain_seconds\":%.3f,\"writes\":%ld,\"writes_per_s\":%.1f,\"reads\":%ld,\"reads_per_s\":%.1f,\"cells_per_read\":%.3f,\"dropped\":%ld,\"out_of_order\":%ld,"
                   "\"delivered_twice\":%ld,\"gaps\":%ld,\"pairs_not_ending_on_last\":%ld,\"refused\":%ld,\"delivery_ms\":{\"p50\":%.3f,\"p99\":%.3f,\"max\":%.3f},\"ack_ms\":{\"p50\":%.3f,\"p99\":%.3f},"
                   "\"server\":{\"pid\":%d,\"cpu_fraction\":%.4f,\"cpu_us_per_write\":%.2f,\"rss_mib\":%.1f,\"threads\":%ld},\"pass\":%s}\n",
                Json::quote(label).c_str(), Json::quote(host).c_str(), port, started, every ? "every" : "latest", ack ? "true" : "false", rate, N, N * (N - 1), wall, drain, S, S / wall, R, R / wall,
                R ? double(G) / R : 0.0, S - G, D, U, GAP, bad_last, refused, l50, l99, lmax, a50, a99, spid, spid ? (cpu1 - cpu0) / wall : 0.0, (spid && S) ? 1e6 * (cpu1 - cpu0) / S : 0.0, rss / 1024.0, thr, ok ? "true" : "false");
        fclose(f); }
    return ok ? 0 : 1;
}
