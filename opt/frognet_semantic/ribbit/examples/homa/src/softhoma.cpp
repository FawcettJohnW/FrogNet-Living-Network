// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// softhoma.cpp -- [SOFT_HOMA_V1] Homa's socket calls on CroakOS memory, entirely in user space. Design: ../DESIGN.md.
//
// A program written to Homa's API (PlatformLab/HomaModule homa.h at 07ce63ef; his util/cp_node and util/homa_test,
// UNCHANGED) is started with this library ahead of libc (LD_PRELOAD). The calls it makes on an IPPROTO_HOMA socket
// are answered here; every other descriptor goes to libc untouched.
//
//   SOFTHOMA_RAM=HOST:PORT   the memory (a RAM host that serves the "homa" space; softhoma-ram is one)
//   SOFTHOMA_IP=a.b.c.d      this machine's address, as its peers name it (default 127.0.0.1, said in the log)
//   SOFTHOMA_EXIT_WHEN_MEMORY_GONE=1   end this process when its session with the memory ends. For programs that
//                            do not stop on a failed recvmsg: his cp_node server calls it again at once, for ever,
//                            and prints a line each time (30 GB of log in a few minutes, measured).
//   SOFTHOMA_NODE<N>=a.b.c.d the address of the machine his cp_node calls node<N> (it looks its servers up by that
//                            name). Without it the name is looked up as usual. /etc/hosts is not written.
//
// MEMORY, NOT MESSAGES (John, 2026-11-16). Each socket is a writer and a consumer on ribbit::Space ([SPACE_V1]):
//
//   a send        ONE write into the sender's own cells, on the topic that is the destination's name
//                 "<ip>:<port>". The call returns when the write is made. Nobody is asked anything.
//   receiving     ONE standing read (Space::follow) on the topic that is this socket's own name. The memory gives it
//                 what lands. THE CONSUMER DECIDES HOW IT CONSUMES: recvmsg hands the application the SHORTEST
//                 message first, because that is what Homa's API promises and cp_node measures. That policy is the
//                 forty lines around Sock::inbox, not a kernel module.
//   a reply       the same write, the other way, under the request's id.
//   at most once  a request is one cell under one id; the server's "answered" is its own state.
//   abort         the client forgets its own id. Nothing is sent to anyone.
//   a peer gone   its cells go with its session ([GONE_WITH_ITS_WRITER_V1]); every server socket keeps one cell on
//                 the topic "can", and a client holds one read on it: when a server's cell is gone, everything the
//                 client has outstanding with it ends, at once, with EHOSTUNREACH. No timer anywhere.
//
// WHOSE CELLS, AND HOW MANY. A request in flight has a SLOT, a number its sender takes from its own free list for that
// destination. Its cells are <sender>.<slot>.<record>, and its reply's cells are <server>.<slot>.<record> on the
// sender's topic. A slot is free again when its reply has been taken (or its request aborted, or its server called
// gone), so a cell is written over only after what it held was consumed, the memory holds as many cells as there are
// requests in flight, and nothing bounds how many that is. All of it is the sender's own state; nobody is asked.
//
// NAMES. A server socket's topic is "<ip>:<port>", the address its clients are given. A client socket's is
// "<ip>:<port>/<nonce>": the port is what Homa's API shows a server, and the nonce makes the name this socket's alone
// whatever port another process picked. RPC ids start at a random even number for the same reason.
//
// THE MEMORY GOING IS SAID. If this process's session with the memory ends, every socket is down (ENETDOWN) and the
// log says why. Nothing carries on in its place.
//
// NOT YET (make it work, make it safe, make it fast -- this is the second): IPv6; two processes that bind the same
// port at the same instant can both succeed (the memory has no compare-and-set); payloads travel as base64 in the
// cell's bag and are not yet kept past the semantic engine.
#include "ribbit_space.hpp"
#include <arpa/inet.h>
#include <atomic>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <map>
#include <ctime>
#include <mutex>
#include <thread>
#include <netinet/in.h>
#include <netdb.h>
#include <set>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <unistd.h>
#include <unordered_map>
#include "homa.h"

namespace {
const char* BUILD = "softhoma-v13-20261118c";
const size_t PIECE = 48000;        // one field of a cell: at most 65,535 bytes on the wire (semcodec TYPE_STR), in base64
size_t RECORD = 240000;            // one cell: bulk goes in large records (PERFORMANCE-LESSONS.md section 2), and a
                                   // frame to the memory is at most 1 MiB (ram_host.cpp MAX_FRAME; over it the memory
                                   // ends the session and says nothing). A message over RECORD bytes is several cells.

struct Libc {
    int (*socket)(int, int, int); int (*bind)(int, const sockaddr*, socklen_t); int (*close)(int);
    ssize_t (*sendmsg)(int, const msghdr*, int); ssize_t (*recvmsg)(int, msghdr*, int);
    int (*setsockopt)(int, int, int, const void*, socklen_t); int (*ioctl)(int, unsigned long, ...);
    int (*shutdown)(int, int); ssize_t (*read)(int, void*, size_t); ssize_t (*write)(int, const void*, size_t);
    Libc() {
#define NEXT(n) n = reinterpret_cast<decltype(n)>(dlsym(RTLD_NEXT, #n))
        NEXT(socket); NEXT(bind); NEXT(close); NEXT(sendmsg); NEXT(recvmsg); NEXT(setsockopt); NEXT(ioctl); NEXT(shutdown); NEXT(read); NEXT(write);
#undef NEXT
    }
};
Libc& libc() { static Libc l; return l; }

// [SOFT_HOMA_FAST_V1] base64 straight into and out of the cell's text: sized once, no byte appended on its own
void b64_into(std::string& o, const char* in, size_t n) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    const size_t at = o.size(); o.resize(at + (n + 2) / 3 * 4);
    char* w = &o[at]; const uint8_t* p = reinterpret_cast<const uint8_t*>(in); size_t i = 0;
    for (; i + 2 < n; i += 3) { const uint32_t v = uint32_t(p[i]) << 16 | uint32_t(p[i + 1]) << 8 | p[i + 2];
        *w++ = T[v >> 18]; *w++ = T[v >> 12 & 63]; *w++ = T[v >> 6 & 63]; *w++ = T[v & 63]; }
    if (i + 1 == n) { const uint32_t v = uint32_t(p[i]) << 16; *w++ = T[v >> 18]; *w++ = T[v >> 12 & 63]; *w++ = '='; *w++ = '='; }
    else if (i + 2 == n) { const uint32_t v = uint32_t(p[i]) << 16 | uint32_t(p[i + 1]) << 8; *w++ = T[v >> 18]; *w++ = T[v >> 12 & 63]; *w++ = T[v >> 6 & 63]; *w++ = '='; }
}
void unb64_into(std::string& o, const std::string& in) {
    static const struct Table { int8_t d[256]; Table() { memset(d, -1, sizeof d); const char* T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for (int i = 0; i < 64; ++i) d[uint8_t(T[i])] = int8_t(i); } } D;
    const size_t at = o.size(); o.resize(at + in.size() / 4 * 3 + 3);
    char* w = &o[at]; uint32_t v = 0; int bits = 0;
    for (unsigned char c : in) { const int8_t x = D.d[c]; if (x < 0) continue; v = v << 6 | uint32_t(x); bits += 6;
        if (bits >= 8) { bits -= 8; *w++ = char(v >> bits & 255); } }
    o.resize(size_t(w - o.data()));
}

