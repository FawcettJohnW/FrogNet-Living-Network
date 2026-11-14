// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-3.0-only
//
// frogtastic_shim.cpp -- [FROGTASTIC_SHM_TRANSPORT_V1] the firmware's side of the memory: every mt_ribbit.h call,
// carried to FrogTastic-RAM (a separate process) through the shared segment in frogtastic_shm.h. The firmware links
// this file and none of the Ribbit platform; FrogTastic-RAM makes the calls into the memory and writes back the answers.
//
// Calls that return nothing are written and the caller goes on. Calls that return something wait for their END on the
// replies ring; their items are handed to the caller's callback on the caller's thread, as before. What the held reads
// hand over arrives on the events ring and is handed over on this file's events thread -- the held read's thread, as
// before.
//
// mtr_start starts this node's FrogTastic-RAM (an exec: its own process), segment /frogtastic-ram-<node>, and waits for
// its "serving" line; $FROGTASTIC_RAM_BIN names the program (default: frogtastic-ram on PATH). $FROGTASTIC_SHM names a
// segment a FrogTastic-RAM already serves instead. FrogTastic-RAM leaves the memory when this firmware exits. When it
// cannot be started or reached, mtr_start says so and returns -1; when it goes away, this file says so once and every
// call does nothing, as the calls did with no memory.
#if defined(ARCH_PORTDUINO) && defined(__linux__)
#include "mt_ribbit.h"
#include "frogtastic_shm.h"

#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <spawn.h>
#include <sys/wait.h>
#include <string>
#include <sys/mman.h>
#include <thread>

using namespace frogtastic;

namespace {
constexpr const char *VERSION = "frogtastic-shim 1.0 [FROGTASTIC_SHM_TRANSPORT_V1] layout 1";

Segment *g_seg = nullptr;
std::mutex g_calls_mu; // the calls ring has one writer process; its threads take turns
std::mutex g_ask_mu;   // one answered call at a time, so its replies are the next on the replies ring
uint64_t g_next_id = 0;
std::atomic<bool> g_gone{false};

mtr_on_packet g_on = nullptr;
void *g_on_ctx = nullptr;
std::atomic<void (*)(void *)> g_wake{nullptr};
std::atomic<void *> g_wake_ctx{nullptr};
std::string g_version = VERSION;
std::string g_front_version = "(FrogTastic-RAM not reached)";

bool bridge_alive()
{
    int32_t pid = g_seg->bridge_pid.load(std::memory_order_acquire);
    bool alive = pid > 0 && (kill(pid, 0) == 0 || errno == EPERM);
    if (!alive && !g_gone.exchange(true))
        fprintf(stderr, "[FROGTASTIC_SHIM] FrogTastic-RAM (pid %d) is gone: this node is off the memory\n", pid);
    return alive;
}

extern "C" char **environ;

// start this node's FrogTastic-RAM and wait for its "serving" line (a blocking read: nothing is polled)
bool spawn_bridge(const std::string &name)
{
    const char *bin = getenv("FROGTASTIC_RAM_BIN") ? getenv("FROGTASTIC_RAM_BIN") : "frogtastic-ram";
    int out[2];
    if (pipe(out) != 0) return false;
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, out[1], 1);
    posix_spawn_file_actions_addclose(&fa, out[0]);
    posix_spawn_file_actions_addclose(&fa, out[1]);
    std::string shm = name;
    char *argv[] = {const_cast<char *>("frogtastic-ram"), const_cast<char *>("--shm"), &shm[0], nullptr};
    pid_t pid = 0;
    int rc = posix_spawnp(&pid, bin, &fa, nullptr, argv, environ);
    posix_spawn_file_actions_destroy(&fa);
    close(out[1]);
    if (rc != 0) {
        close(out[0]);
        fprintf(stderr, "[FROGTASTIC_SHIM] %s: cannot start %s: %s\n", VERSION, bin, strerror(rc));
        return false;
    }
    std::string line;
    char c;
    while (line.size() < 256 && read(out[0], &c, 1) == 1 && c != '\n') line += c;
    close(out[0]);
    if (line != "serving " + name) {
        fprintf(stderr, "[FROGTASTIC_SHIM] %s: %s (pid %d) did not start serving %s%s%s\n", VERSION, bin, pid,
                name.c_str(), line.empty() ? "" : ": said ", line.c_str());
        return false;
    }
    // reap it when it exits, so it does not linger as a zombie of this process
    std::thread([pid] { waitpid(pid, nullptr, 0); }).detach();
    fprintf(stderr, "[FROGTASTIC_SHIM] %s: started %s (pid %d) on %s\n", VERSION, bin, pid, name.c_str());
    return true;
}

