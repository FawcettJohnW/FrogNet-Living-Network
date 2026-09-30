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
struct ConnRec { std::string token; bool authed; std::string user; };
static LFMap<uint64_t, ConnRec*> conns;
static std::string random_token() {
    static thread_local std::mt19937_64 r{std::random_device{}() ^ (uint64_t)std::chrono::steady_clock::now().time_since_epoch().count()};
    char b[33]; snprintf(b, sizeof b, "%016llx%016llx", (unsigned long long)r(), (unsigned long long)r()); return b;
}
static std::string new_id() {
    ebr::Guard epoch;
    uint64_t id = g.next_client_id++; std::string tok = random_token();
    conns.add(id, new ConnRec{tok, g.requirepass.empty(), "default"});
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
    if (in["new_id"].type == Json::Bool) return new_id();
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
    Client& c = *holder; ctx_in(c, in);
    ebr::Guard cepoch;
    ConnRec* rec = conn_of(c.id, S(in, "token"));
    if (!rec) throw std::runtime_error("redis region: the call does not name a connection the region issued (id and token)");
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
    std::string res = "{\"ok\":true,\"r\":" + q(b64enc(out)) + ",\"ctx\":" + q(ctx_out(c)) + "}";
    if (blocking) {                       // unregistered now; freed once no epoch can still see it (CLIENT LIST walkers)
        Client* out = nullptr; g.clients.remove(c.id, &out); reg.on = false;
        ebr::retire(holder.release());
    }
    return res;
}

}  // namespace door
}  // namespace rr

// The one symbol the host sees: no Redis type crosses this line.
std::string redis_region_call(const std::string& body) { return rr::door::call(body, false); }
std::string redis_region_block(const std::string& body) { return rr::door::call(body, true); }
