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
//
// MEMORY, NOT MESSAGES (John, 2026-11-16). Each socket is a writer and a consumer on ribbit::Space ([SPACE_V1]):
//
//   a send        ONE write into the sender's own cells, on the topic that is the destination's name
//                 "<ip>:<port>". The call returns when the write is made. The sender's cells on a topic are a ring
//                 (Space::slot): write n is cell <sender>.<n mod RING>. Nobody is asked anything.
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
// THE RING AND WHAT BOUNDS IT. A request cell may be written over only after its server has taken it, and a reply is
// proof of that. A message of up to 1,000,000 bytes is at most MOST_RECORDS cells, so a socket refuses (EAGAIN) a
// request to a destination with RING / MOST_RECORDS requests unanswered: its own count, nothing asked of the peer.
// Replies outstanding to a client are bounded by the same count.
//
// THE MEMORY GOING IS SAID. If this process's session with the memory ends, every socket is down (ENETDOWN) and the
// log says why. Nothing carries on in its place.
//
// NOT YET (make it work, make it safe, make it fast -- this is the first): IPv6; a client port is a random number
// above 32768 and is not yet claimed in the memory; payloads travel as base64 in the cell's bag, in fields of 48,000
// bytes.
#include "ribbit_space.hpp"
#include <arpa/inet.h>
#include <atomic>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <map>
#include <mutex>
#include <netinet/in.h>
#include <set>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <unistd.h>
#include <unordered_map>
#include "homa.h"

