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
// redis_region.cpp -- the Redis region's operation, executed by the RAM host IN its memory [RESIDENT_REGION_V1].
//
// One call = one connection's pipeline: the commands a front read from one client socket, executed in order against
// the Redis memory resident in this process (g.mem, the lock-free Redis RAM implementation -- a region is its
// implementation), for the connection context the front sends. The host holds no connection: the context travels
// with the call and comes back changed (SELECT, HELLO, AUTH, CLIENT SETNAME/REPLY, MULTI's queue, WATCH).
// Nothing here blocks: a command that would park never reaches the region (the front waits, holding a read).
//
// Wire [REDIS_WIRE_FLAT_V1]: the semantic wire's templates hold strings and flat lists (v0.63's LISP region returns its
// result as JSON text for the same reason), so the request is flat: the connection's context as scalar fields plus
// "p", the pipeline's RESP bytes (base64), and "mx", MULTI/WATCH state as JSON text when there is any. The reply is
// {"ok":true,"r":<the replies' RESP bytes, base64>,"ctx":<the new context, JSON text>}. A shape the wire can learn
// lets repeated calls travel as differences instead of RAW with a failed learning attempt on both ends each time.
#include "server.hpp"
#include "door/redis_region.h"
#include "frogram.hpp"
#include <cstring>
#include <random>
#include <chrono>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <thread>
#include <atomic>

