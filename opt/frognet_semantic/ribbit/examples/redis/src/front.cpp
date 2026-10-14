/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// front.cpp -- the Redis RESP front: a participant of the RAM host that holds the Redis region.
//
// The front speaks RESP to stock clients and holds no keyspace. Each connection's pipeline -- everything one read of
// its socket produced -- goes to the region as ONE call (op=redis, [REDIS_REGION_V1]) with the connection's context;
// the replies and the changed context come back. Many fronts may serve one region: that is the point.
//
//   RIBBIT_RAM=host:port   the RAM host to connect up to (the deployment: a neutral machine, e.g. streamingfrog).
//                          When it is not set, the front starts its own host on a loopback port and stops it on exit:
//                          a fresh region per server, which is what Redis's own test suite expects of each start.
//   RIBBIT_API             the RAM interface endpoint (default /Fawcett.Redis.ram_interface.php)
//   RIBBIT_REDIS_HOST      the host binary for the self-started case (default: ribbit-redis-host beside this program)
//
// One thread per connection: a connection's commands wait for their round trip without stopping any other
// connection. Blocking commands (BLPOP, XREAD BLOCK, WAIT ...) are not yet served: the front answers them with an
// error that names why, rather than parking a region thread (a region operation never waits).
#include "server.hpp"
#include "frogram.hpp"
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/eventfd.h>
#include <poll.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <signal.h>
#include <cstring>
#include <cerrno>
#include <sstream>
#include <sys/epoll.h>
#include <chrono>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <algorithm>
#include <thread>
#include <atomic>

namespace rr {
extern FILE* log_fp;
namespace door { std::string b64enc(const std::string&); std::string b64dec(const std::string&); }
}
using namespace rr;
using frogram::Json;

// [FRONT_RECONNECTS_V1] The session to the RAM host. Across the Internet a connection drops, or the host restarts; the
// front must not stay dead until someone restarts it. A call that fails because the session is gone fails loudly (its
// client gets the error and is closed -- the request may already have run, so nothing is retried); the NEXT call opens
// a new session. A restarted host is a new region: connections the old region issued are refused there, so their
// clients reconnect. The current session is published as an atomic shared_ptr (C++20).
// std::atomic<std::shared_ptr> is C++20 but arrived in libstdc++ with GCC 12; GCC 11 (Ubuntu 22.04, streamingfrog) has
// only the atomic free functions on a plain shared_ptr. Same operations either way.
#if defined(__cpp_lib_atomic_shared_ptr)
struct SessHandle {
    std::atomic<std::shared_ptr<frogram::Session>> p;
    std::shared_ptr<frogram::Session> load() const { return p.load(); }
    void store(std::shared_ptr<frogram::Session> s) { p.store(std::move(s)); }
    bool compare_exchange_strong(std::shared_ptr<frogram::Session>& e, std::shared_ptr<frogram::Session> d) { return p.compare_exchange_strong(e, std::move(d)); }
};
#else
struct SessHandle {
    std::shared_ptr<frogram::Session> p;
    std::shared_ptr<frogram::Session> load() const { return std::atomic_load(&p); }
    void store(std::shared_ptr<frogram::Session> s) { std::atomic_store(&p, std::move(s)); }
    bool compare_exchange_strong(std::shared_ptr<frogram::Session>& e, std::shared_ptr<frogram::Session> d) { return std::atomic_compare_exchange_strong(&p, &e, std::move(d)); }
};
#endif
// [ONE_SESSION_PER_DISPATCHER_V1] Each dispatcher has its own session to the host: a session's semantic engine runs on
// its own send and read threads at each end, so batches on different sessions are encoded, decoded and executed on
// different cores at both ends. Session 0 also carries the single calls (accept, gone, blocking commands).
static const int MAX_SESSIONS = 64;
static SessHandle g_sessv[MAX_SESSIONS];
static SessHandle& g_sess = g_sessv[0];
static std::string g_ram_host; static int g_ram_port = 0;
static std::string g_api;
struct RamRef {   // what the code below calls: the current session, opened on first use after a loss
    std::string call_bytes(const std::string& m, const std::string& p, const std::string& b, double t, int which) {
        return with(which, [&](frogram::Session& s) { return s.call_bytes(m, p, b, t); });
    }
    frogram::Json call(const std::string& m, const std::string& p, const std::string& b = "", double t = 15.0, int which = 0) {
        return with(which, [&](frogram::Session& s) { return s.call(m, p, b, t); });
    }
    template <class F> auto with(int which, F f) -> decltype(f(std::declval<frogram::Session&>())) {
        SessHandle& h = g_sessv[which];
        std::shared_ptr<frogram::Session> s = h.load();
        if (!s) {
            auto fresh = std::make_shared<frogram::Session>(g_ram_host, g_ram_port, g_api);   // throws when unreachable
            std::shared_ptr<frogram::Session> none;
            if (h.compare_exchange_strong(none, fresh)) s = fresh; else s = none;
        }
        try { return f(*s); }
        catch (const frogram::Unreachable&) {
            // the host is one: a session found gone means every session to it is gone. Each is dropped, so the next
            // call on any of them opens a new one (a call already in flight on another fails loudly on its own).
            for (auto& o : g_sessv) { std::shared_ptr<frogram::Session> cur = o.load(); if (cur) o.compare_exchange_strong(cur, nullptr); }
            throw;
        }
    }
};
static RamRef g_ram_ref;
static RamRef* g_ram = &g_ram_ref;
static pid_t g_host_pid = 0;
static std::string g_admin;
static const std::string g_nonce = [] {           // this front's identity in its own requests
    char b[33]; unsigned char r[16] = {0};
    FILE* u = fopen("/dev/urandom", "rb");
    if (!u || fread(r, 1, 16, u) != 16) abort();
    fclose(u);
    for (int k = 0; k < 16; k++) snprintf(b + 2 * k, 3, "%02x", r[k]);
    return std::string(b);
}();     // the admin secret of the host this front started ("" when the host is named)

