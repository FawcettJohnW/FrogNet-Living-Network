// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogtastic_ram.cpp -- FrogTastic-RAM [FROGTASTIC_SHM_TRANSPORT_V1]: the Meshtastic firmware's bridge onto the memory.
//
// Its own process. It creates the shared segment (frogtastic_shm.h), takes each mt_ribbit.h call the firmware writes
// into it, makes that call into mt_ribbit.cpp (linked here, with the Ribbit platform under it), and writes back what the
// call returns and what the held reads hand over. The firmware links none of the platform; this process links none of
// the firmware.
//
//     frogtastic-ram [--shm NAME] [--version]
//
// The firmware starts it (front/src/mesh/ribbit/frogtastic_shim.cpp): one per node, segment /frogtastic-ram-<node>.
// NAME defaults to $FROGTASTIC_SHM, else /frogtastic-ram. The firmware's MESHTASTIC_RIBBIT names the memory, and this
// process goes on it when the firmware's first call (START) says so. It leaves the memory when that firmware exits
// (a pidfd held on it), as the node did when the firmware linked mt_ribbit itself: the node's session ends with it.
// Its one line on stdout, "serving NAME", says it is ready; everything else it says goes to stderr.
#include "frogtastic_shm.h"
#include "mt_ribbit.h"

#include <csignal>
#include <poll.h>
#include <thread>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>

using namespace frogtastic;

