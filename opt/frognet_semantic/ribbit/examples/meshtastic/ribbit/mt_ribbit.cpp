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
#include <cstring>
#include <ctime>
#include <memory>
#include <mutex>
#include <set>
#include <string>

#include "lf/primitives.hpp"
#include "ribbit_space.hpp"

namespace {
constexpr const char *SPACE = "mt";
constexpr uint64_t RING = 1024;
constexpr const char *VERSION = "mt-ribbit 0.7 [MT_PACKETS_ON_THE_RAM_V1] [MT_OWN_FACTS_V1] [MT_NODEDB_IS_A_HELD_VIEW_V1] [MT_DUMP_READS_CELLS_V1] [MT_FACT_CHANGES_TO_CLIENTS_V1] [MT_OTHER_NODES_ARE_CELLS_V1] [MT_OWN_FACTS_ON_CHANGE_V1] [MT_MESSAGES_BY_FAMILY_V1]";

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
#ifndef MTR_FRONT_VERSION
#define MTR_FRONT_VERSION "(VERSION not built in)"
#endif
extern "C" const char *mtr_front_version(void) { return MTR_FRONT_VERSION; }

namespace {
mtr_on_packet g_on = nullptr;
void *g_ctx = nullptr;
std::mutex g_held_mu;
std::set<std::string> g_held;   // families this node holds a read on
std::set<std::string> g_linger; // families this node has said STAYS for

std::string dm_topic(uint32_t to) { return "dm|" + node_name(to); }
std::string msg_topic(uint8_t ch)
{
    char b[12];
    snprintf(b, sizeof b, "msg|%u", (unsigned)ch);
    return b;
}

// one held read on a family, from now on; each row another node wrote is handed to the firmware
void hold(const std::string &topic)
{
    {
        std::lock_guard<std::mutex> g(g_held_mu);
        if (!g_held.insert(topic).second) return;
    }
    const std::string mine = g_me + ".";
    long long from = (long long)g_space->newest(SPACE, topic);
    g_space->watch(SPACE, topic, from, [mine, topic](std::vector<ribbit::SpaceRow> &rows) {
        for (auto &r : rows) {
            if (r.gone) continue;                                       // a ring left with its writer: not a packet
            if (r.instance.compare(0, mine.size(), mine) == 0) continue; // our own write
            auto f = r.fields();
            std::string p, bytes;
            if (!f || !(p = f->str("p")).size() || !unhex(p, bytes)) {
                fprintf(stderr, "[MT_RIBBIT] row %llu (%s,%s): no packet in the bag, skipped\n", (unsigned long long)r.id,
                        topic.c_str(), r.instance.c_str());
                continue;
            }
            g_on(reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size(), g_ctx);
        }
    });
    fprintf(stderr, "[MT_RIBBIT] holding (%s,%s) past id %lld\n", SPACE, topic.c_str(), from);
}
} // namespace

extern "C" int mtr_start(const char *where, uint32_t me, mtr_on_packet on, void *ctx)
{
    try {
        g_me = node_name(me);
        g_on = on;
        g_ctx = ctx;
        g_space.reset(new ribbit::Space(where, "meshtastic"));
        hold(dm_topic(me)); // what is addressed to this node
        fprintf(stderr, "[MT_RIBBIT] %s: node %s on the memory at %s\n", VERSION, g_me.c_str(), where);
        return 0;
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] %s: node %s cannot use the memory at %s: %s\n", VERSION, g_me.c_str(), where,
                e.what());
        g_space.reset();
        return -1;
    }
}

extern "C" void mtr_hold_channel(uint8_t hash)
{
    if (!g_space) return;
    try {
        hold(msg_topic(hash));
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] cannot hold channel %u: %s\n", (unsigned)hash, e.what());
    }
}

