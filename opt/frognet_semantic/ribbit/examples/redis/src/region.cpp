// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// region.cpp -- see region.hpp. [ONE_KEYSPACE_V1]
//
// A connection has one runner at a time and nothing waits for it: whoever has something for a connection (its call, a
// write that moved what it waits on, its deadline, its end) sets a bit in its pending mask and tries to take its busy
// flag; whoever takes the flag runs everything pending, then lets go and looks again. A connection's calls never overlap
// (its front sends the next pipeline after the answer), so the runner owns the connection while it holds the flag.
//
// A blocked command is tried at once; if it would wait, it keeps its call's answer, its claim ticket stays in the line,
// and it is registered on its key's word. A write that moves the word marks it; the writer's thread serves it right after
// the writer's command (Redis serves an unblocked client before the pipeline goes on). One thread holds every deadline.
#include "region.hpp"
#include "server.hpp"
#include "blocking.hpp"
#include "ram_host.hpp"
#include "bytes_text.hpp"
#include "diag.hpp"
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <strings.h>
#include <chrono>
#include <queue>
#include <thread>

namespace rr {
namespace region {

void (*write_kill)(const std::string& front, uint64_t n) = nullptr;

namespace {
enum : uint32_t { PIPE = 1, WAKE = 2, TIMEOUT = 4, GONE = 8, KILL = 16 };

struct Conn {
    std::string key, front; uint64_t n = 0, session = 0;
    Client* c = nullptr;
    std::atomic<uint32_t> busy{0}, pend{0};
    std::string pipeline; void* answer = nullptr;      // PIPE: set before the bit
    std::atomic<uint64_t> due{0};                     // TIMEOUT: the parking whose time ran out
    bool dead = false;                                 // the runner's
    // parked: the runner's, except `word` and `park_no` (read by writers and the deadline thread)
    bool parked = false, recheck = false; Argv cmd; std::vector<Argv> rest; std::string out, timeout_reply; uint32_t seen = 0;
    std::atomic<WaitWord*> word{nullptr}; std::atomic<uint64_t> park_no{0};
};
LFMap<std::string, Conn*> conns;                 // "<front>/<n>"
LFMap<uint64_t, Conn*> by_client;                // by Redis client id (CLIENT KILL)
LFMap<std::string, Conn*> parked;                // the parked ones, for the writers' wakes
std::atomic<uint64_t> parkings{0};

int64_t mono_us() { return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

// ---- wakes: marked by the writer, served by the writer's thread after its command -------------------------------
thread_local std::vector<std::string> woken;
thread_local bool serving = false;
void try_run(Conn* k);
void mark(Conn* k, uint32_t bit) { k->pend.fetch_or(bit, std::memory_order_acq_rel); woken.push_back(k->key); }
void on_wake(WaitWord& w) {
    ebr::Guard e;
    parked.walk_all([&](LFMap<std::string, Conn*>::Node* nd) { if (nd->val->word.load(std::memory_order_acquire) == &w) mark(nd->val, WAKE); return true; });
}
void on_kill(Client* c) { ebr::Guard e; if (auto* nd = by_client.get(c->id)) mark(nd->val, KILL); }
void serve_woken() {
    if (serving) return;                          // the outer one drains what this thread's commands woke
    serving = true;
    for (size_t i = 0; i < woken.size(); i++) { ebr::Guard e; if (auto* nd = conns.get(woken[i])) try_run(nd->val); }
    woken.clear(); serving = false;
}
void job(const std::string& key) { { ebr::Guard e; if (auto* nd = conns.get(key)) try_run(nd->val); } serve_woken(); }

// ---- deadlines: one thread for every parked command --------------------------------------------------------------
struct Due { int64_t at; std::string key; uint64_t no; bool operator>(const Due& o) const { return at > o.at; } };
struct DNode { Due d; DNode* next; };
std::atomic<DNode*> incoming{nullptr};
std::atomic<uint32_t> dl_word{0};
void add_deadline(int64_t at_us, const std::string& key, uint64_t no) {
    auto* d = new DNode{Due{at_us, key, no}, incoming.load(std::memory_order_relaxed)};
    while (!incoming.compare_exchange_weak(d->next, d, std::memory_order_acq_rel)) {}
    dl_word.fetch_add(1, std::memory_order_release);
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&dl_word), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
}
void deadlines() {
    std::priority_queue<Due, std::vector<Due>, std::greater<Due>> due;
    for (;;) {
        const uint32_t seen = dl_word.load(std::memory_order_acquire);
        for (DNode* d = incoming.exchange(nullptr, std::memory_order_acq_rel); d;) { due.push(d->d); DNode* nx = d->next; delete d; d = nx; }
        // [NEVER_BEFORE_THE_DEADLINE_V1] ends only once the deadline has passed (Redis never returns early)
        for (int64_t now = mono_us(); !due.empty() && due.top().at <= now; due.pop()) {
            std::string key = due.top().key; uint64_t no = due.top().no;
            ribbit::pool_run([key, no] {
                { ebr::Guard e; auto* nd = conns.get(key);
                  if (nd && nd->val->park_no.load(std::memory_order_acquire) == no) { nd->val->due.store(no, std::memory_order_release); nd->val->pend.fetch_or(TIMEOUT); try_run(nd->val); } }
                serve_woken();
            });
        }
        timespec ts, *tp = nullptr;
        if (!due.empty()) { int64_t us = due.top().at - mono_us(); if (us < 0) us = 0; ts.tv_sec = us / 1000000; ts.tv_nsec = (us % 1000000) * 1000; tp = &ts; }
        syscall(SYS_futex, reinterpret_cast<uint32_t*>(&dl_word), FUTEX_WAIT_PRIVATE, seen, tp, nullptr, 0);
    }
}

// ---- the runner --------------------------------------------------------------------------------------------------
void answer(Conn* k, std::string out, bool close) {
    if (!k->answer) { DIAG("region", "answer-none", k->key, "close=" + std::to_string(close)); return; }
    DIAG("region", "answer", k->key, "bytes=" + std::to_string(out.size()) + " close=" + std::to_string(close) + " parked=" + std::to_string(k->parked));
    std::string b; b.reserve(out.size() + 3);
    b += k->c->authed ? '1' : '0'; b += close ? '1' : '0'; b += '\n'; b += out;
    // taken out BEFORE it is sent: once the front has the answer it may send the next call, which sets k->answer anew
    void* a = k->answer; k->answer = nullptr;
    ribbit::answer_later(a, bytes_to_text(b));
}
void unpark(Conn* k) {
    Client& c = *k->c;
    WaitWord* w = k->word.exchange(nullptr, std::memory_order_acq_rel);
    if (w) Region::unwatch(*w);
    { Conn* x = nullptr; parked.remove(k->key, &x); }
    k->park_no.store(0, std::memory_order_release); k->parked = false;
    c.waiting_on = nullptr;
    if (auto* bk = c.blocked_keys.exchange(nullptr)) ebr::retire(const_cast<Client::BlockKeys*>(bk));
    g.blocked_clients--;
    drop_claim(c);                                // timed out, unblocked or gone: its place in line goes (served: already gone)
}
void drop(Conn* k) {                              // the connection leaves the region
    DIAG("region", "drop", k->key, "parked=" + std::to_string(k->parked) + " answer=" + std::to_string(k->answer != nullptr));
    if (k->parked) unpark(k);
    k->dead = true;
    { Conn* x = nullptr; conns.remove(k->key, &x); by_client.remove(k->c->id, &x); }
    { Client* x = nullptr; g.clients.remove(k->c->id, &x); }
    ebr::retire(k->c); ebr::retire(k);           // freed once no epoch can still see them
}
bool is_blocking_class(const Argv& a) {
    if (a.empty()) return false;
    const char* n = a[0].c_str(); char f = (char)tolower((unsigned char)n[0]);
    if (f != 'b' && f != 'x') return false;
    static const char* names[] = {"blpop", "brpop", "blmove", "brpoplpush", "blmpop", "bzpopmin", "bzpopmax", "bzmpop", nullptr};
    for (int i = 0; names[i]; i++) if (!strcasecmp(n, names[i])) return true;
    if (!strcasecmp(n, "xread") || !strcasecmp(n, "xreadgroup")) { for (size_t i = 1; i < a.size(); i++) if (!strcasecmp(a[i].c_str(), "block")) return true; }
    return false;
}
void park(Conn* k, const Argv& argv, std::vector<Argv>& cmds, size_t i, std::string timeout_reply, std::string o) {
    Client& c = *k->c;
    k->cmd = argv; for (auto& rw : c.park_rewrite) if (rw.first < k->cmd.size()) k->cmd[rw.first] = rw.second;
    k->rest.assign(std::make_move_iterator(cmds.begin() + (long)i + 1), std::make_move_iterator(cmds.end()));
    k->timeout_reply = std::move(timeout_reply); k->out = std::move(o);
    WaitWord* w = c.park_word; Region::watch(*w);
    k->seen = w->gen.load(std::memory_order_acquire);
    c.waiting_on = w; c.blocked_keys.store(new Client::BlockKeys(c.block_keys), std::memory_order_release);
    g.blocked_clients++;
    const uint64_t no = ++parkings;
    k->parked = true; k->word.store(w, std::memory_order_release); k->park_no.store(no, std::memory_order_release);
    parked.add(k->key, k);
    if (c.park_timeout_ms > 0) add_deadline(mono_us() + c.park_timeout_ms * 1000, k->key, no);
    // a write between the try and the registration moved nothing: try once more now that it is registered
    k->recheck = true; k->pend.fetch_or(WAKE, std::memory_order_acq_rel);
}
// a connection's commands from `from` on; the replies so far are `o`; answers its call unless it parks
void run(Conn* k, std::vector<Argv>& cmds, size_t from, std::string o) {
    Client& c = *k->c;
    c.last_cmd_ms = Memory::now_ms();
    for (size_t i = from; i < cmds.size(); i++) {
        const Argv& argv = cmds[i];
        Reply r; r.proto = c.proto;
        const bool blocking = !c.in_multi && is_blocking_class(argv);
        if (blocking) { c.try_block = true; c.would_block = false; c.park_rewrite.clear(); }
        execute_command(c, argv, r);
        c.try_block = false;
        if (blocking && c.would_block) { park(k, argv, cmds, i, std::move(r.out), std::move(o)); return; }
        if (c.reply_mode == 1) {} else if (c.skip_count > 0) c.skip_count--; else o += r.out;
        if (!strcasecmp(argv[0].c_str(), "quit") || c.closing) c.close_after_reply = true;
        if (c.close_after_reply) break;
        serve_woken();                            // what this command readied, before the next
    }
    if (c.close_after_reply) { answer(k, std::move(o), true); drop(k); return; }
    answer(k, std::move(o), false);
}
void finish_park(Conn* k, std::string reply) {
    Client& c = *k->c;
    unpark(k);
    std::vector<Argv> rest = std::move(k->rest); std::string o = std::move(k->out);
    if (c.reply_mode == 1) {} else if (c.skip_count > 0) c.skip_count--; else o += reply;
    run(k, rest, 0, std::move(o));
}
void serve(Conn* k, uint32_t p) {
    Client& c = *k->c;
    if (int u = c.unblock.exchange(0)) { finish_park(k, u == 1 ? k->timeout_reply : std::string("-UNBLOCKED client unblocked via CLIENT UNBLOCK\r\n")); return; }
    if ((p & TIMEOUT) && k->due.load(std::memory_order_acquire) == k->park_no.load(std::memory_order_acquire)) { finish_park(k, k->timeout_reply); return; }
    WaitWord* w = k->word.load(std::memory_order_acquire);
    if (!k->recheck && w->gen.load(std::memory_order_acquire) == k->seen) return;   // nothing it waits on moved
    k->recheck = false;
    k->seen = w->gen.load(std::memory_order_acquire);
    c.retrying = true; c.try_block = true; c.would_block = false; c.park_rewrite.clear();
    Reply r; r.proto = c.proto;
    execute_command(c, k->cmd, r);
    c.try_block = false; c.retrying = false;
    if (c.would_block) { if (w->gen.load(std::memory_order_acquire) != k->seen) k->pend.fetch_or(WAKE); return; }   // waits again
    finish_park(k, std::move(r.out));
}
void handle(Conn* k, uint32_t p) {
    DIAG("region", "handle", k->key, "pend=" + std::to_string(p) + " dead=" + std::to_string(k->dead) + " parked=" + std::to_string(k->parked) + " answer=" + std::to_string(k->answer != nullptr));
    if (k->dead) { if (p & PIPE) answer(k, std::string(), true); return; }
    if (p & (GONE | KILL)) {
        const bool had_call = k->parked || (p & PIPE);
        if (k->parked) { std::string o = std::move(k->out); unpark(k); answer(k, std::move(o), true); }
        if (p & PIPE) answer(k, std::string(), true);
        if ((p & KILL) && !(p & GONE) && !had_call && write_kill) write_kill(k->front, k->n);   // its front closes the socket
        drop(k); return;
    }
    if (k->parked && (p & (WAKE | TIMEOUT))) serve(k, p);
    if (p & PIPE) {
        RespParser parser; parser.authenticated = true; parser.max_bulk = INT64_MAX;
        parser.feed(k->pipeline.data(), k->pipeline.size()); k->pipeline.clear();
        ParseResult pr = parser.drain();
        run(k, pr.cmds, 0, std::string());
    }
}
void try_run(Conn* k) {                           // inside an epoch
    for (;;) {
        uint32_t z = 0;
        if (!k->busy.compare_exchange_strong(z, 1, std::memory_order_acq_rel)) { DIAG("region", "busy", k->key, "pend=" + std::to_string(k->pend.load())); return; }   // its runner sees the bits
        for (uint32_t p; (p = k->pend.exchange(0, std::memory_order_acq_rel)) != 0;) handle(k, p);
        k->busy.store(0, std::memory_order_seq_cst);
        const uint32_t left = k->pend.load(std::memory_order_seq_cst);
        DIAG("region", "release", k->key, "pend_after=" + std::to_string(left));
        if (left == 0) return;
    }
}

std::vector<std::string> words(const std::string& s) {
    std::vector<std::string> v; size_t a = 0;
    while (a < s.size()) { size_t b = s.find(' ', a); if (b == std::string::npos) b = s.size(); if (b > a) v.push_back(s.substr(a, b - a)); a = b + 1; }
    return v;
}
void apply_config(const std::string& resp) {
    RespParser parser; parser.max_bulk = INT64_MAX; parser.feed(resp.data(), resp.size());
    ParseResult pr = parser.drain();
    for (auto& a : pr.cmds) for (size_t i = 0; i + 1 < a.size(); i += 2) g.cfg.set(a[i], a[i + 1]);
    g.requirepass = g.cfg.get("requirepass");
    g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
}
}  // namespace

void init() {
    g.start_ms = Memory::now_ms();
    g.executable = "ribbit-redis-host";
    g.cfg.init_defaults();
    { char cwd[4096]; if (getcwd(cwd, sizeof cwd)) g.cfg.set("dir", cwd); }   // Redis: the working directory at startup
    if (const char* f = getenv("RIBBIT_REDIS_CONFIG")) { g.config_file = f; g.cfg.load_file(f); }
    g.requirepass = g.cfg.get("requirepass");
    g.maxmemory = atoll(g.cfg.get("maxmemory").c_str());
    g.databases = atoi(g.cfg.get("databases").c_str()); if (g.databases <= 0) g.databases = 16;
    { long long m = atoll(g.cfg.get("proto-max-bulk-len").c_str()); if (m > 0) g.max_bulk = m; }
    std::vector<std::string> svcs; for (int d = 0; d < g.databases; d++) { svcs.push_back(db::svc(d)); svcs.push_back(db::claims(d)); }
    svcs.push_back("batches"); g.mem.init(svcs);
    register_all_commands();
    g.on_kill = on_kill;
    Region::on_wake = on_wake;
    std::thread(deadlines).detach();
}

std::string call(const std::string& text) {
    std::string body; if (!text_to_bytes(text, body)) throw std::runtime_error("redis region: a call's body is bytes as code points");
    const size_t nl = body.find('\n');
    const std::vector<std::string> head = words(body.substr(0, nl));
    if (head.empty() || head[0].size() != 1) throw std::runtime_error("redis region: not a call");
    const char kind = head[0][0];
    DIAG("region", "call", head.size() > 1 ? head[1] : std::string("-"), std::string("kind=") + kind + " bytes=" + std::to_string(body.size()) + " session=" + std::to_string(ribbit::current_session()));
    if (kind == 'x') { apply_config(nl == std::string::npos ? std::string() : body.substr(nl + 1)); return std::string(); }
    if (head.size() < 2) throw std::runtime_error("redis region: a call names its connection");
    const std::string& key = head[1];
    ebr::Guard e;
    switch (kind) {
    case 'o': {                                   // a connection opens (before its first pipeline: the same session, in order)
        if (conns.get(key)) return std::string();
        const size_t sl = key.rfind('/'); if (sl == std::string::npos) throw std::runtime_error("redis region: a connection is <front>/<n>");
        auto* k = new Conn; k->key = key; k->front = key.substr(0, sl); k->n = strtoull(key.c_str() + sl + 1, nullptr, 10);
        k->session = ribbit::current_session();
        k->c = new Client; Client& c = *k->c;
        c.id = g.next_client_id++;
        c.addr = head.size() > 2 ? head[2] : std::string(); c.laddr = head.size() > 3 ? head[3] : std::string();
        c.authed = g.requirepass.empty(); c.user = "default";
        c.created_ms = c.last_cmd_ms = Memory::now_ms();
        g.clients.add(c.id, &c); by_client.add(c.id, k); conns.add(key, k);
        g.stats.connections_received++;
        return std::string();
    }
    case 'p': {                                   // its pipeline: run on the pool, answered when it is done
        auto* nd = conns.get(key);
        if (!nd) { DIAG("region", "p-no-conn", key); return std::string("01\n"); }      // ended (killed, or its session gone): closed (ASCII: the same as text)
        Conn* k = nd->val;
        k->pipeline = nl == std::string::npos ? std::string() : body.substr(nl + 1);
        k->answer = ribbit::defer_answer();
        if (!k->answer) throw std::runtime_error("redis region: a pipeline is answered later, and this call cannot be");
        k->pend.fetch_or(PIPE, std::memory_order_acq_rel);
        DIAG("region", "p-deferred", key, "answer=" + std::to_string(k->answer != nullptr));
        ribbit::pool_run([key] { DIAG("region", "job", key); job(key); });
        return std::string();
    }
    case 'g': {                                   // the client hung up
        auto* nd = conns.get(key);
        if (nd) { nd->val->pend.fetch_or(GONE, std::memory_order_acq_rel); ribbit::pool_run([key] { job(key); }); }
        return std::string();
    }
    default: throw std::runtime_error(std::string("redis region: no such call: ") + kind);
    }
}

void session_down(uint64_t session) {
    ribbit::pool_run([session] {
        { ebr::Guard e;
          conns.walk_all([&](LFMap<std::string, Conn*>::Node* nd) { if (nd->val->session == session) mark(nd->val, GONE); return true; }); }
        serve_woken();
    });
}

}  // namespace region
}  // namespace rr
