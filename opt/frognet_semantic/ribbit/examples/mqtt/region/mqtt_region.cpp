// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mqtt_region.cpp -- see mqtt_region.hpp. The walks below are Mosquitto 2.1.2's (subs.c sub__search / subs__process,
// retain.c retain__search), ported so that what a client receives, and in what order, is what Mosquitto would send.
// The structures they walk are lock-free.
#include "mqtt_region.hpp"
#include <algorithm>
#include <sched.h>
#include <climits>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <chrono>
#include <stdexcept>

namespace mqttr {
namespace lf = ribbit::lf;

bool msg_ref(Msg* m) {
    int r = m->refs.load(std::memory_order_acquire);
    while (r > 0) if (m->refs.compare_exchange_weak(r, r + 1, std::memory_order_acq_rel)) return true;
    return false;
}
void msg_unref(Msg* m) {
    if (m && m->refs.fetch_sub(1, std::memory_order_acq_rel) == 1) lf::ebr::retire(m);
}

static void futex_wait(std::atomic<uint32_t>* w, uint32_t seen, int64_t ms) {
    timespec ts{ms / 1000, (ms % 1000) * 1000000};
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(w), FUTEX_WAIT_PRIVATE, seen, ms >= 0 ? &ts : nullptr, nullptr, 0);
}
static void futex_wake(std::atomic<uint32_t>* w) {
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0);
}

// ---- per-thread counters: each thread bumps its own line; a reader sums them ------------------------------------
namespace {
struct alignas(64) Line { Region::Count c; Line* next = nullptr; };
std::atomic<Line*> lines{nullptr};
Line& my_line() {
    thread_local Line* l = nullptr;
    if (!l) { l = new Line(); Line* h = lines.load(); do { l->next = h; } while (!lines.compare_exchange_weak(h, l)); }
    return *l;
}
}
Region::Count& Region::count() { return my_line().c; }
int64_t Region::retained_count() {
    int64_t n = 0;
    for (Line* l = lines.load(); l; l = l->next) n += int64_t(l->c.retained_added.load()) - int64_t(l->c.retained_removed.load());
    return n;
}
std::string Region::stats_json() {
    uint64_t v[14] = {0}; int64_t retained = retained_count();
    for (Line* l = lines.load(); l; l = l->next) {
        const Count& c = l->c;
        v[0] += c.pubs; v[1] += c.deliveries_own; v[2] += c.deliveries_pushed; v[3] += c.wakes; v[4] += c.waits;
        v[5] += c.subs; v[6] += c.unsubs; v[7] += c.retained_set; v[8] += c.retained_cleared; v[9] += c.messages_made; v[10] += c.wakes_batched; v[11] += c.qos0_dropped; v[12] += c.paced; v[13] += c.paced_us;
    }
    auto& es = lf::ebr::stats();
    return "{\"pubs\":" + std::to_string(v[0]) + ",\"deliveries_own\":" + std::to_string(v[1]) + ",\"deliveries_pushed\":"
        + std::to_string(v[2]) + ",\"wakes\":" + std::to_string(v[3]) + ",\"waits\":" + std::to_string(v[4])
        + ",\"subs\":" + std::to_string(v[5]) + ",\"unsubs\":" + std::to_string(v[6]) + ",\"retained_set\":"
        + std::to_string(v[7]) + ",\"retained_cleared\":" + std::to_string(v[8]) + ",\"messages_made\":"
        + std::to_string(v[9] + id_.load() - 1) + ",\"wakes_batched\":" + std::to_string(v[10]) + ",\"retained\":" + std::to_string(retained) + ",\"qos0_dropped\":" + std::to_string(v[11]) + ",\"paced\":" + std::to_string(v[12]) + ",\"paced_us\":" + std::to_string(v[13]) + ",\"fronts\":" + std::to_string(fronts_n_.load())
        + ",\"ebr_pending\":" + std::to_string(es.pending.load()) + ",\"ebr_freed\":" + std::to_string(es.freed.load()) + "}";
}

// ---- tokenising: Mosquitto's sub__topic_tokenise, exactly --------------------------------------------------------
bool tokenise(const std::string& topic, std::vector<std::string>& out, std::string* share) {
    out.clear();
    if (topic.empty()) return false;
    std::vector<std::string> parts; size_t s = 0;
    for (;;) { size_t p = topic.find('/', s); parts.push_back(topic.substr(s, p == std::string::npos ? std::string::npos : p - s)); if (p == std::string::npos) break; s = p + 1; }
    if (topic[0] != '$') out.push_back("");
    for (auto& p : parts) out.push_back(p);
    if (out[0] == "$share") {
        size_t count = parts.size();
        if (count < 3 || (count == 3 && out[2].empty())) return false;
        if (share) { if (out[1].find_first_of("+#") != std::string::npos) return false; *share = out[1]; }
        std::vector<std::string> t; t.push_back("");
        for (size_t i = 2; i < out.size(); i++) t.push_back(out[i]);
        out.swap(t);
    }
    return true;
}

