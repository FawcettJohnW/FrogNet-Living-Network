// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// front.cpp -- a Redis box on the memory [ONE_ORDER_V1] (docs/CONVERTING-TO-RIBBIT.md).
//
// A box speaks RESP to stock clients and holds the whole region: its own copy of the keyspace (apply.hpp), the same
// copy every box holds. It writes its truth -- what its clients asked -- once, as intents in the memory, and reads
// every box's intents from there, in write order, with one trigger (a held read on the family). Each intent is applied
// as it is read; the box that wrote it answers its client from applying it. Nothing is asked of another machine and
// nothing is acknowledged: the write order is the one order, and every box reaches the same state by it.
//
//   tuple (service redis)        written by                     read by
//   cmd      <box>/<n mod RING>  the box a client asked         every box, in write order: applied to its copy
//   applied  <box>               each box: how far it applied,  every box: which boxes are live (the donor of a copy
//                                every 250 ms                   to a box that joins is the smallest live one)
// [SPACE_V1] Producers write and consumers read (ribbit_space): a box writes its intents in its own time and nothing
// paces it; a box that reads too slowly finds a box's count jump (that box's ring moved past what it had not applied):
// its copy can no longer be the region's, it says so and stops -- its anomaly, its decision.
//
//   RIBBIT_RAM=host:port   the memory (another node). Not set: the box starts one of its own on a loopback port and
//                          stops it on exit -- a fresh region per server, which is what Redis's suite expects.
//   RIBBIT_REDIS_HOST      the memory binary for the self-started case (default: ribbit-redis-host beside this one)
//   RIBBIT_RING            intents a box keeps in the memory (default 4096)
//   RIBBIT_FRONT_LOOPS     event loops reading clients (default: half the cores)
//   RIBBIT_BOX_DIAG=1      [DIAG-APPLY] a line per 10000 intents applied: rate, batch sizes, lag, parked, pacing
#include "server.hpp"
#include "apply.hpp"
#include "frogram.hpp"
#include "ribbit_space.hpp"
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <sys/eventfd.h>
#include <sys/epoll.h>
#include <poll.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <cstring>
#include <cerrno>
#include <chrono>
#include <thread>
#include <atomic>
#include <queue>
#include <map>
#include <unordered_map>

namespace rr { extern FILE* log_fp; }
using namespace rr;