namespace {
const char* BUILD = "softhoma-v1-20261116k";
const uint64_t RING = 4096;
const size_t PIECE = 48000;        // one field of a cell: at most 65,535 bytes on the wire (semcodec TYPE_STR), in base64
size_t RECORD = 240000;            // one cell: bulk goes in large records (PERFORMANCE-LESSONS.md section 2), and a
                                   // frame to the memory is at most 1 MiB (ram_host.cpp MAX_FRAME; over it the memory
                                   // ends the session and says nothing). A message over RECORD bytes is several cells.
const uint64_t MOST_RECORDS = 16;   // a 1,000,000-byte message at the smallest record size used

struct Libc {
    int (*socket)(int, int, int); int (*bind)(int, const sockaddr*, socklen_t); int (*close)(int);
    ssize_t (*sendmsg)(int, const msghdr*, int); ssize_t (*recvmsg)(int, msghdr*, int);
    int (*setsockopt)(int, int, int, const void*, socklen_t); int (*ioctl)(int, unsigned long, ...);
    int (*shutdown)(int, int);
    Libc() {
#define NEXT(n) n = reinterpret_cast<decltype(n)>(dlsym(RTLD_NEXT, #n))
        NEXT(socket); NEXT(bind); NEXT(close); NEXT(sendmsg); NEXT(recvmsg); NEXT(setsockopt); NEXT(ioctl); NEXT(shutdown);
#undef NEXT
    }
};
Libc& libc() { static Libc l; return l; }

std::string b64(const std::string& in) {
    static const char* T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string o; o.reserve((in.size() + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < in.size(); i += 3) { uint32_t v = uint8_t(in[i]) << 16 | uint8_t(in[i + 1]) << 8 | uint8_t(in[i + 2]);
        o += T[v >> 18]; o += T[v >> 12 & 63]; o += T[v >> 6 & 63]; o += T[v & 63]; }
    if (i + 1 == in.size()) { uint32_t v = uint8_t(in[i]) << 16; o += T[v >> 18]; o += T[v >> 12 & 63]; o += "=="; }
    else if (i + 2 == in.size()) { uint32_t v = uint8_t(in[i]) << 16 | uint8_t(in[i + 1]) << 8; o += T[v >> 18]; o += T[v >> 12 & 63]; o += T[v >> 6 & 63]; o += '='; }
    return o;
}
std::string unb64(const std::string& in) {
    static int8_t D[256]; static bool init = false;
    if (!init) { memset(D, -1, sizeof D); const char* T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for (int i = 0; i < 64; ++i) D[uint8_t(T[i])] = int8_t(i);
        init = true; }
    std::string o; o.reserve(in.size() / 4 * 3); uint32_t v = 0; int bits = 0;
    for (unsigned char c : in) { if (D[c] < 0) continue; v = v << 6 | uint32_t(D[c]); bits += 6;
        if (bits >= 8) { bits -= 8; o += char(v >> bits & 255); } }
    return o;
}

const bool DEBUG = getenv("SOFTHOMA_DEBUG") != nullptr;
double now_ms() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

struct Msg { uint64_t id; std::string from, bytes; bool request; int err = 0; };

struct Sock {
    int fd; std::string name; uint16_t port = 0; bool watching = false, server = false, down = false;
    std::mutex m; std::condition_variable cv;
    std::multimap<size_t, Msg> inbox;                 // by length: the consumer's policy is shortest first
    uint64_t next_id = 2, wrote = 0;
    struct Out { uint64_t cookie; std::string to; };
    std::unordered_map<uint64_t, Out> out;            // requests this socket sent that have no reply yet
    std::unordered_map<std::string, uint64_t> unanswered;   // destination -> how many of them
    std::unordered_map<uint64_t, std::string> owed;   // (server) request id | 1 -> the client it came from
    std::unordered_map<std::string, uint64_t> wn;     // topic -> this socket's write count on it (its ring)
    struct Part { std::vector<std::string> recs; size_t have = 0; };
    std::map<std::pair<std::string, uint64_t>, Part> parts;   // (sender, id with the request bit) -> its records so far
    int down_err = 0;                                 // why this socket is down, if the memory went
    char* buf = nullptr; size_t buflen = 0; std::set<uint32_t> free_pages;
    uint64_t n_sent = 0, n_recv = 0, n_gone = 0;
};

std::mutex G; std::unordered_map<int, Sock*> socks;
ribbit::Space* space = nullptr; std::string my_ip; bool can_watch = false;

Sock* find(int fd) { std::lock_guard<std::mutex> g(G); auto it = socks.find(fd); return it == socks.end() ? nullptr : it->second; }

void deliver(Sock* s, Msg&& msg) {
    { std::lock_guard<std::mutex> g(s->m); s->inbox.emplace(msg.bytes.size(), std::move(msg)); }
    uint64_t one = 1; if (write(s->fd, &one, 8) != 8) {}
    s->cv.notify_one();
}

void server_gone(const std::string& who) {            // a server's capability cell is gone: its machine or process left
    std::vector<Sock*> all; { std::lock_guard<std::mutex> g(G); for (auto& kv : socks) all.push_back(kv.second); }
    for (Sock* s : all) {
        std::vector<Msg> failed;
        { std::lock_guard<std::mutex> g(s->m);
          for (auto it = s->out.begin(); it != s->out.end();)
              if (it->second.to == who) { failed.push_back(Msg{it->first, who, std::string(), false, EHOSTUNREACH}); ++it; }
              else ++it;
          s->unanswered.erase(who); s->n_gone += failed.size(); }
        if (!failed.empty()) fprintf(stderr, "[SOFTHOMA] %s is GONE (its capability cell left the memory): %zu request(s) from %s end with it\n", who.c_str(), failed.size(), s->name.c_str());
        for (auto& f : failed) deliver(s, std::move(f));
    }
}

void memory_gone(Sock* s, const std::string& why) {   // this process's session with the memory ended: gone is gone
    size_t n; { std::lock_guard<std::mutex> g(s->m); s->down = true; s->down_err = ENETDOWN; n = s->out.size(); }
    fprintf(stderr, "[SOFTHOMA] the MEMORY IS GONE for socket %s (%s): %zu request(s) outstanding end with it and the socket is down. Nothing is used in its place.\n", s->name.c_str(), why.c_str(), n);
    s->cv.notify_all(); uint64_t one = 1; if (write(s->fd, &one, 8) != 8) {}
}

bool ensure_space() {
    static std::once_flag once;
    std::call_once(once, [] {
        const char* ram = getenv("SOFTHOMA_RAM"); const char* ip = getenv("SOFTHOMA_IP");
        my_ip = ip && *ip ? ip : "127.0.0.1";
        if (const char* r = getenv("SOFTHOMA_RECORD")) { size_t v = size_t(atol(r)); if (v >= 1000 && v <= 700000) RECORD = v; }   // for measuring only
        if (!ram || !*ram) { fprintf(stderr, "[SOFTHOMA] %s: SOFTHOMA_RAM=HOST:PORT is not set: there is no memory to carry Homa on, and nothing is used in its place\n", BUILD); return; }
        try { space = new ribbit::Space(ram, "homa"); }
        catch (const std::exception& e) { fprintf(stderr, "[SOFTHOMA] %s: the memory at %s: %s\n", BUILD, ram, e.what()); return; }
        fprintf(stderr, "[SOFTHOMA] %s Homa's calls on memory at %s; this machine is %s%s; ring %llu\n", BUILD, ram, my_ip.c_str(), ip && *ip ? "" : " (SOFTHOMA_IP not set)", (unsigned long long)RING);
    });
    return space != nullptr;
}

void watch_servers() {                                // one read, per process, on every server's capability cell
    std::lock_guard<std::mutex> g(G);
    if (can_watch) return;
    can_watch = true;
    space->follow("homa", "can", -1, [](std::vector<ribbit::SpaceRow>& rows) { for (auto& r : rows) { if (getenv("SOFTHOMA_DEBUG")) fprintf(stderr, "[SOFTHOMA-DEBUG] can row id=%llu instance=%s gone=%d\n", (unsigned long long)r.id, r.instance.c_str(), int(r.gone)); if (r.gone) server_gone(r.instance); } });
}

void start_watch(Sock* s) {                           // the socket's one standing read, on its own name
    if (s->watching) return;
    s->watching = true;
    space->follow("homa", s->name, -1, [s](std::vector<ribbit::SpaceRow>& rows) {
        for (auto& r : rows) {
            if (r.gone) {                             // a sender's cells went with it; what was taken was taken
                const std::string who = r.instance.substr(0, r.instance.rfind('.'));
                std::lock_guard<std::mutex> g(s->m);
                for (auto it = s->parts.begin(); it != s->parts.end();) it = it->first.first == who ? s->parts.erase(it) : std::next(it);
                continue;
            }
            auto f = r.fields(); if (!f) continue;
            if (DEBUG) fprintf(stderr, "[SOFTHOMA-DEBUG] %.3f %s got row %llu: %s id=%lld record %lld of %lld\n", now_ms(), s->name.c_str(), (unsigned long long)r.id, f->str("k").c_str(), (long long)f->num("i"), (long long)f->num("p") + 1, (long long)f->num("t"));
            std::string bytes; const size_t pieces = size_t(f->num("c"));
            for (size_t p = 0; p < pieces; ++p) bytes += unb64(f->str("b" + std::to_string(p)));
            const bool request = f->str("k") == "q"; const uint64_t id = uint64_t(f->num("i")); const std::string from = f->str("f");
            const size_t total = size_t(f->num("t")), which = size_t(f->num("p"));
            if (total > 1) {                          // one record of several: the message is whole when all are here
                std::lock_guard<std::mutex> g(s->m);
                auto& part = s->parts[{from, id | (request ? 1 : 0)}];
                if (part.recs.size() != total) part.recs.assign(total, std::string());
                if (which < total && part.recs[which].empty()) { part.recs[which] = std::move(bytes); ++part.have; }
                if (part.have < total) continue;
                bytes.clear(); for (auto& x : part.recs) bytes += x;
                s->parts.erase({from, id | (request ? 1 : 0)});
            }
            Msg msg{id, from, std::move(bytes), request};
            if (msg.request) { msg.id |= 1; std::lock_guard<std::mutex> g(s->m); s->owed[msg.id] = msg.from; }
            else {
                std::lock_guard<std::mutex> g(s->m);
                auto it = s->out.find(msg.id);
                if (it == s->out.end()) continue;     // aborted, or its server was called gone: not delivered
            }
            deliver(s, std::move(msg));
        }
    }, [s](const std::string& why) { memory_gone(s, why); });
}

void name_socket(Sock* s, uint16_t port) {
    if (!port) { uint16_t r = 0; if (getrandom(&r, 2, 0) != 2) r = uint16_t(getpid()); port = uint16_t(32768 + (r & 0x7fff)); }
    s->port = port; s->name = my_ip + ":" + std::to_string(port);
    start_watch(s);
}

std::string name_of(const sockaddr* a) {
    const sockaddr_in* in = reinterpret_cast<const sockaddr_in*>(a); char b[64];
    inet_ntop(AF_INET, &in->sin_addr, b, sizeof b);
    return std::string(b) + ":" + std::to_string(ntohs(in->sin_port));
}

void put(Sock* s, const std::string& topic, bool request, uint64_t id, const std::string& bytes) {
    // A message is one cell, or several when it is over RECORD bytes; each cell carries its bytes as fields b0..b<c-1>
    // of at most PIECE bytes. p: which record this is; t: how many there are.
    const size_t recs = (bytes.size() + RECORD - 1) / RECORD;
    for (size_t r = 0; r < recs; ++r) {
        const std::string part = bytes.substr(r * RECORD, RECORD);
        const size_t pieces = (part.size() + PIECE - 1) / PIECE;
        uint64_t n; { std::lock_guard<std::mutex> g(s->m); n = s->wn[topic]++; }
        std::string bag = "{";
        for (size_t p = 0; p < pieces; ++p) bag += "\"b" + std::to_string(p) + "\":\"" + b64(part.substr(p * PIECE, PIECE)) + "\",";
        bag += "\"c\":" + std::to_string(pieces) + ",\"f\":\"" + s->name + "\",\"i\":" + std::to_string(id) + ",\"k\":\"" + (request ? "q" : "r") +
               "\",\"n\":" + std::to_string(n) + ",\"p\":" + std::to_string(r) + ",\"t\":" + std::to_string(recs) + "}";
        space->write("homa", topic, ribbit::Space::slot(s->name, n, RING), bag);
        if (DEBUG) fprintf(stderr, "[SOFTHOMA-DEBUG] %.3f wrote %s id=%llu record %zu of %zu (%zu bytes) to %s\n", now_ms(), request ? "request" : "reply", (unsigned long long)id, r + 1, recs, part.size(), topic.c_str());
    }
}

void release(Sock* s, homa_recvmsg_args* a) {         // bpages the application hands back
    for (uint32_t i = 0; i < a->num_bpages && i < HOMA_MAX_BPAGES; ++i) s->free_pages.insert(a->bpage_offsets[i] >> HOMA_BPAGE_SHIFT);
    a->num_bpages = 0;
}
}  // namespace

extern "C" {

int socket(int domain, int type, int protocol) {
    if (protocol != IPPROTO_HOMA) return libc().socket(domain, type, protocol);
    if (domain != AF_INET) { fprintf(stderr, "[SOFTHOMA] an IPPROTO_HOMA socket of family %d: only AF_INET is carried yet\n", domain); errno = EAFNOSUPPORT; return -1; }
    if (!ensure_space()) { errno = EPROTONOSUPPORT; return -1; }
    int fd = eventfd(0, EFD_SEMAPHORE | EFD_NONBLOCK); if (fd < 0) return -1;
    Sock* s = new Sock; s->fd = fd;
    { std::lock_guard<std::mutex> g(G); socks[fd] = s; }
    return fd;
}

int bind(int fd, const sockaddr* addr, socklen_t len) {
    Sock* s = find(fd); if (!s) return libc().bind(fd, addr, len);
    if (!s->name.empty()) { errno = EINVAL; return -1; }
    name_socket(s, ntohs(reinterpret_cast<const sockaddr_in*>(addr)->sin_port));
    s->server = true;
    space->write("homa", "can", s->name, "{\"build\":\"" + std::string(BUILD) + "\"}");   // gone with this process's session
    return 0;
}

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

ssize_t sendmsg(int fd, const msghdr* h, int flags) {
    Sock* s = find(fd); if (!s) return libc().sendmsg(fd, h, flags);
    homa_sendmsg_args* a = static_cast<homa_sendmsg_args*>(h->msg_control);
    if (!a || !h->msg_name) { errno = EINVAL; return -1; }
    size_t total = 0; for (size_t i = 0; i < size_t(h->msg_iovlen); ++i) total += h->msg_iov[i].iov_len;
    if (total == 0 || total > HOMA_MAX_MESSAGE_LENGTH) { errno = EINVAL; return -1; }
    std::string bytes; bytes.reserve(total);
    for (size_t i = 0; i < size_t(h->msg_iovlen); ++i) bytes.append(static_cast<const char*>(h->msg_iov[i].iov_base), h->msg_iov[i].iov_len);
    if (s->name.empty()) name_socket(s, 0);
    { std::lock_guard<std::mutex> g(s->m); if (s->down_err) { errno = s->down_err; return -1; } }
    if (a->id == 0) {                                 // a request: one write into this socket's own cells
        std::string to = name_of(static_cast<const sockaddr*>(h->msg_name)); uint64_t id;
        { std::lock_guard<std::mutex> g(s->m);
          if (s->unanswered[to] >= RING / MOST_RECORDS) { errno = EAGAIN; return -1; }   // its own count; nothing asked of the peer
          id = s->next_id; s->next_id += 2; s->out[id] = Sock::Out{a->completion_cookie, to}; ++s->unanswered[to]; ++s->n_sent; }
        watch_servers();
        put(s, to, true, id, bytes);
        a->id = id;
        return 0;
    }
    std::string to;                                   // a reply: the same write, the other way
    { std::lock_guard<std::mutex> g(s->m);
      auto it = s->owed.find(a->id); if (it == s->owed.end()) { errno = EINVAL; return -1; }
      to = it->second; s->owed.erase(it); ++s->n_sent; }
    put(s, to, false, a->id & ~1ULL, bytes);
    return 0;
}

ssize_t recvmsg(int fd, msghdr* h, int flags) {
    Sock* s = find(fd); if (!s) return libc().recvmsg(fd, h, flags);
    homa_recvmsg_args* a = static_cast<homa_recvmsg_args*>(h->msg_control);
    if (!a) { errno = EINVAL; return -1; }
    std::unique_lock<std::mutex> g(s->m);
    if (!s->buf) { errno = EINVAL; return -1; }
    release(s, a);
    const uint64_t want = a->id;                      // nonzero: that RPC's response and no other
    auto pick = [&] { for (auto it = s->inbox.begin(); it != s->inbox.end(); ++it) if (!want || it->second.id == want) return it; return s->inbox.end(); };
    auto it = pick();
    while (it == s->inbox.end()) {
        if (s->down) { errno = s->down_err ? s->down_err : ESHUTDOWN; return -1; }
        if (flags & MSG_DONTWAIT) { errno = EAGAIN; return -1; }
        s->cv.wait(g); it = pick();
    }
    Msg msg = std::move(it->second); s->inbox.erase(it);
    uint64_t one; if (read(s->fd, &one, 8) != 8) {}
    a->id = msg.id; a->completion_cookie = 0; a->num_bpages = 0;
    if (!msg.request) {
        auto o = s->out.find(msg.id);
        if (o != s->out.end()) { a->completion_cookie = o->second.cookie; auto u = s->unanswered.find(o->second.to); if (u != s->unanswered.end() && u->second) --u->second; s->out.erase(o); }
    }
    if (h->msg_name && h->msg_namelen >= sizeof(sockaddr_in)) {
        sockaddr_in in{}; in.sin_family = AF_INET; size_t c = msg.from.rfind(':');
        inet_pton(AF_INET, msg.from.substr(0, c).c_str(), &in.sin_addr); in.sin_port = htons(uint16_t(atoi(msg.from.c_str() + c + 1)));
        memcpy(h->msg_name, &in, sizeof in); h->msg_namelen = sizeof in;
    }
    if (msg.err) { errno = msg.err; return -1; }
    const size_t need = (msg.bytes.size() + HOMA_BPAGE_SIZE - 1) >> HOMA_BPAGE_SHIFT;
    if (s->free_pages.size() < need) { s->inbox.emplace(msg.bytes.size(), std::move(msg)); uint64_t w = 1; if (write(s->fd, &w, 8) != 8) {} errno = ENOMEM; return -1; }
    for (size_t p = 0; p < need; ++p) {
        uint32_t page = *s->free_pages.begin(); s->free_pages.erase(s->free_pages.begin());
        size_t off = p << HOMA_BPAGE_SHIFT, n = std::min<size_t>(HOMA_BPAGE_SIZE, msg.bytes.size() - off);
        memcpy(s->buf + (size_t(page) << HOMA_BPAGE_SHIFT), msg.bytes.data() + off, n);
        a->bpage_offsets[a->num_bpages++] = page << HOMA_BPAGE_SHIFT;
    }
    ++s->n_recv;
    return ssize_t(msg.bytes.size());
}

int ioctl(int fd, unsigned long req, ...) {
    va_list ap; va_start(ap, req); void* arg = va_arg(ap, void*); va_end(ap);
    Sock* s = find(fd); if (!s) return libc().ioctl(fd, req, arg);
    if (req == HOMAIOCABORT) {
        homa_abort_args* a = static_cast<homa_abort_args*>(arg); std::lock_guard<std::mutex> g(s->m);
        if (a->id == 0) { s->out.clear(); s->unanswered.clear(); return 0; }
        auto o = s->out.find(a->id); if (o == s->out.end()) { errno = EINVAL; return -1; }
        auto u = s->unanswered.find(o->second.to); if (u != s->unanswered.end() && u->second) --u->second;
        s->out.erase(o); return 0;                    // the client forgets its own id; nothing is sent
    }
    if (req == HOMAIOCRELEASE) { std::lock_guard<std::mutex> g(s->m); release(s, static_cast<homa_recvmsg_args*>(arg)); return 0; }
    if (req == HOMAIOCFREEZE) return 0;
    if (req == HOMAIOCINFO) { memset(arg, 0, sizeof(homa_info)); return 0; }
    errno = ENOTTY; return -1;
}

int shutdown(int fd, int how) {
    Sock* s = find(fd); if (!s) return libc().shutdown(fd, how);
    { std::lock_guard<std::mutex> g(s->m); s->down = true; }
    s->cv.notify_all(); uint64_t one = 1; if (write(s->fd, &one, 8) != 8) {}
    return 0;
}

int close(int fd) {
    Sock* s = find(fd); if (!s) return libc().close(fd);
    { std::lock_guard<std::mutex> g(s->m); s->down = true; }
    s->cv.notify_all();
    fprintf(stderr, "[SOFTHOMA] socket %s closed: sent=%llu received=%llu ended-by-a-peer-leaving=%llu\n", s->name.empty() ? "(unnamed)" : s->name.c_str(),
            (unsigned long long)s->n_sent, (unsigned long long)s->n_recv, (unsigned long long)s->n_gone);
    { std::lock_guard<std::mutex> g(G); socks.erase(fd); }   // the Sock stays: its standing read may still be handing it rows
    return libc().close(fd);
}

}  // extern "C"
