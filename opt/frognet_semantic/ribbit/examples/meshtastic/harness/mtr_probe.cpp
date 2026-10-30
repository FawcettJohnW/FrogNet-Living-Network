// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mtr_probe.cpp -- the firmware's view of the memory (mt_ribbit) driven from a script, no firmware involved.
//   mtr-probe hold  HOST:PORT ME SECONDS        hold every fact family; print each row as the main thread takes it
//   mtr-probe write HOST:PORT NODE TOPIC HEX    write one fact into NODE's own cell, flush, exit
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <atomic>

#include "mt_ribbit.h"

static std::atomic<int> wakes{0};
static void wake(void *) { wakes++; }
static void on_fact(const char *topic, uint32_t node, const uint8_t *b, size_t len, int gone, uint32_t t, int live, void *)
{
    printf("FACT topic=%s node=!%08x len=%zu gone=%d t=%u live=%d first=%02x\n", topic, node, len, gone, t, live, len ? b[0] : 0);
    fflush(stdout);
}
static void on_packet(const uint8_t *, size_t len, void *) { printf("PACKET len=%zu\n", len); fflush(stdout); }

int main(int argc, char **argv)
{
    if (argc < 5) { fprintf(stderr, "usage: see header\n"); return 2; }
    std::string mode = argv[1];
    uint32_t me = (uint32_t)strtoul(argv[3], nullptr, 16);
    if (mtr_start(argv[2], me, on_packet, nullptr) != 0) return 1;
    if (mode == "hold") {
        if (mtr_hold_facts(wake, nullptr) != 0) return 1;
        auto end = std::chrono::steady_clock::now() + std::chrono::seconds(atoi(argv[4]));
        size_t total = 0;
        while (std::chrono::steady_clock::now() < end) {   // a probe, not a participant: it checks every 100 ms
            total += mtr_take_facts(on_fact, nullptr);
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        printf("HOLD DONE wakes=%d rows=%zu\n", wakes.load(), total);
        return 0;
    }
    if (mode == "write" && argc >= 6) {
        std::string hex = argv[5];
        std::string bytes;
        for (size_t i = 0; i + 1 < hex.size(); i += 2) bytes += char(strtoul(hex.substr(i, 2).c_str(), nullptr, 16));
        mtr_write_fact(argv[4], me, reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size());
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        printf("WROTE %s !%08x len=%zu\n", argv[4], me, bytes.size());
        return 0;
    }
    return 2;
}