void tokenise_view(std::string_view topic, std::vector<std::string_view>& out) {
    out.clear();
    std::string_view s = topic;
    if (!s.empty() && s[0] != '$') out.push_back(std::string_view());
    size_t b = 0;
    for (;;) { size_t p = s.find('/', b); out.push_back(s.substr(b, p == std::string_view::npos ? std::string_view::npos : p - b)); if (p == std::string_view::npos) break; b = p + 1; }
}

// ---- nodes -----------------------------------------------------------------------------------------------------
static std::atomic<uint64_t> node_seq{1};
Node* Node::child(std::string_view n) const { auto* x = kids.get(n); return x ? x->val : nullptr; }
Node* Node::child_or_make(const std::string& n) {
    if (Node* c = child(n)) return c;
    Node* c = new Node(); c->name = n; c->seq = node_seq.fetch_add(1);
    LFMap<std::string, Node*>::Node* got = nullptr;
    if (!kids.add(n, c, &got)) { delete c; return got->val; }   // someone made it first: theirs
    kids_order.add(c->seq, c);
    if (n == "+") plus.store(c, std::memory_order_release);
    else if (n == "#") hash.store(c, std::memory_order_release);
    return c;
}

// ---- clients and fronts ----------------------------------------------------------------------------------------
Client* Region::find_client(const std::string& id) const { auto* x = clients_.get(id); return x ? x->val : nullptr; }
Client* Region::client(const std::string& id) {
    if (Client* c = find_client(id)) return c;
    Client* c = new Client(); c->id = id;
    LFMap<std::string, Client*>::Node* got = nullptr;
    if (!clients_.add(id, c, &got)) { delete c; return got->val; }
    return c;
}
int Region::hello() {
    int f = fronts_n_.fetch_add(1);
    if (f >= MAX_FRONTS) throw std::runtime_error("more than " + std::to_string(MAX_FRONTS) + " fronts");
    fronts_[f].live.store(true);
    return f;
}
void Region::own(const std::string& id, int front, int protocol) {
    Client* c = client(id);
    c->protocol.store(protocol, std::memory_order_relaxed);
    c->front.store(front, std::memory_order_release);
}

int Region::claim(const std::string& id, int front, int protocol) {
    Client* c = client(id);
    c->protocol.store(protocol, std::memory_order_relaxed);
    return c->front.exchange(front, std::memory_order_acq_rel);
}
void Region::want_session(const std::string& id, int holder, int wanter, bool clean_start) {
    auto* d = new Delivery{nullptr, id, 0, 0, 0};
    d->kind = Kind::HandoverWanted; d->from = wanter; d->clean_start = clean_start;
    push(holder, d);
}
void Region::give_session(const std::string& id, int to, std::string blob) {
    auto* d = new Delivery{nullptr, id, 0, 0, 0};
    d->kind = Kind::HandoverDone; d->blob = std::move(blob);
    push(to, d);
}
void (*Region::remote_wake)(int front) = nullptr;
void Region::post_result(int front, std::string text) { post_entry(front, Kind::PipelineResult, std::move(text)); }
void Region::post_entry(int front, Kind kind, std::string blob) {
    if (front < 0 || front >= MAX_FRONTS) return;
    auto* d = new Delivery(nullptr, std::string(), 0, 0, 0);
    d->kind = kind; d->blob = std::move(blob);
    push(front, d);
}

bool Region::forward(Msg* m, const std::string& id, uint8_t qos, uint8_t retain, uint32_t sid, int from) {
    Client* c = find_client(id);
    int fr = c ? c->front.load(std::memory_order_acquire) : -1;
    if (fr < 0 || fr == from || !msg_ref(m)) return false;
    push(fr, new Delivery{m, id, qos, retain, sid});
    return true;
}

void Region::publish_counts(int front, int64_t clients, int64_t conns, const int64_t* listener, int n) {
    FrontCounts& k = counts_[front];
    k.clients.store(clients, std::memory_order_relaxed); k.conns.store(conns, std::memory_order_relaxed);
    for (int i = 0; i < n && i < COUNTED_LISTENERS; i++) k.listener[i].store(listener[i], std::memory_order_relaxed);
}
void Region::others_counts(int front, int64_t* clients, int64_t* conns, int listener, int64_t* in_listener) const {
    int64_t c = 0, s = 0, l = 0;
    int n = fronts_n_.load(std::memory_order_acquire);
    for (int f = 0; f < n && f < MAX_FRONTS; f++) {
        if (f == front) continue;
        c += counts_[f].clients.load(std::memory_order_relaxed); s += counts_[f].conns.load(std::memory_order_relaxed);
        if (listener >= 0 && listener < COUNTED_LISTENERS) l += counts_[f].listener[listener].load(std::memory_order_relaxed);
    }
    if (clients) *clients = c;
    if (conns) *conns = s;
    if (in_listener) *in_listener = l;
}