namespace rr {
namespace door {

static const char* B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
std::string b64enc(const std::string& in) {
    std::string out; out.reserve((in.size() + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < in.size(); i += 3) {
        uint32_t v = (uint8_t)in[i] << 16 | (uint8_t)in[i + 1] << 8 | (uint8_t)in[i + 2];
        out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += B64[v & 63];
    }
    if (i + 1 == in.size()) { uint32_t v = (uint8_t)in[i] << 16; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += "=="; }
    else if (i + 2 == in.size()) { uint32_t v = (uint8_t)in[i] << 16 | (uint8_t)in[i + 1] << 8; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += '='; }
    return out;
}
std::string b64dec(const std::string& in) {
    static int8_t T[256]; static bool init = [] { memset(T, -1, sizeof T); for (int k = 0; k < 64; k++) T[(uint8_t)B64[k]] = (int8_t)k; return true; }(); (void)init;
    std::string out; out.reserve(in.size() / 4 * 3);
    uint32_t acc = 0; int bits = 0;
    for (unsigned char ch : in) {
        if (ch == '=') break;
        int8_t v = T[ch];
        if (v < 0) throw std::runtime_error("redis region: argument is not base64");
        acc = acc << 6 | (uint32_t)v; bits += 6;
        if (bits >= 8) { bits -= 8; out += (char)((acc >> bits) & 0xff); }
    }
    return out;
}

// The region comes up once, with Redis's defaults; the front's configuration arrives as CONFIG SET like any client's.
static void init_once() {
    static bool done = [] {
        g.start_ms = Memory::now_ms();
        g.executable = "ribbit-redis-host";
        g.cfg.init_defaults();
        // [REGION_CONFIG_IS_THE_HOSTS_V1] a host started on its own reads its Redis configuration from a file it is
        // given; after that it changes only through CONFIG SET, a command gated by AUTH like any other
        { char cwd[4096]; if (g.cfg.get("dir").empty() && getcwd(cwd, sizeof cwd)) g.cfg.set("dir", cwd); }   // Redis: the working directory at startup
        if (const char* f = getenv("RIBBIT_REDIS_CONFIG")) { g.config_file = f; g.cfg.load_file(f); }
        g.requirepass = g.cfg.get("requirepass");
        g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
        g.databases = atoi(g.cfg.get("databases").c_str()); if (g.databases <= 0) g.databases = 16;
        { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
        std::vector<std::string> svcs; for (int d = 0; d < g.databases; d++) { svcs.push_back(db::svc(d)); svcs.push_back(db::claims(d)); }
        svcs.push_back("batches"); g.mem.init(svcs);
        register_all_commands();
        return true;
    }();
    (void)done;
}

using frogram::Json;
static std::string q(const std::string& s) { return Json::quote(s); }
static std::string num(long long v) { return std::to_string(v); }
static const std::string& S(const Json& j, const char* k) { static const std::string e; return j[k].type == Json::Str ? j[k].s : e; }
static long long N(const Json& j, const char* k, long long d = 0) { return j[k].type == Json::Num ? (long long)j[k].n : d; }
static bool B(const Json& j, const char* k) { return j[k].type == Json::Bool && j[k].b; }

// context in: the connection as the front knows it
static void ctx_in(Client& c, const Json& x) {
    c.id = (uint64_t)N(x, "id"); c.db = (int)N(x, "db"); c.proto = (int)N(x, "proto", 2);
    c.authed = x["authed"].type == Json::Bool ? x["authed"].b : g.requirepass.empty();
    c.user = S(x, "user").empty() ? "default" : S(x, "user");
    c.name = S(x, "name"); c.libname = S(x, "libname"); c.libver = S(x, "libver");
    c.addr = S(x, "addr"); c.laddr = S(x, "laddr");
    c.reply_mode = (int)N(x, "reply_mode"); c.skip_count = (int)N(x, "skip_count");
    c.no_evict = B(x, "no_evict"); c.no_touch = B(x, "no_touch");
    c.flags = S(x, "flags").empty() ? "N" : S(x, "flags");
    c.created_ms = N(x, "created_ms"); c.last_cmd_ms = Memory::now_ms();
    if (!S(x, "mx").empty()) {
        Json m = Json::parse(S(x, "mx"));
        c.in_multi = B(m, "in_multi"); c.multi_err = B(m, "multi_err");
        if (m["watches"].type == Json::Arr)
            for (auto& w : m["watches"].a) c.watches.push_back(Client::Watch{(int)N(w, "db"), b64dec(S(w, "key")), (uint64_t)N(w, "version")});
    }
}
// [MULTI_QUEUE_IN_THE_REGION_V1] A MULTI's queued commands stay here, per connection id -- they used to travel in the
// context, both ways on every call, so queuing N commands cost O(N^2) bytes (10,000 XADDs inside MULTI stalled).
// One connection's calls never overlap (its front serves them in order), so its entry is never touched concurrently.
// EXEC and DISCARD clear it; a front whose connection ends mid-MULTI sends "gone". Only a front that dies mid-MULTI
// leaves one queue behind.
static LFMap<uint64_t, std::vector<Argv>*> queues;
static void queue_load(Client& c) {
    auto* n = queues.get(c.id);
    if (n && n->val) c.queued = std::move(*n->val);
}
static void queue_store(const Client& c) {
    auto* n = queues.get(c.id);
    if (c.in_multi && !c.queued.empty()) {
        if (n && n->val) *n->val = c.queued;
        else queues.add(c.id, new std::vector<Argv>(c.queued));
    } else if (n) { std::vector<Argv>* out = nullptr; if (queues.remove(c.id, &out) && out) ebr::retire(out); }
}
static std::string gone(uint64_t id) {
    ebr::Guard epoch; std::vector<Argv>* out = nullptr;
    if (queues.remove(id, &out) && out) ebr::retire(out);
    return "{\"ok\":true}";
}
// MULTI/WATCH state as JSON text ("" when there is none: the common case stays flat and small); the queue is not in it
static std::string mx_out(const Client& c) {
    if (!c.in_multi && !c.multi_err && c.watches.empty()) return "";
    std::string o = std::string("{\"in_multi\":") + (c.in_multi ? "true" : "false") + ",\"multi_err\":" + (c.multi_err ? "true" : "false");
    o += ",\"watches\":[";
    for (size_t i = 0; i < c.watches.size(); i++)
        o += (i ? "," : "") + std::string("{\"db\":") + num(c.watches[i].db) + ",\"key\":" + q(b64enc(c.watches[i].key)) + ",\"version\":" + num((long long)c.watches[i].version) + "}";
    return o + "]}";
}
// context out: everything a command may have changed
static std::string ctx_out(const Client& c) {
    std::string o = "{\"id\":" + num((long long)c.id) + ",\"db\":" + num(c.db) + ",\"proto\":" + num(c.proto) +
        ",\"authed\":" + (c.authed ? "true" : "false") + ",\"user\":" + q(c.user) + ",\"name\":" + q(c.name) +
        ",\"libname\":" + q(c.libname) + ",\"libver\":" + q(c.libver) + ",\"addr\":" + q(c.addr) + ",\"laddr\":" + q(c.laddr) +
        ",\"reply_mode\":" + num(c.reply_mode) + ",\"skip_count\":" + num(c.skip_count) +
        ",\"close_after_reply\":" + (c.close_after_reply ? "true" : "false") +
        ",\"no_evict\":" + (c.no_evict ? "true" : "false") + ",\"no_touch\":" + (c.no_touch ? "true" : "false") +
        ",\"flags\":" + q(c.flags) + ",\"created_ms\":" + num(c.created_ms) +
        ",\"mx\":" + q(mx_out(c)) + "}";
    return o;
}
// The front's configuration, as loaded from its file and command line, applied to the region's config (it is
// region truth: CONFIG GET on any front sees it) and to the fields the command code reads directly.
static void apply_config(const Json& cfg) {
    for (auto& [k, v] : cfg.o) if (v.type == Json::Str) g.cfg.set(k, v.s);
    g.requirepass = g.cfg.get("requirepass");
    g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
}

// [REGION_HELD_AUTH_V1] Across the Internet the host cannot trust what a front says about a connection's
// authentication: anything that reaches the host port could claim "authed":true. So the region keeps it. A connection
// gets its id (Redis: unique and increasing for the server's life, so unique across fronts) and a random token; every
// call must present both; authed and user are taken from the region's record, never from the call, and written back
// after the call. A front sends "gone" when a connection ends; only a front that dies leaves records behind.
// [CONTEXT_IN_THE_REGION_V1] the connection's context (SELECT, HELLO, CLIENT SETNAME/REPLY, MULTI/WATCH state) is held
// here too, with its record: it used to travel in every call both ways, ~460 bytes the semantic wire had to scan per
// pipeline for a context that almost never changes. A call now carries id, token and the pipeline; the reply carries
// the replies, whether the connection is authenticated and whether it must close.
struct Snap {               // the context's fields, copied in and out of a Client -- no JSON on the batch path
    int db = 0, proto = 2, reply_mode = 0, skip_count = 0; bool no_evict = false, no_touch = false, in_multi = false, multi_err = false;
    std::string name, libname, libver, addr, laddr, flags = "N"; int64_t created_ms = 0; std::vector<Client::Watch> watches;
};
static void snap_in(Client& c, const Snap& s, uint64_t id) {
    c.id = id; c.db = s.db; c.proto = s.proto; c.reply_mode = s.reply_mode; c.skip_count = s.skip_count;
    c.no_evict = s.no_evict; c.no_touch = s.no_touch; c.in_multi = s.in_multi; c.multi_err = s.multi_err;
    c.name = s.name; c.libname = s.libname; c.libver = s.libver; c.addr = s.addr; c.laddr = s.laddr; c.flags = s.flags;
    c.created_ms = s.created_ms; c.last_cmd_ms = Memory::now_ms(); c.watches = s.watches;
}
static void snap_out(Snap& s, const Client& c) {
    s.db = c.db; s.proto = c.proto; s.reply_mode = c.reply_mode; s.skip_count = c.skip_count;
    s.no_evict = c.no_evict; s.no_touch = c.no_touch; s.in_multi = c.in_multi; s.multi_err = c.multi_err;
    s.name = c.name; s.libname = c.libname; s.libver = c.libver; s.addr = c.addr; s.laddr = c.laddr; s.flags = c.flags;
    s.created_ms = c.created_ms; s.watches = c.watches;
}
struct ConnRec { std::string token; bool authed; std::string user; std::string ctx; Snap snap; bool has_snap = false; };
static LFMap<uint64_t, ConnRec*> conns;
static std::string random_token() {
    static thread_local std::mt19937_64 r{std::random_device{}() ^ (uint64_t)std::chrono::steady_clock::now().time_since_epoch().count()};
    char b[33]; snprintf(b, sizeof b, "%016llx%016llx", (unsigned long long)r(), (unsigned long long)r()); return b;
}
static std::string ctx_out(const Client& c);
static std::string new_id(const Json& in) {
    ebr::Guard epoch;
    uint64_t id = g.next_client_id++; std::string tok = random_token();
    std::string ctx;
    if (in["ctx"].type == Json::Str) {        // the connection as its front first knows it (addresses, creation time)
        Client c; ctx_in(c, Json::parse(in["ctx"].s)); c.id = id; c.authed = g.requirepass.empty(); c.user = "default";
        ctx = ctx_out(c);
        auto* rec = new ConnRec{tok, g.requirepass.empty(), "default", ctx, Snap{}, false};
        snap_out(rec->snap, c); rec->has_snap = true;
        conns.add(id, rec);
    } else conns.add(id, new ConnRec{tok, g.requirepass.empty(), "default", ctx, Snap{}, false});
    return "{\"ok\":true,\"id\":" + num((long long)id) + ",\"token\":" + q(tok) + "}";
}
// the record of the connection a call names, if its token matches; nullptr otherwise
static ConnRec* conn_of(uint64_t id, const std::string& token) {
    auto* n = conns.get(id);
    if (!n || !n->val || n->val->token != token) return nullptr;
    return n->val;
}
static std::string gone_conn(uint64_t id, const std::string& token) {
    ebr::Guard epoch;
    if (!conn_of(id, token)) return "{\"ok\":false,\"error\":\"no such connection\"}";
    ConnRec* r = nullptr; if (conns.remove(id, &r) && r) ebr::retire(r);
    return gone(id);
}
// The front saw its client hang up while a blocking command of that client is in flight: leave now.
// The cancel can arrive before the command has started (it runs inline; the command starts on its own thread), so it is
// sticky: the cancel first records the id, THEN looks for the parked client; the command first registers, THEN checks
// the record. Each writes before it reads the other's structure, so at least one of them sees the other. An entry can
// outlive its command only if the command finished at the moment its client hung up: one small entry, never read.
static LFMap<uint64_t, int> cancelled;
static std::string cancel(uint64_t id) {
    ebr::Guard epoch;
    cancelled.add(id, 1);
    auto* n = g.clients.get(id);
    Client* found = n ? n->val : nullptr;
    if (found) {
        Client& c = *found; c.closing = true;
        if (WaitWord* w = c.waiting_on.load(std::memory_order_acquire)) {
            w->gen.fetch_add(1, std::memory_order_release);
            syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w->gen), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0);
        }
    }
    return std::string("{\"ok\":true,\"found\":") + (found ? "true" : "false") + "}";
}

std::string call(const std::string& body, bool blocking) {
    init_once();
    Json in = Json::parse(body);
    if (in["config"].type == Json::Obj) {
        // only whoever started this host (it holds RIBBIT_REDIS_ADMIN) may push a configuration: across the Internet
        // anyone reaching the port could otherwise send {"requirepass":""}. A host with no admin secret refuses.
        static const char* admin = getenv("RIBBIT_REDIS_ADMIN");
        if (!admin || !*admin || in["admin"].type != Json::Str || in["admin"].s != admin)
            throw std::runtime_error("redis region: configuration is not accepted from this caller (use CONFIG SET after AUTH, or start the host with RIBBIT_REDIS_CONFIG)");
        apply_config(in["config"]); return "{\"ok\":true}";
    }
    if (in["new_id"].type == Json::Bool) return new_id(in);
    if (in["cancel"].type == Json::Num) {
        ebr::Guard epoch;
        if (!conn_of((uint64_t)in["cancel"].n, in["token"].type == Json::Str ? in["token"].s : std::string()))
            throw std::runtime_error("redis region: cancel for a connection this caller does not hold");
        return cancel((uint64_t)in["cancel"].n);
    }
    if (in["gone"].type == Json::Num) return gone_conn((uint64_t)in["gone"].n, in["token"].type == Json::Str ? in["token"].s : std::string());
    if (in["p"].type != Json::Str) throw std::runtime_error("redis region: missing p (the pipeline)");
    // A blocking command parks here, on its own request thread, in the memory -- exactly as a held read parks on
    // MEM -- and while it is parked the region knows it: CLIENT UNBLOCK from any connection of any front finds it.
    std::unique_ptr<Client> holder(new Client);
    Client& c = *holder;
    ebr::Guard cepoch;
    ConnRec* rec = conn_of((uint64_t)N(in, "id"), S(in, "token"));
    if (!rec) throw std::runtime_error("redis region: the call does not name a connection the region issued (id and token)");
    const bool held = in["db"].type != Json::Num && !rec->ctx.empty();   // [CONTEXT_IN_THE_REGION_V1] no context in the call
    if (held) { if (rec->has_snap) snap_in(c, rec->snap, (uint64_t)N(in, "id")); else ctx_in(c, Json::parse(rec->ctx)); } else ctx_in(c, in);
    c.authed = rec->authed; c.user = rec->user;          // never from the call
    struct Registered {
        Client* c; bool on;
        Registered(Client* cp, bool b) : c(cp), on(b) { if (on) g.clients.add(c->id, c); }
        ~Registered() { if (on) { Client* out = nullptr; g.clients.remove(c->id, &out); } }
    } reg(&c, blocking);
    if (blocking) { int was = 0; if (cancelled.remove(c.id, &was)) c.closing = true; }   // its client already hung up
    c.region_blocked = blocking;
    ebr::Guard qepoch;
    if (c.in_multi) queue_load(c);
    RespParser parser; parser.authenticated = true; parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
    const std::string p = b64dec(in["p"].s);
    parser.feed(p.data(), p.size());
    ParseResult pr = parser.drain();
    if (pr.fatal) throw std::runtime_error("redis region: the pipeline is not RESP: " + pr.err);   // the front sent it
    std::string out;
    for (auto& argv : pr.cmds) {
        Reply r; r.proto = c.proto;
        execute_command(c, argv, r);
        // per command, as the one-process front did: CLIENT REPLY OFF / SKIP swallow replies; QUIT closes after its own
        bool suppress = false;
        if (c.reply_mode == 1) suppress = true;
        else if (c.skip_count > 0) { suppress = true; c.skip_count--; }
        if (!suppress) out += r.out;
        if (!strcasecmp(argv[0].c_str(), "quit")) c.close_after_reply = true;
        if (c.close_after_reply) break;
    }
    queue_store(c);
    rec->authed = c.authed; rec->user = c.user;          // one connection's calls never overlap: this record is its own
    if (c.counted_blocked) g.blocked_clients--;          // after the command and its accounting: INFO is never ahead
    std::string res;
    if (held) {
        snap_out(rec->snap, c); rec->has_snap = true;   // one connection's calls never overlap: this record is its own
        res = std::string("{\"ok\":true,\"r\":") + q(b64enc(out)) + ",\"a\":" + (c.authed ? "true" : "false") + ",\"c\":" + (c.close_after_reply ? "true" : "false") + "}";
    } else res = "{\"ok\":true,\"r\":" + q(b64enc(out)) + ",\"ctx\":" + q(ctx_out(c)) + "}";
    if (blocking) {                       // unregistered now; freed once no epoch can still see it (CLIENT LIST walkers)
        Client* out = nullptr; g.clients.remove(c.id, &out); reg.on = false;
        ebr::retire(holder.release());
    }
    return res;
}

// ---- [FAN_IN_BATCH_V1] fan-in at the front, fan-out here -------------------------------------------------------------
// A batch is many connections' pipelines in one call. Its items are independent (one connection never has two items
// in flight: its front sends the next only after the reply), so they run across the host's cores and are gathered
// back in order. The pool's workers never take a lock: a batch is offered in one of a fixed set of slots, workers claim
// its items with one fetch_add each, and the caller -- which works its own batch too -- waits only for the items in
// flight when it runs out. A worker reads the slots inside an epoch; the caller retires the batch through the same
// epochs (ebr), so no worker can still hold it when it is freed.
namespace {
// one connection's pipeline, its context held here: the replies, then whether it is authenticated and must close
struct Item { uint64_t id = 0; std::string token, p, r, err; bool authed = false, close = false, raw = false; };
void run_item(Item& it) {
    try {
        std::unique_ptr<Client> holder(new Client); Client& c = *holder;
        ebr::Guard cepoch;
        ConnRec* rec = conn_of(it.id, it.token);
        if (!rec || !rec->has_snap) throw std::runtime_error("redis region: the call does not name a connection the region issued (id and token)");
        snap_in(c, rec->snap, it.id);
        c.authed = rec->authed; c.user = rec->user;          // never from the call
        ebr::Guard qepoch;
        if (c.in_multi) queue_load(c);
        RespParser parser; parser.authenticated = true; parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
        const std::string pb = it.raw ? it.p : b64dec(it.p);
        parser.feed(pb.data(), pb.size());
        ParseResult pr = parser.drain();
        if (pr.fatal) throw std::runtime_error("redis region: the pipeline is not RESP: " + pr.err);
        std::string out;
        for (auto& argv : pr.cmds) {
            Reply r; r.proto = c.proto;
            execute_command(c, argv, r);
            bool suppress = false;
            if (c.reply_mode == 1) suppress = true;
            else if (c.skip_count > 0) { suppress = true; c.skip_count--; }
            if (!suppress) out += r.out;
            if (!strcasecmp(argv[0].c_str(), "quit")) c.close_after_reply = true;
            if (c.close_after_reply) break;
        }
        queue_store(c);
        rec->authed = c.authed; rec->user = c.user; snap_out(rec->snap, c);
        it.r = it.raw ? std::move(out) : b64enc(out); it.authed = c.authed; it.close = c.close_after_reply;
    } catch (const std::exception& e) { it.err = e.what(); }
}
struct Batch {
    std::vector<Item> items;
    std::atomic<size_t> next{0};
    std::atomic<uint32_t> left{0};
    // [NOBODY_WAITS_V1] an asynchronous batch: whoever finishes its last item answers it (done) and frees it; nothing
    // waits for it. A batch with no done is the caller's: the last item wakes the caller.
    std::function<void(std::string)> done; int slot = -1;
};
void finish_async(Batch* b);
// raw futexes: std::atomic::wait spins on sched_yield first, which on a small machine takes the cores from the workers
void fwait(std::atomic<uint32_t>& w, uint32_t v) { while (w.load(std::memory_order_acquire) == v) syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, v, nullptr, nullptr, 0); }
void fwake(std::atomic<uint32_t>& w, int n) { syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, n, nullptr, nullptr, 0); }
constexpr int SLOTS = 64;
std::atomic<Batch*> slots[SLOTS];
std::atomic<uint32_t> offered{0};                  // bumped on every offer: the futex the workers sleep on
// claim and run items until none are left; true if it ran any
bool work(Batch* b) {
    bool ran = false;
    for (;;) {
        size_t i = b->next.fetch_add(1, std::memory_order_acq_rel);
        if (i >= b->items.size()) return ran;
        run_item(b->items[i]); ran = true;
        if (b->left.fetch_sub(1, std::memory_order_acq_rel) == 1) { if (b->done) finish_async(b); else fwake(b->left, INT32_MAX); }
    }
}
void worker() {
    for (;;) {
        uint32_t seen = offered.load(std::memory_order_acquire);
        bool any = false;
        {   ebr::Guard epoch;                                             // a batch seen here is not freed until we leave
            for (int k = 0; k < SLOTS; k++) { Batch* b = slots[k].load(std::memory_order_acquire); if (b) any |= work(b); }
        }
        if (!any) fwait(offered, seen);                                  // nothing to do until the next offer
    }
}
void start_pool() {
    static bool started = [] {
        unsigned n = std::thread::hardware_concurrency(); if (n < 2) n = 2;
        for (unsigned i = 0; i + 1 < n; i++) std::thread(worker).detach();   // the caller is the last core's worker
        return true;
    }();
    (void)started;
}
}  // namespace