const bool DEBUG = getenv("SOFTHOMA_DEBUG") != nullptr;
double now_ms() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

// [WHERE_THE_TIME_GOES_V1] John, 2026-11-17: "add metrics so we can find the bottlenecks. And not just where you think
// they are." Every step a message takes through this file is timed, by the size of the message: on the way out the
// copy from the caller, the base64, the rest of building the cell, and the write into the memory; on the way in the
// decode of the row, the base64, the joining of records, the time the row took to get here from the sender's write
// (the sender's wall clock is in the cell; between two machines that figure includes the difference of their clocks),
// the time a whole message waited for recvmsg to take it, the time recvmsg was blocked with nothing to take, and the
// copy to the caller's pages. Said in this process's own stderr as [SOFTHOMA-METRICS], each time SAY_EVERY messages
// have gone through and when a socket closes. Nothing is on a timer. SOFTHOMA_METRICS=0 turns the saying off.
enum { M_SEND_TOTAL, M_SEND_COPY_IN, M_SEND_PEER_CHECK, M_SEND_B64, M_SEND_BUILD_CELL, M_SEND_MEMORY_WRITE,
       M_IN_ROW_TOTAL, M_IN_DECODE_ROW, M_IN_UNB64, M_IN_JOIN_RECORDS, M_IN_TRANSIT_FROM_SENDERS_WRITE,
       M_RECV_WAITED_IN_INBOX, M_RECV_BLOCKED_NOTHING_TO_TAKE, M_RECV_COPY_OUT, M_RECV_TOTAL_NOT_BLOCKED, M_COUNT };
const char* const M_NAME[M_COUNT] = { "send.total", "send.copy_in", "send.peer_check", "send.base64", "send.build_cell", "send.memory_write",
       "in.row_total", "in.decode_row", "in.unbase64", "in.join_records", "in.transit_from_senders_write",
       "recv.waited_in_inbox", "recv.blocked_nothing_to_take", "recv.copy_out", "recv.total_not_blocked" };
const size_t M_SIZE[] = { 10000, 240000, size_t(-1) }; const char* const M_SIZE_NAME[] = { "<=10,000", "<=240,000", "larger" };
struct Meter { std::atomic<uint64_t> n{0}, ns{0}, mx{0}; };
Meter meters[3][M_COUNT]; std::atomic<uint64_t> m_msgs[3], m_bytes[3];
const uint64_t SAY_EVERY = 2000;
inline uint64_t mono_ns() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return uint64_t(t.tv_sec) * 1000000000ULL + uint64_t(t.tv_nsec); }
inline uint64_t wall_ns() { timespec t; clock_gettime(CLOCK_REALTIME, &t); return uint64_t(t.tv_sec) * 1000000000ULL + uint64_t(t.tv_nsec); }
inline int size_class(size_t bytes) { return bytes <= M_SIZE[0] ? 0 : bytes <= M_SIZE[1] ? 1 : 2; }
inline void meter(int cls, int which, uint64_t d) { Meter& m = meters[cls][which]; m.n.fetch_add(1, std::memory_order_relaxed); m.ns.fetch_add(d, std::memory_order_relaxed);
    uint64_t was = m.mx.load(std::memory_order_relaxed); while (d > was && !m.mx.compare_exchange_weak(was, d, std::memory_order_relaxed)) {} }
void say_metrics(const char* why, int only = -1) {
    static const bool on = !(getenv("SOFTHOMA_METRICS") && getenv("SOFTHOMA_METRICS")[0] == '0'); if (!on) return;
    for (int c = 0; c < 3; ++c) {
        if (!m_msgs[c].load() || (only >= 0 && only != c)) continue;
        std::string line; char b[256];
        for (int w = 0; w < M_COUNT; ++w) { const uint64_t n = meters[c][w].n.load(); if (!n) continue;
            snprintf(b, sizeof b, " %s n=%llu mean=%.3fms max=%.3fms sum=%.1fms;", M_NAME[w], (unsigned long long)n, double(meters[c][w].ns.load()) / double(n) / 1e6, double(meters[c][w].mx.load()) / 1e6, double(meters[c][w].ns.load()) / 1e6); line += b; }
        fprintf(stderr, "[SOFTHOMA-METRICS] %s pid=%d (%s) messages %s: %llu, %llu bytes:%s\n", BUILD, int(getpid()), why, M_SIZE_NAME[c], (unsigned long long)m_msgs[c].load(), (unsigned long long)m_bytes[c].load(), line.c_str());
    }
}
// said at 1, 2, 5, 10, 20, 50 ... 1,000 messages of a size class and then at every SAY_EVERY: a count, not a clock, so a
// process that is ended without closing its sockets has still said where its time went up to its last such count
inline bool say_at(uint64_t n) { if (n >= SAY_EVERY) return n % SAY_EVERY == 0; uint64_t d = 1; while (d * 10 <= n) d *= 10; return n == d || n == 2 * d || n == 5 * d; }
inline void went_through(size_t bytes) { const int c = size_class(bytes); const uint64_t n = m_msgs[c].fetch_add(1) + 1; m_bytes[c].fetch_add(bytes); if (say_at(n)) say_metrics("at this count", c); }

struct Msg { uint64_t id; std::string from, bytes; bool request; int err = 0; uint64_t slot = 0; uint64_t whole_at = 0; };