static bool is_blocking_class(const Argv& a) {
    const char* n = a[0].c_str(); char f = (char)tolower((unsigned char)n[0]);
    if (f != 'b' && f != 'd' && f != 'x') return false;
    static const char* names[] = {"blpop", "brpop", "blmove", "brpoplpush", "blmpop", "bzpopmin", "bzpopmax", "bzmpop", "debug", nullptr};
    for (int i = 0; names[i]; i++) if (!strcasecmp(n, names[i])) return true;
    if (!strcasecmp(n, "xread") || !strcasecmp(n, "xreadgroup")) { for (size_t i = 1; i < a.size(); i++) if (!strcasecmp(a[i].c_str(), "block")) return true; }
    return false;
}

struct Conn {
    int fd = -1;
    uint64_t id = 0;            // the region's id for this connection (unique across fronts)
    std::string token;          // [REGION_HELD_AUTH_V1] the region's token for it: every call presents id and token
    RespParser parser;
    bool authed = true;
    int ep = -1;                // [LOOP_PER_CORE_V1] the event loop this connection lives in
};

// The first context carries every field the region returns, so every request body has one field set from the start
// (the semantic wire keys a template by it).
static std::string initial_ctx(uint64_t id, const std::string& addr, const std::string& laddr) {
    return "{\"id\":" + std::to_string(id) + ",\"db\":0,\"proto\":2,\"authed\":" + (g.requirepass.empty() ? "true" : "false") +
           ",\"user\":\"default\",\"name\":\"\",\"libname\":\"\",\"libver\":\"\",\"addr\":" + Json::quote(addr) + ",\"laddr\":" + Json::quote(laddr) +
           ",\"reply_mode\":0,\"skip_count\":0,\"close_after_reply\":false,\"no_evict\":false,\"no_touch\":false,\"flags\":\"N\"" +
           ",\"created_ms\":" + std::to_string(Memory::now_ms()) + ",\"mx\":\"\"}";
}
static std::string resp_of(const Argv& a) {
    std::string o = "*" + std::to_string(a.size()) + "\r\n";
    for (auto& x : a) { o += "$" + std::to_string(x.size()) + "\r\n"; o += x; o += "\r\n"; }
    return o;
}
static bool send_all(int fd, const std::string& b) {
    size_t o = 0; while (o < b.size()) { ssize_t n = ::send(fd, b.data() + o, b.size() - o, MSG_NOSIGNAL); if (n <= 0) { if (n < 0 && errno == EINTR) continue; return false; } o += (size_t)n; }
    return true;
}
static std::string region_error(const std::string& why) { Reply r; r.error("ERR the Redis region could not be reached: " + why); return r.out; }
// [DIAG-REGION] every failed region call, whole: which connection, which operation, the exact body sent, what the
// platform raised, and the session's wire counters at that moment. Kept, not temporary: a failure across the Internet
// must be diagnosable from one log line.
static void diag_region(uint64_t id, const char* op, const std::string& body, const std::string& what) {
    auto s = g_sess.load();
    std::string st = "none";
    if (s) { auto x = s->stats(); st = "raw=" + std::to_string(x.raw) + " full=" + std::to_string(x.full) + " diff=" + std::to_string(x.diff) + " same=" + std::to_string(x.same) + " rdiff=" + std::to_string(x.rdiff); }
    logmsg('#', "[DIAG-REGION] conn=" + std::to_string(id) + " op=" + op + " error=" + what + " wire{" + st + "} body=" + body);
}