// ---- subscriptions ---------------------------------------------------------------------------------------------
bool Region::subscribe(const std::string& cid, const std::string& filter, uint8_t options, uint32_t identifier) {
    lf::ebr::Guard g;
    count().subs++;
    std::vector<std::string> t; std::string share;
    if (!tokenise(filter, t, &share)) throw std::invalid_argument("bad filter");
    bool shared = filter.compare(0, 7, "$share/") == 0;
    Node* n = (shared ? shared_ : normal_).child_or_make(t[0]);
    for (auto& level : t) n = n->child_or_make(level);
    Sub* s = new Sub(); s->client = cid; s->filter = filter; s->options = options; s->identifier = identifier; s->who = client(cid);
    LFMap<std::string, SubSlot*>* by_client; LFMap<uint64_t, SubSlot*>* order;
    if (shared) {
        Group* gr = nullptr;
        if (auto* x = n->groups.get(share)) gr = x->val;
        else {
            gr = new Group(); gr->name = share;
            LFMap<std::string, Group*>::Node* got = nullptr;
            if (!n->groups.add(share, gr, &got)) { delete gr; gr = got->val; }
            else n->groups_order.add(node_seq.fetch_add(1), gr);
        }
        by_client = &gr->members; order = &gr->order;
    } else { by_client = &n->subs; order = &n->subs_order; }
    if (auto* x = by_client->get(cid)) {                        // a second subscription to the same filter: replace in place
        Sub* old = x->val->sub.load(std::memory_order_acquire);
        s->seq = old ? old->seq : 0;
        Sub* prev = x->val->sub.exchange(s, std::memory_order_acq_rel);
        if (prev) lf::ebr::retire(prev);
        return false;
    }
    s->seq = seq_.fetch_add(1);
    SubSlot* slot = new SubSlot(); slot->sub.store(s, std::memory_order_release);
    LFMap<std::string, SubSlot*>::Node* got = nullptr;
    if (!by_client->add(cid, slot, &got)) {                     // the same client raced itself: one of them wins
        Sub* prev = got->val->sub.exchange(s); if (prev) lf::ebr::retire(prev); delete slot; return false;
    }
    order->add(s->seq, slot);
    client(cid)->filters.add(filter, 1);
    return true;
}

bool Region::sub_of(const std::string& cid, const std::string& filter, uint8_t& options, uint32_t& identifier) {
    lf::ebr::Guard g;
    std::vector<std::string> t; std::string share;
    if (!tokenise(filter, t, &share)) return false;
    const bool shared = filter.compare(0, 7, "$share/") == 0;
    Node* n = (shared ? shared_ : normal_).child(t[0]);
    for (size_t i = 0; n && i < t.size(); i++) n = n->child(t[i]);
    if (!n) return false;
    LFMap<std::string, SubSlot*>* by_client;
    if (shared) { auto* x = n->groups.get(share); if (!x) return false; by_client = &x->val->members; }
    else by_client = &n->subs;
    auto* x = by_client->get(cid); if (!x) return false;
    const Sub* s = x->val->sub.load(std::memory_order_acquire); if (!s) return false;
    options = s->options; identifier = s->identifier;
    return true;
}

bool Region::unsubscribe(const std::string& cid, const std::string& filter, bool* root) {
    lf::ebr::Guard g;
    count().unsubs++;
    std::vector<std::string> t; std::string share;
    if (!tokenise(filter, t, &share)) throw std::invalid_argument("bad filter");
    bool shared = filter.compare(0, 7, "$share/") == 0;
    Node* n = (shared ? shared_ : normal_).child(t[0]);
    if (root) *root = n || t[0].empty() || (!shared && t[0] == "$SYS");
    for (size_t i = 0; n && i < t.size(); i++) n = n->child(t[i]);
    if (!n) return false;
    LFMap<std::string, SubSlot*>* by_client; LFMap<uint64_t, SubSlot*>* order; Group* gr = nullptr;
    if (shared) {
        auto* x = n->groups.get(share); if (!x) return false;
        gr = x->val; by_client = &gr->members; order = &gr->order;
    } else { by_client = &n->subs; order = &n->subs_order; }
    SubSlot* slot = nullptr;
    if (!by_client->remove(cid, &slot)) return false;
    Sub* s = slot->sub.exchange(nullptr);
    if (s) { order->remove(s->seq); lf::ebr::retire(s); }
    lf::ebr::retire(slot);
    if (gr && gr->members.size() == 0) {                        // Mosquitto deletes an emptied share group
        Group* gone = nullptr;
        if (n->groups.remove(share, &gone)) {
            n->groups_order.walk_all([&](LFMap<uint64_t, Group*>::Node* k) { if (k->val == gone) { n->groups_order.remove(k->key); return false; } return true; });
            lf::ebr::retire(gone);
        }
    }
    if (Client* c = find_client(cid)) c->filters.remove(filter);
    return true;
}

