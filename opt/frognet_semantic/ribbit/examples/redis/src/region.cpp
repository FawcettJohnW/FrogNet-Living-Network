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
#include "chunks.hpp"
#include "diag.hpp"
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <strings.h>
#include <chrono>
#include <queue>
#include <thread>
#include <unordered_map>

namespace rr {
namespace region {

void (*write_reply)(const std::string& front, const std::string& instance, ribbit::BagArrP bag) = nullptr;

namespace {
enum : uint32_t { PIPE = 1, WAKE = 2, TIMEOUT = 4, GONE = 8, KILL = 16 };

struct Conn {
    std::string key, front; uint64_t n = 0, session = 0;
    Client* c = nullptr;
    std::atomic<uint32_t> busy{0}, pend{0};
    std::string pipeline;                              // PIPE: set before the bit
    std::atomic<uint64_t> due{0};                     // TIMEOUT: the parking whose time ran out
    bool dead = false;                                 // the runner's
    // parked: the runner's, except `word` and `park_no` (read by writers and the deadline thread)
    bool parked = false, recheck = false; Argv cmd; std::vector<Argv> rest; std::string out, timeout_reply; uint32_t seen = 0;
    std::atomic<WaitWord*> word{nullptr}; std::atomic<uint64_t> park_no{0};
    int fi = -1;                                       // its front's number (the cache's holders) [CACHED_UNTIL_TOLD_V1]
    std::string ents;                                  // the cacheable GETs in the answer being built (the runner's)
};
LFMap<std::string, Conn*> conns;                 // "<front>/<n>"
LFMap<uint64_t, Conn*> by_client;                // by Redis client id (CLIENT KILL)
LFMap<std::string, Conn*> parked;                // the parked ones, for the writers' wakes
LFMap<std::string, uint64_t> front_session;      // a front's session (its `h` call): the session's end takes its connections
LFMap<std::string, int> killed;                  // CLIENT KILL: a pipeline already on its way is not run (its front's g<n> clears it)
std::atomic<uint64_t> parkings{0};

// ---- the fronts' caches [CACHED_UNTIL_TOLD_V1] ------------------------------------------------------------------
// A front keeps what a GET read and answers the next GET of that key itself, until the region tells it the key changed.
// The region keeps, per key, which fronts hold it (one bit each); a write to the key clears the bits and writes each of
// those fronts a notice, (redis.rep, <front>, i<n>), into the same family as its replies, so the front reads it in order
// with them. Every GET answer and notice carries a tick: a front does not keep a GET's value when a notice newer than the
// GET's tick has come for the key (the write raced the read).
constexpr int MAX_FRONTS = 256;
LFMap<std::string, int> front_idx;
std::atomic<const std::string*> front_names[MAX_FRONTS];
std::atomic<int> nfronts{0};
struct Track { std::atomic<uint64_t> m[MAX_FRONTS / 64]; Track() { for (auto& x : m) x.store(0); } };
LFMap<std::string, Track*> tracked;                // "<db>\n<key>"
std::atomic<long> ntracked{0};
std::atomic<uint64_t> tick{0};
std::atomic<uint64_t> notices{0};
std::string tkey(int db, const std::string& key) { return std::to_string(db) + "\n" + key; }
void hold(int db, const std::string& key, int fi) {   // before the read: a write after this sees the bit
    std::string tk = tkey(db, key); Track* t;
    if (auto* nd = tracked.get(tk)) t = nd->val;
    else { auto* n = new Track; LFMap<std::string, Track*>::Node* ex = nullptr;
           if (tracked.add(tk, n, &ex)) { t = n; ntracked.fetch_add(1, std::memory_order_seq_cst); } else { delete n; t = ex->val; } }
    t->m[fi / 64].fetch_or(1ull << (fi % 64), std::memory_order_seq_cst);
}
void notice(int fi, const char* field, std::string text) {
    const std::string* name = front_names[fi].load(std::memory_order_acquire); if (!name) return;
    static const auto names_i = std::make_shared<const std::vector<std::string>>(std::vector<std::string>{"i"});
    static const auto names_a = std::make_shared<const std::vector<std::string>>(std::vector<std::string>{"a"});
    auto a = std::make_shared<ribbit::BagArr>(); a->names = field[0] == 'i' ? names_i : names_a;
    a->vals.emplace_back(pyv::Str{std::move(text)});
    write_reply(*name, "i" + std::to_string(notices.fetch_add(1, std::memory_order_relaxed) + 1), std::move(a));
}
void on_touch(Region& R, const std::string& variable) {
    if (R.dbi < 0 || ntracked.load(std::memory_order_seq_cst) == 0) return;
    ebr::Guard e;
    auto* nd = tracked.get(tkey(R.dbi, variable)); if (!nd) return;
    uint64_t bits[MAX_FRONTS / 64]; bool any = false;
    for (int w = 0; w < MAX_FRONTS / 64; w++) { bits[w] = nd->val->m[w].exchange(0, std::memory_order_seq_cst); any |= bits[w] != 0; }
    if (!any) return;
    const uint64_t v = tick.fetch_add(1, std::memory_order_seq_cst) + 1;
    const std::string text = std::to_string(R.dbi) + " " + std::to_string(v) + " " + bytes_to_text(variable);
    DIAG("region", "notice", variable, "db=" + std::to_string(R.dbi) + " tick=" + std::to_string(v));
    for (int w = 0; w < MAX_FRONTS / 64; w++) for (uint64_t b = bits[w]; b; b &= b - 1) notice(w * 64 + __builtin_ctzll(b), "i", text);
}
void on_touch_all(Region& R) {                     // FLUSHALL, FLUSHDB, SWAPDB: every copy of this db goes
    if (R.dbi < 0) return;
    const uint64_t v = tick.fetch_add(1, std::memory_order_seq_cst) + 1;
    const std::string text = std::to_string(R.dbi) + " " + std::to_string(v);
    for (int i = 0, n = std::min(nfronts.load(std::memory_order_acquire), MAX_FRONTS); i < n; i++) notice(i, "a", text);
}
// a connection whose replies are plain: what a front may answer for it itself
bool plain(const Client& c) { return c.authed && !c.in_multi && c.reply_mode == 0 && c.skip_count == 0; }

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
// [REPLIES_AS_TRUTH_V1] The region's answer is a cell it writes, (redis.rep, <front>, <n>): "f" is
// "<authenticated><0 open | 1 close | 2 parked: more follows>", "r" the replies (bytes as code points). The front holds
// one standing read on its family and gets them as rows, batched; nothing is answered per request. The replies before
// a command that parks go to their own cell, <n>.b, so the final reply cannot overwrite them before they are read
// [REPLIES_BEFORE_THE_BLOCK_V1]. One pipeline is in flight per connection, so <n> is read before it is written again.
void answer(Conn* k, std::string out, bool close, bool parked_more = false) {
    std::string f; f += k->c->authed ? '1' : '0'; f += close ? '1' : parked_more ? '2' : '0';
    // [CACHED_UNTIL_TOLD_V1] s: "<plain> <proto> <db>" after this answer; c: the cacheable GETs in it
    const Client& cl = *k->c;
    std::string st = plain(cl) && !parked_more ? "1 " + std::to_string(cl.proto) + " " + std::to_string(cl.db) : std::string("0");
    std::string ents = std::move(k->ents); k->ents.clear();
    DIAG("region", "answer", k->key, "bytes=" + std::to_string(out.size()) + " f=" + f);
    // as fields, not JSON text: no parse and no template analysis of the bag on either end [DECODE_ONCE_V1]
    static ChunkNames names({"f", "s", "c"}, "r");   // [PAYLOAD_IN_FIELDS_V1] a reply past one wire string: in pieces
    static ChunkNames piece_names({}, "r");
    const std::string inst = std::to_string(k->n) + (parked_more ? ".b" : "");
    std::vector<std::string> parts = cells(bytes_to_text(out));   // [PAYLOAD_IN_CELLS_V1] past one frame: piece cells first
    for (size_t i = 0; i + 1 < parts.size(); ++i) {
        std::vector<std::string> ps = cut(parts[i]);
        auto pa = std::make_shared<ribbit::BagArr>(); pa->names = piece_names.of(ps.size());
        for (auto& x : ps) pa->vals.emplace_back(pyv::Str{std::move(x)});
        DIAG("region", "write-piece", k->key, inst + ":" + std::to_string(i));
        write_reply(k->front, inst + ":" + std::to_string(i), std::move(pa));
    }
    std::vector<std::string> pieces = cut(parts.back());
    auto a = std::make_shared<ribbit::BagArr>(); a->names = names.of(pieces.size());
    a->vals.reserve(3 + pieces.size()); a->vals.emplace_back(pyv::Str{f}); a->vals.emplace_back(pyv::Str{std::move(st)}); a->vals.emplace_back(pyv::Str{std::move(ents)});
    for (auto& x : pieces) a->vals.emplace_back(pyv::Str{std::move(x)});
    DIAG("region", "write-main", k->key, inst + " pieces=" + std::to_string(parts.size()));
    write_reply(k->front, inst, std::move(a));
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
    DIAG("region", "drop", k->key, "parked=" + std::to_string(k->parked));
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
    std::string before = std::move(k->out); k->out.clear();
    answer(k, std::move(before), false, true);   // the replies before it, now [REPLIES_BEFORE_THE_BLOCK_V1]
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
        // [CACHED_UNTIL_TOLD_V1] a plain GET: the front is marked as holding the key before the read
        const bool cget = k->fi >= 0 && argv.size() == 2 && plain(c) && !strcasecmp(argv[0].c_str(), "get");
        const int gdb = c.db; uint64_t gtick = 0;
        if (cget) { hold(gdb, argv[1], k->fi); gtick = tick.load(std::memory_order_seq_cst); }
        execute_command(c, argv, r);
        if (cget && !r.out.empty() && (r.out[0] == '$' || r.out[0] == '_') && db::expire_at(gdb, argv[1]) < 0) {
            std::string kt = bytes_to_text(argv[1]);
            std::string e = std::to_string(gdb) + " " + std::to_string(gtick) + " " + std::to_string(c.proto) + " " + std::to_string(o.size()) + " "
                          + std::to_string(r.out.size()) + " " + std::to_string(kt.size()) + ":" + kt;
            if (k->ents.size() + e.size() <= CHUNK) k->ents += e;   // one wire string: past it, those GETs are just not kept
        }
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
    DIAG("region", "handle", k->key, "pend=" + std::to_string(p) + " dead=" + std::to_string(k->dead) + " parked=" + std::to_string(k->parked));
    if (k->dead) return;
    if (p & GONE) { if (k->parked) unpark(k); drop(k); return; }   // the client hung up: nobody to answer
    if (p & KILL) {                               // CLIENT KILL: the reply cell says close; its front closes the socket
        std::string o; if (k->parked) { o = std::move(k->out); unpark(k); }
        answer(k, std::move(o), true);
        killed.add(k->key, 1); drop(k); return;
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
    Region::on_touch = on_touch; Region::on_touch_all = on_touch_all;
    std::thread(deadlines).detach();
}

void bind(const std::string& front) {           // the `h` call: this session is that front's
    front_session.add(front, ribbit::current_session());
    if (!front_idx.get(front)) {                   // its number, for the caches (past MAX_FRONTS: its GETs are not kept)
        const int i = nfronts.fetch_add(1, std::memory_order_acq_rel);
        if (i < MAX_FRONTS) { front_names[i].store(new std::string(front), std::memory_order_release); front_idx.add(front, i); }
    }
    DIAG("region", "bind", front, "session=" + std::to_string(ribbit::current_session()));
}

std::unordered_map<std::string, std::string> request_pieces;   // [PAYLOAD_IN_CELLS_V1] the reader's: piece cells so far
void request(const std::string& front, const std::string& instance, const ribbit::BagArr& bag) {
    auto str = [&](const char* f) { return bag.str(f); };
    const size_t colon = instance.find(':');
    if (colon != std::string::npos) { request_pieces[front + "/" + instance.substr(0, colon)] += joined(bag, "p"); return; }
    std::string text = joined(bag, "p");
    { auto pc = request_pieces.find(front + "/" + instance); if (pc != request_pieces.end()) { text = std::move(pc->second) + text; request_pieces.erase(pc); } }
    std::string p;
    if (!text_to_bytes(text, p)) { logmsg('#', "redis region: a request from " + front + " is not bytes as code points"); return; }
    DIAG("region", "request", front + "/" + instance, "bytes=" + std::to_string(p.size()));
    {   ebr::Guard e;
        if (instance == "x") { apply_config(p); return; }              // the configuration: before its first pipeline
        if (!instance.empty() && instance[0] == 'g') {                 // g<n>: the client hung up (after its last pipeline)
            const std::string key = front + "/" + instance.substr(1);
            { int x = 0; killed.remove(key, &x); }
            if (auto* nd = conns.get(key)) { nd->val->pend.fetch_or(GONE, std::memory_order_acq_rel); try_run(nd->val); }
        } else {                                                        // <n>: its pipeline
            const std::string key = front + "/" + instance;
            if (killed.get(key)) return;
            Conn* k;
            if (auto* nd = conns.get(key)) k = nd->val;
            else {                                                      // its first: the connection opens
                k = new Conn; k->key = key; k->front = front; k->n = strtoull(instance.c_str(), nullptr, 10);
                if (auto* fs = front_session.get(front)) k->session = fs->val;
            if (auto* fx = front_idx.get(front)) k->fi = fx->val;
                k->c = new Client; Client& c = *k->c;
                c.id = g.next_client_id++; c.addr = str("a"); c.laddr = str("l");
                c.authed = g.requirepass.empty(); c.user = "default";
                c.created_ms = c.last_cmd_ms = Memory::now_ms();
                g.clients.add(c.id, &c); by_client.add(c.id, k); conns.add(key, k);
                g.stats.connections_received++;
            }
            k->pipeline = std::move(p);
            k->pend.fetch_or(PIPE, std::memory_order_acq_rel);
            try_run(k);
        }
    }
    serve_woken();
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
