// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// libmosquitto_ram.cpp -- [CLIENT_ON_THE_MEMORY_V1] libmosquitto, the same ABI, on the memory ([SPACE_V1]: ribbit_space).
//
// There is no broker, no connection between a client and a server, and no message between two parties. Producers
// write and consumers read: the tuple space is addressed by what MQTT addresses, the topic. [TOPIC_IS_THE_ADDRESS_V1]
//
//   truth                        owner            address                                         bag
//   a publish                    its publisher    (mqtt, <topic>, <writer>.<n mod RING>)           b e n o p pr prq q qos r si sp t
//   that a writer uses a topic   its publisher    (mqtt.topics, all, <topic>␟<writer>)             t      (written once)
//   a topic's retained message   its topic        (mqtt.retained, <topic>, hwm)                    live p q qos t
//   (its high water mark)
//
// Every cell has one owner, its one writer; nobody writes anyone else's, and the memory never decides between writers.
//   publish      writes its cell at its topic (and its retained cell if retained), and is done: on_publish is called
//                when it is written. n is the writer's count on that topic. The first time a writer uses a topic it
//                says so in the directory.
//   subscribe    the instance reads each topic its filters match, once per topic however many filters match it: a
//                topic filter is its topic; a wildcard filter is the topics the directory names that match it, now
//                and as they appear. The retained cells it matches are read and given. It returns at once.
//   the readers  [READERS_IN_PARALLEL_V1] every topic an instance reads is a thread of its own, asking the memory for
//                what was written there past what it has and waiting for its own answer (FNWP's fan-in and fan-out;
//                identical reads in flight coalesce). Every family read is one family's write order: no race.
//   retained     a new subscriber looks up each matching topic's retained tuple by its address
// Nothing paces a publisher and nothing comes back to it. A consumer that reads too slowly finds a writer's count on a
// topic jumping (its ring moved past what was not read yet): it counts what it missed and says so -- its anomaly, its
// decision, nobody else's.
//
// Where the memory is: MQTT_MEMORY -- here[:HOST:PORT] (this process holds it; others reach it at HOST:PORT), or
// HOST:PORT (else MQTT_REGION, else 127.0.0.1:9009, the node's FrogNet daemon). The host and port a client names in
// mosquitto_connect are not where anything goes: there is nothing to connect to.
#include "frogram.hpp"
#include "ribbit_space.hpp"
#include "ram_host.hpp"
extern "C" {
#include "mosquitto.h"
}
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <unistd.h>
#include <sys/eventfd.h>
#include <unordered_map>
#include <vector>