// [FAN_IN_BATCH_V1] Fan-in. A connection thread does not call the region itself: it puts its pipeline on a lock-free
// list and waits on its own word. Dispatchers take EVERYTHING on the list at once -- every connection that has a
// pipeline ready -- and send it as ONE call (op=redis_batch); several dispatchers mean several batches in flight on the
// session at once (the wire multiplexes them). The host fans the batch out across its cores and gathers it back in
// order; each reply is handed to its connection. One connection never has two pipelines in flight (it sends the next
// only after its reply), so its order is kept. Nothing here takes a lock; an idle dispatcher sleeps on a futex word.
// Waits are raw futexes: std::atomic::wait spins on sched_yield first, and fifty connections spinning starve the very
// dispatchers they wait for.
static void fwait(std::atomic<uint32_t>& w, uint32_t v) { while (w.load(std::memory_order_acquire) == v) syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, v, nullptr, nullptr, 0); }
static void fwake(std::atomic<uint32_t>& w, int n) { syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, n, nullptr, nullptr, 0); }

// [RESP_ON_THE_WIRE_V1] The semantic wire carries text (its codec holds a body as a Python str: bytes that are not UTF-8
// cannot cross it). So the frame is text -- decimal lengths, one header line per item -- and a payload goes as its own
// RESP when that is valid UTF-8 (what nearly every client sends), base64 only when it is not ('B' instead of 'T').
static bool utf8_ok(const std::string& s) {
    size_t i = 0, n = s.size();
    while (i < n) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x80) { i++; continue; }
        int k = (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : -1;
        if (k < 0) return false;
        uint32_t cp = c & (0x3F >> k);
        for (int j = 1; j <= k; j++) { if (i + j >= n) return false; unsigned char d = (unsigned char)s[i + j]; if ((d >> 6) != 2) return false; cp = cp << 6 | (d & 0x3F); }
        if ((k == 1 && cp < 0x80) || (k == 2 && cp < 0x800) || (k == 3 && cp < 0x10000) || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += k + 1;
    }
    return true;
}
static void put_text(std::string& o, const std::string& v, std::string (*b64)(const std::string&)) {   // "T<len>\n<bytes>" or "B<len>\n<base64>"
    if (utf8_ok(v)) { o += 'T'; o += std::to_string(v.size()); o += '\n'; o += v; }
    else { std::string e = b64(v); o += 'B'; o += std::to_string(e.size()); o += '\n'; o += e; }
}
static std::string get_line(const std::string& s, size_t& at) {
    size_t e = s.find('\n', at); if (e == std::string::npos) throw std::runtime_error("the region\'s answer to a batch is truncated");
    std::string l = s.substr(at, e - at); at = e + 1; return l;
}
static std::string get_text(const std::string& s, size_t& at, std::string (*b64d)(const std::string&)) {
    std::string h = get_line(s, at);
    if (h.empty() || (h[0] != 'T' && h[0] != 'B')) throw std::runtime_error("raw batch: a payload header is malformed");
    size_t len = std::stoull(h.substr(1));
    if (at + len > s.size()) throw std::runtime_error("the region\'s answer to a batch is truncated");
    std::string v = s.substr(at, len); at += len;
    return h[0] == 'T' ? v : b64d(v);
}
struct Conn;
// A job is one connection's pipeline. Either a thread waits on `done` (the blocking-command path), or `conn` is set
// and the dispatcher finishes the connection's turn itself: writes the replies and hands the socket back to the loop.
struct Job { uint64_t id = 0; std::string token, p;                   // the call: connection and pipeline (RESP bytes)
             std::string r, error; bool authed = false, close = false;   // the answer: replies (RESP bytes), the region's word
             std::atomic<uint32_t> done{0}; Job* next = nullptr;
             Conn* conn = nullptr; bool fatal = false; std::string fatal_err; };