namespace {
const char* const SVC = "redis";
const std::string API = "/redis-api";
uint64_t RING = 16384;
std::unique_ptr<ribbit::Space> SP;               // [SPACE_V1] the memory
std::string ME;                                   // this box's name in the tuple space
pid_t g_host_pid = 0;
bool g_own_memory = false;
int64_t mono_ms() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return int64_t(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000; }
int64_t wall_ms() { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count(); }
void fatal(const std::string& why) { logmsg('#', why); if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(1); }

// ---- a client connection, as this box holds it ------------------------------------------------------------------
struct Conn {
    int fd = -1, ep = -1;
    uint64_t local = 0;                           // this box's number for it: what its intents name
    RespParser parser;
    std::atomic<bool> authed{true};
    std::atomic<bool> gone{false};                // its 'g' intent is written (once)
    std::atomic<uint64_t> park{0};                // parked: which parking (0: not parked)
    bool fatal = false; std::string fatal_err;    // a protocol error after the pipeline in flight
    // [REPLIES_IN_ORDER_V1] what is still to be written to the client, in order: the applier appends, whoever holds
    // `sending` writes (the applier while the socket takes it, a thread of its own when it does not)
    struct Chunk { std::atomic<Chunk*> next{nullptr}; std::string b; size_t off = 0; bool close = false; };
    Chunk stub; Chunk* ohead = &stub; std::atomic<Chunk*> otail{&stub}; std::atomic<bool> sending{false};
    std::atomic<int> refs{1};                     // the box's table holds one; a writing thread holds one while it waits
    ~Conn() { if (fd >= 0) ::close(fd); for (Chunk* h = ohead; h;) { Chunk* n = h->next.load(); if (h != &stub) delete h; h = n; } }
};
void release(Conn* c) { if (c->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) ebr::retire(c); }
LFMap<uint64_t, Conn*> conns;
std::atomic<uint64_t> next_local{0};
std::vector<int> loops;
int watch_ep = -1, watch_efd = -1;               // parked connections: hang-ups and deadlines

// ---- the intents this box writes, in order ([ONE_ORDER_V1]) ----------------------------------------------------------
struct Pend { std::atomic<Pend*> next{nullptr}; char kind = 0; uint64_t conn = 0, park = 0; std::string p; };
std::atomic<Pend*> q_tail;                        // Vyukov MPSC: producers swap the tail; the drainer walks from head
Pend* q_head;
Pend q_stub;
std::atomic<bool> draining{false};
uint64_t my_n = 0;                                // intents written (drainer only)
std::atomic<uint64_t> applied_id{0};              // every intent up to this write id is applied here
std::atomic<bool> caught_up{false};               // the region's history so far is applied here: clients may come
// [JOIN_BY_COPY_V1] the other live boxes' names, as the readers trigger last saw them (EBR: read inside a guard)
std::atomic<const std::vector<std::string>*> live_boxes{new std::vector<std::string>()};

// The wire carries a bag as text: bytes that are not UTF-8 cannot cross it (refused). A pipeline that is valid UTF-8 --
// nearly every one -- goes as itself ("p"); any other as base64 ("q"). [BINARY_SAFE_INTENTS_V1]
bool utf8_ok(const std::string& s) {
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
const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
std::string b64(const std::string& in) {
    std::string out; out.reserve((in.size() + 2) / 3 * 4); size_t i = 0;
    for (; i + 2 < in.size(); i += 3) { uint32_t v = (uint8_t)in[i] << 16 | (uint8_t)in[i + 1] << 8 | (uint8_t)in[i + 2]; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += B64[v & 63]; }
    if (i + 1 == in.size()) { uint32_t v = (uint8_t)in[i] << 16; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += "=="; }
    else if (i + 2 == in.size()) { uint32_t v = (uint8_t)in[i] << 16 | (uint8_t)in[i + 1] << 8; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += '='; }
    return out;
}
std::string unb64(const std::string& in) {
    static int8_t T[256]; static bool init = [] { memset(T, -1, sizeof T); for (int k = 0; k < 64; k++) T[(uint8_t)B64[k]] = (int8_t)k; return true; }(); (void)init;
    std::string out; uint32_t acc = 0; int bits = 0;
    for (unsigned char ch : in) { if (ch == '=') break; int8_t v = T[ch]; if (v < 0) break; acc = acc << 6 | (uint32_t)v; bits += 6; if (bits >= 8) { bits -= 8; out += (char)((acc >> bits) & 0xff); } }
    return out;
}
// [DECODE_ONCE_V1] an intent is written as its values, in one shape (p, the pipeline as text when it is UTF-8; q, base64
// when it is not -- the other one empty): encoded once, at the edge, by the session
const std::vector<std::string> FO_INTENT = {"b", "c", "k", "n", "p", "q", "t", "u"};
// [DECODE_ONCE_V1] the box's other truths, as values: one order vector per shape
const std::vector<std::string> FO_APPLIED = {"id"};
const std::vector<std::string> FO_SNAP = {"k", "q"};
const std::vector<std::string> FO_SNAPDONE = {"b", "id", "k"};
semcodec::Fields applied_fields(uint64_t a) { return {{"id", pyv::Int::of(int64_t(a))}}; }
void write_intent(Pend* x) {
    const uint64_t n = ++my_n;
    const bool text = utf8_ok(x->p);
    using pyv::Str; using pyv::Int;
    semcodec::Fields f; f.reserve(8);
    f.emplace_back("b", Str{ME}); f.emplace_back("c", Int::of(int64_t(x->conn))); f.emplace_back("k", Str{std::string(1, x->kind)});
    f.emplace_back("n", Int::of(int64_t(n))); f.emplace_back("p", Str{text ? x->p : std::string()}); f.emplace_back("q", Str{text ? std::string() : b64(x->p)});
    f.emplace_back("t", Int::of(wall_ms())); f.emplace_back("u", Int::of(int64_t(x->park)));
    try { SP->write_fields(SVC, "cmd", ME + "/" + std::to_string(n % RING), FO_INTENT, std::move(f)); }
    catch (const std::exception& e) { fatal(std::string("region: the memory is gone (") + e.what() + "): this box stops"); }
}
void drain() {
    for (;;) {
        if (draining.exchange(true, std::memory_order_acq_rel)) return;
        for (;;) {
            Pend* h = q_head; Pend* nx = h->next.load(std::memory_order_acquire);
            if (!nx) break;
            write_intent(nx);
            q_head = nx; nx->kind = 0; nx->p.clear();
            if (h != &q_stub) delete h;           // the old head is done with; nx is the new stub
        }
        draining.store(false, std::memory_order_seq_cst);
        Pend* nx = q_head->next.load(std::memory_order_seq_cst);
        if (!nx) return;
    }
}
// [BIG_INTENTS_IN_PIECES_V1] An intent larger than a write frame (a 4 MB SET) goes as pieces ('P'), each an intent of its
// own in this box's order, then the intent itself with the last piece: every box gathers the pieces of a connection and
// applies the whole when its intent arrives. Nothing leaves the order and nothing travels outside the binary write.
constexpr size_t PIECE = 9000;
void enqueue(char kind, uint64_t conn, std::string p, uint64_t park) {
    auto* x = new Pend; x->kind = kind; x->conn = conn; x->park = park; x->p = std::move(p);
    Pend* prev = q_tail.exchange(x, std::memory_order_acq_rel);
    prev->next.store(x, std::memory_order_release);
}
void intent(char kind, uint64_t conn, std::string p = std::string(), uint64_t park = 0) {
    size_t at = 0;
    while (p.size() - at > PIECE) { enqueue('P', conn, p.substr(at, PIECE), 0); at += PIECE; }
    enqueue(kind, conn, at ? p.substr(at) : std::move(p), park);
    drain();
}
void gone(Conn* c) { if (!c->gone.exchange(true)) intent('g', c->local); }

std::string resp_of(const Argv& a) {
    std::string o = "*" + std::to_string(a.size()) + "\r\n";
    for (auto& x : a) { o += "$" + std::to_string(x.size()) + "\r\n"; o += x; o += "\r\n"; }
    return o;
}
void rearm(Conn* c) { epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.u64 = c->local; epoll_ctl(c->ep, EPOLL_CTL_MOD, c->fd, &ev); }
// Replies to a client. The connection is re-armed BEFORE its reply is written: the client's next command can only follow
// the reply, so it is read in arrival order with every other connection's (an EXEC sent before another client's LPUSH
// is applied before it -- list.tcl "BRPOPLPUSH does not affect WATCH while still blocked"). Replies leave in the order
// they were applied: one writer at a time per connection, and a slow client never holds the applier.
void pump(Conn* c) {
    for (;;) {
        if (c->sending.exchange(true, std::memory_order_acq_rel)) return;
        for (;;) {
            Conn::Chunk* h = c->ohead; Conn::Chunk* nx = h->next.load(std::memory_order_acquire);
            if (!nx) break;
            while (nx->off < nx->b.size()) {
                ssize_t n = ::send(c->fd, nx->b.data() + nx->off, nx->b.size() - nx->off, MSG_NOSIGNAL | MSG_DONTWAIT);
                if (n > 0) { nx->off += size_t(n); continue; }
                if (n < 0 && errno == EINTR) continue;
                if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {     // the socket is full: a thread waits for room
                    c->refs.fetch_add(1, std::memory_order_acq_rel);
                    std::thread([c] { pollfd p{c->fd, POLLOUT, 0}; while (::poll(&p, 1, -1) < 0 && errno == EINTR) {}
                                      c->sending.store(false, std::memory_order_release); pump(c); release(c); }).detach();
                    return;
                }
                nx->off = nx->b.size();          // the peer is gone: its hang-up arrives on the loop
            }
            if (nx->close) { ::shutdown(c->fd, SHUT_RDWR); gone(c); }
            c->ohead = nx; nx->b.clear(); nx->b.shrink_to_fit();
            if (h != &c->stub) delete h;
        }
        c->sending.store(false, std::memory_order_seq_cst);
        if (!c->ohead->next.load(std::memory_order_seq_cst)) return;
    }
}
void send_out(Conn* c, std::string bytes, bool close = false) {
    if (bytes.empty() && !close) return;
    auto* k = new Conn::Chunk; k->b = std::move(bytes); k->close = close;
    Conn::Chunk* prev = c->otail.exchange(k, std::memory_order_acq_rel);
    prev->next.store(k, std::memory_order_release);
    pump(c);
}

// ---- parked connections: the client may hang up, and a parked command's time runs out --------------------------
struct Deadline { int64_t at; uint64_t local, park; bool operator>(const Deadline& o) const { return at > o.at; } };
struct DNode { Deadline d; DNode* next; };
std::atomic<DNode*> new_deadlines{nullptr};
void watcher() {
    std::priority_queue<Deadline, std::vector<Deadline>, std::greater<Deadline>> due;
    epoll_event evs[256];
    for (;;) {
        int timeout = -1;
        if (!due.empty()) { int64_t d = due.top().at - mono_ms(); timeout = d < 0 ? 0 : int(d) + 1; }
        int n = epoll_wait(watch_ep, evs, 256, timeout);
        for (int i = 0; i < n; i++) {
            if (evs[i].data.u64 == UINT64_MAX) { uint64_t v; if (::read(watch_efd, &v, sizeof v) < 0) {} continue; }
            ebr::Guard g; auto* x = conns.get(evs[i].data.u64);
            if (x && x->val && (evs[i].events & (EPOLLRDHUP | EPOLLHUP | EPOLLERR))) gone(x->val);
        }
        for (DNode* d = new_deadlines.exchange(nullptr); d;) { due.push(d->d); DNode* nx = d->next; delete d; d = nx; }
        const int64_t now = mono_ms();
        while (!due.empty() && due.top().at <= now) {
            Deadline d = due.top(); due.pop();
            ebr::Guard g; auto* x = conns.get(d.local);
            if (x && x->val && x->val->park.load() == d.park) intent('u', d.local, std::string(), d.park);
        }
    }
}

// ---- what applying said to this box's connections ----------------------------------------------------------------
void deliver(sm::Out& o) {
    ebr::Guard g;
    auto* x = conns.get(o.conn); Conn* c = x ? x->val : nullptr;
    if (!c) return;
    c->authed = o.authed;
    switch (o.what) {
    case sm::Out::DONE: {
        if (c->park.exchange(0)) epoll_ctl(watch_ep, EPOLL_CTL_DEL, c->fd, nullptr);
        if (c->fatal) { Reply e; e.error("ERR " + c->fatal_err); o.bytes += e.out; }
        const bool close = c->fatal;
        if (!close && !c->gone.load()) rearm(c);
        send_out(c, std::move(o.bytes), close);
        break;
    }
    case sm::Out::PARKED: {
        c->park.store(o.park);
        epoll_event ev{}; ev.events = EPOLLRDHUP | EPOLLONESHOT; ev.data.u64 = c->local;
        epoll_ctl(watch_ep, EPOLL_CTL_ADD, c->fd, &ev);
        if (o.timeout_ms > 0) {
            auto* d = new DNode{Deadline{mono_ms() + o.timeout_ms, c->local, o.park}, nullptr};
            d->next = new_deadlines.load(); while (!new_deadlines.compare_exchange_weak(d->next, d)) {}
            uint64_t one = 1; if (::write(watch_efd, &one, sizeof one) < 0) {}
        }
        send_out(c, std::move(o.bytes));                 // the replies before it: the client may need them
        break;
    }
    case sm::Out::CLOSE:
        if (c->park.exchange(0)) epoll_ctl(watch_ep, EPOLL_CTL_DEL, c->fd, nullptr);
        send_out(c, std::move(o.bytes), true);
        break;
    case sm::Out::FREED: {
        Conn* out = nullptr;
        if (conns.remove(o.conn, &out) && out) {
            epoll_ctl(out->ep, EPOLL_CTL_DEL, out->fd, nullptr); epoll_ctl(watch_ep, EPOLL_CTL_DEL, out->fd, nullptr);
            release(out);                         // an event loop or the watcher may still hold it: freed after them
        }
        break;
    }
    }
}

// [RAW_ROWS_V1] An intent's bag, read as this box wrote it: one flat JSON object, strings and integers. Read here in one
// pass, without building a JSON tree for every intent. False if it is not that shape (said by the caller).
// [DECODE_ONCE_V1] an intent from its bag's values, as the session decoded them: no text
bool intent_of(const ribbit::BagArr& a, sm::Intent& in) {
    if (!a.get("b") || !a.get("n")) return false;
    in.box = a.str("b"); in.n = uint64_t(a.num("n")); in.t = a.num("t"); in.conn = uint64_t(a.num("c")); in.park = uint64_t(a.num("u"));
    const std::string k = a.str("k"); in.kind = k.empty() ? 0 : k[0];
    if (const pyv::Obj* q = a.get("q"); q && q->is<pyv::Str>() && !q->as<pyv::Str>().s.empty()) in.p = unb64(q->as<pyv::Str>().s); else in.p = a.str("p");
    return true;
}
bool intent_of(const ribbit::SpaceRow& r, sm::Intent& in) { ribbit::BagArrP a = r.fields(); return a && intent_of(*a, in); }

// ---- the trigger: every box's intents, in write order, applied here ------------------------------------------------
// [FOLLOW_STREAM_V1] One standing trigger on the family: the memory sends every intent as it is written, in write order,
// and this runs on the session's reader thread as each batch lands -- no read per batch, no thread between the socket
// and the order. The first batch is the region's history (or nothing): after it, the box is the region.
struct Applier {
    int64_t after = 0, clock = 0;
    std::string joiner;                                   // [JOIN_BY_COPY_V1] a box whose 'j' this box answers with a copy
    std::unordered_map<std::string, std::string> pieces;   // [BIG_INTENTS_IN_PIECES_V1] box + connection -> the pieces so far
    std::unordered_map<std::string, uint64_t> last_n;   // each box's last intent applied here
    std::unordered_map<std::string, std::map<uint64_t, std::pair<uint64_t, sm::Intent>>> held;   // [READ_IN_OWNERS_ORDER_V1] read ahead of a gap
    std::vector<sm::Out> outs;
    uint64_t applied = 0, batches = 0, last_written = 0; int64_t t0 = 0;
};
// [JOIN_BY_COPY_V1] A box whose history the rings no longer hold asks for the region with an intent ('j'). The box that
// answers writes the region as it stands right after that intent -- a copy at that write id, as truths of its own:
// (redis, snap.<joiner>, <donor>/<k>) pieces, then (redis, snapdone, <joiner>) naming the id and the pieces. The joiner
// loads it and follows the order from that id. Nobody is asked: a request is an intent, a copy is a truth.
constexpr size_t COPY_PIECE = 9000;                    // raw bytes per piece: 12 KB as base64, one write frame
void put_u64(std::string& o, uint64_t v) { for (int k = 0; k < 8; k++) o += char(v >> (8 * k)); }
uint64_t get_u64(const std::string& s, size_t& at) { if (at + 8 > s.size()) throw std::runtime_error("the region's copy is cut short"); uint64_t v = 0; for (int k = 7; k >= 0; k--) v = v << 8 | uint8_t(s[at + k]); at += 8; return v; }
void put_str(std::string& o, const std::string& v) { put_u64(o, v.size()); o += v; }
std::string get_str(const std::string& s, size_t& at) { uint64_t n = get_u64(s, at); if (at + n > s.size()) throw std::runtime_error("the region's copy is cut short"); std::string v = s.substr(at, n); at += n; return v; }
struct Applier;
void write_copy(Applier& A, const std::string& joiner, uint64_t at_id);
void on_intents(Applier& A, std::vector<ribbit::SpaceRow>& rows, const std::string& err) {
    static const bool diag = getenv("RIBBIT_BOX_DIAG") != nullptr;
    if (!err.empty()) fatal("region: the memory is gone (" + err + "): this box stops");
    A.batches++;
    // [READ_IN_OWNERS_ORDER_V1] a box's intents are stored as parallel jobs, so one of them can land after a later one
    // of the same box: it is held here until the one before it lands, then both are applied, in the box's order. Every
    // box reads the same rows in the same order and holds the same way, so every box applies the same order. A gap is
    // an intent overwritten before it was applied only once the box's ring has moved past it (RING held behind it).
    auto apply_one = [&](uint64_t id, sm::Intent& in) {
        A.last_n[in.box] = in.n;
        if (in.kind == 'P' || !A.pieces.empty()) {   // [BIG_INTENTS_IN_PIECES_V1]
            const std::string key = in.box + '\x1f' + std::to_string(in.conn);
            if (in.kind == 'P') { A.pieces[key] += in.p; applied_id.store(id, std::memory_order_release); return; }
            auto pc = A.pieces.find(key);
            if (pc != A.pieces.end()) { in.p = std::move(pc->second) + in.p; A.pieces.erase(pc); }
        }
        if (in.kind == 'j' && in.box != ME) {       // [JOIN_BY_COPY_V1] a box asks for the region: the smallest live box answers
            ebr::Guard g; bool donor = true;
            for (auto& b : *live_boxes.load(std::memory_order_acquire)) if (b != in.box && b < ME) donor = false;
            if (donor) A.joiner = in.box;
        }
        if (in.t > A.clock) A.clock = in.t;          // the order's clock never goes back
        apply_now_ms = A.clock; apply_rng().seed(id);
        A.outs.clear();
        sm::apply(in, ME, A.outs);
        A.clock = apply_now_ms;                      // a DEBUG SLEEP moved it on
        apply_now_ms = 0;
        applied_id.store(id, std::memory_order_release);
        for (auto& o : A.outs) deliver(o);
        A.applied++;
        if (!A.joiner.empty()) { write_copy(A, A.joiner, id); A.joiner.clear(); }
        if (diag && A.applied % 10000 == 0) {
            const int64_t t = mono_ms();
            logmsg('-', "[DIAG-APPLY] applied=" + std::to_string(A.applied) + " batches=" + std::to_string(A.batches) + " rate=" + std::to_string(10000 * 1000 / std::max<int64_t>(1, t - A.t0)) +
                "/s this_batch=" + std::to_string(rows.size()) + " written_here=" + std::to_string(my_n) +
                " applied_id=" + std::to_string(id) + " connections=" + std::to_string(sm::connections()) + " parked=" + std::to_string(sm::parked()));
            A.t0 = t;
        }
    };
    for (auto& r : rows) {
        if (int64_t(r.id) <= A.after) continue;
        A.after = int64_t(r.id);
        sm::Intent in; in.id = r.id;
        if (!intent_of(r, in)) fatal("region: the intent at " + r.instance + " (write " + std::to_string(r.id) + ") is not an intent: " + r.text().substr(0, 200) + "; this box stops");
        auto it = A.last_n.find(in.box);
        const uint64_t want = it == A.last_n.end() ? 1 : it->second + 1;
        { static const bool tr = getenv("RIBBIT_BOX_DIAG") && atoi(getenv("RIBBIT_BOX_DIAG")) >= 2;   // [DIAG-INTENT] each intent read
          if (tr) logmsg('-', "[DIAG-INTENT] id=" + std::to_string(r.id) + " box=" + in.box + " n=" + std::to_string(in.n) + " want=" + std::to_string(want) +
                              " kind=" + std::string(1, in.kind ? in.kind : '?') + " p=" + std::to_string(in.p.size()) + "B"); }
        if (in.n < want)
            fatal("region: box " + in.box + "'s intent " + std::to_string(in.n) + " arrived again after " + std::to_string(want - 1) + "; this box stops");
        if (in.n > want) {
            auto& held = A.held[in.box];
            held.emplace(in.n, std::make_pair(r.id, std::move(in)));
            if (held.size() > RING)
                fatal("region: box " + held.begin()->second.second.box + "'s intent " + std::to_string(want) + (want == 1 ? " (its first)" : "") + " is no longer in the memory (" +
                      std::to_string(held.size()) + " later ones read): " + (it == A.last_n.end() ? "this box joined a region whose history it cannot replay" : "it was overwritten before this box applied it") + "; this box stops");
            continue;
        }
        apply_one(r.id, in);
        auto h = A.held.find(in.box);
        while (h != A.held.end() && !h->second.empty() && h->second.begin()->first == A.last_n[h->first] + 1) {
            auto node = h->second.extract(h->second.begin()); apply_one(node.mapped().first, node.mapped().second);
        }
    }
    const uint64_t a = applied_id.load();            // this box's place, when it moved far (the heartbeat does the rest)
    if (a > A.last_written + RING / 4) { try { SP->write_fields(SVC, "applied", ME, FO_APPLIED, applied_fields(a)); } catch (const std::exception&) {} A.last_written = a; }
    caught_up.store(true, std::memory_order_release);
    drain();
}
void write_copy(Applier& A, const std::string& joiner, uint64_t at_id) {
    const int64_t t0 = mono_ms();
    std::string b;
    put_u64(b, uint64_t(A.clock));
    put_u64(b, A.last_n.size()); for (auto& [box, n] : A.last_n) { put_str(b, box); put_u64(b, n); }
    put_u64(b, A.pieces.size()); for (auto& [key, v] : A.pieces) { put_str(b, key); put_str(b, v); }
    sm::dump(b);
    size_t k = 0;
    for (size_t at = 0; at < b.size() || k == 0; at += COPY_PIECE, k++)
        SP->write_fields(SVC, "snap." + joiner, ME + "/" + std::to_string(k), FO_SNAP, {{"k", pyv::Int::of(int64_t(k))}, {"q", pyv::Str{b64(b.substr(at, COPY_PIECE))}}});
    SP->write_fields(SVC, "snapdone." + joiner, ME, FO_SNAPDONE, {{"b", pyv::Str{ME}}, {"id", pyv::Int::of(int64_t(at_id))}, {"k", pyv::Int::of(int64_t(k))}});
    logmsg('*', "[JOIN_BY_COPY] box " + joiner + " asked for the region: written as it stands at write " + std::to_string(at_id) + ", " +
        std::to_string(b.size()) + " B in " + std::to_string(k) + " pieces, " + std::to_string(sm::connections()) + " connections, in " + std::to_string(mono_ms() - t0) + " ms");
}
// the history in the memory, without applying it: complete when every box's first intent is still there
bool history_complete() {
    std::unordered_map<std::string, uint64_t> first; int64_t after = 0;
    for (;;) {
        std::vector<ribbit::SpaceRow> rows = SP->read(SVC, "cmd", "", after, 0);
        if (rows.empty()) break;
        for (auto& r : rows) {
            if (int64_t(r.id) > after) after = int64_t(r.id);
            sm::Intent in; if (!intent_of(r, in)) continue;
            auto it = first.find(in.box); if (it == first.end() || in.n < it->second) first[in.box] = in.n;
        }
    }
    for (auto& [box, n] : first) if (n != 1) return false;
    return true;
}
// join by copy: ask, wait for a copy, load it; the order is followed from the copy's write id
void join_by_copy(Applier& A) {
    const int64_t t0 = mono_ms();
    std::this_thread::sleep_for(std::chrono::milliseconds(500));   // the other boxes see this one (applied 0) before it asks
    std::string donor; uint64_t at_id = 0, pieces = 0;
    for (int ask = 0; donor.empty(); ask++) {
        if (ask == 12) fatal("region: no box answered this box's request for the region in 60 s (are any running?); this box stops");
        intent('j', 0);
        const int64_t until = mono_ms() + 5000; int64_t after = 0;
        while (donor.empty() && mono_ms() < until) {
            std::vector<ribbit::SpaceRow> rows = SP->read(SVC, "snapdone." + ME, "", after, 1.0);   // each donor's own cell
            for (auto& r : rows) {
                if (int64_t(r.id) > after) after = int64_t(r.id);
                ribbit::BagArrP j = r.fields(); if (!j) continue;
                donor = j->str("b"); at_id = uint64_t(j->num("id")); pieces = uint64_t(j->num("k"));
            }
        }
    }
    std::vector<std::string> got(pieces); std::vector<bool> seen(pieces, false); size_t have = 0; int64_t after = 0; const std::string prefix = donor + "/";
    for (const int64_t until = mono_ms() + 30000; have < pieces;) {
        if (mono_ms() > until) fatal("region: the copy from " + donor + " came incomplete (" + std::to_string(have) + " of " + std::to_string(pieces) + " pieces); this box stops");
        std::vector<ribbit::SpaceRow> rows = SP->read(SVC, "snap." + ME, "", after, 0.2);
        for (auto& r : rows) {
            if (int64_t(r.id) > after) after = int64_t(r.id);
            if (r.instance.compare(0, prefix.size(), prefix) != 0) continue;
            const size_t k = size_t(atoll(r.instance.c_str() + prefix.size()));
            if (k >= pieces || seen[k]) continue;
            ribbit::BagArrP a = r.fields(); got[k] = a ? unb64(a->str("q")) : std::string(); seen[k] = true;
            have++;
        }
    }
    std::string b; for (auto& x : got) b += x;
    size_t at = 0;
    try {
        A.clock = int64_t(get_u64(b, at));
        for (uint64_t n = get_u64(b, at); n; n--) { std::string box = get_str(b, at); A.last_n[box] = get_u64(b, at); }
        for (uint64_t n = get_u64(b, at); n; n--) { std::string key = get_str(b, at); A.pieces[key] = get_str(b, at); }
        sm::load(b, at);
    } catch (const std::exception& e) { fatal(std::string("region: the copy from ") + donor + " cannot be loaded (" + e.what() + "); this box stops"); }
    A.after = int64_t(at_id);
    applied_id.store(at_id, std::memory_order_release);
    logmsg('*', "[JOIN_BY_COPY] the region, from " + donor + " at write " + std::to_string(at_id) + ": " + std::to_string(b.size()) + " B, " +
        std::to_string(sm::connections()) + " connections, " + std::to_string(A.last_n.size()) + " boxes' histories, in " + std::to_string(mono_ms() - t0) + " ms");
}
// this box's place in the order every 250 ms: that it is live (a box silent for 3 s is gone); and an intent
// the memory refused is a request the region never saw: the box can no longer answer for its clients. Said, and the
// box stops. [BINARY_SAFE_INTENTS_V1]
void heartbeat() {
    for (;;) {
        if (const uint64_t r = SP->refused()) fatal("region: the memory refused " + std::to_string(r) + " of this box's writes: this box stops");
        try { SP->write_fields(SVC, "applied", ME, FO_APPLIED, applied_fields(applied_id.load())); }
        catch (const std::exception& e) { fatal(std::string("region: the memory is gone (") + e.what() + "): this box stops"); }
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
}
// the other boxes that are live: the donor of a copy is the smallest of them
void readers() {
    struct R { uint64_t id; int64_t seen; };
    std::unordered_map<std::string, R> boxes;
    int64_t after = -1;
    for (;;) {
        std::vector<ribbit::SpaceRow> rows;
        try { rows = SP->read(SVC, "applied", "", after, after < 0 ? 0 : 1.0); }
        catch (const std::exception& e) { fatal(std::string("region: the memory is gone (") + e.what() + "): this box stops"); }
        const int64_t now = mono_ms();
        for (auto& r : rows) {
            if (int64_t(r.id) > after) after = int64_t(r.id);
            if (r.instance == ME) continue;
            { ribbit::BagArrP a = r.fields(); boxes[r.instance] = R{a ? uint64_t(a->num("id")) : 0, now}; }
        }
        if (after < 0) after = 0;
        for (auto it = boxes.begin(); it != boxes.end();) {
            if (now - it->second.seen > 3000) { it = boxes.erase(it); continue; }   // gone
            ++it;
        }
        auto* names = new std::vector<std::string>(); for (auto& b : boxes) names->push_back(b.first);
        if (const auto* old = live_boxes.exchange(names, std::memory_order_acq_rel)) ebr::retire(const_cast<std::vector<std::string>*>(old));
    }
}

// ---- clients --------------------------------------------------------------------------------------------------
void event_loop(int ep) {
    epoll_event evs[512]; char buf[65536];
    for (;;) {
        int n = epoll_wait(ep, evs, 512, -1);
        if (n < 0) { if (errno == EINTR) continue; fatal(std::string("epoll_wait: ") + strerror(errno)); }
        for (int k = 0; k < n; k++) {
            ebr::Guard epoch;
            auto* x = conns.get(evs[k].data.u64); Conn* c = x ? x->val : nullptr;
            if (!c) continue;
            ssize_t r = ::recv(c->fd, buf, sizeof buf, MSG_DONTWAIT);
            if (r < 0 && (errno == EAGAIN || errno == EINTR)) { rearm(c); continue; }
            if (r <= 0) { gone(c); continue; }
            c->parser.authenticated = c->authed.load();
            c->parser.feed(buf, size_t(r));
            c->parser.max_bulk = g.max_bulk.load(std::memory_order_relaxed);
            ParseResult pr = c->parser.drain();
            if (pr.cmds.empty()) {
                if (pr.fatal) { Reply e; e.error("ERR " + pr.err); send_out(c, e.out, true); }
                else rearm(c);                    // a partial command: wait for the rest
                continue;
            }
            c->fatal = pr.fatal; c->fatal_err = pr.err;
            std::string p; for (auto& a : pr.cmds) p += resp_of(a);
            intent('p', c->local, std::move(p));
        }
    }
}
void accept_conn(int fd, const std::string& addr, const std::string& laddr) {
    auto* c = new Conn; c->fd = fd; c->local = ++next_local; c->authed = g.requirepass.empty();
    static std::atomic<uint32_t> deal{0};
    c->ep = loops[deal.fetch_add(1, std::memory_order_relaxed) % loops.size()];
    conns.add(c->local, c);
    intent('o', c->local, addr + "\n" + laddr);  // written before anything it sends: one session, one order
    epoll_event ev{}; ev.events = EPOLLIN | EPOLLRDHUP | EPOLLONESHOT; ev.data.u64 = c->local;
    epoll_ctl(c->ep, EPOLL_CTL_ADD, fd, &ev);
}

// A memory of its own, on a loopback port, when none is named: it dies with this process (PR_SET_PDEATHSIG).
std::string start_own_memory(const std::string& self) {
    int s = ::socket(AF_INET, SOCK_STREAM, 0); sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ::bind(s, (sockaddr*)&a, sizeof a); socklen_t l = sizeof a; getsockname(s, (sockaddr*)&a, &l); int port = ntohs(a.sin_port); ::close(s);
    const char* hb = getenv("RIBBIT_REDIS_HOST");
    std::string bin = hb ? hb : self.substr(0, self.rfind('/') + 1) + "ribbit-redis-host";
    std::string listen = "127.0.0.1:" + std::to_string(port);
    g_host_pid = fork();
    if (g_host_pid == 0) {
        prctl(PR_SET_PDEATHSIG, SIGTERM);
        int dn = open("/dev/null", O_WRONLY); if (dn >= 0) { dup2(dn, 1); dup2(dn, 2); }
        execl(bin.c_str(), bin.c_str(), "--listen", listen.c_str(), (char*)nullptr);
        _exit(127);
    }
    return listen;
}
void on_term(int) { if (g_host_pid > 0) kill(g_host_pid, SIGTERM); _exit(0); }
}  // namespace

int main(int argc, char** argv) {
    signal(SIGPIPE, SIG_IGN);
    g.executable = argv[0];
    int from = 1;
    if (argc > 1 && argv[1][0] != '-') { g.config_file = argv[1]; from = 2; }
    g.cfg.init_defaults();
    { char cwd[4096]; if (getcwd(cwd, sizeof cwd)) g.cfg.set("dir", cwd); }   // Redis: dir is the working directory at startup
    if (!g.config_file.empty()) g.cfg.load_file(g.config_file);
    g.cfg.apply_args(argc, argv, from);
    g.port = atoi(g.cfg.get("port").c_str());
    std::string lf = g.cfg.get("logfile");
    if (!lf.empty()) { FILE* f = fopen(lf.c_str(), "a"); if (f) log_fp = f; }
    if (const char* r = getenv("RIBBIT_RING")) { long v = atol(r); if (v >= 64) RING = uint64_t(v); }
    q_head = &q_stub; q_tail.store(&q_stub);
    {   char h[256] = {0}; gethostname(h, sizeof h - 1); unsigned char r[4]; FILE* u = fopen("/dev/urandom", "rb");
        if (!u || fread(r, 1, 4, u) != 4) fatal("no /dev/urandom");
        fclose(u);
        char x[16]; snprintf(x, sizeof x, "%02x%02x%02x%02x", r[0], r[1], r[2], r[3]);
        ME = std::string(h) + "." + std::to_string(getpid()) + "." + x; }
    const char* ram = getenv("RIBBIT_RAM");
    g_own_memory = !ram;
    std::string ep = ram ? ram : start_own_memory(argv[0]);
    signal(SIGTERM, on_term); signal(SIGINT, on_term);
    for (int tries = 0; !SP; tries++) {          // a memory started here needs a moment to listen; a named one must answer
        try { SP.reset(new ribbit::Space(ep, "redis")); }
        catch (const std::exception& e) {
            if (ram || tries > 300) fatal(std::string("Cannot reach the memory ") + ep + ": " + e.what());
            usleep(10000);
        }
    }
    sm::init();
    // the region's configuration is this box's when it started the memory (the suite's case), or when it is told to
    // (RIBBIT_REDIS_CONFIG_OWNER=1 for the box an operator starts first): an intent, applied by every box
    if (g_own_memory || getenv("RIBBIT_REDIS_CONFIG_OWNER")) {
        Argv kv; for (auto& [k, v] : g.cfg.v) { kv.push_back(k); kv.push_back(v); }
        intent('x', 0, resp_of(kv));
    }
    watch_ep = epoll_create1(EPOLL_CLOEXEC); watch_efd = eventfd(0, EFD_NONBLOCK);
    { epoll_event ev{}; ev.events = EPOLLIN; ev.data.u64 = UINT64_MAX; epoll_ctl(watch_ep, EPOLL_CTL_ADD, watch_efd, &ev); }
    std::thread(watcher).detach();
    std::thread(readers).detach();
    std::thread(heartbeat).detach();
    {   static Applier A; A.t0 = mono_ms();
        // a box whose region has history the rings no longer hold takes a copy first [JOIN_BY_COPY_V1]
        if (!history_complete()) join_by_copy(A);
        SP->watch(SVC, "cmd", A.after, [](std::vector<ribbit::SpaceRow>& rows) { on_intents(A, rows, std::string()); });
    }
    while (!caught_up.load(std::memory_order_acquire)) usleep(1000);   // a box serves only once it holds the region
    logmsg('*', "Redis Ribbit box " + ME + ", Redis compatibility 7.2.11, PID: " + std::to_string(getpid()) + ", memory " + ep + API + " [ONE_ORDER_V1]");
    logmsg('*', "Running mode=standalone, port=" + std::to_string(g.port) + ".");
    logmsg('#', "Server initialized");
    int lfd = ::socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in sa{}; sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)g.port); sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    { std::string b = g.cfg.get("bind"); if (b.find('*') != std::string::npos || b.find("0.0.0.0") != std::string::npos || b.empty()) sa.sin_addr.s_addr = htonl(INADDR_ANY); }
    if (::bind(lfd, (sockaddr*)&sa, sizeof sa) < 0 || ::listen(lfd, 511) < 0) fatal("Failed listening on port " + std::to_string(g.port) + " (tcp), aborting.");
    {   const char* e = getenv("RIBBIT_FRONT_LOOPS");
        int n = e ? atoi(e) : (int)std::thread::hardware_concurrency() / 2; if (n < 1) n = 1;
        for (int i = 0; i < n; i++) { int l = epoll_create1(EPOLL_CLOEXEC); loops.push_back(l); std::thread(event_loop, l).detach(); }
    }
    logmsg('*', "Ready to accept connections tcp");
    for (;;) {
        sockaddr_in p{}; socklen_t pl = sizeof p;
        int fd = ::accept(lfd, (sockaddr*)&p, &pl);
        if (fd < 0) continue;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip);
        std::string addr = std::string(ip) + ":" + std::to_string(ntohs(p.sin_port));
        sockaddr_in la{}; socklen_t ll = sizeof la; getsockname(fd, (sockaddr*)&la, &ll); inet_ntop(AF_INET, &la.sin_addr, ip, sizeof ip);
        accept_conn(fd, addr, std::string(ip) + ":" + std::to_string(ntohs(la.sin_port)));
    }
}
