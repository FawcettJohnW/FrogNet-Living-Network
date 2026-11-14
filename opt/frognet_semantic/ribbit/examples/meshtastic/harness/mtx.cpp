// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mtx.cpp -- coordination cells for the distributed side-by-side, on the same memory the nodes use, space "mtx".
// Participants and the coordinator never talk to each other: each writes its own cells and holds a read on the family
// it waits for. Cells written here linger (STAYS): the tool exits at once and the cell stays for the readers.
//   mtx put  HOST:PORT TOPIC INSTANCE JSON        write (mtx, TOPIC, INSTANCE) = JSON, then exit
//   mtx wait HOST:PORT TOPIC COUNT SECONDS        hold (mtx, TOPIC, *) from what is there now; print each row as
//                                                 "ROW <topic> <instance> <json>"; exit 0 when COUNT distinct instances
//                                                 have been seen (COUNT 0: print what is there and exit), 1 at SECONDS
//   mtx wait HOST:PORT TOPIC INSTANCE SECONDS     the same, for one instance (a name, not a number)
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <set>
#include <string>
#include <thread>

#include "ribbit_space.hpp"

int main(int argc, char **argv)
{
    if (argc < 5) { fprintf(stderr, "usage: see header\n"); return 2; }
    std::string mode = argv[1], where = argv[2], topic = argv[3];
    try {
        ribbit::Space sp(where, "meshtastic");
        if (mode == "put" && argc >= 6) {
            sp.linger("mtx", topic, ribbit::Space::STAYS);
            sp.write("mtx", topic, argv[4], argv[5]);
            sp.flush(5.0);
            printf("PUT (mtx,%s,%s)\n", topic.c_str(), argv[4]);
            return 0;
        }
        if (mode == "wait" && argc >= 6) {
            std::string want = argv[4];
            char *end = nullptr;
            long count = strtol(want.c_str(), &end, 10);
            bool byCount = end && *end == 0;
            int seconds = atoi(argv[5]);
            std::mutex mu;
            std::condition_variable cv;
            std::set<std::string> seen;
            bool done = false;
            sp.watch("mtx", topic, 0, [&](std::vector<ribbit::SpaceRow> &rows) {
                std::lock_guard<std::mutex> g(mu);
                for (auto &r : rows) {
                    if (r.gone) continue;
                    printf("ROW %s %s %s\n", r.topic.c_str(), r.instance.c_str(), r.text().c_str());
                    fflush(stdout);
                    seen.insert(r.instance);
                    if (!byCount && r.instance == want) done = true;
                }
                if (byCount && (long)seen.size() >= count) done = true;
                cv.notify_all();
            });
            std::unique_lock<std::mutex> lk(mu);
            if (!cv.wait_for(lk, std::chrono::seconds(seconds), [&] { return done; })) {
                fprintf(stderr, "mtx: (mtx,%s) %s not reached in %d s (%zu seen)\n", topic.c_str(), want.c_str(), seconds, seen.size());
                return 1;
            }
            return 0;
        }
    } catch (const std::exception &e) {
        fprintf(stderr, "mtx: %s\n", e.what());
        return 1;
    }
    return 2;
}