void Region::clean(const std::string& cid) {
    Client* c = find_client(cid);
    if (!c) return;
    std::vector<std::string> fs;
    { lf::ebr::Guard g; c->filters.walk_all([&](LFMap<std::string, int>::Node* x) { fs.push_back(x->key); return true; }); }
    for (auto& f : fs) unsubscribe(cid, f);
}

// ---- publish: Mosquitto's fan-out --------------------------------------------------------------------------------
void Region::send(const Sub* s, uint8_t qos, uint8_t retain, std::vector<Hit>& out) {
    uint8_t client_qos = s->options & 0x03, q;
    if (cfg.upgrade_outgoing_qos) q = client_qos; else q = qos > client_qos ? client_qos : qos;
    uint8_t r = (s->options & 0x08) ? retain : 0;            // retain-as-published
    out.push_back(Hit{s, q, r});
}

void Region::process(Node* n, std::string_view source, uint8_t qos, uint8_t retain, std::vector<Hit>& out, bool& have) {
    bool any = false;
    n->groups_order.walk_all([&](LFMap<uint64_t, Group*>::Node* gx) {      // subs__shared_process
        Group* gr = gx->val; any = true;
        size_t k = gr->order.size(); if (!k) return true;
        uint64_t turn = gr->turn.fetch_add(1, std::memory_order_relaxed);
        auto* pick = gr->order.nth(size_t(turn % k));
        if (!pick) pick = gr->order.first();
        if (pick) if (Sub* s = pick->val->sub.load(std::memory_order_acquire)) send(s, qos, retain, out);
        return true;
    });
    n->subs_order.walk_all([&](LFMap<uint64_t, SubSlot*>::Node* x) {         // subs__process
        any = true;
        Sub* s = x->val->sub.load(std::memory_order_acquire);
        if (!s) return true;
        if ((s->options & 0x04) && s->client == source) return true;          // no local
        send(s, qos, retain, out);
        return true;
    });
    if (any) have = true;
}

void Region::search(Node* n, const std::vector<std::string_view>& t, size_t i, std::string_view source, uint8_t qos,
                    uint8_t retain, std::vector<Hit>& out, bool& have) {
    if (i < t.size()) {
        if (Node* b = n->child(t[i])) {                                       // literal
            search(b, t, i + 1, source, qos, retain, out, have);
            if (i + 1 == t.size()) process(b, source, qos, retain, out, have);
        }
        if (Node* b = n->plus.load(std::memory_order_acquire)) {              // +
            search(b, t, i + 1, source, qos, retain, out, have);
            if (i + 1 == t.size()) process(b, source, qos, retain, out, have);
        }
    }
    if (Node* b = n->hash.load(std::memory_order_acquire))                    // #
        if (b->kids.size() == 0) process(b, source, qos, retain, out, have);
}

namespace {
thread_local bool t_defer_wakes = false;                // this thread is a co-resident front: its wakes are batched
thread_local std::vector<int> t_wake_list;              // fronts it pushed to since its last flush
thread_local std::vector<char> t_wake_marked;
}

int64_t Region::most_behind(int except) {
    int n = fronts_n_.load(std::memory_order_acquire); if (n > TRACKED_FRONTS) n = TRACKED_FRONTS;
    int64_t worst = 0;
    for (int fr = 0; fr < n; fr++) if (fr != except) { int64_t p = pending(fr); if (p > worst) worst = p; }
    return worst;
}
int64_t Region::pending(int fr) {
    uint64_t pushed = 0;
    for (Line* l = lines.load(std::memory_order_acquire); l; l = l->next) pushed += l->c.pushed_to[fr].load(std::memory_order_relaxed);
    return int64_t(pushed - fronts_[fr].taken.load(std::memory_order_acquire)) + fronts_[fr].held.load(std::memory_order_acquire);
}

int64_t Region::pace() {
    thread_local std::vector<int> targets;
    targets.clear();
    for (int fr : t_wake_list) if (fr < TRACKED_FRONTS) targets.push_back(fr);
    if (targets.empty()) return 0;
    auto t0 = std::chrono::steady_clock::now();
    int64_t us = 0;
    for (;;) {
        bool behind = false; int64_t cap = PACE_MAX_US;
        for (int fr : targets) {
            int64_t p = pending(fr);
            if (p > PACE_PENDING) behind = true;
            if (p > hold_limit(fr) / 2) cap = PACE_HARD_US;     // [HOLD_FOR_ROOM_V1] near the hold limit: wait longer
        }
        if (!behind) break;
        us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count();
        if (us >= cap) break;
        flush_wakes_list(targets);
        sched_yield();
    }
    if (us) { count().paced++; count().paced_us += uint64_t(us); }
    return us;
}