struct Sock {
    int fd; std::string name; uint16_t port = 0; bool watching = false, server = false, down = false;
    ribbit::Space* sp = nullptr;                      // the memory this socket was opened on, for its whole life
    std::mutex m; std::condition_variable cv;
    std::multimap<size_t, Msg> inbox;                 // by length: the consumer's policy is shortest first
    uint64_t next_id = 2;
    struct Out { uint64_t cookie; std::string to; uint64_t slot; };
    std::unordered_map<uint64_t, Out> out;            // requests this socket sent that have no reply yet
    struct Slots { std::set<uint64_t> free; uint64_t next = 0; };
    std::unordered_map<std::string, Slots> slots;     // destination -> this socket's slots there (its own state)
    struct Owed { std::string from; uint64_t slot; };
    std::map<std::pair<std::string, uint64_t>, Owed> owed;   // (server) (the client's address, id | 1) -> who and which slot
    struct Part { std::vector<std::string> recs; size_t have = 0; };
    std::map<std::pair<std::string, uint64_t>, Part> parts;   // (sender, id with the request bit) -> its records so far
    int down_err = 0;                                 // why this socket is down, if the memory went
    char* buf = nullptr; size_t buflen = 0; std::set<uint32_t> free_pages;
    uint64_t n_sent = 0, n_recv = 0, n_gone = 0;
};

std::mutex G; std::unordered_map<int, Sock*> socks;
ribbit::Space* space = nullptr; std::string my_ip; bool can_watch = false;
// [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] where the memory is can change while this process runs (the role is elected; the
// network splits and merges). softhoma_memory() below is how the driver says where it is NOW. Each socket keeps the
// memory it was opened on; `space` is the one a NEW socket opens on.
std::mutex SP; bool tried = false; std::string told_ram, told_ip; std::atomic<uint64_t> memory_number{0};
std::atomic<bool> is_homa[1 << 16];                   // by descriptor: every other descriptor costs one load here

Sock* find(int fd) {
    if (fd < 0 || fd >= (1 << 16) || !is_homa[fd].load(std::memory_order_acquire)) return nullptr;
    std::lock_guard<std::mutex> g(G); auto it = socks.find(fd); return it == socks.end() ? nullptr : it->second;
}
void wake(Sock* s) { uint64_t one = 1; if (libc().write(s->fd, &one, 8) != 8) {} }
void free_slot(Sock* s, const std::string& to, uint64_t slot) { s->slots[to].free.insert(slot); }   // under s->m
std::string addr_of(const std::string& name) { return name.substr(0, name.find('/')); }           // "<ip>:<port>"

void deliver(Sock* s, Msg&& msg) {
    msg.whole_at = mono_ns();
    { std::lock_guard<std::mutex> g(s->m); s->inbox.emplace(msg.bytes.size(), std::move(msg)); }
    wake(s);
    s->cv.notify_one();
}

// servers this process has been told are gone or closed (their capability cell said so); one that announces itself again is taken out
std::set<std::string> not_there;                      // under G
std::set<std::string> there;                          // under G: servers whose capability cell this process has seen there
// [A_SERVER_ARRIVING_IS_AN_EVENT_V1] every change to the two sets, and every change of memory, is counted and told to
// whoever waits in softhoma_wait_servers(): a server arriving in the memory is an event a driver can wait on.
uint64_t servers_changed = 0; std::condition_variable servers_cv;     // under G

void server_gone(const std::string& who, bool closed) {            // a server's capability cell is gone or says closed: its machine or process left, or it closed its socket
    std::vector<Sock*> all; { std::lock_guard<std::mutex> g(G); for (auto& kv : socks) all.push_back(kv.second); }
    for (Sock* s : all) {
        std::vector<Msg> failed;
        { std::lock_guard<std::mutex> g(s->m);
          for (auto it = s->out.begin(); it != s->out.end();)
              if (it->second.to == who) { failed.push_back(Msg{it->first, who, std::string(), false, EHOSTUNREACH}); ++it; }
              else ++it;
          s->n_gone += failed.size(); }
        if (!failed.empty()) fprintf(stderr, "[SOFTHOMA] %s is GONE (its capability cell %s): %zu request(s) from %s end with it\n", who.c_str(), closed ? "says it closed its socket" : "left the memory", failed.size(), s->name.c_str());
        for (auto& f : failed) deliver(s, std::move(f));
    }
}

// [THE_MEMORY_CAN_COME_BACK_WHERE_IT_WAS_V1] Found between machines (simulation/wire_between_machines_tier.py (b),
// 17 Nov): the SystemRam's machine was killed and came back at the same address. This process kept the Space of the
// memory that had died, so the next socket was opened on it, the platform threw frogram::Unreachable through a call
// made from Python, and the whole proxy ended ("terminate called"). John, the same day: a system restarting "is a
// network event that everything else on the network will see". So: when this process's session with a memory ends,
// that memory is let go of (as softhoma_memory() does for one that moved); nothing is opened on it again; the next
// connection is made when the driver says an event happened (softhoma_memory(), same address or not), and never
// on a timer. And no call the driver makes lets an exception out: it fails with ENETDOWN and says why.
void let_go(ribbit::Space* sp, const std::string& why) {
    { std::lock_guard<std::mutex> g(SP); if (space != sp) return; space = nullptr; tried = true; }   // not tried again until an event (softhoma_memory)
    memory_number.fetch_add(1);
    { std::lock_guard<std::mutex> g(G); can_watch = false; there.clear(); not_there.clear(); ++servers_changed; }
    servers_cv.notify_all();
    fprintf(stderr, "[SOFTHOMA] %s this process has LET GO of the memory at %s (%s). It connects again when the driver says a network event happened; nothing is used in its place.\n", BUILD, sp->where().c_str(), why.c_str());
}

int went(const char* call, const std::exception& e) {  // an exception at a call the driver made: said, and the call fails
    fprintf(stderr, "[SOFTHOMA] %s: %s failed: %s. The call returns ENETDOWN; nothing is used in its place.\n", BUILD, call, e.what());
    errno = ENETDOWN; return -1;
}

void memory_gone(Sock* s, const std::string& why) {   // this process's session with the memory ended: gone is gone
    let_go(s->sp, why);
    size_t n; { std::lock_guard<std::mutex> g(s->m); s->down = true; s->down_err = ENETDOWN; n = s->out.size(); }
    fprintf(stderr, "[SOFTHOMA] the MEMORY IS GONE for socket %s (%s): %zu request(s) outstanding end with it and the socket is down. Nothing is used in its place.\n", s->name.c_str(), why.c_str(), n);
    s->cv.notify_all(); wake(s);
    if (getenv("SOFTHOMA_EXIT_WHEN_MEMORY_GONE")) {
        fprintf(stderr, "[SOFTHOMA] this process ENDS HERE: its memory is gone and SOFTHOMA_EXIT_WHEN_MEMORY_GONE is set\n");
        _exit(1);
    }
}