// [FAN_IN_BATCH_V1] {"ids":[n...],"tokens":[s...],"ps":[s...]} -> {"ok":true,"r":[s...],"a":"0101..","c":"0010..","e":[s...]}:
// parallel lists, so the wire carries each item's few fields once and never JSON inside JSON.
std::string batch(const std::string& body) {
    init_once(); start_pool();
    Json in = Json::parse(body);
    if (in["ids"].type != Json::Arr || in["tokens"].type != Json::Arr || in["ps"].type != Json::Arr ||
        in["ids"].a.size() != in["tokens"].a.size() || in["ids"].a.size() != in["ps"].a.size())
        throw std::runtime_error("redis region: a batch is three lists of one length: ids, tokens, ps");
    auto* b = new Batch;
    size_t n = in["ids"].a.size(); b->items.resize(n);
    for (size_t i = 0; i < n; i++) {
        if (in["ids"].a[i].type != Json::Num || in["tokens"].a[i].type != Json::Str || in["ps"].a[i].type != Json::Str) { delete b; throw std::runtime_error("redis region: batch item " + std::to_string(i) + " is malformed"); }
        b->items[i].id = (uint64_t)in["ids"].a[i].n; b->items[i].token = in["tokens"].a[i].s; b->items[i].p = in["ps"].a[i].s;
    }
    b->left.store((uint32_t)n, std::memory_order_release);
    int slot = -1;
    if (n > 1) {                                      // one item is run where it is: nothing to fan out
        for (int k = 0; k < SLOTS && slot < 0; k++) { Batch* e = nullptr; if (slots[k].compare_exchange_strong(e, b)) slot = k; }
        if (slot >= 0) { offered.fetch_add(1, std::memory_order_acq_rel); fwake(offered, INT32_MAX); }
        // every slot taken: this batch runs on this thread alone -- the same work, without helpers
    }
    work(b);
    for (uint32_t l; (l = b->left.load(std::memory_order_acquire)) != 0; ) fwait(b->left, l);
    std::string o = "{\"ok\":true,\"r\":[", a, c, e = "[";
    for (size_t i = 0; i < n; i++) {
        const Item& it = b->items[i];
        if (i) { o += ','; e += ','; }
        o += q(it.r); e += q(it.err); a += it.authed ? '1' : '0'; c += it.close ? '1' : '0';
    }
    o += "],\"a\":" + q(a) + ",\"c\":" + q(c) + ",\"e\":" + e + "]}";
    if (slot >= 0) { slots[slot].store(nullptr, std::memory_order_release); ebr::Guard epoch; ebr::retire(b); }
    else delete b;
    return o;
}