namespace {
const char* SVC = "mqtt";                               // publishes, at their topics
const char* SVC_RET = "mqtt.retained";                  // a topic's retained message, its high water mark: (<topic>, hwm)
const char* HWM = "hwm";
const char* SVC_TOPICS = "mqtt.topics";                 // the directory: (all, <topic>␟<writer>)
const char* ALL = "all";
constexpr uint64_t RING = 16384;                        // a writer's cells on one topic: its publish n there is cell n mod RING
const std::vector<std::string> FO_MSG = {"b", "e", "n", "o", "p", "pr", "prq", "q", "qos", "r", "si", "sp", "t"};

bool utf8_ok(const unsigned char* p, size_t n) {
    for (size_t i = 0; i < n;) {
        unsigned c = p[i];
        if (c < 0x80) { if (c == 0) return false; ++i; continue; }
        int k = (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2 : (c & 0xF8) == 0xF0 ? 3 : -1;
        if (k < 0 || i + size_t(k) >= n) return false;
        for (int j = 1; j <= k; ++j) if ((p[i + j] & 0xC0) != 0x80) return false;
        i += size_t(k) + 1;
    }
    return true;
}
const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
std::string b64(const void* d, size_t n) {
    const unsigned char* in = static_cast<const unsigned char*>(d); std::string o; o.reserve((n + 2) / 3 * 4); size_t i = 0;
    for (; i + 2 < n; i += 3) { uint32_t v = in[i] << 16 | in[i + 1] << 8 | in[i + 2]; o += B64[v >> 18]; o += B64[(v >> 12) & 63]; o += B64[(v >> 6) & 63]; o += B64[v & 63]; }
    if (i + 1 == n) { uint32_t v = in[i] << 16; o += B64[v >> 18]; o += B64[(v >> 12) & 63]; o += "=="; }
    else if (i + 2 == n) { uint32_t v = in[i] << 16 | in[i + 1] << 8; o += B64[v >> 18]; o += B64[(v >> 12) & 63]; o += B64[(v >> 6) & 63]; o += '='; }
    return o;
}
std::string unb64(const std::string& s) {
    static int8_t T[256]; static bool init = [] { memset(T, -1, sizeof T); for (int k = 0; k < 64; k++) T[uint8_t(B64[k])] = int8_t(k); return true; }(); (void)init;
    std::string o; uint32_t acc = 0; int bits = 0;
    for (unsigned char c : s) { if (c == '=') break; int8_t v = T[c]; if (v < 0) break; acc = acc << 6 | uint32_t(v); bits += 6; if (bits >= 8) { bits -= 8; o += char((acc >> bits) & 0xff); } }
    return o;
}
bool wildcard(const std::string& f) { return f.find_first_of("+#") != std::string::npos; }
}  // namespace

// a subscription of one client instance; a list of them is replaced whole when it changes, never edited (the reader
// holds the list it loaded; the old one is let go when the instance is destroyed)
struct Sub { std::string filter; int qos; int options; };
struct Subs { std::vector<Sub> v; const Subs* prev = nullptr; };
// one topic an instance publishes to: its count there (insert-only, found by CAS: no lock)
struct TopicState { std::string topic; TopicState* next_in_bucket = nullptr; std::atomic<uint64_t> seq{0}; };
struct Proc;
template <class N, size_t B> struct InsertOnly {
    std::atomic<N*> b[B] = {};
    template <class Eq, class Make> N* get(size_t h, Eq eq, Make make, bool* made = nullptr) {
        auto& slot = b[h % B];
        for (;;) { N* head = slot.load(std::memory_order_acquire);
            for (N* n = head; n; n = n->next_in_bucket) if (eq(n)) return n;
            N* f = make(); f->next_in_bucket = head;
            if (slot.compare_exchange_strong(head, f, std::memory_order_acq_rel)) { if (made) *made = true; return f; }
            delete f; }
    }
};

struct Held { std::string key; Held* next_in_bucket = nullptr; };
struct mosquitto {
    std::string id, writer; bool clean = true; void* obj = nullptr; int protocol = 4;
    LIBMOSQ_CB_connect on_connect = nullptr; LIBMOSQ_CB_connect_with_flags on_connect_flags = nullptr; LIBMOSQ_CB_connect_v5 on_connect_v5 = nullptr;
    LIBMOSQ_CB_pre_connect on_pre_connect = nullptr;
    LIBMOSQ_CB_disconnect on_disconnect = nullptr; LIBMOSQ_CB_disconnect_v5 on_disconnect_v5 = nullptr;
    LIBMOSQ_CB_publish on_publish = nullptr; LIBMOSQ_CB_publish_v5 on_publish_v5 = nullptr;
    LIBMOSQ_CB_message on_message = nullptr; LIBMOSQ_CB_message_v5 on_message_v5 = nullptr;
    LIBMOSQ_CB_subscribe on_subscribe = nullptr; LIBMOSQ_CB_subscribe_v5 on_subscribe_v5 = nullptr;
    LIBMOSQ_CB_unsubscribe on_unsubscribe = nullptr; LIBMOSQ_CB_unsubscribe_v5 on_unsubscribe_v5 = nullptr; LIBMOSQ_CB_unsubscribe2_v5 on_unsubscribe2_v5 = nullptr;
    LIBMOSQ_CB_log on_log = nullptr;
    std::atomic<bool> connected{false}, dead{false}, connect_pending{false}, disconnect_pending{false};
    std::atomic<uint32_t> state{0};                  // bumped when something for the loop happens; loop_forever sleeps on it
    std::atomic<int> mid{0};
    std::atomic<const Subs*> subs{nullptr};
    std::thread loop_thread; std::atomic<bool> loop_stop{false};
    int efd = -1;                                     // mosquitto_socket: an fd for applications that select on it
    std::string will_topic, will_payload; int will_qos = 0; bool will_retain = false, will_set = false;
    InsertOnly<TopicState, 256> topics;               // the topics it publishes to
    InsertOnly<Held, 1024> held;                      // [READERS_IN_PARALLEL_V1] the topics it reads: a thread each
    std::atomic<bool> directory{false};               // it reads the directory (it has a wildcard filter)
    TopicState* topic(const std::string& t) {
        return topics.get(std::hash<std::string>()(t), [&](TopicState* x) { return x->topic == t; }, [&] { auto* x = new TopicState; x->topic = t; return x; });
    }
};

namespace {
struct Proc {
    std::unique_ptr<ribbit::Space> sp; std::string where;
    std::atomic<mosquitto*> insts[1024] = {};         // the client instances that subscribe (insert-only; a destroyed one is marked dead)
    std::atomic<int> n_insts{0};
    std::atomic<uint64_t> delivered{0}, missed{0};
};
Proc* P = nullptr;
std::once_flag P_once;
std::atomic<uint64_t> g_instances{0};
Proc* proc() {
    std::call_once(P_once, [] {
        auto* p = new Proc;
        const char* e = getenv("MQTT_MEMORY"); if (!e || !*e) e = getenv("MQTT_REGION"); if (!e || !*e) e = "127.0.0.1:9009";
        p->where = e;
        try { p->sp.reset(new ribbit::Space(e, "mqtt")); }
        catch (const std::exception& x) { fprintf(stderr, "libmosquitto: the memory at %s: %s\n", e, x.what()); }
        P = p;
    });
    return P;
}
bool usable(Proc* p) { return p && p->sp != nullptr; }
void log_to(mosquitto* m, int level, const std::string& s) { if (m->on_log) m->on_log(m, m->obj, level, s.c_str()); }
void poke(mosquitto* m) { m->state.fetch_add(1, std::memory_order_release); m->state.notify_all(); }

// one publish, given to every instance whose subscriptions match it -- on the reader. An instance is given a publish by
// one held read only: the one on its topic if it subscribed to the topic itself, else the space's.
// [READERS_IN_PARALLEL_V1] FNWP's reader model: every instance reads for itself. Each family an instance subscribes
// to -- its topic's, or the space's for a wildcard filter -- is a read held by a thread of its own, asking the memory
// for what was written past what it has and waiting for its own answer; the answer comes back to it alone, and it gives
// what it read to its instance, there and then. Two instances reading the same family past the same id are one request
// on the wire (the session coalesces it) and are released together by its one answer. An instance is given a publish
// by one of its reads only: the one on its topic if it subscribed to the topic itself, else the space's.
// [DECODE_ONCE_V1] the publish as the session decoded it: its values, read in place -- the payload is handed to the
// application straight out of the decoded bag
const std::string& sval(const ribbit::BagArr& a, const char* k) { static const std::string none; const pyv::Obj* v = a.get(k); return v && v->is<pyv::Str>() ? v->as<pyv::Str>().s : none; }
void give(Proc* p, mosquitto* m, const ribbit::BagArr& g, uint64_t& last) {
    if (m->dead.load(std::memory_order_acquire) || !m->connected.load(std::memory_order_acquire)) return;
    const std::string& topic = sval(g, "t");
    const std::string& writer = sval(g, "b");
    {                                                 // the consumer's own check: did a writer's ring move past it?
        const uint64_t n = uint64_t(g.num("n"));
        if (last && n > last + 1) {
            const uint64_t gap = n - last - 1; const uint64_t before = p->missed.fetch_add(gap, std::memory_order_relaxed);
            if (before < 1000000 && (before == 0 || before / 100000 != (before + gap) / 100000))
                fprintf(stderr, "libmosquitto: missed %llu publishes of %s on %s (read too late: its ring had moved on); %llu missed in all\n",
                        (unsigned long long)gap, writer.c_str(), topic.c_str(), (unsigned long long)(before + gap));
        }
        if (n > last) last = n;
    }
    const Subs* ss = m->subs.load(std::memory_order_acquire); if (!ss) return;
    const int qos = int(g.num("qos"));
    int best = -1;
    for (auto& s : ss->v) { bool hit = false; if (mosquitto_topic_matches_sub(s.filter.c_str(), topic.c_str(), &hit) == MOSQ_ERR_SUCCESS && hit) {
        if ((s.options & MQTT_SUB_OPT_NO_LOCAL) && writer == m->writer) continue;
        best = std::max(best, std::min(qos, s.qos)); } }
    if (best < 0) return;                             // no filter of its matches it now (unsubscribed)
    const std::string& q = sval(g, "q"); std::string decoded; if (!q.empty()) decoded = unb64(q);
    const std::string& payload = q.empty() ? sval(g, "p") : decoded;
    mosquitto_message msg{}; msg.mid = 0; msg.topic = const_cast<char*>(topic.c_str()); msg.payload = payload.empty() ? nullptr : const_cast<char*>(payload.data());
    msg.payloadlen = int(payload.size()); msg.qos = best; msg.retain = false;
    if (m->on_message_v5) m->on_message_v5(m, m->obj, &msg, nullptr);
    else if (m->on_message) m->on_message(m, m->obj, &msg);
    p->delivered.fetch_add(1, std::memory_order_relaxed);
}
// an instance's read of one topic, once per instance however many of its filters match the topic: a thread of its own.
// from < 0: what is written from now on; else past that id (a topic that appeared after the instance subscribed: all of it)
void hold_topic(Proc* p, mosquitto* m, const std::string& topic, long long from) {
    bool made = false;
    m->held.get(std::hash<std::string>()(topic), [&](Held* x) { return x->key == topic; }, [&] { auto* x = new Held; x->key = topic; return x; }, &made);
    if (!made) return;
    auto last = std::make_shared<std::unordered_map<std::string, uint64_t>>();   // this read's alone: one thread
    p->sp->watch(SVC, topic, from, [p, m, last](std::vector<ribbit::SpaceRow>& rows) {
        for (auto& r : rows) { const size_t dot = r.instance.rfind('.'); if (ribbit::BagArrP a = r.fields()) give(p, m, *a, (*last)[r.instance.substr(0, dot)]); } });
}
bool matches_wild(mosquitto* m, const std::string& topic) {
    const Subs* ss = m->subs.load(std::memory_order_acquire); if (!ss) return false;
    for (auto& s : ss->v) { if (!wildcard(s.filter)) continue; bool hit = false;
        if (mosquitto_topic_matches_sub(s.filter.c_str(), topic.c_str(), &hit) == MOSQ_ERR_SUCCESS && hit) return true; }
    return false;
}
std::string topic_of(const std::string& inst) { return inst.substr(0, inst.find('\x1f')); }
// a filter's topics: a topic filter is its topic, from now on; a wildcard filter is every topic the directory names that
// matches it -- those there now from now on, and, through the instance's read of the directory, every one that appears
void hold(Proc* p, mosquitto* m, const std::string& filter) {
    if (!wildcard(filter)) { hold_topic(p, m, filter, -1); return; }
    const uint64_t d0 = p->sp->newest(SVC_TOPICS, ALL);
    for (auto& r : p->sp->read(SVC_TOPICS, ALL)) {   // the topics in use now; a later one is the directory read's
        if (r.id > d0) continue;
        const std::string t = topic_of(r.instance); bool hit = false;
        if (mosquitto_topic_matches_sub(filter.c_str(), t.c_str(), &hit) == MOSQ_ERR_SUCCESS && hit) hold_topic(p, m, t, -1);
    }
    if (m->directory.exchange(true)) return;          // its directory read is running: it matches against every filter
    p->sp->watch(SVC_TOPICS, ALL, (long long)d0, [p, m](std::vector<ribbit::SpaceRow>& rows) {
        for (auto& r : rows) { const std::string t = topic_of(r.instance); if (matches_wild(m, t)) hold_topic(p, m, t, 0); } });
}
int reg(Proc* p, mosquitto* m) {
    const int n = p->n_insts.load(std::memory_order_acquire);
    for (int k = 0; k < n; ++k) if (p->insts[k].load(std::memory_order_acquire) == m) return 0;
    const int k = p->n_insts.fetch_add(1, std::memory_order_acq_rel);
    if (k >= 1024) return MOSQ_ERR_NOMEM;
    p->insts[k].store(m, std::memory_order_release);
    return 0;
}
// the loop's work: the callbacks a connect and a disconnect owe, on the application's loop thread as stock gives them
void run_pending(mosquitto* m) {
    if (m->connect_pending.exchange(false)) {
        if (m->on_connect_v5) m->on_connect_v5(m, m->obj, 0, 0, nullptr);
        else if (m->on_connect_flags) m->on_connect_flags(m, m->obj, 0, 0);
        else if (m->on_connect) m->on_connect(m, m->obj, 0);
    }
    if (m->disconnect_pending.exchange(false)) {
        if (m->on_disconnect_v5) m->on_disconnect_v5(m, m->obj, 0, nullptr);
        else if (m->on_disconnect) m->on_disconnect(m, m->obj, 0);
    }
}
void give_ack(mosquitto* m, int id) { if (m->on_publish_v5) m->on_publish_v5(m, m->obj, id, 0, nullptr); else if (m->on_publish) m->on_publish(m, m->obj, id); }
const std::vector<std::string> FO_RET = {"live", "p", "q", "qos", "t"};
const std::vector<std::string> FO_DIR = {"t"};
int do_publish(mosquitto* m, int* mid, const char* topic, int len, const void* payload, int qos, bool retain) {
    if (!m || !topic || qos < 0 || qos > 2 || len < 0 || (len > 0 && !payload)) return MOSQ_ERR_INVAL;
    if (mosquitto_pub_topic_check(topic) != MOSQ_ERR_SUCCESS) return MOSQ_ERR_INVAL;
    if (!m->connected.load(std::memory_order_acquire)) return MOSQ_ERR_NO_CONN;
    Proc* p = proc(); if (!usable(p)) return MOSQ_ERR_NO_CONN;
    const int id = m->mid.fetch_add(1, std::memory_order_relaxed) % 65535 + 1; if (mid) *mid = id;
    TopicState* t = m->topic(topic);
    const bool pu = utf8_ok(static_cast<const unsigned char*>(payload), size_t(len));
    std::string ptext = pu ? std::string(static_cast<const char*>(payload), size_t(len)) : std::string();
    std::string pb64 = pu ? std::string() : b64(payload, size_t(len));
    const uint64_t n = t->seq.fetch_add(1, std::memory_order_acq_rel) + 1;
    if (n == 1) {                                     // this writer's first publish on the topic: the directory says so
        try { p->sp->write_fields(SVC_TOPICS, ALL, t->topic + '\x1f' + m->writer, FO_DIR, {{"t", pyv::Str{t->topic}}}); }
        catch (const std::exception& x) { log_to(m, MOSQ_LOG_ERR, std::string("the memory: ") + x.what()); return MOSQ_ERR_NO_CONN; }
    }
    using pyv::Str; using pyv::Int;
    try {
        if (retain) {
            semcodec::Fields r; r.reserve(5);
            r.emplace_back("live", Int::of(len ? 1 : 0)); r.emplace_back("p", Str{ptext}); r.emplace_back("q", Str{pb64});
            r.emplace_back("qos", Int::of(qos)); r.emplace_back("t", Str{t->topic});
            p->sp->write_fields(SVC_RET, t->topic, HWM, FO_RET, std::move(r));
        }
        semcodec::Fields f; f.reserve(13);
        f.emplace_back("b", Str{m->writer}); f.emplace_back("e", Int::of(0)); f.emplace_back("n", Int::of(int64_t(n))); f.emplace_back("o", Int::of(0));
        f.emplace_back("p", Str{std::move(ptext)}); f.emplace_back("pr", Str{std::string()}); f.emplace_back("prq", Str{std::string()});
        f.emplace_back("q", Str{std::move(pb64)}); f.emplace_back("qos", Int::of(qos)); f.emplace_back("r", Int::of(retain ? 1 : 0));
        f.emplace_back("si", Str{m->id}); f.emplace_back("sp", Int::of(0)); f.emplace_back("t", Str{t->topic});
        p->sp->write_fields(SVC, t->topic, ribbit::Space::slot(m->writer, n, RING), FO_MSG, std::move(f));
    } catch (const std::exception& x) { log_to(m, MOSQ_LOG_ERR, std::string("the memory: ") + x.what()); return MOSQ_ERR_NO_CONN; }
    give_ack(m, id);                                  // written: that is the publish done -- nothing comes back from anyone
    return MOSQ_ERR_SUCCESS;
}
// the retained messages a filter matches, now: per topic, the newest of its writers' retained cells, if it is live
// the retained messages a filter matches, looked up by address: a topic filter's topic, or each topic the directory
// names that a wildcard filter matches -- (mqtt.retained, <topic>, hwm)
void give_retained(Proc* p, mosquitto* m, const std::string& filter, int qos) {
    std::vector<std::string> topics;
    if (!wildcard(filter)) topics.push_back(filter);
    else for (auto& r : p->sp->read(SVC_TOPICS, ALL)) {
        const std::string t = topic_of(r.instance); bool hit = false;
        if (mosquitto_topic_matches_sub(filter.c_str(), t.c_str(), &hit) == MOSQ_ERR_SUCCESS && hit &&
            std::find(topics.begin(), topics.end(), t) == topics.end()) topics.push_back(t);
    }
    for (auto& topic : topics) {
        auto got = p->sp->read(SVC_RET, topic, HWM); if (got.empty()) continue;
        ribbit::BagArrP gp = got.front().fields(); if (!gp) continue;
        const ribbit::BagArr& g = *gp;
        if (g.num("live") == 0) continue;
        const std::string& q = sval(g, "q"); std::string payload = q.empty() ? sval(g, "p") : unb64(q);
        mosquitto_message msg{}; msg.topic = const_cast<char*>(topic.c_str()); msg.payload = payload.empty() ? nullptr : &payload[0];
        msg.payloadlen = int(payload.size()); msg.qos = std::min(qos, int(g.num("qos"))); msg.retain = true;
        if (m->on_message_v5) m->on_message_v5(m, m->obj, &msg, nullptr); else if (m->on_message) m->on_message(m, m->obj, &msg);
    }
}
int do_subscribe(mosquitto* m, int* mid, int count, const char* const* subs, int qos, int options) {
    if (!m || count <= 0 || qos < 0 || qos > 2) return MOSQ_ERR_INVAL;
    for (int k = 0; k < count; ++k) if (!subs[k] || mosquitto_sub_topic_check(subs[k]) != MOSQ_ERR_SUCCESS) return MOSQ_ERR_INVAL;
    if (!m->connected.load(std::memory_order_acquire)) return MOSQ_ERR_NO_CONN;
    Proc* p = proc(); if (!usable(p)) return MOSQ_ERR_NO_CONN;
    const int id = m->mid.fetch_add(1, std::memory_order_relaxed) % 65535 + 1; if (mid) *mid = id;
    auto* ns = new Subs; const Subs* old = m->subs.load(std::memory_order_acquire);
    if (old) for (auto& s : old->v) { bool again = false; for (int k = 0; k < count; ++k) if (s.filter == subs[k]) again = true; if (!again) ns->v.push_back(s); }
    for (int k = 0; k < count; ++k) ns->v.push_back(Sub{subs[k], qos, options});
    ns->prev = old; m->subs.store(ns, std::memory_order_release);
    if (int e = reg(p, m)) return e;
    std::vector<int> granted(size_t(count), qos);
    try {
        for (int k = 0; k < count; ++k) hold(p, m, subs[k]);   // a read held on its family, from now on
        if (!(options & MQTT_SUB_OPT_SEND_RETAIN_NEVER)) for (int k = 0; k < count; ++k) give_retained(p, m, subs[k], qos);
    } catch (const std::exception& x) { log_to(m, MOSQ_LOG_ERR, std::string("the memory: ") + x.what()); return MOSQ_ERR_NO_CONN; }
    if (m->on_subscribe_v5) m->on_subscribe_v5(m, m->obj, id, count, granted.data(), nullptr);
    else if (m->on_subscribe) m->on_subscribe(m, m->obj, id, count, granted.data());
    return MOSQ_ERR_SUCCESS;
}
// an unsubscribe changes what this instance is given; the process's held read stays (others may share it)
int do_unsubscribe(mosquitto* m, int* mid, int count, const char* const* subs) {
    if (!m || count <= 0) return MOSQ_ERR_INVAL;
    if (!m->connected.load(std::memory_order_acquire)) return MOSQ_ERR_NO_CONN;
    const int id = m->mid.fetch_add(1, std::memory_order_relaxed) % 65535 + 1; if (mid) *mid = id;
    auto* ns = new Subs; const Subs* old = m->subs.load(std::memory_order_acquire);
    if (old) for (auto& s : old->v) { bool gone = false; for (int k = 0; k < count; ++k) if (s.filter == subs[k]) gone = true; if (!gone) ns->v.push_back(s); }
    ns->prev = old; m->subs.store(ns, std::memory_order_release);
    std::vector<int> rc(size_t(count), 0);
    if (m->on_unsubscribe2_v5) m->on_unsubscribe2_v5(m, m->obj, id, count, rc.data(), nullptr);
    else if (m->on_unsubscribe_v5) m->on_unsubscribe_v5(m, m->obj, id, nullptr);
    else if (m->on_unsubscribe) m->on_unsubscribe(m, m->obj, id);
    return MOSQ_ERR_SUCCESS;
}
// connect and disconnect write nothing: no one reads "this client is here". Connecting is having the memory.
int do_connect(mosquitto* m) {
    if (!m) return MOSQ_ERR_INVAL;
    Proc* p = proc(); if (!usable(p)) return MOSQ_ERR_NO_CONN;
    if (m->on_pre_connect) m->on_pre_connect(m, m->obj);
    m->connected.store(true, std::memory_order_release);
    m->connect_pending.store(true, std::memory_order_release); poke(m);
    return MOSQ_ERR_SUCCESS;
}
void flush(Proc* p) { if (usable(p)) p->sp->flush(); }   // a memory elsewhere: this process's writes have left it
int do_disconnect(mosquitto* m) {
    if (!m) return MOSQ_ERR_INVAL;
    if (!m->connected.exchange(false)) return MOSQ_ERR_NO_CONN;
    flush(P);
    m->disconnect_pending.store(true, std::memory_order_release); poke(m);
    return MOSQ_ERR_SUCCESS;
}
void init(mosquitto* m, const char* id, bool clean, void* obj) {
    char hn[64] = ""; gethostname(hn, sizeof hn - 1);
    const uint64_t k = g_instances.fetch_add(1) + 1;
    m->writer = std::string(hn) + "." + std::to_string(getpid()) + "." + std::to_string(k);
    m->id = id ? id : ("auto-" + m->writer); m->clean = clean; m->obj = obj ? obj : m;
}
}  // namespace

extern "C" {
int mosquitto_lib_version(int* major, int* minor, int* revision) {
    if (major) { *major = LIBMOSQUITTO_MAJOR; }
    if (minor) { *minor = LIBMOSQUITTO_MINOR; }
    if (revision) { *revision = LIBMOSQUITTO_REVISION; }
    return LIBMOSQUITTO_VERSION_NUMBER;
}
int mosquitto_lib_init(void) { return MOSQ_ERR_SUCCESS; }
int mosquitto_lib_cleanup(void) { flush(P); return MOSQ_ERR_SUCCESS; }

struct mosquitto* mosquitto_new(const char* id, bool clean_session, void* obj) {
    if (!id && !clean_session) { errno = EINVAL; return nullptr; }
    auto* m = new mosquitto; init(m, id, clean_session, obj); m->efd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    return m;
}
void mosquitto_destroy(struct mosquitto* m) {
    if (!m) return;
    if (m->connected.load() && m->will_set) do_publish(m, nullptr, m->will_topic.c_str(), int(m->will_payload.size()), m->will_payload.data(), m->will_qos, m->will_retain);
    m->dead.store(true, std::memory_order_release); m->connected.store(false);
    flush(P);
    m->loop_stop.store(true); poke(m); if (m->loop_thread.joinable()) m->loop_thread.join();
    if (m->efd >= 0) close(m->efd);
    // the instance stays allocated: the trigger's thread may still hold it in the instance list (it skips a dead one)
}
int mosquitto_reinitialise(struct mosquitto* m, const char* id, bool clean_session, void* obj) {
    if (!m || (!id && !clean_session)) return MOSQ_ERR_INVAL;
    m->subs.store(nullptr); init(m, id, clean_session, obj); return MOSQ_ERR_SUCCESS;
}
int mosquitto_username_pw_set(struct mosquitto* m, const char*, const char*) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }   // the fabric authenticates

void mosquitto_connect_callback_set(struct mosquitto* m, LIBMOSQ_CB_connect cb) { if (m) m->on_connect = cb; }
void mosquitto_connect_with_flags_callback_set(struct mosquitto* m, LIBMOSQ_CB_connect_with_flags cb) { if (m) m->on_connect_flags = cb; }
void mosquitto_connect_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_connect_v5 cb) { if (m) m->on_connect_v5 = cb; }
void mosquitto_pre_connect_callback_set(struct mosquitto* m, LIBMOSQ_CB_pre_connect cb) { if (m) m->on_pre_connect = cb; }
void mosquitto_disconnect_callback_set(struct mosquitto* m, LIBMOSQ_CB_disconnect cb) { if (m) m->on_disconnect = cb; }
void mosquitto_disconnect_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_disconnect_v5 cb) { if (m) m->on_disconnect_v5 = cb; }
void mosquitto_publish_callback_set(struct mosquitto* m, LIBMOSQ_CB_publish cb) { if (m) m->on_publish = cb; }
void mosquitto_publish_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_publish_v5 cb) { if (m) m->on_publish_v5 = cb; }
void mosquitto_message_callback_set(struct mosquitto* m, LIBMOSQ_CB_message cb) { if (m) m->on_message = cb; }
void mosquitto_message_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_message_v5 cb) { if (m) m->on_message_v5 = cb; }
void mosquitto_subscribe_callback_set(struct mosquitto* m, LIBMOSQ_CB_subscribe cb) { if (m) m->on_subscribe = cb; }
void mosquitto_subscribe_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_subscribe_v5 cb) { if (m) m->on_subscribe_v5 = cb; }
void mosquitto_unsubscribe_callback_set(struct mosquitto* m, LIBMOSQ_CB_unsubscribe cb) { if (m) m->on_unsubscribe = cb; }
void mosquitto_unsubscribe_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_unsubscribe_v5 cb) { if (m) m->on_unsubscribe_v5 = cb; }
void mosquitto_unsubscribe2_v5_callback_set(struct mosquitto* m, LIBMOSQ_CB_unsubscribe2_v5 cb) { if (m) m->on_unsubscribe2_v5 = cb; }
void mosquitto_log_callback_set(struct mosquitto* m, LIBMOSQ_CB_log cb) { if (m) m->on_log = cb; }
void mosquitto_ext_auth_callback_set(struct mosquitto*, LIBMOSQ_CB_ext_auth) {}
int mosquitto_ext_auth_continue(struct mosquitto*, const char*, uint16_t, const void*, const mosquitto_property*) { return MOSQ_ERR_NOT_SUPPORTED; }