void Region::flush_wakes_list(const std::vector<int>& fronts) {     // wake these (if asleep) without clearing the batch
    for (int fr : fronts) {
        Front& f = fronts_[fr];
        if (f.armed.exchange(false, std::memory_order_seq_cst)) {
            uint64_t one = 1; ssize_t w = ::write(f.efd.load(std::memory_order_relaxed), &one, sizeof one); (void)w; count().wakes++;
        }
    }
}

void Region::wake_all() {                                              // every sleeping front of this box, once
    const int n = fronts_n_.load(std::memory_order_acquire);
    for (int fr = 0; fr < n && fr < MAX_FRONTS; fr++) {
        Front& f = fronts_[fr];
        if (f.efd.load(std::memory_order_relaxed) >= 0 && f.armed.exchange(false, std::memory_order_seq_cst)) {
            uint64_t one = 1; ssize_t w = ::write(f.efd.load(std::memory_order_relaxed), &one, sizeof one); (void)w; count().wakes++;
        }
    }
}
void Region::flush_wakes() {
    for (int fr : t_wake_list) {
        t_wake_marked[fr] = 0;
        Front& f = fronts_[fr];
        if (f.armed.exchange(false, std::memory_order_seq_cst)) {
            uint64_t one = 1; ssize_t w = ::write(f.efd.load(std::memory_order_relaxed), &one, sizeof one); (void)w; count().wakes++;
        }
    }
    t_wake_list.clear();
}

// [QOS0_BACKLOG_V1] undrained = every thread's pushes to that front, less what it took -- all single-writer cells,
// summed by the reader. Read every 32nd QoS 0 push per target, not every one.
thread_local int t_dropped = 0;
// [MEMORY_IS_MEMORY_V1] false on the thread that delivers another box's publishes: their retained state arrives as the
// retained tuple itself, applied in write order, never as a side effect of the delivery
thread_local bool t_store_retained = true;
void Region::store_retained_here(bool on) { t_store_retained = on; }
int Region::last_dropped() { return t_dropped; }
thread_local bool t_backpressure = false;
bool Region::last_backpressure() { return t_backpressure; }
// [QOS0_PACED_V1] Measured (v28, Pi 5 and the 2-core sandbox): one publisher into 16 subscribers on two fronts lost
// 21-23% at QoS 0 here, stock 0 -- stock's one thread is paced by its own subscribers' sockets, and a front behind
// dropped instead. Behind (more than max_queued_messages per client) now pauses the publisher at its socket, as a QoS 1/2
// delivery into a front behind does ([HOLD_FOR_ROOM_V1]); only far behind (QOS0_FAR times that, the publisher's pause
// already spent) is a QoS 0 delivery dropped, and counted as Mosquitto's dropped metric.
constexpr int64_t QOS0_FAR = 16;
int Region::backlogged(int fr) {
    // every front: co-resident ones publish their counts directly, remote ones with each pipeline ([FRONT_COUNTS_V1])
    if (cfg.max_queued_messages <= 0 || fr >= TRACKED_FRONTS) return 0;
    thread_local uint32_t seen[TRACKED_FRONTS] = {};
    thread_local int over[TRACKED_FRONTS] = {};
    if ((seen[fr]++ & 31) != 0) return over[fr];
    uint64_t pushed = 0;
    for (Line* l = lines.load(std::memory_order_acquire); l; l = l->next) pushed += l->c.pushed_to[fr].load(std::memory_order_relaxed);
    uint64_t taken = fronts_[fr].taken.load(std::memory_order_acquire);
    int64_t pending = int64_t(pushed - taken) + fronts_[fr].held.load(std::memory_order_acquire);
    int64_t clients = counts_[fr].clients.load(std::memory_order_relaxed);
    const int64_t bound = cfg.max_queued_messages * (clients > 1 ? clients : 1);
    over[fr] = pending > QOS0_FAR * bound ? 2 : pending > bound ? 1 : 0;
    return over[fr];
}