bool ensure_space() {
    std::lock_guard<std::mutex> g(SP);
    if (space) return true;
    if (tried) return false;                          // said once; asked again only after softhoma_memory()
    tried = true;
    // The driver says where (softhoma_memory). A program that cannot be changed is told by its environment
    // (SOFTHOMA_RAM, SOFTHOMA_IP: his cp_node), once, for its life.
    const char* e_ram = getenv("SOFTHOMA_RAM"); const char* e_ip = getenv("SOFTHOMA_IP");
    const std::string ram = !told_ram.empty() ? told_ram : (e_ram ? e_ram : "");
    const std::string ip = !told_ip.empty() ? told_ip : (e_ip ? e_ip : "");
    my_ip = !ip.empty() ? ip : "127.0.0.1";
    if (const char* r = getenv("SOFTHOMA_RECORD")) { size_t v = size_t(atol(r)); if (v >= 1000 && v <= 700000) RECORD = v; }   // for measuring only
    if (ram.empty()) { fprintf(stderr, "[SOFTHOMA] %s: it has not been said where the memory is (the driver's softhoma_memory(), or SOFTHOMA_RAM=HOST:PORT): there is no memory to carry Homa on, and nothing is used in its place\n", BUILD); return false; }
    try { space = new ribbit::Space(ram, "homa"); }
    catch (const std::exception& e) { fprintf(stderr, "[SOFTHOMA] %s: the memory at %s: %s\n", BUILD, ram.c_str(), e.what()); return false; }
    fprintf(stderr, "[SOFTHOMA] %s Homa's calls on memory at %s; this machine is %s%s\n", BUILD, ram.c_str(), my_ip.c_str(), !ip.empty() ? "" : " (not said: 127.0.0.1)");
    return true;
}

void watch_servers(ribbit::Space* sp) {               // one read, per memory, on every server's capability cell
    std::lock_guard<std::mutex> g(G);
    if (can_watch) return;
    can_watch = true;
    const uint64_t mine = memory_number.load();       // rows from a memory this process has let go of are not this memory's news
    sp->follow("homa", "can", 0, [mine](std::vector<ribbit::SpaceRow>& rows) { if (mine != memory_number.load()) return; for (auto& r : rows) { if (getenv("SOFTHOMA_DEBUG")) fprintf(stderr, "[SOFTHOMA-DEBUG] can row id=%llu instance=%s gone=%d\n", (unsigned long long)r.id, r.instance.c_str(), int(r.gone)); const bool left = r.gone || r.fields()->num("closed");
        { std::lock_guard<std::mutex> g(G); if (left) { not_there.insert(r.instance); there.erase(r.instance); } else { not_there.erase(r.instance); there.insert(r.instance); } ++servers_changed; }
        servers_cv.notify_all();
        if (left) server_gone(r.instance, !r.gone); } },
        [sp](const std::string& why) { let_go(sp, why); });
}

void start_watch(Sock* s) {                           // the socket's one standing read, on its own name
    if (s->watching) return;
    s->watching = true;
    s->sp->follow("homa", s->name, -1, [s](std::vector<ribbit::SpaceRow>& rows) {
        for (auto& r : rows) {
            if (r.gone) {                             // a sender's cells went with it; what was taken was taken
                std::string who = r.instance; for (int k = 0; k < 2; ++k) who = who.substr(0, who.rfind('.'));   // <name>.<slot>.<record>
                std::lock_guard<std::mutex> g(s->m);
                for (auto it = s->parts.begin(); it != s->parts.end();) it = it->first.first == who ? s->parts.erase(it) : std::next(it);
                continue;
            }
            const uint64_t t_row = mono_ns();
            auto f = r.fields(); if (!f) continue;
            const uint64_t t_dec = mono_ns();
            if (DEBUG) fprintf(stderr, "[SOFTHOMA-DEBUG] %.3f %s got row %llu: %s id=%lld record %lld of %lld\n", now_ms(), s->name.c_str(), (unsigned long long)r.id, f->str("k").c_str(), (long long)f->num("i"), (long long)f->num("p") + 1, (long long)f->num("t"));
            std::string bytes; const size_t pieces = size_t(f->num("c"));
            for (size_t p = 0; p < pieces; ++p) unb64_into(bytes, f->str("b" + std::to_string(p)));
            const uint64_t t_unb = mono_ns(); const uint64_t sent_us = uint64_t(f->num("w")), now_us = wall_ns() / 1000; const size_t rec_bytes = bytes.size();
            const bool request = f->str("k") == "q"; const uint64_t id = uint64_t(f->num("i")); const std::string from = f->str("f");
            const size_t total = size_t(f->num("t")), which = size_t(f->num("p"));
            const int rc = total > 1 ? 2 : size_class(rec_bytes);      // a record of several belongs to a message larger than one record
            meter(rc, M_IN_DECODE_ROW, t_dec - t_row); meter(rc, M_IN_UNB64, t_unb - t_dec);
            if (sent_us && now_us > sent_us) meter(rc, M_IN_TRANSIT_FROM_SENDERS_WRITE, (now_us - sent_us) * 1000);
            struct Row { uint64_t t0; int c; ~Row() { meter(c, M_IN_ROW_TOTAL, mono_ns() - t0); } } row{t_row, rc};
            if (total > 1) {
                const uint64_t t_join = mono_ns(); struct Join { uint64_t t0; ~Join() { meter(2, M_IN_JOIN_RECORDS, mono_ns() - t0); } } join{t_join};                          // one record of several: the message is whole when all are here
                std::lock_guard<std::mutex> g(s->m);
                auto& part = s->parts[{from, id | (request ? 1 : 0)}];
                if (part.recs.size() != total) part.recs.assign(total, std::string());
                if (which < total && part.recs[which].empty()) { part.recs[which] = std::move(bytes); ++part.have; }
                if (part.have < total) continue;
                bytes.clear(); for (auto& x : part.recs) bytes += x;
                s->parts.erase({from, id | (request ? 1 : 0)});
            }
            Msg msg{id, from, std::move(bytes), request}; msg.slot = uint64_t(f->num("s"));
            if (msg.request) { msg.id |= 1; std::lock_guard<std::mutex> g(s->m); s->owed[{addr_of(msg.from), msg.id}] = Sock::Owed{msg.from, msg.slot}; }
            else {
                std::lock_guard<std::mutex> g(s->m);
                auto it = s->out.find(msg.id);
                if (it == s->out.end()) continue;     // aborted, or its server was called gone: not delivered
            }
            deliver(s, std::move(msg));
        }
    }, [s](const std::string& why) { memory_gone(s, why); });
}