int mosquitto_connect(struct mosquitto* m, const char*, int, int) { return do_connect(m); }
int mosquitto_connect_bind(struct mosquitto* m, const char*, int, int, const char*) { return do_connect(m); }
int mosquitto_connect_bind_v5(struct mosquitto* m, const char*, int, int, const char*, const mosquitto_property*) { return do_connect(m); }
int mosquitto_connect_async(struct mosquitto* m, const char*, int, int) { return do_connect(m); }
int mosquitto_connect_bind_async(struct mosquitto* m, const char*, int, int, const char*) { return do_connect(m); }
int mosquitto_connect_srv(struct mosquitto*, const char*, int, const char*) { return MOSQ_ERR_NOT_SUPPORTED; }
int mosquitto_reconnect(struct mosquitto* m) { return do_connect(m); }
int mosquitto_reconnect_async(struct mosquitto* m) { return do_connect(m); }
int mosquitto_disconnect(struct mosquitto* m) { return do_disconnect(m); }
int mosquitto_disconnect_v5(struct mosquitto* m, int, const mosquitto_property*) { return do_disconnect(m); }

int mosquitto_publish(struct mosquitto* m, int* mid, const char* topic, int len, const void* payload, int qos, bool retain) { return do_publish(m, mid, topic, len, payload, qos, retain); }
int mosquitto_publish_v5(struct mosquitto* m, int* mid, const char* topic, int len, const void* payload, int qos, bool retain, const mosquitto_property*) { return do_publish(m, mid, topic, len, payload, qos, retain); }
int mosquitto_subscribe(struct mosquitto* m, int* mid, const char* sub, int qos) { const char* s[1] = {sub}; return do_subscribe(m, mid, 1, s, qos, 0); }
int mosquitto_subscribe_v5(struct mosquitto* m, int* mid, const char* sub, int qos, int options, const mosquitto_property*) { const char* s[1] = {sub}; return do_subscribe(m, mid, 1, s, qos, options); }
int mosquitto_subscribe_multiple(struct mosquitto* m, int* mid, int count, char* const* const sub, int qos, int options, const mosquitto_property*) { return do_subscribe(m, mid, count, sub, qos, options); }
int mosquitto_unsubscribe(struct mosquitto* m, int* mid, const char* sub) { const char* s[1] = {sub}; return do_unsubscribe(m, mid, 1, s); }
int mosquitto_unsubscribe_v5(struct mosquitto* m, int* mid, const char* sub, const mosquitto_property*) { const char* s[1] = {sub}; return do_unsubscribe(m, mid, 1, s); }
int mosquitto_unsubscribe_multiple(struct mosquitto* m, int* mid, int count, char* const* const sub, const mosquitto_property*) { return do_unsubscribe(m, mid, count, sub); }