std::string Region::diag_fronts() {
    std::string o;
    uint64_t dropped = 0, paced = 0, paced_us = 0, pubs = 0, pushed_all = 0;
    for (Line* l = lines.load(std::memory_order_acquire); l; l = l->next) { dropped += l->c.qos0_dropped; paced += l->c.paced; paced_us += l->c.paced_us; pubs += l->c.pubs; pushed_all += l->c.deliveries_pushed; }
    o += "pubs=" + std::to_string(pubs) + " pushed=" + std::to_string(pushed_all) + " qos0_dropped=" + std::to_string(dropped) + " paced=" + std::to_string(paced) + " paced_us=" + std::to_string(paced_us);
    const int n = fronts_n_.load(std::memory_order_acquire);
    for (int fr = 0; fr < n && fr < TRACKED_FRONTS; fr++) {
        uint64_t pushed = 0;
        for (Line* l = lines.load(std::memory_order_acquire); l; l = l->next) pushed += l->c.pushed_to[fr].load(std::memory_order_relaxed);
        const uint64_t taken = fronts_[fr].taken.load(std::memory_order_acquire);
        o += " | f" + std::to_string(fr) + " pushed_to=" + std::to_string(pushed) + " taken=" + std::to_string(taken) + " backlog=" + std::to_string(int64_t(pushed - taken))
           + " held=" + std::to_string(fronts_[fr].held.load(std::memory_order_acquire)) + " clients=" + std::to_string(counts_[fr].clients.load(std::memory_order_relaxed))
           + " limit=" + std::to_string(cfg.max_queued_messages * std::max<int64_t>(1, counts_[fr].clients.load(std::memory_order_relaxed)))
           + " parked=" + (fronts_[fr].parked.load(std::memory_order_acquire) ? "1" : "0") + " waiters=" + std::to_string(fronts_[fr].waiters.load(std::memory_order_relaxed));
    }
    return o;
}

void Region::push(int front, Delivery* d) {
    Front& f = fronts_[front];
    Delivery* h = f.head.load(std::memory_order_relaxed);
    do { d->next = h; } while (!f.head.compare_exchange_weak(h, d, std::memory_order_seq_cst, std::memory_order_relaxed));
    int efd = f.efd.load(std::memory_order_relaxed);
    if (front < TRACKED_FRONTS) count().pushed_to[front].fetch_add(1, std::memory_order_relaxed);
    if (efd >= 0) {                                                          // a front in this process
        if (t_defer_wakes) {                                                 // [BATCHED_WAKE_V1]
            if (!t_wake_marked[front]) { t_wake_marked[front] = 1; t_wake_list.push_back(front); count().wakes_batched++; }
            return;
        }
        if (f.armed.exchange(false, std::memory_order_seq_cst)) { uint64_t one = 1; ssize_t w = ::write(efd, &one, sizeof one); (void)w; count().wakes++; }
        return;
    }
    f.gen.fetch_add(1, std::memory_order_release);
    if (f.waiters.load(std::memory_order_seq_cst)) { futex_wake(&f.gen); count().wakes++; }
    if (remote_wake && f.parked.load(std::memory_order_seq_cst)) { remote_wake(front); count().wakes++; }   // [HELD_WAIT_V1]
}

int Region::hello_local(int efd) {
    int f = hello();
    fronts_[f].efd.store(efd, std::memory_order_release);
    t_defer_wakes = true; t_wake_marked.assign(MAX_FRONTS, 0);             // [BATCHED_WAKE_V1] this thread is that front
    return f;
}
std::vector<Delivery*> Region::take(int front) {
    Delivery* list = fronts_[front].head.exchange(nullptr, std::memory_order_acquire);
    std::vector<Delivery*> out;
    for (Delivery* d = list; d; d = d->next) out.push_back(d);
    if (!out.empty()) fronts_[front].taken.store(fronts_[front].taken.load(std::memory_order_relaxed) + out.size(), std::memory_order_release);
    std::reverse(out.begin(), out.end());                                    // a stack: oldest last, so turn it round
    return out;
}
bool Region::arm(int front) {
    Front& f = fronts_[front];
    f.armed.store(true, std::memory_order_seq_cst);
    return f.head.load(std::memory_order_seq_cst) != nullptr;
}

// [LAZY_MESSAGE_V1] A message made from a publish's views is numbered from its front's own range: unique without a
// counter every front writes (the shared counter was one cache line all fronts fought over).
// a message id: this thread's own range ((slot+1) << 44), then its own count -- no shared counter, and no two threads
// (fronts, or mqtt-ram's workers publishing for the same front) ever make the same id
static std::atomic<uint64_t> id_slots{0};
static thread_local uint64_t local_ids = (id_slots.fetch_add(1, std::memory_order_relaxed) + 1) << 44;
static Msg* materialise(const MsgIn& in, int front) {
    auto* m = new Msg();
    (void)front;
    m->id = ++local_ids;
    m->topic.assign(in.topic.data(), in.topic.size());
    m->payload.assign(in.payload.data(), in.payload.size());
    m->props.assign(in.props.data(), in.props.size());
    m->src_id.assign(in.src_id.data(), in.src_id.size());
    if (in.has_user) { m->has_user = true; m->src_user.assign(in.src_user.data(), in.src_user.size()); }
    m->qos = in.qos; m->retain = in.retain; m->origin = in.origin; m->expiry_ms = in.expiry_ms; m->src_port = in.src_port;
    return m;
}