static std::atomic<uint64_t> g_batches{0}, g_batched{0};          // for INFO-style diagnostics: calls, and items in them
static void complete_async(Job* j);
// [NOBODY_WAITS_V1] John 2026-10-14: "There's nothing requiring us to wait, is there?" -- no. A batch is sent by the
// thread that gathered it (Session::call_async puts the frame on the wire on this thread) and answered on the thread
// that reads the reply off the return socket, which writes each connection's replies and hands it back to the loop. No
// dispatcher thread, no thread waiting on the wire, any number of batches in flight. Batches go round-robin over the
// sessions (RIBBIT_FRONT_CALLS, default one per core), so replies are read and written on that many threads at once.
static int g_nsess = 1;
static std::atomic<uint32_t> g_next_sess{0};
static void drop_sessions() {   // the host is one: a session found gone means every session to it is gone
    // A session is never destroyed on one of its own threads (its destructor joins them): this runs on a reader thread,
    // so the last reference to each dropped session is let go on a thread of its own.
    for (auto& o : g_sessv) { std::shared_ptr<frogram::Session> cur = o.load(); if (cur && o.compare_exchange_strong(cur, nullptr)) std::thread([cur] {}).detach(); }
}
static void deliver(const std::vector<Job*>& v, const std::string& a, std::string err) {
    if (err.empty()) {
        try {
            size_t at = 0;
            if (get_line(a, at) != "RRA1 " + std::to_string(v.size())) throw std::runtime_error("the region's answer to a batch of " + std::to_string(v.size()) + " is not that batch's answer");
            for (auto* j : v) {
                std::string f = get_line(a, at);
                if (f.size() != 3) throw std::runtime_error("the region's answer to a batch is malformed");
                j->authed = f[1] == '1'; j->close = f[2] == '1';
                (f[0] == '1' ? j->error : j->r) = get_text(a, at, door::b64dec);
            }
        } catch (const std::exception& e) { err = e.what(); }
    }
    if (!err.empty()) for (auto* j : v) j->error = err;
    for (auto* j : v) {
        if (j->conn) { complete_async(j); continue; }
        j->done.store(1, std::memory_order_release); fwake(j->done, 1);
    }
}
static void send_batch(std::vector<Job*> v) {
    // [RESP_ON_THE_WIRE_V1] the batch as bytes: per item id, token and the pipeline's RESP as it came off the client's
    // socket -- no JSON, no base64. The wire carries it raw; past the ceiling, on the data socket.
    std::string body = "RRB1 " + std::to_string(v.size()) + "\n";
    for (auto* j : v) { body += std::to_string(j->id); body += ' '; body += j->token; body += '\n'; put_text(body, j->p, door::b64enc); }
    uint64_t nb = g_batches.fetch_add(1, std::memory_order_relaxed) + 1; uint64_t ni = g_batched.fetch_add(v.size(), std::memory_order_relaxed) + v.size();
    static const bool diag = getenv("RIBBIT_FRONT_DIAG") != nullptr;   // [DIAG-BATCH] every 1000th batch: size, bytes, round trip
    const auto t0 = std::chrono::steady_clock::now(); const size_t bytes = body.size();
    std::shared_ptr<frogram::Session> s;
    try {
        SessHandle& h = g_sessv[g_next_sess.fetch_add(1, std::memory_order_relaxed) % (uint32_t)g_nsess];
        s = h.load();
        if (!s) {
            auto fresh = std::make_shared<frogram::Session>(g_ram_host, g_ram_port, g_api);   // throws when unreachable
            std::shared_ptr<frogram::Session> none;
            if (h.compare_exchange_strong(none, fresh)) s = fresh; else s = none;
        }
    } catch (const std::exception& e) { drop_sessions(); deliver(v, "", e.what()); return; }
    s->call_async("POST", g_api + "?op=redis_raw", body, [v, nb, ni, t0, bytes](int status, std::string a, std::string err) {
        if (diag && nb % 1000 == 0) logmsg('#', "[DIAG-BATCH] batches=" + std::to_string(nb) + " items=" + std::to_string(ni) + " this=" + std::to_string(v.size()) +
            " body=" + std::to_string(bytes) + "B answer=" + std::to_string(a.size()) + "B round_trip_us=" + std::to_string(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count()));
        if (!err.empty()) { logmsg('#', "[DIAG-BATCH] batch " + std::to_string(nb) + " of " + std::to_string(v.size()) + " failed: " + err); drop_sessions(); deliver(v, a, err); return; }
        if (status >= 400) { deliver(v, a, "HTTP " + std::to_string(status) + ": " + a); return; }
        deliver(v, a, "");
    });
}
// a thread that has nothing else to do (the blocking-command path) waits for its own pipeline's answer
static void submit(Job& j) { send_batch({&j}); fwait(j.done, 0); }
static void start_sessions() {
    const char* e = getenv("RIBBIT_FRONT_CALLS");                   // sessions to the host (default: one per core)
    int n = e ? atoi(e) : (int)std::thread::hardware_concurrency(); if (n < 1) n = 1;
    if (n > MAX_SESSIONS) n = MAX_SESSIONS;
    g_nsess = n;
}

