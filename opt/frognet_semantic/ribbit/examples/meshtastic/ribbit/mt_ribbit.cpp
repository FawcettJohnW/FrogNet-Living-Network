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
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>

#include "lf/primitives.hpp"
#include "ribbit_space.hpp"

namespace {
constexpr const char *SPACE = "mt";
constexpr uint64_t RING = 1024;
constexpr const char *VERSION = "mt-ribbit 0.10 [MT_PACKETS_ON_THE_RAM_V1] [MT_OWN_FACTS_V1] [MT_NODEDB_IS_A_HELD_VIEW_V1] [MT_DUMP_READS_CELLS_V1] [MT_FACT_CHANGES_TO_CLIENTS_V1] [MT_OTHER_NODES_ARE_CELLS_V1] [MT_OWN_FACTS_ON_CHANGE_V1] [MT_MESSAGES_BY_FAMILY_V1] [MT_READ_MARKS_V1] [MT_PEER_MARKS_V1] [MT_RADIO_EDGE_V1]";

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

constexpr const char *MARK = "mark";
std::string mark_instance(const std::string &family) { return g_me + "." + family; }

// a row of a message family, handed to the firmware if it is another node's packet
void hand(ribbit::SpaceRow &r, const std::string &topic, mtr_on_packet on, void *ctx)
{
    const std::string mine = g_me + ".";
    if (r.gone) return;                                       // a ring left with its writer: not a packet
    if (r.instance.compare(0, mine.size(), mine) == 0) return; // our own write
    auto f = r.fields();
    std::string p, bytes;
    if (!f || !(p = f->str("p")).size() || !unhex(p, bytes)) {
        fprintf(stderr, "[MT_RIBBIT] row %llu (%s,%s): no packet in the bag, skipped\n", (unsigned long long)r.id,
                topic.c_str(), r.instance.c_str());
        return;
    }
    on(reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size(), r.id, topic.c_str(), ctx);
}

uint64_t mark_of(const std::string &family)
{
    auto rows = g_space->read(SPACE, MARK, mark_instance(family));
    for (auto &r : rows)
        if (!r.gone && r.fields()) return (uint64_t)r.fields()->num("row");
    return 0;
}

// one held read on a family, from the node's mark on it (none: from now on); each row is handed to the firmware
void hold(const std::string &topic)
{
    {
        std::lock_guard<std::mutex> g(g_held_mu);
        if (!g_held.insert(topic).second) return;
    }
    uint64_t mark = mark_of(topic);
    long long from = mark ? (long long)mark : (long long)g_space->newest(SPACE, topic);
    g_space->watch(SPACE, topic, from, [topic](std::vector<ribbit::SpaceRow> &rows) {
        for (auto &r : rows) hand(r, topic, g_on, g_ctx);
    });
    fprintf(stderr, "[MT_RIBBIT] holding (%s,%s) past id %lld (%s)\n", SPACE, topic.c_str(), from,
            mark ? "the node's mark" : "no mark: from now on");
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

extern "C" void mtr_hold_dm_for(uint32_t node)
{
    if (!g_space) return;
    try {
        hold(dm_topic(node));
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] cannot hold dm for !%08x: %s\n", node, e.what());
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

// the subject of a fact cell: "!S" (the node's own cell) or "!F.!S" (written by bridge F about S, which is not on the
// memory [MT_RADIO_EDGE_V1]: the cell is F's -- "cells are owned by whoever writes them" -- and S is in its name).
// Either way the fact is about S.
bool node_of(const std::string &instance, uint32_t &n)
{
    size_t dot = instance.find('.');
    std::string subj = dot == std::string::npos ? instance : instance.substr(dot + 1);
    if (subj.size() != 9 || subj[0] != '!') return false;
    char *end = nullptr;
    n = (uint32_t)strtoul(subj.c_str() + 1, &end, 16);
    return end && *end == 0;
}
std::string bridged_name(uint32_t by, uint32_t subject) { return node_name(by) + "." + node_name(subject); }
} // namespace

extern "C" void mtr_write_fact(const char *topic, uint32_t node, const uint8_t *payload, size_t len)
{
    if (!g_space) return;
    // t: when the owner wrote it (its own clock, unix seconds). A reader's last_heard is this, never a time it keeps.
    g_space->write(FACTS, topic, node_name(node),
                   "{\"p\":\"" + hex(payload, len) + "\",\"t\":" + std::to_string((long long)time(nullptr)) + "}");
}

extern "C" void mtr_write_fact_for(const char *topic, uint32_t by, uint32_t subject, const uint8_t *payload, size_t len)
{
    if (!g_space) return;
    g_space->write(FACTS, topic, bridged_name(by, subject),
                   "{\"p\":\"" + hex(payload, len) + "\",\"t\":" + std::to_string((long long)time(nullptr)) + "}");
}

extern "C" void mtr_write_fact_kept(const char *topic, uint32_t node, const uint8_t *payload, size_t len)
{
    if (!g_space) return;
    {
        std::lock_guard<std::mutex> g(g_held_mu);
        if (g_linger.insert(std::string("mtf/") + topic).second) g_space->linger(FACTS, topic, ribbit::Space::STAYS);
    }
    mtr_write_fact(topic, node, payload, len);
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
        // one fact per (topic, subject): a node's own cell first; else the newest of the bridges' cells about it
        struct Pick { ribbit::SpaceRow *r; bool own; double t; };
        std::map<std::pair<std::string, uint32_t>, Pick> picks;
        for (auto &r : rows) {
            if (r.gone || (prefix && r.topic.compare(0, want.size(), want) != 0)) continue;
            uint32_t node = 0;
            auto f = r.fields();
            if (!node_of(r.instance, node) || !f) continue;
            if (f->str("p").empty()) continue; // cleared by its owner: no fact
            bool own = r.instance.find('.') == std::string::npos;
            double t = f->num("t");
            auto key = std::make_pair(r.topic, node);
            auto it = picks.find(key);
            if (it == picks.end() || (own && !it->second.own) || (own == it->second.own && t > it->second.t))
                picks[key] = Pick{&r, own, t};
        }
        for (auto &kv : picks) {
            auto &r = *kv.second.r;
            auto f = r.fields();
            std::string bytes;
            if (!unhex(f->str("p"), bytes)) {
                fprintf(stderr, "[MT_RIBBIT] cell %llu (%s,%s) unreadable, skipped\n", (unsigned long long)r.id,
                        r.topic.c_str(), r.instance.c_str());
                continue;
            }
            on(r.topic.c_str(), kv.first.second, reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size(),
               (uint32_t)f->num("t"), ctx);
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
        auto take = [&](ribbit::SpaceRow &r) -> int {
            auto f = r.fields();
            std::string p, bytes;
            if (f && (p = f->str("p")).empty()) return 0; // cleared by its owner: no fact
            if (!f || !unhex(p, bytes) || bytes.size() > cap) {
                fprintf(stderr, "[MT_RIBBIT] cell (%s,%s) unreadable, not used\n", topic, r.instance.c_str());
                return 0;
            }
            memcpy(out, bytes.data(), bytes.size());
            *len = bytes.size();
            if (t) *t = (uint32_t)f->num("t");
            return 1;
        };
        auto rows = g_space->read(FACTS, topic, node_name(node));
        for (auto &r : rows)
            if (!r.gone) return take(r);
        // not its own cell: a bridge's cell about it ("!F.!node"), the newest when several bridges hear it
        const std::string suffix = "." + node_name(node);
        auto all = g_space->read(FACTS, topic);
        ribbit::SpaceRow *best = nullptr;
        double bt = -1;
        for (auto &r : all) {
            if (r.gone || r.instance.size() <= suffix.size() ||
                r.instance.compare(r.instance.size() - suffix.size(), suffix.size(), suffix) != 0)
                continue;
            auto f = r.fields();
            double tt = f ? f->num("t") : 0;
            if (tt >= bt) { bt = tt; best = &r; }
        }
        if (best) return take(*best);
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

extern "C" void mtr_mark(const char *family, uint64_t row)
{
    if (!g_space || !family) return;
    {
        std::lock_guard<std::mutex> g(g_held_mu);
        if (g_linger.insert(MARK).second) g_space->linger(SPACE, MARK, ribbit::Space::STAYS);
    }
    g_space->write(SPACE, MARK, mark_instance(family), "{\"row\":" + std::to_string((unsigned long long)row) + "}");
}

extern "C" uint64_t mtr_mark_read(const char *family)
{
    if (!g_space || !family) return 0;
    try {
        return mark_of(family);
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] read of the mark on %s failed: %s\n", family, e.what());
        return 0;
    }
}

extern "C" size_t mtr_read_past_mark(const char *family, mtr_on_packet on, void *ctx)
{
    if (!g_space || !family) return 0;
    try {
        uint64_t mark = mark_of(family);
        auto rows = g_space->read(SPACE, family, "", (long long)mark);
        size_t n = 0;
        for (auto &r : rows) {
            if (r.gone || r.id <= mark) continue;
            hand(r, family, on, ctx);
            n++;
        }
        return n;
    } catch (const std::exception &e) {
        fprintf(stderr, "[MT_RIBBIT] read of %s past the mark failed: %s\n", family, e.what());
        return 0;
    }
}

extern "C" size_t mtr_families(void (*on)(const char *family, void *ctx), void *ctx)
{
    std::vector<std::string> fams;
    {
        std::lock_guard<std::mutex> g(g_held_mu);
        fams.assign(g_held.begin(), g_held.end());
    }
    for (auto &f : fams) on(f.c_str(), ctx);
    return fams.size();
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