// Scratch per nesting depth: an own delivery runs front code, which could publish again (a plugin, a will) -- the inner
// publish gets its own lists, the outer one's stay intact.
namespace {
struct Scratch { std::vector<std::string_view> t; std::vector<Hit> hits; std::vector<const Client*> sent_to; };
thread_local std::vector<Scratch*> scratch_pool;
thread_local size_t scratch_depth = 0;
struct ScratchUse {
    Scratch* s;
    ScratchUse() {
        if (scratch_depth == scratch_pool.size()) scratch_pool.push_back(new Scratch());
        s = scratch_pool[scratch_depth++];
        s->hits.clear(); s->sent_to.clear();
    }
    ~ScratchUse() { scratch_depth--; }
};
}

bool Region::fan_out(const MsgIn& in, Msg* kept, int front, OwnFn own, void* ud, std::vector<Match>* own_list, bool* had_retained) {
    lf::ebr::Guard g;
    count().pubs++;
    ScratchUse use; Scratch& s = *use.s;
    tokenise_view(in.topic, s.t);
    bool have_n = false, have_s = false;
    if (Node* r = normal_.child(s.t[0])) search(r, s.t, 0, in.src_id, in.qos, in.retain, s.hits, have_n);
    if (Node* r = shared_.child(s.t[0])) search(r, s.t, 0, in.src_id, in.qos, in.retain, s.hits, have_s);
    Msg* m = kept;
    bool made = false;
    t_dropped = 0;
    t_backpressure = false;
    uint64_t looked = 0;                                  // [HOLD_FOR_ROOM_V1] fronts whose backlog this publish read
    for (auto& x : s.hits) {
        const Client* c = x.sub->who;
        int fr = c ? c->front.load(std::memory_order_acquire) : -1;
        // allow_duplicate_messages false: one copy per client, for clients below MQTT 5, non-retained (database.c 586-596)
        if (!cfg.allow_duplicate_messages && !x.retain && c && c->protocol.load(std::memory_order_relaxed) != 5) {
            bool dup = false; for (auto* d : s.sent_to) if (d == c) { dup = true; break; }
            if (dup) continue;
            s.sent_to.push_back(c);
        }
        if (fr == front) {
            count().deliveries_own++;
            if (own) own(ud, x.sub->client, x.qos, x.retain, x.sub->identifier);
            else own_list->push_back(Match{x.sub->client, x.qos, x.retain, x.sub->identifier});
            continue;
        }
        if (fr < 0) continue;
        if (x.qos == 0) {                                                  // [QOS0_BACKLOG_V1] [QOS0_PACED_V1]
            const int behind = backlogged(fr);
            if (behind == 2) { count().qos0_dropped++; t_dropped++; continue; }
            if (behind == 1) t_backpressure = true;
        }
        if (!m) { m = materialise(in, front); made = true; count().messages_made++; }
        if (!msg_ref(m)) continue;
        push(fr, new Delivery{m, x.sub->client, x.qos, x.retain, x.sub->identifier});
        count().deliveries_pushed++;
        // [HOLD_FOR_ROOM_V1] a QoS 1/2 delivery into a front already behind: the publisher should be paused at its socket
        if (x.qos > 0 && fr < TRACKED_FRONTS && !(looked & (1ull << fr))) {
            looked |= 1ull << fr;
            if (pending(fr) > PACE_PENDING) t_backpressure = true;
        }
    }
    if (in.retain && t_store_retained) {                                      // retain__store, after the live fan-out
        if (!m) { m = materialise(in, front); made = true; count().messages_made++; }
        bool had = retain(m); if (had_retained) *had_retained = had;
    }
    if (made) msg_unref(m);
    return have_n || have_s;
}

bool Region::publish(Msg* m, int front, std::vector<Match>& own, bool* had_retained) {
    MsgIn in;
    in.topic = m->topic; in.payload = m->payload; in.props = m->props; in.src_id = m->src_id; in.src_user = m->src_user;
    in.has_user = m->has_user; in.qos = m->qos; in.retain = m->retain; in.origin = m->origin;
    in.expiry_ms = m->expiry_ms; in.src_port = m->src_port;
    return fan_out(in, m, front, nullptr, nullptr, &own, had_retained);
}

bool Region::publish(const MsgIn& in, int front, OwnFn own, void* ud, bool* had_retained) {
    return fan_out(in, nullptr, front, own, ud, nullptr, had_retained);
}