namespace {
constexpr const char *VERSION = "frogtastic-ram 1.0 [FROGTASTIC_SHM_TRANSPORT_V1] layout 1";

Segment *g_seg = nullptr;
std::mutex g_events_mu; // the events ring has one writer process; its threads (the held reads) take turns
bool g_started = false;
uint32_t g_me = 0;
uint64_t g_dropped = 0;
std::string g_name;

// [FROGTASTIC_SHM_TRANSPORT_V1] the node's session is the firmware's: when it exits, this process leaves the memory
bool watch_firmware()
{
    int32_t pid = g_seg->firmware_pid.load(std::memory_order_acquire);
    int fd = pid > 0 ? int(syscall(SYS_pidfd_open, pid, 0)) : -1;
    if (fd < 0) {
        fprintf(stderr, "[FROGTASTIC_RAM] cannot hold the firmware (pid %d): %s\n", pid, strerror(errno));
        return false;
    }
    std::thread([fd, pid] {
        pollfd p{fd, POLLIN, 0};
        while (poll(&p, 1, -1) < 0 && errno == EINTR) {}
        fprintf(stderr, "[FROGTASTIC_RAM] the firmware (pid %d) exited: !%08x leaves the memory\n", pid, g_me);
        shm_unlink(g_name.c_str());
        _exit(0);
    }).detach();
    return true;
}

bool firmware_alive()
{
    int32_t pid = g_seg->firmware_pid.load(std::memory_order_acquire);
    return pid > 0 && (kill(pid, 0) == 0 || errno == EPERM);
}

// an event for the firmware; when no firmware is there to read it, it is dropped and counted (the packet is still in
// its sender's ring; the node's mark says what a client has been handed)
void event(uint16_t op, const std::string &payload)
{
    std::lock_guard<std::mutex> g(g_events_mu);
    if (!firmware_alive() || !put(g_seg->events, op, 0, payload, firmware_alive)) {
        if (++g_dropped == 1 || g_dropped % 1000 == 0)
            fprintf(stderr, "[FROGTASTIC_RAM] no firmware reading: %llu events dropped\n", (unsigned long long)g_dropped);
    }
}

void on_packet(const uint8_t *bytes, size_t len, uint64_t row, const char *family, void *)
{
    event(EV_PACKET, Out().bytes(bytes, len).u64(row).str(family).b);
}

void on_wake(void *) { event(EV_FACTS_WAKE, std::string()); }

// replies: written only by the loop below, so no lock
uint64_t g_call = 0;
void reply(uint16_t op, const std::string &payload)
{
    if (!put(g_seg->replies, op, g_call, payload, firmware_alive))
        fprintf(stderr, "[FROGTASTIC_RAM] reply to call %llu not delivered: the firmware is gone\n",
                (unsigned long long)g_call);
}
void end(int64_t ret, const Out &more = Out()) { reply(END, Out().i64(ret).b + more.b); }

void item_packet(const uint8_t *bytes, size_t len, uint64_t row, const char *family, void *)
{
    reply(EV_PACKET, Out().bytes(bytes, len).u64(row).str(family).b);
}
void item_family(const char *family, void *) { reply(IT_FAMILY, Out().str(family).b); }
void item_fact(const char *topic, uint32_t node, const uint8_t *p, size_t len, int gone, uint32_t t, int live, void *)
{
    reply(IT_FACT, Out().str(topic).u32(node).bytes(p, len).u8(uint8_t(gone)).u32(t).u8(uint8_t(live)).b);
}
void item_cell(const char *topic, uint32_t node, const uint8_t *p, size_t len, uint32_t t, void *)
{
    reply(IT_CELL, Out().str(topic).u32(node).bytes(p, len).u32(t).b);
}

void serve(const Header &h, const std::string &payload)
{
    In in(payload);
    g_call = h.id;
    switch (h.op) {
    case START: {
        std::string where = in.bytes();
        uint32_t me = in.u32();
        int ret;
        if (!in.ok) {
            ret = -1;
        } else if (g_started) {
            // the firmware restarted: its held reads are still running here; another node number is another bridge's
            ret = me == g_me ? 0 : -1;
            if (ret)
                fprintf(stderr, "[FROGTASTIC_RAM] START as !%08x refused: this bridge is on the memory as !%08x\n", me,
                        g_me);
        } else if (!watch_firmware()) {
            ret = -1;
        } else {
            ret = mtr_start(where.c_str(), me, on_packet, nullptr);
            if (ret == 0) g_started = true, g_me = me;
        }
        fprintf(stderr, "[FROGTASTIC_RAM] START !%08x at %s: %s\n", me, where.c_str(), ret == 0 ? "on the memory" : "refused");
        end(ret, Out().str(mtr_version()).str(mtr_front_version()));
        return;
    }
    case SEND: {
        std::string b = in.bytes();
        uint32_t to = in.u32();
        uint8_t ch = in.u8();
        if (in.ok) mtr_send(reinterpret_cast<const uint8_t *>(b.data()), b.size(), to, ch);
        break;
    }
    case HOLD_CHANNEL: { uint8_t hash = in.u8(); if (in.ok) mtr_hold_channel(hash); break; }
    case HOLD_DM_FOR: { uint32_t n = in.u32(); if (in.ok) mtr_hold_dm_for(n); break; }
    case MARK: {
        std::string f = in.bytes();
        uint64_t row = in.u64();
        if (in.ok) mtr_mark(f.c_str(), row);
        break;
    }
    case MARK_READ: { std::string f = in.bytes(); end(in.ok ? int64_t(mtr_mark_read(f.c_str())) : 0); return; }
    case READ_PAST_MARK: {
        std::string f = in.bytes();
        end(in.ok ? int64_t(mtr_read_past_mark(f.c_str(), item_packet, nullptr)) : 0);
        return;
    }
    case FAMILIES: end(int64_t(mtr_families(item_family, nullptr))); return;
    case WRITE_FACT:
    case WRITE_FACT_KEPT: {
        std::string topic = in.bytes();
        uint32_t node = in.u32();
        std::string p = in.bytes();
        if (!in.ok) break;
        auto *b = reinterpret_cast<const uint8_t *>(p.data());
        if (h.op == WRITE_FACT) mtr_write_fact(topic.c_str(), node, b, p.size());
        else mtr_write_fact_kept(topic.c_str(), node, b, p.size());
        break;
    }
    case WRITE_FACT_FOR: {
        std::string topic = in.bytes();
        uint32_t by = in.u32(), subject = in.u32();
        std::string p = in.bytes();
        if (in.ok) mtr_write_fact_for(topic.c_str(), by, subject, reinterpret_cast<const uint8_t *>(p.data()), p.size());
        break;
    }
    case CLEAR_FACT: {
        std::string topic = in.bytes();
        uint32_t node = in.u32();
        if (in.ok) mtr_clear_fact(topic.c_str(), node);
        break;
    }
    case HOLD_FACTS: end(mtr_hold_facts(on_wake, nullptr)); return;
    case TAKE_FACTS: end(int64_t(mtr_take_facts(item_fact, nullptr))); return;
    case READ_FACTS: {
        std::string topic = in.bytes();
        end(in.ok ? int64_t(mtr_read_facts(topic.c_str(), item_cell, nullptr)) : 0);
        return;
    }
    case READ_CELL: {
        std::string topic = in.bytes();
        uint32_t node = in.u32();
        uint64_t cap = in.u64();
        if (!in.ok || cap > MAX_RECORD) cap = MAX_RECORD / 2;
        std::string out(cap, '\0');
        size_t len = 0;
        uint32_t t = 0;
        int ret = in.ok ? mtr_read_cell(topic.c_str(), node, reinterpret_cast<uint8_t *>(&out[0]), cap, &len, &t) : 0;
        end(ret, Out().bytes(out.data(), ret ? len : 0).u32(t));
        return;
    }
    default:
        fprintf(stderr, "[FROGTASTIC_RAM] call %llu: unknown op %u, ignored\n", (unsigned long long)h.id, h.op);
        return;
    }
    if (!in.ok)
        fprintf(stderr, "[FROGTASTIC_RAM] call %llu op %u: record too short, ignored\n", (unsigned long long)h.id, h.op);
}
} // namespace