namespace {
constexpr const char *FACTS = "mtf";
struct Fact {
    Fact *next = nullptr;
    std::string topic, bytes;
    uint32_t node = 0, t = 0;
    bool gone = false, live = false;
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
    // t: when the owner wrote it (its own clock, unix seconds). A reader's last_heard is this, never a time it keeps.
    g_space->write(FACTS, topic, node_name(node),
                   "{\"p\":\"" + hex(payload, len) + "\",\"t\":" + std::to_string((long long)time(nullptr)) + "}");
}

extern "C" void mtr_clear_fact(const char *topic, uint32_t node)
{
    if (!g_space) return;
    g_space->write(FACTS, topic, node_name(node), "{\"t\":" + std::to_string((long long)time(nullptr)) + "}");
}

extern "C" size_t mtr_read_facts(const char *topic, mtr_on_cell on, void *ctx)
{
    if (!g_space) return 0;
    // topic "x.*": every topic starting "x."; the memory is asked for the whole space and the rest is not handed on
    std::string want = topic ? topic : "";
    bool prefix = want.size() && want.back() == '*';
    if (prefix) want.pop_back();
    size_t n = 0;
    try {
        auto rows = g_space->read(FACTS, prefix ? "" : want);
        for (auto &r : rows) {
            if (r.gone || (prefix && r.topic.compare(0, want.size(), want) != 0)) continue;
            uint32_t node = 0;
            auto f = r.fields();
            std::string p, bytes;
            if (f && (p = f->str("p")).empty()) continue; // cleared by its owner: no fact
            if (!node_of(r.instance, node) || !f || !unhex(p, bytes)) {
                fprintf(stderr, "[MT_RIBBIT] cell %llu (%s,%s) unreadable, skipped\n", (unsigned long long)r.id,
                        r.topic.c_str(), r.instance.c_str());
                continue;
            }
            on(r.topic.c_str(), node, reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size(), (uint32_t)f->num("t"),
               ctx);
            n++;
        }
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] read of (%s,%s) failed: %s\n", FACTS, topic ? topic : "", e.what());
    }
    return n;
}

extern "C" int mtr_read_cell(const char *topic, uint32_t node, uint8_t *out, size_t cap, size_t *len, uint32_t *t)
{
    if (!g_space || !topic) return 0;
    try {
        auto rows = g_space->read(FACTS, topic, node_name(node));
        for (auto &r : rows) {
            if (r.gone) continue;
            auto f = r.fields();
            std::string p, bytes;
            if (f && (p = f->str("p")).empty()) return 0; // cleared by its owner: no fact
            if (!f || !unhex(p, bytes) || bytes.size() > cap) {
                fprintf(stderr, "[MT_RIBBIT] cell (%s,%s) unreadable, not used\n", topic, node_name(node).c_str());
                return 0;
            }
            memcpy(out, bytes.data(), bytes.size());
            *len = bytes.size();
            if (t) *t = (uint32_t)f->num("t");
            return 1;
        }
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] read of (%s,%s,%s) failed: %s\n", FACTS, topic, node_name(node).c_str(), e.what());
    }
    return 0;
}

extern "C" int mtr_hold_facts(void (*wake)(void *ctx), void *ctx)
{
    if (!g_space) return -1;
    try {
        // read, then hold: what is there now is handed over first, then every change past the newest id it saw
        auto now = g_space->read(FACTS, "");
        long long past = 0;
        auto add = [wake, ctx](ribbit::SpaceRow &r, bool live) {
            auto *f = new Fact;
            f->topic = r.topic;
            f->gone = r.gone || (r.fields() && r.fields()->str("p").empty()); // cleared by its owner: gone
            f->live = live;
            if (!r.gone && r.fields()) f->t = (uint32_t)r.fields()->num("t");
            std::string p;
            if (!node_of(r.instance, f->node) ||
                (!f->gone && (!(r.fields()) || (p = r.fields()->str("p")).empty() || !unhex(p, f->bytes)))) {
                fprintf(stderr, "[MT_RIBBIT] fact row %llu (%s,%s) unreadable, skipped\n", (unsigned long long)r.id,
                        r.topic.c_str(), r.instance.c_str());
                delete f;
                return;
            }
            if (g_facts.push(f) && wake) wake(ctx);
        };
        for (auto &r : now) {
            add(r, false);
            if ((long long)r.id > past) past = (long long)r.id;
        }
        g_space->watch(FACTS, "", past, [add](std::vector<ribbit::SpaceRow> &rows) {
            for (auto &r : rows) add(r, true);
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
        on(f->topic.c_str(), f->node, reinterpret_cast<const uint8_t *>(f->bytes.data()), f->bytes.size(), f->gone, f->t,
           f->live, ctx);
        delete f;
    }
    return got.size();
}

extern "C" void mtr_send(const uint8_t *bytes, size_t len, uint32_t to, uint8_t channel)
{
    if (!g_space) return;
    std::string topic = to == 0xffffffffu ? msg_topic(channel) : dm_topic(to);
    {
        // the ring outlives this session: said once per family, before its first write
        std::lock_guard<std::mutex> g(g_held_mu);
        if (g_linger.insert(topic).second) g_space->linger(SPACE, topic, ribbit::Space::STAYS);
    }
    uint64_t n = g_n.fetch_add(1) + 1;
    g_space->write(SPACE, topic, ribbit::Space::slot(g_me, n, RING), "{\"p\":\"" + hex(bytes, len) + "\"}");
}