int mosquitto_will_set(struct mosquitto* m, const char* topic, int len, const void* payload, int qos, bool retain) {
    if (!m || !topic || qos < 0 || qos > 2 || len < 0 || mosquitto_pub_topic_check(topic) != MOSQ_ERR_SUCCESS) return MOSQ_ERR_INVAL;
    m->will_topic = topic; m->will_payload.assign(static_cast<const char*>(payload ? payload : ""), size_t(len)); m->will_qos = qos; m->will_retain = retain; m->will_set = true;
    return MOSQ_ERR_SUCCESS;
}
int mosquitto_will_set_v5(struct mosquitto* m, const char* topic, int len, const void* payload, int qos, bool retain, mosquitto_property*) { return mosquitto_will_set(m, topic, len, payload, qos, retain); }
int mosquitto_will_clear(struct mosquitto* m) { if (!m) return MOSQ_ERR_INVAL; m->will_set = false; return MOSQ_ERR_SUCCESS; }

// ---- the loop: nothing to read or write -- the triggers deliver; the loop gives the connect and disconnect callbacks
int mosquitto_loop(struct mosquitto* m, int timeout, int) {
    if (!m) return MOSQ_ERR_INVAL;
    run_pending(m);
    if (!m->connected.load()) return MOSQ_ERR_NO_CONN;
    if (timeout > 0) std::this_thread::sleep_for(std::chrono::milliseconds(timeout));   // as stock's select would: nothing arrives here
    run_pending(m);
    return m->connected.load() ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NO_CONN;
}
int mosquitto_loop_forever(struct mosquitto* m, int, int) {
    if (!m) return MOSQ_ERR_INVAL;
    for (;;) {
        const uint32_t seen = m->state.load(std::memory_order_acquire);
        run_pending(m);
        if (!m->connected.load() || m->loop_stop.load()) return MOSQ_ERR_SUCCESS;
        m->state.wait(seen, std::memory_order_acquire);
    }
}
int mosquitto_loop_start(struct mosquitto* m) {
    if (!m || m->loop_thread.joinable()) return MOSQ_ERR_INVAL;
    m->loop_stop.store(false);
    m->loop_thread = std::thread([m] {
        for (;;) { const uint32_t seen = m->state.load(std::memory_order_acquire); run_pending(m);
                   if (m->loop_stop.load()) { return; }
                   m->state.wait(seen, std::memory_order_acquire); } });
    return MOSQ_ERR_SUCCESS;
}
int mosquitto_loop_stop(struct mosquitto* m, bool) {
    if (!m || !m->loop_thread.joinable()) return MOSQ_ERR_INVAL;
    m->loop_stop.store(true); poke(m); m->loop_thread.join(); return MOSQ_ERR_SUCCESS;
}
int mosquitto_loop_read(struct mosquitto* m, int) { if (!m) return MOSQ_ERR_INVAL; run_pending(m); return m->connected.load() ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NO_CONN; }
int mosquitto_loop_write(struct mosquitto* m, int) { if (!m) return MOSQ_ERR_INVAL; return m->connected.load() ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NO_CONN; }
int mosquitto_loop_misc(struct mosquitto* m) { if (!m) return MOSQ_ERR_INVAL; run_pending(m); return m->connected.load() ? MOSQ_ERR_SUCCESS : MOSQ_ERR_NO_CONN; }
int mosquitto_socket(struct mosquitto* m) { return m ? m->efd : -1; }
bool mosquitto_want_write(struct mosquitto*) { return false; }
int mosquitto_threaded_set(struct mosquitto* m, bool) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }

// ---- options: what a socket and a broker would need has no meaning here, and is accepted
int mosquitto_opts_set(struct mosquitto* m, enum mosq_opt_t option, void* value) {
    if (!m) return MOSQ_ERR_INVAL;
    if (option == MOSQ_OPT_PROTOCOL_VERSION && value) m->protocol = *static_cast<int*>(value);
    return MOSQ_ERR_SUCCESS;
}
int mosquitto_int_option(struct mosquitto* m, enum mosq_opt_t option, int value) {
    if (!m) return MOSQ_ERR_INVAL;
    if (option == MOSQ_OPT_PROTOCOL_VERSION) m->protocol = value;
    return MOSQ_ERR_SUCCESS;
}
int mosquitto_string_option(struct mosquitto* m, enum mosq_opt_t, const char*) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
int mosquitto_void_option(struct mosquitto* m, enum mosq_opt_t, void*) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
int mosquitto_reconnect_delay_set(struct mosquitto* m, unsigned int, unsigned int, bool) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
int mosquitto_max_inflight_messages_set(struct mosquitto* m, unsigned int) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
void mosquitto_message_retry_set(struct mosquitto*, unsigned int) {}
void mosquitto_user_data_set(struct mosquitto* m, void* obj) { if (m) m->obj = obj; }
void* mosquitto_userdata(struct mosquitto* m) { return m ? m->obj : nullptr; }
int mosquitto_tls_set(struct mosquitto* m, const char*, const char*, const char*, const char*, int (*)(char*, int, int, void*)) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }   // the fabric's transport
int mosquitto_tls_insecure_set(struct mosquitto* m, bool) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
int mosquitto_tls_opts_set(struct mosquitto* m, int, const char*, const char*) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
int mosquitto_tls_psk_set(struct mosquitto* m, const char*, const char*, const char*) { return m ? MOSQ_ERR_SUCCESS : MOSQ_ERR_INVAL; }
void* mosquitto_ssl_get(struct mosquitto*) { return nullptr; }
int mosquitto_socks5_set(struct mosquitto*, const char*, int, const char*, const char*) { return MOSQ_ERR_NOT_SUPPORTED; }

// ---- messages
int mosquitto_message_copy(struct mosquitto_message* dst, const struct mosquitto_message* src) {
    if (!dst || !src) return MOSQ_ERR_INVAL;
    dst->mid = src->mid; dst->topic = src->topic ? strdup(src->topic) : nullptr; dst->qos = src->qos; dst->retain = src->retain;
    dst->payloadlen = src->payloadlen;
    if (src->payloadlen) { dst->payload = malloc(size_t(src->payloadlen) + 1); if (!dst->payload) return MOSQ_ERR_NOMEM;
                           memcpy(dst->payload, src->payload, size_t(src->payloadlen)); static_cast<char*>(dst->payload)[src->payloadlen] = 0; }
    else dst->payload = nullptr;
    return MOSQ_ERR_SUCCESS;
}
void mosquitto_message_free_contents(struct mosquitto_message* msg) { if (!msg) return; free(msg->topic); free(msg->payload); msg->topic = nullptr; msg->payload = nullptr; }
void mosquitto_message_free(struct mosquitto_message** msg) { if (!msg || !*msg) return; mosquitto_message_free_contents(*msg); free(*msg); *msg = nullptr; }
}  // extern "C"
