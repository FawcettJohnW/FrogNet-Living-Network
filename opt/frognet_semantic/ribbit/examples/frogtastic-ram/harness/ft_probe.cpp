// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only
//
// ft_probe.cpp -- [FROGTASTIC_SHM_TRANSPORT_V1] every mt_ribbit.h call, between two nodes, no firmware involved.
// Built twice by harness/ft-transport.sh: linked to libmtribbit.a (the calls made here), and linked to the firmware's
// shim (the calls carried to FrogTastic-Bridge). Node a's output must be the same both ways.
//
//   ft-probe a HOST:PORT     node !0000000a: holds, writes, reads, marks, sends; prints what every call returned
//   ft-probe b HOST:PORT     node !0000000b: sends to a at the times a waits for, prints what a sent it
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "mt_ribbit.h"

namespace {
constexpr uint32_t A = 0xa, B = 0xb, BRIDGED = 0x77;
constexpr uint8_t CH = 8;
std::mutex mu;
std::vector<std::string> held; // what the held read handed over, in arrival order
std::map<std::string, uint64_t> last_row; // per family
int wakes = 0;

std::string hexs(const uint8_t *b, size_t n)
{
    std::string s;
    char x[3];
    for (size_t i = 0; i < n; i++) snprintf(x, sizeof x, "%02x", b[i]), s += x;
    return s;
}
void sleep_ms(int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }

void on_packet(const uint8_t *b, size_t n, uint64_t row, const char *family, void *)
{
    std::lock_guard<std::mutex> g(mu);
    held.push_back(std::string(family) + " " + hexs(b, n));
    last_row[family] = row;
}
void on_wake(void *) { std::lock_guard<std::mutex> g(mu); wakes++; }

void print_held(const char *when)
{
    std::lock_guard<std::mutex> g(mu);
    std::sort(held.begin(), held.end());
    printf("%s: held read handed %zu\n", when, held.size());
    for (auto &h : held) printf("  HELD %s\n", h.c_str());
    held.clear();
}

int node_a(const char *where)
{
    printf("start=%d\n", mtr_start(where, A, on_packet, nullptr));
    printf("version has a line: %d\n", mtr_version() && *mtr_version());
    printf("front version has a line: %d\n", mtr_front_version() && *mtr_front_version());
    mtr_hold_channel(CH);
    mtr_hold_dm_for(BRIDGED);

    const uint8_t user[] = {0x0a, 0x01, 0x41}, pos[] = {0x10, 0x20}, fav[] = {0x01}, mine[] = {0x55};
    mtr_write_fact("user", A, user, sizeof user);
    mtr_write_fact_kept("fav", A, fav, sizeof fav);
    mtr_write_fact_for("pos", A, BRIDGED, pos, sizeof pos);
    mtr_write_fact("status", A, mine, sizeof mine);
    mtr_clear_fact("status", A);
    sleep_ms(400);

    printf("hold_facts=%d\n", mtr_hold_facts(on_wake, nullptr));
    sleep_ms(400);
    std::vector<std::string> facts;
    size_t n = mtr_take_facts(
        [](const char *topic, uint32_t node, const uint8_t *b, size_t len, int gone, uint32_t t, int live, void *ctx) {
            auto *v = static_cast<std::vector<std::string> *>(ctx);
            char line[256];
            snprintf(line, sizeof line, "FACT %s !%08x %s gone=%d t>0=%d live=%d", topic, node, hexs(b, len).c_str(), gone,
                     t > 0, live);
            v->push_back(line);
        },
        &facts);
    std::sort(facts.begin(), facts.end());
    printf("take_facts=%zu (woken: %d)\n", n, wakes > 0);
    for (auto &f : facts) printf("  %s\n", f.c_str());

    std::vector<std::string> cells;
    n = mtr_read_facts("user", [](const char *topic, uint32_t node, const uint8_t *b, size_t len, uint32_t t, void *ctx) {
        char line[256];
        snprintf(line, sizeof line, "CELL %s !%08x %s t>0=%d", topic, node, hexs(b, len).c_str(), t > 0);
        static_cast<std::vector<std::string> *>(ctx)->push_back(line);
    }, &cells);
    std::sort(cells.begin(), cells.end());
    printf("read_facts(user)=%zu\n", n);
    for (auto &c : cells) printf("  %s\n", c.c_str());

    uint8_t out[64];
    size_t len = 0;
    uint32_t t = 0;
    int got = mtr_read_cell("pos", BRIDGED, out, sizeof out, &len, &t);
    printf("read_cell(pos,!%08x)=%d %s t>0=%d\n", BRIDGED, got, got ? hexs(out, len).c_str() : "", t > 0);
    got = mtr_read_cell("status", A, out, sizeof out, &len, &t);
    printf("read_cell(status,!%08x)=%d (cleared)\n", A, got);
    got = mtr_read_cell("user", 0x99, out, sizeof out, &len, &t);
    printf("read_cell(user,!00000099)=%d (no such cell)\n", got);

    // b sends: one dm to a, one broadcast on CH, one dm to the bridged node
    printf("ready for b\n");
    fflush(stdout);
    sleep_ms(2500);
    print_held("after b's first sends");

    const std::string fam = "dm|!00000077"; // the bridged node's family: b's third send and the two after the mark
    uint64_t row;
    {
        std::lock_guard<std::mutex> g(mu);
        row = last_row[fam];
    }
    printf("a row arrived on %s: %d\n", fam.c_str(), row > 0);
    mtr_mark(fam.c_str(), row);
    sleep_ms(300);
    printf("mark_read(%s) is the row marked: %d\n", fam.c_str(), mtr_mark_read(fam.c_str()) == row);

    // b sends two more on that family (at about 5.5 s); the held read hands them, and read_past_mark finds them
    sleep_ms(4000);
    print_held("after b's second sends");
    std::vector<std::string> past;
    n = mtr_read_past_mark(fam.c_str(), [](const uint8_t *b, size_t len, uint64_t, const char *family, void *ctx) {
        static_cast<std::vector<std::string> *>(ctx)->push_back(std::string(family) + " " + hexs(b, len));
    }, &past);
    printf("read_past_mark(%s)=%zu\n", fam.c_str(), n);
    for (auto &p : past) printf("  PAST %s\n", p.c_str());

    std::vector<std::string> fams;
    n = mtr_families([](const char *f, void *ctx) { static_cast<std::vector<std::string> *>(ctx)->push_back(f); }, &fams);
    std::sort(fams.begin(), fams.end());
    printf("families=%zu\n", n);
    for (auto &f : fams) printf("  FAMILY %s\n", f.c_str());

    const uint8_t dm[] = {0xd1, 0x01}, bc[] = {0xbc, 0x02};
    mtr_send(dm, sizeof dm, B, 0);
    mtr_send(bc, sizeof bc, 0xffffffffu, CH);
    sleep_ms(1500);
    printf("done\n");
    return 0;
}

int node_b(const char *where)
{
    if (mtr_start(where, B, on_packet, nullptr) != 0) return 1;
    mtr_hold_channel(CH);
    sleep_ms(2500); // a is writing and reading facts
    const uint8_t p1[] = {0xb1}, p2[] = {0xb2}, p3[] = {0xb3}, p4[] = {0xb4}, p5[] = {0xb5};
    mtr_send(p1, 1, A, 0);
    mtr_send(p2, 1, 0xffffffffu, CH);
    mtr_send(p3, 1, BRIDGED, 0);
    sleep_ms(3000); // a marks (at about 3.4 s)
    mtr_send(p4, 1, BRIDGED, 0);
    mtr_send(p5, 1, BRIDGED, 0);
    sleep_ms(4000); // a sends to b (at about 7.7 s)
    print_held("b");
    return 0;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: ft-probe a|b HOST:PORT\n");
        return 2;
    }
    setvbuf(stdout, nullptr, _IOLBF, 0);
    return !strcmp(argv[1], "a") ? node_a(argv[2]) : node_b(argv[2]);
}