void name_socket(Sock* s, uint16_t port, const std::string& ip) {   // port 0: a client socket
    uint64_t r[2] = {uint64_t(getpid()), uint64_t(reinterpret_cast<uintptr_t>(s))};
    if (getrandom(r, sizeof r, 0) != sizeof r) {}
    s->next_id = (r[1] & 0xffffffffffffULL) << 1 | 2;
    if (port) { s->port = port; s->name = ip + ":" + std::to_string(port); }
    else { char nonce[20]; snprintf(nonce, sizeof nonce, "%012llx", (unsigned long long)(r[0] & 0xffffffffffffULL));
           s->port = uint16_t(32768 + (r[1] >> 48 & 0x7fff)); s->name = ip + ":" + std::to_string(s->port) + "/" + nonce; }
    start_watch(s);
}

std::string name_of(const sockaddr* a) {
    const sockaddr_in* in = reinterpret_cast<const sockaddr_in*>(a); char b[64];
    inet_ntop(AF_INET, &in->sin_addr, b, sizeof b);
    return std::string(b) + ":" + std::to_string(ntohs(in->sin_port));
}

void put(Sock* s, const std::string& topic, bool request, uint64_t id, uint64_t slot, const std::string& bytes) {
    // A message is one cell, or several when it is over RECORD bytes; each cell carries its bytes as fields b0..b<c-1>
    // of at most PIECE bytes. p: which record this is; t: how many there are.
    const size_t recs = (bytes.size() + RECORD - 1) / RECORD; const int mc = size_class(bytes.size());
    for (size_t r = 0; r < recs; ++r) {
        const uint64_t t_rec = mono_ns(); uint64_t t_b64 = 0;
        const char* part = bytes.data() + r * RECORD; const size_t part_size = std::min(RECORD, bytes.size() - r * RECORD);
        const size_t pieces = (part_size + PIECE - 1) / PIECE;
        std::string bag; bag.reserve(part_size / 3 * 4 + 16 * pieces + 256); bag = "{";
        for (size_t p = 0; p < pieces; ++p) { bag += "\"b" + std::to_string(p) + "\":\"";
            const uint64_t t0 = mono_ns(); b64_into(bag, part + p * PIECE, std::min(PIECE, part_size - p * PIECE)); t_b64 += mono_ns() - t0; bag += "\","; }
        bag += "\"c\":" + std::to_string(pieces) + ",\"f\":\"" + s->name + "\",\"i\":" + std::to_string(id) + ",\"k\":\"" + (request ? "q" : "r") +
               "\",\"p\":" + std::to_string(r) + ",\"s\":" + std::to_string(slot) + ",\"t\":" + std::to_string(recs) + ",\"w\":" + std::to_string(wall_ns() / 1000) + "}";
        const uint64_t t_built = mono_ns();
        s->sp->write("homa", topic, s->name + "." + std::to_string(slot) + "." + std::to_string(r), bag);
        const uint64_t t_done = mono_ns();
        meter(mc, M_SEND_B64, t_b64); meter(mc, M_SEND_BUILD_CELL, t_built - t_rec - t_b64); meter(mc, M_SEND_MEMORY_WRITE, t_done - t_built);
        if (DEBUG) fprintf(stderr, "[SOFTHOMA-DEBUG] %.3f wrote %s id=%llu record %zu of %zu (%zu bytes) to %s\n", now_ms(), request ? "request" : "reply", (unsigned long long)id, r + 1, recs, part_size, topic.c_str());
    }
}

void release(Sock* s, homa_recvmsg_args* a) {         // bpages the application hands back
    for (uint32_t i = 0; i < a->num_bpages && i < HOMA_MAX_BPAGES; ++i) s->free_pages.insert(a->bpage_offsets[i] >> HOMA_BPAGE_SHIFT);
    a->num_bpages = 0;
}
}  // namespace