bool attach(uint32_t me)
{
    if (g_seg) return true;
    std::string name;
    if (getenv(NAME_ENV)) {
        name = getenv(NAME_ENV);
    } else {
        char b[40];
        snprintf(b, sizeof b, "/frogtastic-ram-%08x", me);
        name = b;
        if (!spawn_bridge(name)) return false;
    }
    int fd = shm_open(name.c_str(), O_RDWR, 0);
    if (fd < 0) {
        fprintf(stderr, "[FROGTASTIC_SHIM] %s: no FrogTastic-RAM segment %s: %s\n", VERSION, name.c_str(),
                strerror(errno));
        return false;
    }
    void *m = mmap(nullptr, sizeof(Segment), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) {
        fprintf(stderr, "[FROGTASTIC_SHIM] %s: cannot map %s: %s\n", VERSION, name.c_str(), strerror(errno));
        return false;
    }
    auto *seg = static_cast<Segment *>(m);
    if (seg->magic != MAGIC || seg->layout != LAYOUT) {
        fprintf(stderr, "[FROGTASTIC_SHIM] %s: %s is layout %u, this firmware speaks layout %u\n", VERSION, name.c_str(),
                seg->magic == MAGIC ? seg->layout : 0, LAYOUT);
        munmap(m, sizeof(Segment));
        return false;
    }
    g_seg = seg;
    // what an earlier firmware left unread is not for this one
    uint64_t ev = drop_all(g_seg->events), re = drop_all(g_seg->replies);
    g_seg->firmware_pid.store(getpid(), std::memory_order_release);
    fprintf(stderr, "[FROGTASTIC_SHIM] %s: attached to %s (FrogTastic-RAM pid %d); %llu+%llu bytes left by an earlier "
                    "firmware dropped\n",
            VERSION, name.c_str(), g_seg->bridge_pid.load(), (unsigned long long)ev, (unsigned long long)re);
    return true;
}

bool usable() { return g_seg && !g_gone.load(std::memory_order_acquire); }

uint64_t cast(uint16_t op, const std::string &payload)
{
    if (!usable()) return 0;
    std::lock_guard<std::mutex> g(g_calls_mu);
    uint64_t id = ++g_next_id;
    if (!put(g_seg->calls, op, id, payload, bridge_alive)) return 0;
    return id;
}

// an answered call: items to on_item, then END; returns END's ret and leaves the rest of END in `rest`
template <class F> int64_t ask(uint16_t op, const std::string &payload, F on_item, std::string *rest = nullptr)
{
    if (!usable()) return 0;
    std::lock_guard<std::mutex> g(g_ask_mu);
    uint64_t id = cast(op, payload);
    if (!id) return 0;
    Header h{};
    std::string p;
    while (take(g_seg->replies, h, p, bridge_alive)) {
        if (h.id != id) continue; // the answer to a call an earlier firmware made
        if (h.op == END) {
            In in(p);
            int64_t ret = in.i64();
            if (rest) *rest = p.substr(in.at);
            return ret;
        }
        on_item(h.op, p);
    }
    return 0;
}
auto no_items = [](uint16_t, const std::string &) {};

void events()
{
    Header h{};
    std::string p;
    while (take(g_seg->events, h, p, bridge_alive)) {
        In in(p);
        if (h.op == EV_PACKET) {
            std::string b = in.bytes();
            uint64_t row = in.u64();
            std::string family = in.bytes();
            if (in.ok && g_on) g_on(reinterpret_cast<const uint8_t *>(b.data()), b.size(), row, family.c_str(), g_on_ctx);
        } else if (h.op == EV_FACTS_WAKE) {
            if (auto w = g_wake.load()) w(g_wake_ctx.load());
        }
    }
}
} // namespace

extern "C" const char *mtr_version(void) { return g_version.c_str(); }
extern "C" const char *mtr_front_version(void) { return g_front_version.c_str(); }

extern "C" int mtr_start(const char *where, uint32_t me, mtr_on_packet on, void *ctx)
{
    if (!attach(me)) return -1;
    g_on = on;
    g_on_ctx = ctx;
    std::string rest;
    int64_t ret = ask(START, Out().str(where).u32(me).b, no_items, &rest);
    In in(rest);
    std::string bridge = in.bytes(), front = in.bytes();
    if (in.ok) {
        g_version = std::string(VERSION) + "; " + bridge;
        g_front_version = front;
    }
    if (ret != 0 || !in.ok) {
        fprintf(stderr, "[FROGTASTIC_SHIM] node !%08x: FrogTastic-RAM did not put it on the memory at %s\n", me,
                where ? where : "");
        return -1;
    }
    static std::once_flag once;
    std::call_once(once, [] { std::thread(events).detach(); });
    return 0;
}

