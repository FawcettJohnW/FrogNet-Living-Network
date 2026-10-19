// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_family_hooks.cpp -- [FAMILY_HOOKS_V1] oracle: readers of families, each on its own thread with one wake word,
// reading by position while writers write in parallel with no order between them.
//   every write to a family is read by each reader of that family exactly once (its rows are never rewritten here),
//   one writer's writes to one family are read in the order it wrote them,
//   a reader sleeps on its word (no timeout) and is woken by the writes -- no lost wake: every reader ends with all of them,
//   a reader reading two families on one word gets both,
//   a reader too slow for the window reads what it lost from the rows: nothing lost, nothing twice.
#include "ram_memory.hpp"
#include <cstdio>
#include <map>
#include <set>
#include <thread>
using namespace ramm;
static int fails = 0;
static void check(bool ok, const std::string& what) { std::printf("%s %s\n", ok ? "PASS" : "FAIL", what.c_str()); if (!ok) ++fails; }

int main() {
    Memory mem;
    const int W = 6, N = 20000, VARS = 3;
    // reader k: families listed; one word for all of them
    struct Reader { std::vector<Memory::Cursor> fam; std::vector<std::set<std::string>> got;
                    std::map<std::string, int> last; bool order_ok = true, lapped = false; size_t want = 0; uint64_t laps = 0, recovered = 0; };
    std::vector<Reader> rd(4);
    rd[0].fam = {mem.cursor("svc", "v0", "", -1)};
    rd[1].fam = {mem.cursor("svc", "", "", -1)};
    rd[2].fam = {mem.cursor("svc", "v1", "", -1), mem.cursor("svc", "v2", "", -1)};
    rd[3].fam = {mem.cursor("svc", "v0", "w3.0", -1)};   // one instance only
    auto in_var = [&](int v) { size_t k = 0; for (int n = 0; n < N; ++n) if (n % VARS == v) ++k; return k * size_t(W); };
    rd[0].want = in_var(0); rd[1].want = size_t(W) * N; rd[2].want = in_var(1) + in_var(2); rd[3].want = 1;
    std::atomic<int> finished{0};
    std::vector<std::thread> rt; std::vector<uint64_t> laps(rd.size()), recovered(rd.size());
    for (auto& r : rd) {
        r.got.resize(r.fam.size());
    }
    for (auto& rr : rd) rt.emplace_back([&mem, &rr, &finished, W] {
        WakeWord* w = mem.wake_word(); std::vector<Hook*> hs;
        for (auto& f : rr.fam) hs.push_back(Memory::hook(f, w));
        for (;;) {
            const uint32_t seen = w->n.load(std::memory_order_acquire);
            size_t total = 0;
            for (size_t k = 0; k < rr.fam.size(); ++k) {
                std::vector<Cell> out;
                mem.read(rr.fam[k], out);
                for (auto& c : out) {
                    rr.got[k].insert(c.variable + "/" + c.instance);
                    const int wr = c.bag[1] - '0', n = std::stoi(c.bag.substr(3));
                    (void)W; auto it = rr.last.emplace(c.variable + "." + std::to_string(wr), -1).first; if (n <= it->second) rr.order_ok = false; it->second = n;
                }
                total += rr.got[k].size();
            }
            if (total >= rr.want) break;
            w->n.wait(seen, std::memory_order_acquire);   // no timeout: only a write wakes it
        }
        for (auto& f : rr.fam) { rr.laps += f.laps; rr.recovered += f.recovered; }
        for (Hook* h : hs) Memory::unhook(h);
        Memory::release(w); finished++;
    });
    std::vector<std::thread> wt;
    for (int w = 0; w < W; ++w) wt.emplace_back([&mem, w] {
        for (int n = 0; n < N; ++n) {
            const std::string v = "v" + std::to_string(n % VARS);
            // bag: "w<k>:<n>" -- the reader parses it; instance unique per write, so no row is rewritten
            mem.write("svc", v, "w" + std::to_string(w) + "." + std::to_string(n), "w" + std::to_string(w) + ":" + std::to_string(n), 0);
            if ((n & 1023) == 0) std::this_thread::yield();
        }
    });
    for (auto& t : wt) t.join();
    // the readers must finish on their own: give them 20 s
    for (int k = 0; k < 2000 && finished.load() < int(rd.size()); ++k) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    const bool all_done = finished.load() == int(rd.size());
    check(all_done, "no lost wake: every reader woke for and read every write of its families (" + std::to_string(finished.load()) + "/4 finished)");
    if (!all_done) { for (auto& r : rd) { size_t t = 0; for (auto& g : r.got) t += g.size(); std::printf("  reader: %zu of %zu%s\n", t, r.want, r.lapped ? " LAPPED" : ""); }
                     std::printf("\nFAMILY-HOOKS FAIL\n"); std::fflush(stdout); std::_Exit(1); }
    for (auto& t : rt) t.join();
    const char* names[] = {"variable family", "service family", "two families on one word", "one instance of a family"};
    for (size_t k = 0; k < rd.size(); ++k) {
        size_t total = 0; for (auto& g : rd[k].got) total += g.size();
        check(total == rd[k].want, std::string(names[k]) + ": read " + std::to_string(total) + " of " + std::to_string(rd[k].want) + " exactly once (window lapped " + std::to_string(rd[k].laps) + " times, " + std::to_string(rd[k].recovered) + " read from the rows)");
        check(rd[k].order_ok, std::string(names[k]) + ": each writer's writes to a variable read in the order written");
    }
    // a reader that does not read while 3 windows' worth are written: the rows answer for what the window lost
    { Memory::Cursor c = mem.cursor("lap", "v", "", -1);
      for (uint64_t n = 0; n < 3 * WINDOW; ++n) mem.write("lap", "v", std::to_string(n), "x", 0);
      std::vector<Cell> out; mem.read(c, out);
      check(out.size() == 3 * WINDOW && c.laps == 1, "a reader lapped by the window reads all " + std::to_string(3 * WINDOW) + " (" + std::to_string(out.size()) + ", " + std::to_string(c.recovered) + " from the rows)"); }
    // a reader starting after an id: every row past it, then the window
    { Memory::Cursor c = mem.cursor("lap", "v", "", 0); std::vector<Cell> out; mem.read(c, out);
      check(out.size() == 3 * WINDOW, "a cursor after id 0 reads every row there (" + std::to_string(out.size()) + ")");
      mem.write("lap", "v", "new", "y", 0); out.clear(); mem.read(c, out);
      check(out.size() == 1 && out[0].instance == "new", "and then only what is written next"); }
    std::printf("\n%s: %d fail\n", fails ? "FAMILY-HOOKS FAIL" : "FAMILY-HOOKS PASS", fails);
    return fails ? 1 : 0;
}