int main(int argc, char **argv)
{
    const char *name = getenv(NAME_ENV) ? getenv(NAME_ENV) : DEFAULT_NAME;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--version")) {
            printf("%s\n%s\n%s\n", VERSION, mtr_version(), mtr_front_version());
            return 0;
        } else if (!strcmp(argv[i], "--shm") && i + 1 < argc) {
            name = argv[++i];
        } else {
            fprintf(stderr, "usage: frogtastic-ram [--shm NAME] [--version]\n");
            return 2;
        }
    }
    g_name = name;
    // a fresh segment each start: whatever a previous bridge left is not this one's
    shm_unlink(name);
    int fd = shm_open(name, O_CREAT | O_EXCL | O_RDWR, 0660);
    if (fd < 0 || ftruncate(fd, sizeof(Segment)) != 0) {
        fprintf(stderr, "[FROGTASTIC_RAM] %s: cannot create shared segment %s: %s\n", VERSION, name, strerror(errno));
        return 1;
    }
    void *m = mmap(nullptr, sizeof(Segment), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) {
        fprintf(stderr, "[FROGTASTIC_RAM] %s: cannot map %s: %s\n", VERSION, name, strerror(errno));
        return 1;
    }
    g_seg = static_cast<Segment *>(m); // ftruncate zero-filled it: every ring empty
    g_seg->layout = LAYOUT;
    g_seg->magic = MAGIC;
    g_seg->bridge_pid.store(getpid(), std::memory_order_release);
    fprintf(stderr, "[FROGTASTIC_RAM] %s (%s): serving %s, %zu bytes\n", VERSION, mtr_version(), name, sizeof(Segment));
    printf("serving %s\n", name);
    fflush(stdout);
    // the firmware reads that one line and closes its end; nothing more goes to stdout
    if (!freopen("/dev/null", "w", stdout)) fprintf(stderr, "[FROGTASTIC_RAM] stdout not closed: %s\n", strerror(errno));

    Header h{};
    std::string payload;
    auto forever = [] { return true; };
    while (take(g_seg->calls, h, payload, forever)) serve(h, payload);
    return 0;
}