extern "C" {

// [SOFT_HOMA_CARRIER_V1] how a program learns that the Homa it has opened is this one (core/homa_transport.py)
const char* softhoma_build() { return BUILD; }

// [FRAMES_GO_IN_PIECES_V1] the largest message that goes as ONE cell here. The driver (core/homa_socket.py) asks, and
// cuts a frame into pieces of this size, so each piece is a whole message and takes the one-cell path.
size_t softhoma_record_bytes() { ensure_space(); return RECORD; }

// [A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1] The driver's question before it opens a session: is a server bound at `addr`
// ("a.b.c.d:port") in the memory this process is on NOW? After the memory moves, each end arrives in the new one when
// its own hosts file changes, so a peer can be a moment behind. The answer is its capability cell, and waiting for it
// is one held read on that cell for up to `seconds` (0: just look). 1: it is there. 0: it is not (no cell, or it says
// closed, when the time was up). -1: this process has no memory.
int softhoma_await(const char* addr, double seconds) try {
    if (!addr || !ensure_space()) return -1;
    ribbit::Space* sp; { std::lock_guard<std::mutex> g(SP); sp = space; }
    if (!sp) return -1;
    const std::string to = addr; const double until = now_ms() + seconds * 1000.0; long long after = 0;
    watch_servers(sp);                                // the one standing read on every server's cell keeps the two sets below current
    { std::lock_guard<std::mutex> g(G); if (there.count(to)) return 1; if (not_there.count(to) && seconds <= 0) return 0; }   // known: nothing is asked
    for (;;) {
        const double left = (until - now_ms()) / 1000.0;
        bool there_now = false, any = false;
        for (auto& r : sp->read("homa", "can", to, after, left > 0 ? left : 0)) {
            any = true; if (static_cast<long long>(r.id) > after) after = static_cast<long long>(r.id);
            there_now = !r.gone && !r.fields()->num("closed");
        }
        if (there_now) { std::lock_guard<std::mutex> g(G); not_there.erase(to); there.insert(to); return 1; }
        if (!any || left <= 0) { std::lock_guard<std::mutex> g(G); if (!there.count(to)) not_there.insert(to); return 0; }   // nothing new before the time was up
    }
} catch (const std::exception& e) { return went("softhoma_await", e); }

// [A_SERVER_ARRIVING_IS_AN_EVENT_V1] Found between machines (simulation/wire_between_machines_tier.py, 17 Nov): a node
// whose hold on the ControlRam failed because the ControlRam's machine had not yet arrived in soft Homa's memory was
// told to hold again "at the next network event", and none came: the hosts file had already changed. A server
// arriving in (or leaving) the memory IS the event. This call returns when the set of servers this process knows has
// changed since `seen` (the number it returned before; 0 the first time), or the memory itself has changed. It is one
// held read: the standing read on the capability cells wakes it. It does not return on a timer.
uint64_t softhoma_wait_servers(uint64_t seen) try {
    if (ensure_space()) { ribbit::Space* sp; { std::lock_guard<std::mutex> g(SP); sp = space; } if (sp) watch_servers(sp); }
    std::unique_lock<std::mutex> g(G);
    servers_cv.wait(g, [&] { return servers_changed != seen; });
    return servers_changed;
} catch (const std::exception& e) { went("softhoma_wait_servers", e); return seen + 1; }

// [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] John 2026-11-17: "You know it can change address during a run. Right?" The
// driver's call: the memory is at `ram` (HOST:PORT) NOW, and this machine is `ip` to its peers NOW. Every socket this
// process has on a memory it had before is down from here (gone is gone: what was outstanding ends, as when a memory
// dies), and the next socket opens on the memory named. The old memory's session is not torn down here (the platform's
// Space has no close); it ends with this process, and this process's cells in it with it. Returns how many sockets
// went down.
int softhoma_memory(const char* ram, const char* ip) {
    ribbit::Space* old = nullptr; std::string was; bool same = false;
    { std::lock_guard<std::mutex> g(SP);
      const std::string r = ram ? ram : "", i = ip ? ip : "";
      if (r == told_ram && i == told_ip) {            // [THE_MEMORY_CAN_COME_BACK_WHERE_IT_WAS_V1] an event, and nothing moved
          if (!space && tried) { tried = false; fprintf(stderr, "[SOFTHOMA] %s a network event: the memory at %s is connected to again at the next socket\n", BUILD, r.c_str()); }
          same = true;
      }
      else { old = space; was = old ? old->where() : ""; space = nullptr; tried = false; told_ram = r; told_ip = i; } }
    if (same) return 0;
    memory_number.fetch_add(1);
    std::vector<Sock*> all;
    { std::lock_guard<std::mutex> g(G); for (auto& kv : socks) all.push_back(kv.second); can_watch = false; there.clear(); not_there.clear(); ++servers_changed; }
    servers_cv.notify_all();
    int n = 0;
    for (Sock* s : all) {
        size_t out; bool already;
        { std::lock_guard<std::mutex> g(s->m); already = s->down; s->down = true; s->down_err = ENETDOWN; out = s->out.size(); }
        if (already) continue;
        ++n;
        fprintf(stderr, "[SOFTHOMA] the memory has MOVED: socket %s was on the memory at %s; %zu request(s) outstanding end with it and the socket is down\n", s->name.c_str(), was.c_str(), out);
        s->cv.notify_all(); wake(s);
    }
    fprintf(stderr, "[SOFTHOMA] %s the memory is now at %s and this machine is %s (it was %s; %d socket(s) on it went down)\n", BUILD, told_ram.c_str(), told_ip.c_str(), old ? was.c_str() : "nowhere yet", n);
    return n;
}


int socket(int domain, int type, int protocol) {
    if (protocol != IPPROTO_HOMA) return libc().socket(domain, type, protocol);
    if (domain != AF_INET) { fprintf(stderr, "[SOFTHOMA] an IPPROTO_HOMA socket of family %d: only AF_INET is carried yet\n", domain); errno = EAFNOSUPPORT; return -1; }
    if (!ensure_space()) { errno = EPROTONOSUPPORT; return -1; }
    int fd = eventfd(0, EFD_SEMAPHORE | EFD_NONBLOCK); if (fd < 0) return -1;
    Sock* s = new Sock; s->fd = fd;
    { std::lock_guard<std::mutex> g(SP); s->sp = space; }
    if (!s->sp) { libc().close(fd); delete s; errno = ENETDOWN; return -1; }      // the memory was let go of between the two lines above
    try { watch_servers(s->sp); } catch (const std::exception& e) { libc().close(fd); ribbit::Space* sp = s->sp; delete s; let_go(sp, e.what()); return went("socket", e); }
    if (fd >= (1 << 16)) { libc().close(fd); delete s; errno = EMFILE; return -1; }
    { std::lock_guard<std::mutex> g(G); socks[fd] = s; }
    is_homa[fd].store(true, std::memory_order_release);
    return fd;
}

int bind(int fd, const sockaddr* addr, socklen_t len) try {
    Sock* s = find(fd); if (!s) return libc().bind(fd, addr, len);
    if (!s->name.empty()) { errno = EINVAL; return -1; }
    const sockaddr_in* in = reinterpret_cast<const sockaddr_in*>(addr); const uint16_t port = ntohs(in->sin_port);
    // bound to one address: that is the address its peers send to. Bound to any: SOFTHOMA_IP.
    std::string ip = my_ip; if (in->sin_addr.s_addr != htonl(INADDR_ANY)) { char b[64]; inet_ntop(AF_INET, &in->sin_addr, b, sizeof b); ip = b; }
    if (port) for (auto& r : s->sp->read("homa", "can", ip + ":" + std::to_string(port))) if (!r.fields()->num("closed")) { errno = EADDRINUSE; return -1; }
    name_socket(s, port, ip);
    s->server = true;
    s->sp->write("homa", "can", s->name, "{\"build\":\"" + std::string(BUILD) + "\"}");   // gone with this process's session
    return 0;
} catch (const std::exception& e) { return went("bind", e); }

int setsockopt(int fd, int level, int opt, const void* val, socklen_t len) {
    Sock* s = find(fd); if (!s) return libc().setsockopt(fd, level, opt, val, len);
    if (level != IPPROTO_HOMA) return 0;
    if (opt == SO_HOMA_RCVBUF) {
        const homa_rcvbuf_args* a = static_cast<const homa_rcvbuf_args*>(val);
        std::lock_guard<std::mutex> g(s->m);
        s->buf = reinterpret_cast<char*>(a->start); s->buflen = a->length; s->free_pages.clear();
        for (uint32_t p = 0; p < a->length >> HOMA_BPAGE_SHIFT; ++p) s->free_pages.insert(p);
        return 0;
    }
    if (opt == SO_HOMA_SERVER) { s->server = *static_cast<const int*>(val) != 0; return 0; }
    errno = ENOPROTOOPT; return -1;
}

ssize_t sendmsg(int fd, const msghdr* h, int flags) try {
    Sock* s = find(fd); if (!s) return libc().sendmsg(fd, h, flags);
    homa_sendmsg_args* a = static_cast<homa_sendmsg_args*>(h->msg_control);
    if (!a || !h->msg_name) { errno = EINVAL; return -1; }
    size_t total = 0; for (size_t i = 0; i < size_t(h->msg_iovlen); ++i) total += h->msg_iov[i].iov_len;
    if (total == 0 || total > HOMA_MAX_MESSAGE_LENGTH) { errno = EINVAL; return -1; }
    const uint64_t t_send = mono_ns(); const int mc = size_class(total);
    struct Whole { uint64_t t0; int c; size_t b; ~Whole() { meter(c, M_SEND_TOTAL, mono_ns() - t0); went_through(b); } } whole{t_send, mc, total};
    std::string bytes; bytes.reserve(total);
    for (size_t i = 0; i < size_t(h->msg_iovlen); ++i) bytes.append(static_cast<const char*>(h->msg_iov[i].iov_base), h->msg_iov[i].iov_len);
    meter(mc, M_SEND_COPY_IN, mono_ns() - t_send);
    if (s->name.empty()) name_socket(s, 0, my_ip);
    { std::lock_guard<std::mutex> g(s->m); if (s->down_err) { errno = s->down_err; return -1; } }
    if (a->id == 0) {                                 // a request: one write into this socket's own cells
        std::string to = name_of(static_cast<const sockaddr*>(h->msg_name)); uint64_t id, slot;
        { std::lock_guard<std::mutex> g(s->m);
          auto& sl = s->slots[to];                    // a slot of this socket's own: the lowest that is free, or a new one
          if (sl.free.empty()) slot = sl.next++; else { slot = *sl.free.begin(); sl.free.erase(sl.free.begin()); }
          id = s->next_id; s->next_id += 2; s->out[id] = Sock::Out{a->completion_cookie, to, slot}; ++s->n_sent; }
        watch_servers(s->sp);
        a->id = id;
        bool gone, known; { std::lock_guard<std::mutex> g(G); gone = not_there.count(to) != 0; known = gone || there.count(to) != 0; }
        if (!known) {                                 // nothing has been heard of it yet: ask the memory, once, whether its capability cell is there
            struct Check { uint64_t t0; int c; ~Check() { meter(c, M_SEND_PEER_CHECK, mono_ns() - t0); } } check{mono_ns(), mc};
            std::string build;
            for (auto& r : s->sp->read("homa", "can", to)) if (!r.gone && !r.fields()->num("closed")) build = r.fields()->str("build");
            gone = build.empty();
            { std::lock_guard<std::mutex> g(G); if (gone) not_there.insert(to); else there.insert(to); }
            if (gone) fprintf(stderr, "[SOFTHOMA] %s has NO capability cell in the memory at %s: nothing is bound to that address and port on this memory (is the server running, and on this memory?)\n", to.c_str(), s->sp->where().c_str());
            else fprintf(stderr, "[SOFTHOMA] %s is there (its capability cell is in the memory; it runs %s)\n", to.c_str(), build.c_str());
        }
        if (gone) {                                   // the memory says it is not there: the request ends as Homa ends one, in recvmsg
            { std::lock_guard<std::mutex> g(s->m); ++s->n_gone; }
            fprintf(stderr, "[SOFTHOMA] %s is NOT THERE (no capability cell, or it said gone or closed): request %llu from %s ends at once, nothing was written\n", to.c_str(), (unsigned long long)id, s->name.c_str());
            deliver(s, Msg{id, to, std::string(), false, EHOSTUNREACH});
            return 0;
        }
        put(s, to, true, id, slot, bytes);
        return 0;
    }
    Sock::Owed o;                                     // a reply: the same write, the other way, in the request's slot
    { std::lock_guard<std::mutex> g(s->m);
      auto it = s->owed.find({name_of(static_cast<const sockaddr*>(h->msg_name)), a->id}); if (it == s->owed.end()) { errno = EINVAL; return -1; }
      o = it->second; s->owed.erase(it); ++s->n_sent; }
    put(s, o.from, false, a->id & ~1ULL, o.slot, bytes);
    return 0;
} catch (const std::exception& e) { return went("sendmsg", e); }

ssize_t recvmsg(int fd, msghdr* h, int flags) try {
    Sock* s = find(fd); if (!s) return libc().recvmsg(fd, h, flags);
    homa_recvmsg_args* a = static_cast<homa_recvmsg_args*>(h->msg_control);
    if (!a) { errno = EINVAL; return -1; }
    std::unique_lock<std::mutex> g(s->m);
    if (!s->buf) { errno = EINVAL; return -1; }
    release(s, a);
    if (s->down) { errno = s->down_err ? s->down_err : ESHUTDOWN; return -1; }   // as the module: a socket shut down gives nothing more
    const uint64_t want = a->id;                      // nonzero: that RPC's response and no other
    auto pick = [&] { for (auto it = s->inbox.begin(); it != s->inbox.end(); ++it) if (!want || it->second.id == want) return it; return s->inbox.end(); };
    auto it = pick();
    const uint64_t t_enter = mono_ns();
    while (it == s->inbox.end()) {
        if (s->down) { errno = s->down_err ? s->down_err : ESHUTDOWN; return -1; }
        if (flags & MSG_DONTWAIT) { errno = EAGAIN; return -1; }
        s->cv.wait(g); it = pick();
    }
    Msg msg = std::move(it->second); s->inbox.erase(it);
    const uint64_t t_took = mono_ns(); const int mc = size_class(msg.bytes.size());
    meter(mc, M_RECV_BLOCKED_NOTHING_TO_TAKE, t_took - t_enter);
    if (msg.whole_at && t_took > msg.whole_at) meter(mc, M_RECV_WAITED_IN_INBOX, t_took - msg.whole_at);
    uint64_t one; if (libc().read(s->fd, &one, 8) != 8) {}
    a->id = msg.id; a->completion_cookie = 0; a->num_bpages = 0;
    if (!msg.request) {
        auto o = s->out.find(msg.id);
        if (o != s->out.end()) { a->completion_cookie = o->second.cookie; free_slot(s, o->second.to, o->second.slot); s->out.erase(o); }
    }
    if (h->msg_name && h->msg_namelen >= sizeof(sockaddr_in)) {
        const std::string addr = addr_of(msg.from);
        sockaddr_in in{}; in.sin_family = AF_INET; size_t c = addr.rfind(':');
        inet_pton(AF_INET, addr.substr(0, c).c_str(), &in.sin_addr); in.sin_port = htons(uint16_t(atoi(addr.c_str() + c + 1)));
        memcpy(h->msg_name, &in, sizeof in); h->msg_namelen = sizeof in;
    }
    if (msg.err) { errno = msg.err; return -1; }
    const size_t need = (msg.bytes.size() + HOMA_BPAGE_SIZE - 1) >> HOMA_BPAGE_SHIFT;
    if (s->free_pages.size() < need) { s->inbox.emplace(msg.bytes.size(), std::move(msg)); wake(s); errno = ENOMEM; return -1; }
    const uint64_t t_copy = mono_ns();
    for (size_t p = 0; p < need; ++p) {
        uint32_t page = *s->free_pages.begin(); s->free_pages.erase(s->free_pages.begin());
        size_t off = p << HOMA_BPAGE_SHIFT, n = std::min<size_t>(HOMA_BPAGE_SIZE, msg.bytes.size() - off);
        memcpy(s->buf + (size_t(page) << HOMA_BPAGE_SHIFT), msg.bytes.data() + off, n);
        a->bpage_offsets[a->num_bpages++] = page << HOMA_BPAGE_SHIFT;
    }
    ++s->n_recv;
    { const uint64_t t_end = mono_ns(); meter(mc, M_RECV_COPY_OUT, t_end - t_copy); meter(mc, M_RECV_TOTAL_NOT_BLOCKED, t_end - t_took); }
    went_through(msg.bytes.size());
    return ssize_t(msg.bytes.size());
} catch (const std::exception& e) { return went("recvmsg", e); }

int ioctl(int fd, unsigned long req, ...) {
    va_list ap; va_start(ap, req); void* arg = va_arg(ap, void*); va_end(ap);
    Sock* s = find(fd); if (!s) return libc().ioctl(fd, req, arg);
    if (req == HOMAIOCABORT) {
        homa_abort_args* a = static_cast<homa_abort_args*>(arg); std::lock_guard<std::mutex> g(s->m);
        if (a->id == 0) { for (auto& kv : s->out) free_slot(s, kv.second.to, kv.second.slot); s->out.clear(); return 0; }
        auto o = s->out.find(a->id); if (o == s->out.end()) { errno = EINVAL; return -1; }
        free_slot(s, o->second.to, o->second.slot);
        s->out.erase(o); return 0;                    // the client forgets its own id; nothing is sent
    }
    if (req == HOMAIOCRELEASE) { std::lock_guard<std::mutex> g(s->m); release(s, static_cast<homa_recvmsg_args*>(arg)); return 0; }
    if (req == HOMAIOCFREEZE) return 0;
    if (req == HOMAIOCINFO) { memset(arg, 0, sizeof(homa_info)); return 0; }
    errno = EINVAL; return -1;                        // as the module answers a request it does not know
}

int shutdown(int fd, int how) {
    Sock* s = find(fd); if (!s) return libc().shutdown(fd, how);
    { std::lock_guard<std::mutex> g(s->m); s->down = true; }
    s->cv.notify_all(); wake(s);
    return 0;
}

int close(int fd) try {
    Sock* s = find(fd); if (!s) return libc().close(fd);
    bool memory_went; { std::lock_guard<std::mutex> g(s->m); s->down = true; memory_went = s->down_err != 0; }
    s->cv.notify_all();
    // a server that closes says so in its capability cell: the cell itself leaves only with this process's session,
    // and those with requests outstanding to it are owed the news now. (Not when its memory went or moved: there is
    // nothing of this socket's to say there.)
    if (s->server && !memory_went) s->sp->write("homa", "can", s->name, "{\"build\":\"" + std::string(BUILD) + "\",\"closed\":1}");
    fprintf(stderr, "[SOFTHOMA] socket %s closed: sent=%llu received=%llu ended-by-a-peer-leaving=%llu\n", s->name.empty() ? "(unnamed)" : s->name.c_str(),
            (unsigned long long)s->n_sent, (unsigned long long)s->n_recv, (unsigned long long)s->n_gone);
    say_metrics("a socket closed");
    is_homa[fd].store(false, std::memory_order_release);
    { std::lock_guard<std::mutex> g(G); socks.erase(fd); }   // the Sock stays: its standing read may still be handing it rows
    return libc().close(fd);
} catch (const std::exception& e) { went("close", e); is_homa[fd].store(false, std::memory_order_release); { std::lock_guard<std::mutex> g(G); socks.erase(fd); } return libc().close(fd); }

// His cp_node finds its servers by the names node1, node2, ...: his testbed's /etc/hosts has them. Where
// SOFTHOMA_NODE<N> is set, that is node<N>'s address; any other name, and node<N> without it, is libc's to answer.
int getaddrinfo(const char* node, const char* service, const struct addrinfo* hints, struct addrinfo** res) {
    static const auto real = reinterpret_cast<int (*)(const char*, const char*, const struct addrinfo*, struct addrinfo**)>(dlsym(RTLD_NEXT, "getaddrinfo"));
    if (node && !strncmp(node, "node", 4) && node[4] >= '0' && node[4] <= '9' && strspn(node + 4, "0123456789") == strlen(node + 4)) {
        const std::string var = "SOFTHOMA_NODE" + std::string(node + 4);
        if (const char* ip = getenv(var.c_str())) { fprintf(stderr, "[SOFTHOMA] %s is %s (%s)\n", node, ip, var.c_str()); return real(ip, service, hints, res); }
    }
    return real(node, service, hints, res);
}

// read() and write() are not Homa calls: the module answers EINVAL, and the descriptor here must not be read by them
ssize_t read(int fd, void* buf, size_t n) { if (!find(fd)) return libc().read(fd, buf, n); errno = EINVAL; return -1; }
ssize_t write(int fd, const void* buf, size_t n) { if (!find(fd)) return libc().write(fd, buf, n); errno = EINVAL; return -1; }

}  // extern "C"