// A region answer for a connection: its replies onto `out`; the region's word on authentication and closing. False if
// the connection ends here.
static bool take_reply(Conn& c, const Job& j, std::string& out) {
    if (!j.error.empty()) { diag_region(c.id, "redis_batch", j.p, j.error); out += region_error(j.error); return false; }   // no region, no service
    out += j.r;
    c.authed = j.authed; c.parser.authenticated = c.authed;
    return !j.close;
}
// One pipeline to the region: returns the bytes to write, and whether the connection stays open.
static bool run_pipeline(Conn& c, std::vector<Argv>& cmds, std::string& out) {
    size_t i = 0;
    while (i < cmds.size()) {
        // everything up to the next blocking-class command goes as one call: a body of any size crosses on the data
        // socket ([DATA_MARKER_V1]), so there is no frame budget to cut to
        size_t j = i; while (j < cmds.size() && !is_blocking_class(cmds[j])) j++;
        if (j > i) {
            std::string pipeline; for (size_t k = i; k < j; k++) pipeline += resp_of(cmds[k]);
            // [REDIS_WIRE_FLAT_V1] the context's fields plus "p", the pipeline's RESP bytes
            // [CONTEXT_IN_THE_REGION_V1] the region holds the context: the call is the connection and its pipeline;
            // [FAN_IN_BATCH_V1] fanned in with every other ready connection
            Job job; job.id = c.id; job.token = c.token; job.p = std::move(pipeline);
            submit(job);
            if (!take_reply(c, job, out)) return false;
        }
        if (j < cmds.size() && is_blocking_class(cmds[j])) {
            // the replies before a blocking command go out now: the client may need them to unblock this very command
            // (a pipeline of LPUSH then BLPOP whose reader waits for the LPUSH's reply)
            if (!out.empty()) { if (!send_all(c.fd, out)) return false; out.clear(); }
            // A blocking command goes alone, as op=redis_block: the region parks it on its own request thread, in the
            // memory, with the command's own claim and wait. This connection's thread waits on the call -- that is
            // the client being blocked. If the client hangs up meanwhile, a watcher (on the socket and an eventfd,
            // no time slices) tells the region, which wakes the parked command and lets it go.
            std::string body = "{\"id\":" + std::to_string(c.id) + ",\"token\":" + Json::quote(c.token) + ",\"p\":" + Json::quote(door::b64enc(resp_of(cmds[j]))) + "}";
            int efd = eventfd(0, 0);
            std::thread watcher([&c, efd] {
                pollfd pf[2] = {{c.fd, POLLRDHUP, 0}, {efd, POLLIN, 0}};
                while (::poll(pf, 2, -1) < 0 && errno == EINTR) {}
                if (pf[0].revents & (POLLRDHUP | POLLHUP | POLLERR))
                    try { g_ram->call("POST", g_api + "?op=redis", "{\"cancel\":" + std::to_string(c.id) + ",\"token\":" + Json::quote(c.token) + "}"); } catch (const std::exception&) {}
            });
            Json r; std::string failed;
            // the command's own wait decides how long this takes (BLPOP ... 0 is forever), so the call imposes none; a
            // host that dies still ends it at once (its connection closes)
            try { r = g_ram->call("POST", g_api + "?op=redis_block", body, 1e8); } catch (const std::exception& e) { failed = e.what(); }
            uint64_t one = 1; if (::write(efd, &one, sizeof one) < 0) {}
            watcher.join(); ::close(efd);
            if (!failed.empty()) { diag_region(c.id, "redis_block", body, failed); out += region_error(failed); return false; }
            if (r["ok"].type != Json::Bool || !r["ok"].b || r["r"].type != Json::Str || r["a"].type != Json::Bool || r["c"].type != Json::Bool) {
                out += region_error(r["error"].type == Json::Str ? r["error"].s : "refused"); return false; }
            out += door::b64dec(r["r"].s);
            c.authed = r["a"].b; c.parser.authenticated = c.authed;
            if (r["c"].b) return false;
            j++;
        }
        i = j;
    }
    return true;
}

