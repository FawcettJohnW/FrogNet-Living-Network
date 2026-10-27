// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// lanbench.cpp -- ribbit-lanbench: the benchmark's coordination for tools/lan.sh, through the shared RAM, by held reads.
// [LAN_BENCH_ON_THE_RAM_V1] Nothing polls and nothing is called:
//   a node        announces itself (API++ announce: (lanbench.nodes, all, node␟<name>), its round trip in the bag) and
//                 holds a read on the coordinator's instruction cell (lanbench.ctl, go, now). Each instruction it
//                 reads it prints on stdout; lan.sh runs that redis-benchmark and writes the rate on stdin; the node
//                 writes it as its own result cell (lanbench.res, <seq>, <name>).
//   the coordinator finds the nodes (API++ capable, a held read) until N have announced and prints them; then for each
//                 instruction lan.sh writes on its stdin it writes the instruction cell and holds a read on that run's
//                 result family until every node's result is there, and prints them.
// Each side is one session (lan.sh keeps it as a coprocess). When a session ends its cells go with it: a coordinator
// that stops takes its instruction with it, and a node that stops is said to be gone.
//
//   ribbit-lanbench node <MEMORY host:port> <NAME> <RTT_SECONDS>
//       stdout: "go <seq> <arm> <test> <P> <n>" for each instruction, "end" at the end
//       stdin:  "res <seq> <req/s>" for each run
//   ribbit-lanbench coordinate <MEMORY host:port> <N>
//       stdout: "nodes <N> <slowest rtt> <name>..." once N nodes have announced
//       stdin:  "go <seq> <arm> <test> <P> <n>" -> stdout "res <seq> <got> <name> <req/s> ..." (once all N reported)
//               "end"                          -> the nodes are told; it exits
#include "ribbit_space.hpp"
#include "ribbit_pp.hpp"
#include <condition_variable>
#include <cstdio>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <unistd.h>

using namespace ribbit;
static const char* NODES = "lanbench.nodes";
static const char* CTL = "lanbench.ctl";
static const char* RES = "lanbench.res";

// what a held read was handed, and a wait for it: the reader's thread hands it over, this one waits on it
struct Handed { std::mutex m; std::condition_variable cv; };

static int node(const std::string& mem, const std::string& name, const std::string& rtt) {
    Space sp(mem, "redis");
    const long long rtt_us = (long long)(atof(rtt.c_str()) * 1e6);   // a bag's numbers are read as integers: microseconds
    pp::announce(sp, NODES, "node", name, "{\"rtt_us\":" + std::to_string(rtt_us) + "}");
    sp.flush(10);
    std::mutex out_m; std::string last;
    // the instruction cell: from its first write (a node may start after the coordinator wrote it)
    sp.watch(CTL, "go", 0, [&](std::vector<SpaceRow>& rows) {
        for (auto& r : rows) {
            if (r.gone) continue;                         // the coordinator stopped: its instruction went with it
            BagArrP a = r.fields(); if (!a) continue;
            const std::string spec = a->str("spec");
            if (spec.empty() || spec == last) continue;   // read again: already run
            last = spec;
            std::lock_guard<std::mutex> l(out_m);
            std::cout << spec << std::endl;               // "go <seq> ..." or "end": lan.sh runs it
            if (spec == "end") { std::cout.flush(); _exit(0); }
        }
    });
    std::string line;
    while (std::getline(std::cin, line)) {                // "res <seq> <req/s>": this node's result, its own cell
        std::istringstream in(line); std::string w, seq, rps; in >> w >> seq >> rps;
        if (w != "res" || seq.empty()) continue;
        sp.write(RES, seq, name, "{\"rps\":\"" + (rps.empty() ? std::string("0") : rps) + "\"}");
        sp.flush(10);
    }
    return 0;
}

static int coordinate(const std::string& mem, int want) {
    Space sp(mem, "redis");
    Handed h; std::map<std::string, double> nodes;        // name -> its round trip
    pp::capable(sp, NODES, [](const std::string& c) { return c == "node"; }, [&](const pp::Capable& c) {
        std::lock_guard<std::mutex> l(h.m);
        if (c.gone) { nodes.erase(c.who); std::cerr << "lanbench: node " << c.who << " is gone" << std::endl; }
        else nodes[c.who] = c.about ? double(c.about->num("rtt_us")) / 1e6 : 0.0;
        h.cv.notify_all();
    });
    {   std::unique_lock<std::mutex> l(h.m);
        h.cv.wait(l, [&] { return int(nodes.size()) >= want; });
        double slowest = 0; std::string names;
        for (auto& [n, r] : nodes) { slowest = std::max(slowest, r); names += " " + n; }
        std::cout << "nodes " << nodes.size() << " " << slowest << names << std::endl; }
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::string w, seq; in >> w;
        if (w == "end") {
            sp.write(CTL, "go", "now", "{\"spec\":\"end\"}"); sp.flush(10);
            std::cout << "ended" << std::endl;
            _exit(0);                                     // its reads are still held: the session ends with the process
        }
        if (w != "go") continue;
        in >> seq;
        // this run's results: a family of its own, read from now (nothing of it exists before the instruction). Its read
        // outlives this run (a node that leaves later is handed to it as gone): it owns what it fills
        struct Run { Handed h; std::map<std::string, std::string> got; };
        auto run = std::make_shared<Run>();
        sp.watch(RES, seq, (long long)sp.newest(RES, seq), [run](std::vector<SpaceRow>& rows) {
            std::lock_guard<std::mutex> l(run->h.m);
            for (auto& r : rows) if (!r.gone) if (BagArrP a = r.fields()) run->got[r.instance] = a->str("rps");
            run->h.cv.notify_all();
        });
        sp.write(CTL, "go", "now", "{\"spec\":\"" + line + "\"}"); sp.flush(10);
        std::unique_lock<std::mutex> l(run->h.m);
        run->h.cv.wait(l, [&] { std::lock_guard<std::mutex> g(h.m); return run->got.size() >= nodes.size(); });
        std::cout << "res " << seq << " " << run->got.size();
        for (auto& [n, r] : run->got) std::cout << " " << n << " " << r;
        std::cout << std::endl;
    }
    return 0;
}

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "";
    try {
        if (mode == "node" && argc == 5) return node(argv[2], argv[3], argv[4]);
        if (mode == "coordinate" && argc == 4) return coordinate(argv[2], atoi(argv[3]));
    } catch (const std::exception& x) { std::cerr << "lanbench: " << x.what() << std::endl; return 1; }
    std::cerr << "usage: ribbit-lanbench node <MEMORY host:port> <NAME> <RTT_SECONDS> | coordinate <MEMORY host:port> <N>" << std::endl;
    return 2;
}