bool Region::retain(Msg* m) {
    lf::ebr::Guard g;
    std::vector<std::string> t;
    tokenise(m->topic, t, nullptr);
    Node* n = retains_.child_or_make(t[0]);
    for (auto& level : t) n = n->child_or_make(level);
    Msg* old;
    if (m->payload.empty()) { old = n->retained.exchange(nullptr, std::memory_order_acq_rel); count().retained_cleared++; if (old) count().retained_removed++; }
    else { msg_ref(m); old = n->retained.exchange(m, std::memory_order_acq_rel); count().retained_set++; if (!old) count().retained_added++; }
    if (old) msg_unref(old);
    return old != nullptr;
}

void Region::retain_walk(Node* n, std::vector<Msg*>& out) {
    n->kids_order.walk_all([&](LFMap<uint64_t, Node*>::Node* x) { retain_take(x->val, out); retain_walk(x->val, out); return true; });
}
void Region::retained_all(std::vector<Msg*>& out) {
    lf::ebr::Guard g;
    retain_walk(&retains_, out);
}

// ---- retained on subscribe: retain__search, exactly ------------------------------------------------------------
void Region::retain_take(Node* n, std::vector<Msg*>& out) {
    Msg* m = n->retained.load(std::memory_order_acquire);
    if (m && msg_ref(m)) out.push_back(m);
}
int Region::retain_search(Node* n, const std::vector<std::string>& t, size_t i, int level, std::vector<Msg*>& out) {
    int flag = 0;
    bool last = i + 1 >= t.size();
    if (t[i] == "#" && last) {
        n->kids_order.walk_all([&](LFMap<uint64_t, Node*>::Node* x) {
            Node* b = x->val; flag = -1;
            retain_take(b, out);
            if (b->kids.size()) retain_search(b, t, i, level + 1, out);
            return true;
        });
    } else if (t[i] == "+") {
        n->kids_order.walk_all([&](LFMap<uint64_t, Node*>::Node* x) {
            Node* b = x->val;
            if (!last) {
                if (retain_search(b, t, i + 1, level + 1, out) == -1 || (t[i + 1] == "#" && level > 0)) retain_take(b, out);
            } else retain_take(b, out);
            return true;
        });
    } else if (Node* b = n->child(t[i])) {
        if (!last) {
            if (retain_search(b, t, i + 1, level + 1, out) == -1 || (t[i + 1] == "#" && level > 0)) retain_take(b, out);
        } else retain_take(b, out);
    }
    return flag;
}
void Region::retained_for(const std::string& filter, std::vector<Msg*>& out) {
    if (filter.compare(0, 7, "$share/") == 0) return;
    lf::ebr::Guard g;
    std::vector<std::string> t;
    if (!tokenise(filter, t, nullptr)) return;
    if (Node* r = retains_.child(t[0])) retain_search(r, t, 0, 0, out);
}

// ---- a front's one parked wait -----------------------------------------------------------------------------------
bool Region::wait_ready(int front, int64_t wait_ms) {
    Front& f = fronts_[front];
    count().waits++;
    if (f.head.load(std::memory_order_acquire)) return true;
    if (wait_ms <= 0) return false;
    auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(wait_ms);
    f.waiters.fetch_add(1, std::memory_order_seq_cst);
    bool ready = false;
    for (;;) {
        uint32_t seen = f.gen.load(std::memory_order_acquire);
        if (f.head.load(std::memory_order_acquire)) { ready = true; break; }
        int64_t left = std::chrono::duration_cast<std::chrono::milliseconds>(until - std::chrono::steady_clock::now()).count();
        if (left <= 0) break;
        futex_wait(&f.gen, seen, left);
    }
    f.waiters.fetch_sub(1, std::memory_order_seq_cst);
    return ready;
}

std::vector<Delivery*> Region::wait(int front, int64_t wait_ms) {
    Front& f = fronts_[front];
    count().waits++;
    Delivery* list = f.head.exchange(nullptr, std::memory_order_acquire);
    if (!list && wait_ms > 0) {
        auto until = std::chrono::steady_clock::now() + std::chrono::milliseconds(wait_ms);
        f.waiters.fetch_add(1, std::memory_order_seq_cst);
        for (;;) {
            uint32_t seen = f.gen.load(std::memory_order_acquire);
            list = f.head.exchange(nullptr, std::memory_order_acquire);
            if (list) break;
            int64_t left = std::chrono::duration_cast<std::chrono::milliseconds>(until - std::chrono::steady_clock::now()).count();
            if (left <= 0) break;
            futex_wait(&f.gen, seen, left);
        }
        f.waiters.fetch_sub(1, std::memory_order_seq_cst);
    }
    std::vector<Delivery*> out;
    for (Delivery* d = list; d; d = d->next) out.push_back(d);
    if (!out.empty()) fronts_[front].taken.store(fronts_[front].taken.load(std::memory_order_relaxed) + out.size(), std::memory_order_release);
    std::reverse(out.begin(), out.end());                                    // a stack: oldest last, so turn it round
    return out;
}

}  // namespace mqttr