// [EVENT_LOOP_FAN_IN_V1] The front's connections live in one epoll set, one-shot: a connection is read only when it
// has no pipeline in flight. One pass of the loop reads every connection that is ready and puts each one's pipeline on
// the list, then wakes ONE dispatcher: everything ready goes to the region as one batch. The dispatcher writes each
// connection's replies and re-arms it. No thread per connection, so no thread hand-off per pipeline. A connection whose
// pipeline holds a blocking command (BLPOP ...) leaves the set for a thread of its own until that command returns.
// [LOOP_PER_CORE_V1] one event loop per core (RIBBIT_FRONT_LOOPS), connections dealt round-robin at accept: reading the
// clients, parsing, and gathering batches run on every core at once. Stock Redis does all of it on one thread.
static std::vector<int> g_eps;
static void rearm(Conn* c) { epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.ptr = c; epoll_ctl(c->ep, EPOLL_CTL_MOD, c->fd, &ev); }
static void finish_conn(Conn* c) {
    // the connection is over: the region drops its record (and a MULTI queue, if it ended inside one)
    // [NOBODY_WAITS_V1] nobody waits for it: this may run on a session's reader thread, which a wait would deadlock
    if (auto s = g_sess.load()) s->call_async("POST", g_api + "?op=redis", "{\"gone\":" + std::to_string(c->id) + ",\"token\":" + Json::quote(c->token) + "}", [](int, std::string, std::string) {});
    epoll_ctl(c->ep, EPOLL_CTL_DEL, c->fd, nullptr);
    ::close(c->fd); delete c;
}
static void complete_async(Job* j) {
    Conn& c = *j->conn; std::string out;
    bool keep = take_reply(c, *j, out);
    if (j->fatal && keep) { Reply r; r.error("ERR " + j->fatal_err); out += r.out; keep = false; }
    delete j;
    if (!out.empty() && !send_all(c.fd, out)) keep = false;
    if (keep) rearm(&c); else finish_conn(&c);
}
static void blocking_turn(Conn* c, ParseResult pr) {
    std::string out;
    bool keep = run_pipeline(*c, pr.cmds, out);
    if (pr.fatal && keep) { Reply r; r.error("ERR " + pr.err); out += r.out; keep = false; }
    if (!out.empty() && !send_all(c->fd, out)) keep = false;
    if (keep) rearm(c); else finish_conn(c);
}
static void event_loop(int ep) {
    epoll_event evs[512]; char buf[65536];
    for (;;) {
        int n = epoll_wait(ep, evs, 512, -1);
        if (n < 0) { if (errno == EINTR) continue; logmsg('#', std::string("epoll_wait: ") + strerror(errno)); _exit(1); }
        std::vector<Job*> batch;
        for (int k = 0; k < n; k++) {
            Conn* c = static_cast<Conn*>(evs[k].data.ptr);
            ssize_t r = ::recv(c->fd, buf, sizeof buf, MSG_DONTWAIT);
            if (r < 0 && (errno == EAGAIN || errno == EINTR)) { rearm(c); continue; }
            if (r <= 0) { finish_conn(c); continue; }
            c->parser.feed(buf, (size_t)r);
            c->parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
            ParseResult pr = c->parser.drain();
            if (pr.cmds.empty()) {
                if (pr.fatal) { Reply e; e.error("ERR " + pr.err); send_all(c->fd, e.out); finish_conn(c); }
                else rearm(c);                                          // a partial command: wait for the rest
                continue;
            }
            bool blocking = false; for (auto& a : pr.cmds) if (is_blocking_class(a)) { blocking = true; break; }
            if (blocking) { std::thread(blocking_turn, c, std::move(pr)).detach(); continue; }
            std::string pipeline; for (auto& a : pr.cmds) pipeline += resp_of(a);
            Job* j = new Job; j->conn = c; j->fatal = pr.fatal; j->fatal_err = pr.err;
            j->id = c->id; j->token = c->token; j->p = std::move(pipeline);
            batch.push_back(j);
        }
        if (!batch.empty()) send_batch(std::move(batch));               // everything this pass read: one batch, sent now
    }
}
static void accept_conn(int fd, std::string addr, std::string laddr) {
    Conn* c = new Conn; c->fd = fd;
    try {                                   // the region assigns the id: unique across every front of the region
        // every accept asks for its own id: the request carries this front's nonce and accept count, so the client's
        // coalescing ([CLIENT_COALESCES_V1]) never serves two connections one id and token
        static std::atomic<uint64_t> accepts{0};
        Json r = g_ram->call("POST", g_api + "?op=redis", "{\"new_id\":true,\"n\":\"" + g_nonce + ":" + std::to_string(++accepts) + "\",\"ctx\":" + Json::quote(initial_ctx(0, addr, laddr)) + "}");
        if (r["id"].type != Json::Num) throw std::runtime_error("no id");
        c->id = (uint64_t)r["id"].n;
        if (r["token"].type != Json::Str || r["token"].s.size() != 32) throw std::runtime_error("no token (the raw batch carries 32 bytes of it)");
        c->token = r["token"].s;
    } catch (const std::exception& e) { std::string m = region_error(e.what()); send_all(fd, m); ::close(fd); delete c; return; }
    c->authed = g.requirepass.empty(); c->parser.authenticated = c->authed;
    epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.ptr = c;
    static std::atomic<uint32_t> deal{0};
    c->ep = g_eps[deal.fetch_add(1, std::memory_order_relaxed) % g_eps.size()];
    epoll_ctl(c->ep, EPOLL_CTL_ADD, fd, &ev);
}