// [RESP_ON_THE_WIRE_V1] The same batch with no JSON at all: the body is the pipelines' RESP, framed in text.
// Request: "RRB1 <count>\n", then per item "<id> <token>\n" and its pipeline as a payload. Answer: "RRA1 <count>\n",
// then per item "<status><authenticated><close>\n" (each '0' or '1'; status 1 = refused) and the replies (or the
// refusal's text) as a payload. A payload is "T<len>\n<RESP>" -- or "B<len>\n<base64>" when the bytes are not UTF-8.
namespace {
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
    size_t e = s.find('\n', at); if (e == std::string::npos) throw std::runtime_error("raw batch truncated");
    std::string l = s.substr(at, e - at); at = e + 1; return l;
}
static std::string get_text(const std::string& s, size_t& at, std::string (*b64d)(const std::string&)) {
    std::string h = get_line(s, at);
    if (h.empty() || (h[0] != 'T' && h[0] != 'B')) throw std::runtime_error("raw batch: a payload header is malformed");
    size_t len = std::stoull(h.substr(1));
    if (at + len > s.size()) throw std::runtime_error("raw batch truncated");
    std::string v = s.substr(at, len); at += len;
    return h[0] == 'T' ? v : b64d(v);
}
}  // namespace
Batch* parse_raw(const std::string& body) {
    size_t at = 0;
    std::string h = get_line(body, at);
    if (h.compare(0, 5, "RRB1 ") != 0) throw std::runtime_error("redis region: not a raw batch (RRB1)");
    uint32_t n = (uint32_t)std::stoul(h.substr(5));
    auto* b = new Batch; b->items.resize(n);
    try {
        for (uint32_t i = 0; i < n; i++) {
            Item& it = b->items[i]; it.raw = true;
            std::string l = get_line(body, at); size_t sp = l.find(' ');
            if (sp == std::string::npos) throw std::runtime_error("redis region: raw batch item header is malformed");
            it.id = std::stoull(l.substr(0, sp)); it.token = l.substr(sp + 1);
            it.p = get_text(body, at, b64dec);
        }
    } catch (...) { delete b; throw; }
    return b;
}
std::string raw_answer(const Batch* b) {
    const uint32_t n = (uint32_t)b->items.size();
    std::string o = "RRA1 " + std::to_string(n) + "\n";
    for (uint32_t i = 0; i < n; i++) {
        const Item& it = b->items[i]; const bool bad = !it.err.empty();
        o += bad ? '1' : '0'; o += it.authed ? '1' : '0'; o += it.close ? '1' : '0'; o += '\n';
        put_text(o, bad ? it.err : it.r, b64enc);
    }
    return o;
}
std::string batch_raw(const std::string& body) {
    init_once(); start_pool();
    Batch* b = parse_raw(body);
    const uint32_t n = (uint32_t)b->items.size();
    b->left.store(n, std::memory_order_release);
    int slot = -1;
    if (n > 1) {
        for (int k = 0; k < SLOTS && slot < 0; k++) { Batch* e = nullptr; if (slots[k].compare_exchange_strong(e, b)) slot = k; }
        if (slot >= 0) { offered.fetch_add(1, std::memory_order_acq_rel); fwake(offered, INT32_MAX); }
    }
    work(b);
    for (uint32_t l; (l = b->left.load(std::memory_order_acquire)) != 0; ) fwait(b->left, l);
    std::string o = raw_answer(b);
    if (slot >= 0) { slots[slot].store(nullptr, std::memory_order_release); ebr::Guard epoch; ebr::retire(b); }
    else delete b;
    return o;
}
// [NOBODY_WAITS_V1] John 2026-10-14: "why is the RAM host creating a new thread? It should be using a thread pool" and
// "there's nothing requiring us to wait, is there?" -- there is not. The batch is parsed on the socket's thread and
// offered to the pool; the socket thread works on it too and goes back to its socket as soon as no item is left to
// claim. Whoever finishes the LAST item builds the answer and sends it (done). No thread is made, none waits.
// One item runs right here: handing it to another core would cost more than running it.
namespace {
void finish_async(Batch* b) {
    std::string o = raw_answer(b);
    auto done = std::move(b->done);
    if (b->slot >= 0) { slots[b->slot].store(nullptr, std::memory_order_release); ebr::retire(b); }   // every toucher is inside an epoch
    done(std::move(o));
}
}  // namespace
void batch_raw_async(const std::string& body, std::function<void(std::string)> done) {
    init_once(); start_pool();
    Batch* b = parse_raw(body);
    const uint32_t n = (uint32_t)b->items.size();
    b->left.store(n, std::memory_order_release);
    if (n == 0) { std::string o = raw_answer(b); delete b; done(std::move(o)); return; }
    b->done = std::move(done);
    // The epoch is entered BEFORE the batch is published: from the moment it is in a slot, a worker may finish its last
    // item and retire it, and this thread must not touch it after that unless it is inside an epoch.
    ebr::Guard epoch;
    int slot = -1;
    if (n > 1)
        for (int k = 0; k < SLOTS; k++) { b->slot = k; Batch* e = nullptr; if (slots[k].compare_exchange_strong(e, b)) { slot = k; break; } }
    if (slot >= 0) { offered.fetch_add(1, std::memory_order_acq_rel); fwake(offered, INT32_MAX); work(b); return; }
    b->slot = -1;
    work(b);            // in no slot, no other thread can see it: this thread runs it all, and its last item answered and ...
    delete b;           // ... left it here to free
}

}  // namespace door
}  // namespace rr

// The one symbol the host sees: no Redis type crosses this line.
std::string redis_region_call(const std::string& body) { return rr::door::call(body, false); }
std::string redis_region_block(const std::string& body) { return rr::door::call(body, true); }
std::string redis_region_batch(const std::string& body) { return rr::door::batch(body); }
std::string redis_region_batch_raw(const std::string& body) { return rr::door::batch_raw(body); }
void redis_region_batch_raw_async(const std::string& body, std::function<void(std::string)> done) { rr::door::batch_raw_async(body, std::move(done)); }