extern "C" void mtr_send(const uint8_t *bytes, size_t len, uint32_t to, uint8_t channel)
{
    cast(SEND, Out().bytes(bytes, len).u32(to).u8(channel).b);
}
extern "C" void mtr_hold_channel(uint8_t hash) { cast(HOLD_CHANNEL, Out().u8(hash).b); }
extern "C" void mtr_hold_dm_for(uint32_t node) { cast(HOLD_DM_FOR, Out().u32(node).b); }
extern "C" void mtr_mark(const char *family, uint64_t row)
{
    if (family) cast(MARK, Out().str(family).u64(row).b);
}
extern "C" uint64_t mtr_mark_read(const char *family)
{
    return family ? uint64_t(ask(MARK_READ, Out().str(family).b, no_items)) : 0;
}
extern "C" size_t mtr_read_past_mark(const char *family, mtr_on_packet on, void *ctx)
{
    if (!family) return 0;
    return size_t(ask(READ_PAST_MARK, Out().str(family).b, [&](uint16_t op, const std::string &p) {
        In in(p);
        std::string b = in.bytes();
        uint64_t row = in.u64();
        std::string fam = in.bytes();
        if (op == EV_PACKET && in.ok) on(reinterpret_cast<const uint8_t *>(b.data()), b.size(), row, fam.c_str(), ctx);
    }));
}
extern "C" size_t mtr_families(void (*on)(const char *family, void *ctx), void *ctx)
{
    return size_t(ask(FAMILIES, std::string(), [&](uint16_t op, const std::string &p) {
        In in(p);
        std::string fam = in.bytes();
        if (op == IT_FAMILY && in.ok) on(fam.c_str(), ctx);
    }));
}

extern "C" void mtr_write_fact(const char *topic, uint32_t node, const uint8_t *payload, size_t len)
{
    if (topic) cast(WRITE_FACT, Out().str(topic).u32(node).bytes(payload, len).b);
}
extern "C" void mtr_write_fact_for(const char *topic, uint32_t by, uint32_t subject, const uint8_t *payload, size_t len)
{
    if (topic) cast(WRITE_FACT_FOR, Out().str(topic).u32(by).u32(subject).bytes(payload, len).b);
}
extern "C" void mtr_write_fact_kept(const char *topic, uint32_t node, const uint8_t *payload, size_t len)
{
    if (topic) cast(WRITE_FACT_KEPT, Out().str(topic).u32(node).bytes(payload, len).b);
}
extern "C" void mtr_clear_fact(const char *topic, uint32_t node)
{
    if (topic) cast(CLEAR_FACT, Out().str(topic).u32(node).b);
}

extern "C" int mtr_hold_facts(void (*wake)(void *ctx), void *ctx)
{
    if (!usable()) return -1;
    g_wake_ctx.store(ctx);
    g_wake.store(wake);
    return int(ask(HOLD_FACTS, std::string(), no_items));
}
extern "C" size_t mtr_take_facts(mtr_on_fact on, void *ctx)
{
    return size_t(ask(TAKE_FACTS, std::string(), [&](uint16_t op, const std::string &p) {
        In in(p);
        std::string topic = in.bytes();
        uint32_t node = in.u32();
        std::string b = in.bytes();
        int gone = in.u8();
        uint32_t t = in.u32();
        int live = in.u8();
        if (op == IT_FACT && in.ok)
            on(topic.c_str(), node, reinterpret_cast<const uint8_t *>(b.data()), b.size(), gone, t, live, ctx);
    }));
}
extern "C" size_t mtr_read_facts(const char *topic, mtr_on_cell on, void *ctx)
{
    return size_t(ask(READ_FACTS, Out().str(topic ? topic : "").b, [&](uint16_t op, const std::string &p) {
        In in(p);
        std::string tp = in.bytes();
        uint32_t node = in.u32();
        std::string b = in.bytes();
        uint32_t t = in.u32();
        if (op == IT_CELL && in.ok) on(tp.c_str(), node, reinterpret_cast<const uint8_t *>(b.data()), b.size(), t, ctx);
    }));
}
extern "C" int mtr_read_cell(const char *topic, uint32_t node, uint8_t *out, size_t cap, size_t *len, uint32_t *t)
{
    if (!topic) return 0;
    std::string rest;
    int64_t ret = ask(READ_CELL, Out().str(topic).u32(node).u64(cap).b, no_items, &rest);
    In in(rest);
    std::string b = in.bytes();
    uint32_t tt = in.u32();
    if (ret != 1 || !in.ok || b.size() > cap) return 0;
    memcpy(out, b.data(), b.size());
    *len = b.size();
    if (t) *t = tt;
    return 1;
}
#endif