// A host of its own, on a loopback port, when no RAM host is named: dies with this process (PR_SET_PDEATHSIG).
static std::string start_own_host(const std::string& self) {
    int s = ::socket(AF_INET, SOCK_STREAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ::bind(s, (sockaddr*)&a, sizeof a); socklen_t l = sizeof a; getsockname(s, (sockaddr*)&a, &l); int port = ntohs(a.sin_port); ::close(s);
    const char* hb = getenv("RIBBIT_REDIS_HOST");
    std::string host_bin = hb ? hb : self.substr(0, self.rfind('/') + 1) + "ribbit-redis-host";
    std::string listen = "127.0.0.1:" + std::to_string(port);
    {   // [REGION_CONFIG_IS_THE_HOSTS_V1] the host this front starts accepts this front's configuration, and nobody else's
        unsigned char r[16]; FILE* u = fopen("/dev/urandom", "rb"); if (!u || fread(r, 1, sizeof r, u) != sizeof r) { fprintf(stderr, "no /dev/urandom\n"); _exit(1); } fclose(u);
        char h[33]; for (int k = 0; k < 16; k++) snprintf(h + 2 * k, 3, "%02x", r[k]); g_admin = h;
        setenv("RIBBIT_REDIS_ADMIN", h, 1);
    }
    g_host_pid = fork();
    if (g_host_pid == 0) {
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        int dn = open("/dev/null", O_WRONLY); if (dn >= 0) { dup2(dn, 1); }
        execl(host_bin.c_str(), host_bin.c_str(), "--listen", listen.c_str(), "--api", g_api.c_str(), "--quiet", (char*)nullptr);
        fprintf(stderr, "cannot start the RAM host %s: %s\n", host_bin.c_str(), strerror(errno)); _exit(127);
    }
    return listen;
}

static void on_term(int) { if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(0); }

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    g.executable = argv[0]; g.start_ms = Memory::now_ms();
    int from = 1;
    if (argc > 1 && argv[1][0] != '-') { g.config_file = argv[1]; from = 2; }
    g.cfg.init_defaults();
    { char cwd[4096]; if (getcwd(cwd, sizeof cwd)) g.cfg.set("dir", cwd); }   // Redis: dir is the working directory at startup
    if (!g.config_file.empty()) g.cfg.load_file(g.config_file);
    g.cfg.apply_args(argc, argv, from);
    g.port = atoi(g.cfg.get("port").c_str());
    g.requirepass = g.cfg.get("requirepass");
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    std::string lf = g.cfg.get("logfile");
    if (!lf.empty()) { FILE* f = fopen(lf.c_str(), "a"); if (f) log_fp = f; }
    const char* api = getenv("RIBBIT_API"); g_api = api ? api : "/Fawcett.Redis.ram_interface.php";
    // The semantic wire keeps large fields in a blob store (semtpl.hpp: FROGNET_BLOB_ROOT, default
    // /opt/frognet_semantic/blob_cache). Without a writable one, learning a large answer fails on both ends -- and
    // surfaced only as "REQ_RAW answered with something other than RESP_RAW". A front that names none gets its own,
    // private to this run, and the host it starts inherits it.
    if (!getenv("FROGNET_BLOB_ROOT")) {
        std::string root = "/tmp/ribbit-redis-blobs-" + std::to_string(getpid());
        if (::mkdir(root.c_str(), 0700) != 0 && errno != EEXIST) { logmsg('#', "Cannot create the blob store " + root + ": " + strerror(errno)); return 1; }
        setenv("FROGNET_BLOB_ROOT", root.c_str(), 1);
    }
    const char* ram = getenv("RIBBIT_RAM");
    std::string ep = ram ? ram : start_own_host(argv[0]);
    signal(SIGTERM, on_term); signal(SIGINT, on_term);
    // a front holding a named host's admin secret may configure it (an operator's front; the internet test wrapper)
    if (ram) if (const char* a = getenv("RIBBIT_REDIS_ADMIN")) g_admin = a;
    g_ram_host = ep.substr(0, ep.rfind(':')); g_ram_port = atoi(ep.c_str() + ep.rfind(':') + 1);
    for (int tries = 0; !g_sess.load(); tries++) {         // a self-started host needs a moment to listen; a named one must answer
        try { g_sess.store(std::make_shared<frogram::Session>(g_ram_host, g_ram_port, g_api)); }
        catch (const std::exception& e) {
            if (ram || tries > 200) { logmsg('#', std::string("Cannot reach the RAM host ") + ep + ": " + e.what()); on_term(0); }
            usleep(10000);
        }
    }
    if (!g_admin.empty()) {   // the host this front started takes this front's configuration (the test suite's case)
        std::string body = "{\"admin\":" + Json::quote(g_admin) + ",\"config\":{"; bool first = true;
        for (auto& [k, v] : g.cfg.v) { body += (first ? "" : ",") + Json::quote(k) + ":" + Json::quote(v); first = false; }
        Json r = g_ram->call("POST", g_api + "?op=redis", body + "}}");
        if (r["ok"].type != Json::Bool || !r["ok"].b) { logmsg('#', "The RAM host refused the configuration"); on_term(0); }
    }   // a named host was configured by whoever runs it (RIBBIT_REDIS_CONFIG); this front sends it nothing
    logmsg('*', "Redis Ribbit front, Redis compatibility 7.2.11, PID: " + std::to_string(getpid()) + ", region " + ep + g_api);
    logmsg('*', "Running mode=standalone, port=" + std::to_string(g.port) + ".");
    logmsg('#', "Server initialized");
    int lfd = ::socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa{}; sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)g.port); sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    { std::string b = g.cfg.get("bind"); if (b.find('*') != std::string::npos || b.find("0.0.0.0") != std::string::npos || b.empty()) sa.sin_addr.s_addr = htonl(INADDR_ANY); }
    if (::bind(lfd, (sockaddr*)&sa, sizeof sa) < 0 || ::listen(lfd, 511) < 0) {
        logmsg('#', "Failed listening on port " + std::to_string(g.port) + " (tcp), aborting."); on_term(0);
    }
    start_sessions();
    {   const char* e = getenv("RIBBIT_FRONT_LOOPS");      // default: half the cores (the other half write replies and run the host)
        int n = e ? atoi(e) : (int)std::thread::hardware_concurrency() / 2; if (n < 1) n = 1;
        for (int i = 0; i < n; i++) { int ep = epoll_create1(EPOLL_CLOEXEC); g_eps.push_back(ep); std::thread(event_loop, ep).detach(); }
    }
    logmsg('*', "Ready to accept connections tcp");
    for (;;) {
        sockaddr_in p{}; socklen_t pl = sizeof p;
        int fd = ::accept(lfd, (sockaddr*)&p, &pl);
        if (fd < 0) { if (errno == EINTR) continue; continue; }
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip);
        std::string addr = std::string(ip) + ":" + std::to_string(ntohs(p.sin_port));
        sockaddr_in la{}; socklen_t ll = sizeof la; getsockname(fd, (sockaddr*)&la, &ll); inet_ntop(AF_INET, &la.sin_addr, ip, sizeof ip);
        std::string laddr = std::string(ip) + ":" + std::to_string(ntohs(la.sin_port));
        accept_conn(fd, addr, laddr);
    }
}
