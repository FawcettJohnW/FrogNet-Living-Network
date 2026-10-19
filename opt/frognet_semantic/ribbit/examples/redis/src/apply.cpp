// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// apply.cpp -- see apply.hpp. One thread applies (the box's applier): nothing here is shared with another thread
// except the box's own outs, handed back by value.
#include "apply.hpp"
#include "server.hpp"
#include "blocking.hpp"
#include <list>
#include <strings.h>
#include <unordered_map>

namespace rr {
namespace sm {

namespace {
struct Park;
struct Conn {                       // a connection of the region, held by every box
    Client* c = nullptr;
    std::string box; uint64_t local = 0;
    Park* park = nullptr;
    uint64_t parks = 0;             // parkings so far: a 'u' intent names one
};
struct Park {                       // [PARKED_IN_THE_ORDER_V1] a blocking command waiting, in arrival order
    Conn* k = nullptr;
    Argv cmd; std::vector<Argv> rest;   // the command, and what its pipeline still holds after it
    std::string timeout_reply;          // what it answers if its time runs out (or CLIENT UNBLOCK ... TIMEOUT)
    WaitWord* w = nullptr; uint32_t seen = 0;
    uint64_t no = 0;
};
std::unordered_map<std::string, std::unordered_map<uint64_t, Conn*>> by_box;
std::unordered_map<Client*, Conn*> by_client;
std::list<Park*> parks;
std::vector<Client*> killed;        // CLIENT KILL while a command ran: dropped after it
const std::string* g_me = nullptr;
std::vector<Out>* g_outs = nullptr;

Conn* find(const std::string& box, uint64_t local) {
    auto b = by_box.find(box); if (b == by_box.end()) return nullptr;
    auto k = b->second.find(local); return k == b->second.end() ? nullptr : k->second;
}
bool mine(const Conn* k) { return k->box == *g_me; }
void out(const Conn* k, Out::What w, std::string bytes, int64_t timeout_ms = 0, uint64_t park = 0) {
    if (!mine(k)) return;
    Out o; o.what = w; o.conn = k->local; o.bytes = std::move(bytes); o.authed = k->c->authed; o.timeout_ms = timeout_ms; o.park = park;
    g_outs->push_back(std::move(o));
}
void unpark_state(Park* p) {            // the client is no longer waiting (served, timed out, unblocked, gone)
    Client& c = *p->k->c;
    Region::unwatch(*p->w);
    c.waiting_on = nullptr;
    if (auto* bk = c.blocked_keys.exchange(nullptr)) delete bk;
    g.blocked_clients--;
    p->k->park = nullptr;
}
void drop(Conn* k) {                    // the connection leaves the region
    if (Park* p = k->park) { parks.remove(p); unpark_state(p); delete p; }
    by_client.erase(k->c);
    Client* out_c = nullptr; g.clients.remove(k->c->id, &out_c);
    auto b = by_box.find(k->box); if (b != by_box.end()) { b->second.erase(k->local); if (b->second.empty()) by_box.erase(b); }
    delete k->c; delete k;
}
void on_kill(Client* c) { killed.push_back(c); }

void run(Conn* k, std::vector<Argv>& cmds, size_t from, std::string out_bytes);
// a parked command finished with `reply`: its pipeline goes on
void finish(Park* p, std::string reply) {
    Conn* k = p->k; Client& c = *k->c;
    unpark_state(p);
    std::vector<Argv> rest = std::move(p->rest); delete p;
    std::string o;
    if (c.reply_mode == 1) {} else if (c.skip_count > 0) c.skip_count--; else o = std::move(reply);
    run(k, rest, 0, std::move(o));
}
bool serving = false, again = false;
void serve_parked() {
    // Redis serves the clients a command readied right after that command, before the next one runs (even the next in
    // the same pipeline: "Unblock fairness is kept while pipelining"). A command run while serving -- the rest of a
    // served client's pipeline -- asks again instead of serving inside the walk.
    if (serving) { again = true; return; }
    serving = true;
    for (bool progress = true; progress || again;) {
        again = false;
        progress = false;
        for (auto it = parks.begin(); it != parks.end();) {
            Park* p = *it; Client& c = *p->k->c;
            if (int u = c.unblock.exchange(0)) {        // CLIENT UNBLOCK
                it = parks.erase(it); progress = true;
                finish(p, u == 1 ? p->timeout_reply : std::string("-UNBLOCKED client unblocked via CLIENT UNBLOCK\r\n"));
                continue;
            }
            if (p->w->gen.load(std::memory_order_acquire) == p->seen) { ++it; continue; }   // nothing it waits on moved
            c.retrying = true; c.try_block = true; c.would_block = false; c.park_rewrite.clear();
            Reply r; r.proto = c.proto; execute_command(c, p->cmd, r);
            c.try_block = false; c.retrying = false;
            if (c.would_block) { p->seen = p->w->gen.load(std::memory_order_acquire); ++it; continue; }
            it = parks.erase(it); progress = true;
            finish(p, std::move(r.out));
        }
    }
    serving = false;
}
void drop_killed() {
    while (!killed.empty()) {
        Client* c = killed.back(); killed.pop_back();
        auto it = by_client.find(c); if (it == by_client.end()) continue;
        Conn* k = it->second;
        out(k, Out::CLOSE, "");
        drop(k);
    }
}
// a connection's commands from `from` on; the replies so far are `o`
void run(Conn* k, std::vector<Argv>& cmds, size_t from, std::string o) {
    Client& c = *k->c;
    c.last_cmd_ms = Memory::now_ms();
    for (size_t i = from; i < cmds.size(); i++) {
        Argv& argv = cmds[i];
        Reply r; r.proto = c.proto;
        const bool blocking = !c.in_multi && is_blocking_class(argv);
        if (blocking) { c.try_block = true; c.would_block = false; c.park_rewrite.clear(); }
        execute_command(c, argv, r);
        c.try_block = false;
        if (apply_clock_advance) { apply_now_ms += apply_clock_advance; apply_clock_advance = 0; }
        if (blocking && c.would_block) {
            // [PARKED_IN_THE_ORDER_V1] it waits: parked at the end of the line, watching what it waits on
            auto* p = new Park; p->k = k; p->cmd = argv; p->no = ++k->parks;
            for (auto& rw : c.park_rewrite) if (rw.first < p->cmd.size()) p->cmd[rw.first] = rw.second;
            p->rest.assign(std::make_move_iterator(cmds.begin() + (long)i + 1), std::make_move_iterator(cmds.end()));
            p->timeout_reply = std::move(r.out);
            p->w = c.park_word; Region::watch(*p->w); p->seen = p->w->gen.load(std::memory_order_acquire);
            c.waiting_on = p->w;
            c.blocked_keys.store(new Client::BlockKeys(c.block_keys), std::memory_order_release);
            g.blocked_clients++;
            k->park = p; parks.push_back(p);
            out(k, Out::PARKED, std::move(o), c.park_timeout_ms, p->no);
            return;
        }
        if (c.reply_mode == 1) {} else if (c.skip_count > 0) c.skip_count--; else o += r.out;
        if (!strcasecmp(argv[0].c_str(), "quit")) c.close_after_reply = true;
        if (c.close_after_reply) break;
        if (!parks.empty() && i + 1 < cmds.size()) serve_parked();   // what this command readied, before the next
    }
    if (c.close_after_reply) { out(k, Out::CLOSE, std::move(o)); drop(k); return; }
    out(k, Out::DONE, std::move(o));
}
}  // namespace

bool is_blocking_class(const std::vector<std::string>& a) {
    if (a.empty()) return false;
    const char* n = a[0].c_str(); char f = (char)tolower((unsigned char)n[0]);
    if (f != 'b' && f != 'x') return false;
    static const char* names[] = {"blpop", "brpop", "blmove", "brpoplpush", "blmpop", "bzpopmin", "bzpopmax", "bzmpop", nullptr};
    for (int i = 0; names[i]; i++) if (!strcasecmp(n, names[i])) return true;
    if (!strcasecmp(n, "xread") || !strcasecmp(n, "xreadgroup")) { for (size_t i = 1; i < a.size(); i++) if (!strcasecmp(a[i].c_str(), "block")) return true; }
    return false;
}
size_t connections() { return by_client.size(); }
size_t parked() { return parks.size(); }

void init() {
    g.start_ms = Memory::now_ms();
    g.requirepass = g.cfg.get("requirepass");
    g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    g.databases = atoi(g.cfg.get("databases").c_str()); if (g.databases <= 0) g.databases = 16;
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    std::vector<std::string> svcs; for (int d = 0; d < g.databases; d++) { svcs.push_back(db::svc(d)); svcs.push_back(db::claims(d)); }
    svcs.push_back("batches"); g.mem.init(svcs);
    register_all_commands();
    g.on_kill = on_kill;
}

void apply(const Intent& in, const std::string& me, std::vector<Out>& outs) {
    g_me = &me; g_outs = &outs;
    ebr::Guard epoch;
    switch (in.kind) {
    case 'x': {                                     // the region's configuration: name, value, name, value ... as RESP
        RespParser parser; parser.max_bulk = INT64_MAX; parser.feed(in.p.data(), in.p.size());
        ParseResult pr = parser.drain();
        for (auto& a : pr.cmds) for (size_t i = 0; i + 1 < a.size(); i += 2) g.cfg.set(a[i], a[i + 1]);
        g.requirepass = g.cfg.get("requirepass");
        g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
        { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
        break;
    }
    case 'o': {
        if (find(in.box, in.conn)) break;            // said twice: the first stands
        auto* k = new Conn; k->box = in.box; k->local = in.conn;
        k->c = new Client; Client& c = *k->c;
        c.id = g.next_client_id++;
        size_t nl = in.p.find('\n');
        c.addr = in.p.substr(0, nl); c.laddr = nl == std::string::npos ? "" : in.p.substr(nl + 1);
        c.authed = g.requirepass.empty(); c.user = "default";
        c.created_ms = c.last_cmd_ms = Memory::now_ms();
        g.clients.add(c.id, &c); by_client[&c] = k; by_box[in.box][in.conn] = k;
        g.stats.connections_received++;
        break;
    }
    case 'g': {
        Conn* k = find(in.box, in.conn);
        if (k) drop(k);
        if (in.box == me) { Out o; o.what = Out::FREED; o.conn = in.conn; outs.push_back(std::move(o)); }
        break;
    }
    case 'u': {
        Conn* k = find(in.box, in.conn);
        if (!k || !k->park || k->park->no != in.park) break;   // served (or gone) before its time ran out
        Park* p = k->park; parks.remove(p);
        finish(p, p->timeout_reply);
        break;
    }
    case 'p': {
        Conn* k = find(in.box, in.conn);
        if (!k) break;                               // ended (killed) before this arrived
        RespParser parser; parser.authenticated = true; parser.max_bulk = INT64_MAX;
        parser.feed(in.p.data(), in.p.size());
        ParseResult pr = parser.drain();
        run(k, pr.cmds, 0, std::string());
        break;
    }
    default: break;
    }
    drop_killed();
    if (!parks.empty()) serve_parked();
    drop_killed();
}


// ---------------------------------------------------------------- [JOIN_BY_COPY_V1] the region as bytes
namespace {
void p64(std::string& o, uint64_t v) { for (int k = 0; k < 8; k++) o += char(v >> (8 * k)); }
void pstr(std::string& o, const std::string& s) { p64(o, s.size()); o += s; }
void pargv(std::string& o, const Argv& a) { p64(o, a.size()); for (auto& x : a) pstr(o, x); }
struct Rd {
    const std::string& s; size_t& at;
    void need(size_t n) { if (at + n > s.size()) throw std::runtime_error("the region's copy is cut short"); }
    uint64_t u64() { need(8); uint64_t v = 0; for (int k = 7; k >= 0; k--) v = v << 8 | uint8_t(s[at + k]); at += 8; return v; }
    std::string str() { uint64_t n = u64(); need(n); std::string v = s.substr(at, n); at += n; return v; }
    Argv argv() { Argv a(u64()); for (auto& x : a) x = str(); return a; }
};
std::vector<std::string> services() {
    std::vector<std::string> v; for (int d = 0; d < g.databases; d++) { v.push_back(db::svc(d)); v.push_back(db::claims(d)); } return v;
}
}  // namespace
void dump(std::string& o) {
    p64(o, uint64_t(g.databases));
    p64(o, g.cfg.v.size()); for (auto& [k, v] : g.cfg.v) { pstr(o, k); pstr(o, v); }
    p64(o, g.next_client_id.load()); p64(o, uint64_t(g.used_memory.load()));
    p64(o, by_client.size());
    for (auto& [cp, k] : by_client) {
        const Client& c = *cp;
        pstr(o, k->box); p64(o, k->local); p64(o, k->parks);
        p64(o, c.id); pstr(o, c.addr); pstr(o, c.laddr); pstr(o, c.name); pstr(o, c.libname); pstr(o, c.libver);
        p64(o, uint64_t(c.db)); p64(o, uint64_t(c.proto)); p64(o, c.authed); pstr(o, c.user);
        p64(o, uint64_t(c.created_ms)); p64(o, uint64_t(c.last_cmd_ms)); pstr(o, c.flags); pstr(o, c.lastcmd);
        p64(o, uint64_t(c.reply_mode)); p64(o, uint64_t(c.skip_count)); p64(o, c.no_evict); p64(o, c.no_touch);
        p64(o, c.in_multi); p64(o, c.multi_err);
        p64(o, c.queued.size()); for (auto& a : c.queued) pargv(o, a);
        p64(o, c.watches.size()); for (auto& w : c.watches) { p64(o, uint64_t(w.db)); pstr(o, w.key); p64(o, w.version); }
    }
    p64(o, parks.size());                              // the line, in order
    for (Park* p : parks) {
        pstr(o, p->k->box); p64(o, p->k->local); p64(o, p->no); pargv(o, p->cmd);
        p64(o, p->rest.size()); for (auto& a : p->rest) pargv(o, a);
        pstr(o, p->timeout_reply);
        const Client::BlockKeys* bk = p->k->c->blocked_keys.load(); p64(o, bk ? bk->keys.size() : 0); if (bk) for (auto& x : bk->keys) pstr(o, x); p64(o, bk && bk->nokey);
        p64(o, uint64_t(p->k->c->park_timeout_ms));
    }
    g.mem.dump(services(), o);
}
void load(const std::string& in, size_t& at) {
    Rd r{in, at};
    if (int(r.u64()) != g.databases) throw std::runtime_error("the region has another number of databases than this box");
    for (uint64_t n = r.u64(); n; n--) { std::string k = r.str(), v = r.str(); g.cfg.set(k, v); }
    g.requirepass = g.cfg.get("requirepass"); g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    g.next_client_id.store(r.u64()); g.used_memory = int64_t(r.u64());
    for (uint64_t n = r.u64(); n; n--) {
        auto* k = new Conn; k->box = r.str(); k->local = r.u64(); k->parks = r.u64();
        k->c = new Client; Client& c = *k->c;
        c.id = r.u64(); c.addr = r.str(); c.laddr = r.str(); c.name = r.str(); c.libname = r.str(); c.libver = r.str();
        c.db = int(r.u64()); c.proto = int(r.u64()); c.authed = r.u64() != 0; c.user = r.str();
        c.created_ms = int64_t(r.u64()); c.last_cmd_ms = int64_t(r.u64()); c.flags = r.str(); c.lastcmd = r.str();
        c.reply_mode = int(r.u64()); c.skip_count = int(r.u64()); c.no_evict = r.u64() != 0; c.no_touch = r.u64() != 0;
        c.in_multi = r.u64() != 0; c.multi_err = r.u64() != 0;
        for (uint64_t q = r.u64(); q; q--) c.queued.push_back(r.argv());
        for (uint64_t w = r.u64(); w; w--) { Client::Watch x; x.db = int(r.u64()); x.key = r.str(); x.version = r.u64(); c.watches.push_back(x); }
        g.clients.add(c.id, &c); by_client[&c] = k; by_box[k->box][k->local] = k;
    }
    struct PendingPark { Park* p; Client::BlockKeys bk; };
    std::vector<PendingPark> pending;
    for (uint64_t n = r.u64(); n; n--) {
        std::string box = r.str(); uint64_t local = r.u64();
        Conn* k = find(box, local); if (!k) throw std::runtime_error("the region's copy parks a connection it does not hold");
        auto* p = new Park; p->k = k; p->no = r.u64(); p->cmd = r.argv();
        for (uint64_t q = r.u64(); q; q--) p->rest.push_back(r.argv());
        p->timeout_reply = r.str();
        Client::BlockKeys bk; for (uint64_t q = r.u64(); q; q--) bk.keys.push_back(r.str()); bk.nokey = r.u64() != 0;
        k->c->park_timeout_ms = int64_t(r.u64());
        pending.push_back(PendingPark{p, bk});
    }
    g.mem.load(services(), in, at);
    for (auto& pp : pending) {                         // parked again, in the same line, watching what it waits on
        Park* p = pp.p; Client& c = *p->k->c;
        c.block_keys = pp.bk;
        p->w = &wait_word_for(c, pp.bk.keys); Region::watch(*p->w); p->seen = p->w->gen.load(std::memory_order_acquire);
        c.waiting_on = p->w; c.blocked_keys.store(new Client::BlockKeys(pp.bk), std::memory_order_release);
        g.blocked_clients++; p->k->park = p; parks.push_back(p);
    }
}

}  // namespace sm
}  // namespace rr
