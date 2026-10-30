// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mt_ribbit.cpp -- [MT_PACKETS_ON_THE_RAM_V1] Meshtastic packets on the shared RAM.
//
// Truth: a MeshPacket a node sends. Owner: that node. Address: (mt, pkt, <node>.<n mod RING>) -- the node's own ring.
// Distinct state: one cell per packet, every reader sees each once, in write order; a ring that moves past a slow
// reader is a miss the reader is told of, never a wait for it. Readers: every node, by one held read on (mt, pkt).
// Gone with its writer: when a node's session ends its ring goes, and every reader is handed those rows as gone.
#include "mt_ribbit.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>

#include "lf/primitives.hpp"
#include "ribbit_space.hpp"

namespace {
constexpr const char *SPACE = "mt", *TOPIC = "pkt";
constexpr uint64_t RING = 1024;
constexpr const char *VERSION = "mt-ribbit 0.2 [MT_PACKETS_ON_THE_RAM_V1] [MT_OWN_FACTS_V1] [MT_NODEDB_IS_A_HELD_VIEW_V1]";

std::unique_ptr<ribbit::Space> g_space;
std::string g_me;
std::atomic<uint64_t> g_n{0};

std::string hex(const uint8_t *b, size_t n)
{
    static const char *d = "0123456789abcdef";
    std::string s(n * 2, '0');
    for (size_t i = 0; i < n; i++) {
        s[2 * i] = d[b[i] >> 4];
        s[2 * i + 1] = d[b[i] & 15];
    }
    return s;
}

int nib(char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }

bool unhex(const std::string &s, std::string &out)
{
    if (s.size() % 2) return false;
    out.resize(s.size() / 2);
    for (size_t i = 0; i < out.size(); i++) {
        int h = nib(s[2 * i]), l = nib(s[2 * i + 1]);
        if (h < 0 || l < 0) return false;
        out[i] = char(h << 4 | l);
    }
    return true;
}

std::string node_name(uint32_t n)
{
    char b[16];
    snprintf(b, sizeof b, "!%08x", n);
    return b;
}
} // namespace

extern "C" const char *mtr_version(void) { return VERSION; }

extern "C" int mtr_start(const char *where, uint32_t me, mtr_on_packet on, void *ctx)
{
    try {
        g_me = node_name(me);
        g_space.reset(new ribbit::Space(where, "meshtastic"));
        const std::string mine = g_me + ".";
        long long from = (long long)g_space->newest(SPACE, TOPIC);
        g_space->watch(SPACE, TOPIC, from, [on, ctx, mine](std::vector<ribbit::SpaceRow> &rows) {
            for (auto &r : rows) {
                if (r.gone) continue;                                       // a writer's session ended: not a packet
                if (r.instance.compare(0, mine.size(), mine) == 0) continue; // our own write
                auto f = r.fields();
                std::string p, bytes;
                if (!f || !(p = f->str("p")).size() || !unhex(p, bytes)) {
                    fprintf(stderr, "[MT_RIBBIT] row %llu %s: no packet in the bag, skipped\n", (unsigned long long)r.id,
                            r.instance.c_str());
                    continue;
                }
                on(reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size(), ctx);
            }
        });
        fprintf(stderr, "[MT_RIBBIT] %s: node %s on the memory at %s, held read on (%s,%s) past id %lld\n", VERSION,
                g_me.c_str(), where, SPACE, TOPIC, from);
        return 0;
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] %s: node %s cannot use the memory at %s: %s\n", VERSION, g_me.c_str(), where,
                e.what());
        g_space.reset();
        return -1;
    }
}

namespace {
constexpr const char *FACTS = "mtf";
struct Fact {
    Fact *next = nullptr;
    std::string topic, bytes;
    uint32_t node = 0;
    bool gone = false;
};
ribbit::lf::TakeStack<Fact> g_facts;

bool node_of(const std::string &instance, uint32_t &n)
{
    if (instance.size() != 9 || instance[0] != '!') return false;
    char *end = nullptr;
    n = (uint32_t)strtoul(instance.c_str() + 1, &end, 16);
    return end && *end == 0;
}
} // namespace

extern "C" void mtr_write_fact(const char *topic, uint32_t node, const uint8_t *payload, size_t len)
{
    if (!g_space) return;
    g_space->write(FACTS, topic, node_name(node), "{\"p\":\"" + hex(payload, len) + "\"}");
}

extern "C" int mtr_hold_facts(void (*wake)(void *ctx), void *ctx)
{
    if (!g_space) return -1;
    try {
        // read, then hold: what is there now is handed over first, then every change past the newest id it saw
        auto now = g_space->read(FACTS, "");
        long long past = 0;
        auto add = [wake, ctx](ribbit::SpaceRow &r) {
            auto *f = new Fact;
            f->topic = r.topic;
            f->gone = r.gone;
            std::string p;
            if (!node_of(r.instance, f->node) ||
                (!r.gone && (!(r.fields()) || (p = r.fields()->str("p")).empty() || !unhex(p, f->bytes)))) {
                fprintf(stderr, "[MT_RIBBIT] fact row %llu (%s,%s) unreadable, skipped\n", (unsigned long long)r.id,
                        r.topic.c_str(), r.instance.c_str());
                delete f;
                return;
            }
            if (g_facts.push(f) && wake) wake(ctx);
        };
        for (auto &r : now) {
            add(r);
            if ((long long)r.id > past) past = (long long)r.id;
        }
        g_space->watch(FACTS, "", past, [add](std::vector<ribbit::SpaceRow> &rows) {
            for (auto &r : rows) add(r);
        });
        fprintf(stderr, "[MT_RIBBIT] holding every fact family (%s,*): %zu rows now, past id %lld\n", FACTS, now.size(),
                past);
        return 0;
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] cannot hold the fact families: %s\n", e.what());
        return -1;
    }
}

extern "C" size_t mtr_take_facts(mtr_on_fact on, void *ctx)
{
    auto got = g_facts.take();
    for (Fact *f : got) {
        on(f->topic.c_str(), f->node, reinterpret_cast<const uint8_t *>(f->bytes.data()), f->bytes.size(), f->gone, ctx);
        delete f;
    }
    return got.size();
}

extern "C" void mtr_send(const uint8_t *bytes, size_t len)
{
    if (!g_space) return;
    uint64_t n = g_n.fetch_add(1) + 1;
    g_space->write(SPACE, TOPIC, ribbit::Space::slot(g_me, n, RING), "{\"p\":\"" + hex(bytes, len) + "\"}");
}
