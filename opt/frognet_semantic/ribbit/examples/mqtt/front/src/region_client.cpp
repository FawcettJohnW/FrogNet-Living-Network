// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// region_client.cpp -- see region_client.h.
//
// [MEMORY_IS_MEMORY_V1] (2026-10-17; docs/CONVERTING-TO-RIBBIT.md) The MQTT state is memory. On a box, every front
// thread shares the box's region (mqtt_region.hpp) in this process: a call is a call, a publish fans out to this box's
// clients there and then, and the client is answered at once. Between boxes there is no server and no message: each
// box writes its own truths to its own tuples in the memory (MQTT_REGION=host:port, or "own": a memory beside this
// broker), and reads the other boxes' truths there, where they are, when it needs them ([ONE_MEMORY_V1]): nothing
// another box owns is copied into this box. A box's clients' subscriptions are held reads on the topics they match;
// what lands is delivered to those clients. Nothing is acknowledged or confirmed between boxes.
//
// [SPACE_V1] [TOPIC_IS_THE_ADDRESS_V1] Producers write and consumers read (ribbit_space): the boxes and libmosquitto's
// own clients ([CLIENT_ON_THE_MEMORY_V1]) share one tuple space, addressed by topic.
//
//   tuple                                         written by (its one owner)        read by
//   (mqtt, <topic>, <box>.<n mod RING>)           the box a publish was made on      every box whose clients read the
//                                                 (API++ Publisher: _b the box, _n   topic: into its own clients
//                                                 its count on the topic)            (API++ subscribe)
//   (mqtt.topics, all, <topic>␟<box>)             a box, the first time it uses one  boxes with wildcard subscribers
//                                                 (API++ announce)                   (API++ capable)
//   (mqtt.retained, <topic>, <box>)               the box a retained publish was     looked up when a client
//                                                 made on (API++ offer; it STAYS     subscribes: the newest of the
//                                                 when the box goes)                 boxes' cells (API++ latest)
//   (mqtt.subs, <filter>, <client>␟<box>␟<group>) the box holding the client         a box with no local subscriber
//                                                 ([SUBS_BY_FILTER_V1]: <filter>     to a topic it publishes: the
//                                                 without $share/<group>/)           filters that can match, looked up
//   (mqtt.subs.shapes, all, <shape>␟<box>)        a box, the first time it uses one  the same: which filters to look up
//   (mqtt.box, at, <client>␟<box>)                the box the client connected to    the box that held it before
//   (mqtt.box, session.<client>, <box>)           the box that gave the session up   the box that took the client over
//   (mqtt.box, sys, <box>)                        each box: its counts               every box: the broker-wide limits
// [LINGER_BY_FAMILY_V1] When a box's session ends, its cells go with it -- its publishes, its topics, its subscriptions,
// its claims -- except its retained messages, which stay (the box says so: Space::linger(mqtt.retained, STAYS)).
// Every cell is its owner's alone: the box is part of the address of every truth it writes, and nothing decides
// between writers. Nothing paces a box and nothing comes back to it. A box that reads too slowly finds a writer's
// count on a topic jumping (its ring moved past what was not read yet): it counts what it missed and says so.
// Everything a front keeps is per thread (thread_local): each front thread is its own front.
#include "region_client.h"
#include <sys/eventfd.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <map>
#include <functional>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <random>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#include "mqtt_region.hpp"
#include "sub_address.hpp"
#include "ribbit_mqtt_version.h"                         // made by tools/build.sh from VERSION
#include "frogram.hpp"
#include "ribbit_space.hpp"
#include "ribbit_pp.hpp"                                  // [API_PLUS_PLUS_V2] publish/subscribe, the directory, retained
extern "C" int mosquitto_topic_matches_sub(const char* sub, const char* topic, bool* result);   // libcommon
#include "ram_host.hpp"

using frogram::Json;

namespace {
// ---- the box's region: one per process, made by the first front thread to open it --------------------------------
std::atomic<mqttr::Region*> RESIDENT{nullptr};
mqttr::Region* resident() {
    mqttr::Region* r = RESIDENT.load(std::memory_order_acquire);
    if (r) return r;
    auto* n = new mqttr::Region();
    if (!RESIDENT.compare_exchange_strong(r, n)) { delete n; return r; }
    return n;
}

// ---- per front (per thread) ---------------------------------------------------------------------------------------
// [ASYNC_PUBACK_V1] answers to "any subscriber on another box?", pushed by the pool's threads, taken by the front's own
// thread: an intrusive MPSC list (lock-free: one exchange to push; only the front pops)
struct Answer { std::atomic<Answer*> next{nullptr}; std::string client; uint64_t token = 0, seq = 0; bool has = false; };
struct AnswerList {
    Answer stub; std::atomic<Answer*> head{&stub}; Answer* tail = &stub;
    void push(Answer* n) { n->next.store(nullptr, std::memory_order_relaxed); Answer* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
    Answer* pop() {                                    // the front only
        Answer* t = tail; Answer* next = t->next.load(std::memory_order_acquire);
        if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { tail = next; return t; }
        if (t != head.load(std::memory_order_acquire)) return nullptr;
        stub.next.store(nullptr, std::memory_order_relaxed); Answer* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
        next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
        return nullptr;
    }
};
// [FIELDS_NOT_TEXT_V1] a publish as the fields of its bag, in its shape's order; n (index 2) is set when it is numbered
struct PendMsg { semcodec::Fields f; bool su = false; size_t bytes = 0; };
struct Front {
    mqttr::Region* r = nullptr;
    std::deque<mqttr::Delivery*> held;     // [HOLD_FOR_ROOM_V1] taken, not yet delivered: the client had no room
    uint64_t x_held = 0, x_forced = 0;
    int front = -1, efd = -1;
    std::string err, stats;
    int dropped = 0;                       // [QOS0_BACKLOG_V1] QoS 0 deliveries the last publish dropped
    bool last_bp = false;
    std::string claim_key;                 // this front's last claim of a client: the session written for it names it
    std::vector<rgn_msg> ret_m; std::vector<mqttr::Msg*> ret_refs;
    // this pass's publishes, each bag without its number: numbered and written before the loop sleeps -- before
    // this pass's PUBACKs leave (out__flush)
    std::vector<PendMsg> pend; size_t pend_bytes = 0;
    std::unordered_map<std::string, int> held_for;      // [ONE_LINE_PER_SUBSCRIBER_V1] deliveries from other fronts held, by client
    AnswerList answers;                                  // [ASYNC_PUBACK_V1]
};
thread_local Front* F = nullptr;
Front& me() { if (!F) F = new Front(); return *F; }
int fail(const std::exception& x) { me().err = x.what(); return 1; }

const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
std::string b64(const void* data, size_t n) {
    const unsigned char* in = static_cast<const unsigned char*>(data);
    std::string out; out.reserve((n + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < n; i += 3) {
        uint32_t v = (in[i] << 16) | (in[i + 1] << 8) | in[i + 2];
        out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += B64[v & 63];
    }
    if (i + 1 == n) { uint32_t v = in[i] << 16; out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += "=="; }
    else if (i + 2 == n) { uint32_t v = (in[i] << 16) | (in[i + 1] << 8); out += B64[v >> 18]; out += B64[(v >> 12) & 63]; out += B64[(v >> 6) & 63]; out += '='; }
    return out;
}
int8_t B64T[256];
std::once_flag B64T_once;
std::string unb64(const std::string& in) {
    std::call_once(B64T_once, [] { memset(B64T, -1, sizeof B64T); for (int i = 0; i < 64; i++) B64T[uint8_t(B64[i])] = int8_t(i); });
    std::string out; uint32_t v = 0; int bits = 0;
    for (unsigned char ch : in) { if (ch == '=') break; int8_t d = B64T[ch]; if (d < 0) break; v = (v << 6) | uint32_t(d); bits += 6; if (bits >= 8) { bits -= 8; out += char((v >> bits) & 0xFF); } }
    return out;
}
std::string q(const std::string& s) { return Json::quote(s); }
int64_t num(const Json& j, const char* k) { const Json& v = j[k]; return v.type == Json::Num ? int64_t(v.n) : 0; }

// a region message seen through the C view: it points into the message itself (valid while a reference is held)
rgn_msg view(const mqttr::Msg* x) {
    rgn_msg m{};
    m.id = x->id; m.topic = x->topic.c_str(); m.payload = x->payload.data(); m.payloadlen = uint32_t(x->payload.size());
    m.props = x->props.data(); m.propslen = uint32_t(x->props.size()); m.qos = x->qos; m.retain = x->retain; m.origin = x->origin;
    m.expiry_ms = x->expiry_ms; m.src_id = x->src_id.c_str(); m.src_user = x->has_user ? x->src_user.c_str() : nullptr; m.src_port = x->src_port;
    return m;
}
mqttr::Msg* make(mqttr::Region* r, const rgn_msg* m) {
    auto* x = new mqttr::Msg();
    x->id = r->next_id(); x->topic = m->topic;
    x->payload.assign(static_cast<const char*>(m->payload), m->payloadlen);
    x->props.assign(static_cast<const char*>(m->props), m->propslen);
    x->qos = m->qos; x->retain = m->retain; x->origin = m->origin; x->expiry_ms = m->expiry_ms;
    x->src_id = m->src_id ? m->src_id : ""; x->src_port = m->src_port;
    if (m->src_user) { x->has_user = true; x->src_user = m->src_user; }
    return x;
}
const int64_t RHOLD_MS = [] { const char* v = getenv("RIBBIT_HOLD_MAX_MS"); long n = v ? atol(v) : 0; return int64_t(n > 0 ? n : 1000); }();
int64_t mono_ms() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return int64_t(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000; }
// [DIAG-SUBLOOKUP] per lookup: reads made, rows seen, time the calling front thread spent in them
std::atomic<uint64_t> SL_calls{0}, SL_reads{0}, SL_rows{0}, SL_us{0}, SL_max_us{0}, SL_hits{0};
std::atomic<uint64_t> SL_asked{0}, SL_answered{0}, SL_failed{0};   // [ASYNC_PUBACK_V1]
int64_t mono_us() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return int64_t(ts.tv_sec) * 1000000 + ts.tv_nsec / 1000; }

// ---- [MEMORY_IS_MEMORY_V1] the box in the tuple space ------------------------------------------------------------
// One per process. Its fronts write through it (each write encoded by the thread that makes it and handed to the
// session's sender thread, which owns the socket);
// each read it holds runs on a thread of its own ([READERS_IN_PARALLEL_V1]). A client of another box is marked in this
// box's region as held by REMOTE (< 0: no front of this box delivers to it).
constexpr int REMOTE = -2;                 // a client another box holds
constexpr int FROM_ELSEWHERE = -3;         // the publishing "front" of a publish another box made
constexpr int REMOTE_FRONT = mqttr::Region::MAX_FRONTS - 1;   // what rgn_claim says when the previous holder is another box
const char* const SVC = "mqtt";
const char* const SVC_RET = "mqtt.retained";
const char* const SVC_TOPICS = "mqtt.topics";
const char* const SVC_BOX = "mqtt.box";
const char* const SVC_SUBS = "mqtt.subs";              // [SUBS_BY_FILTER_V1]
const char* const SVC_SHAPES = "mqtt.subs.shapes";
// a writer's cells on one topic: its publish n there is cell <writer>.<n mod RING>. A reader further behind than this
// loses what was overwritten, and says so ([LOST_IS_SAID_V1]).
constexpr uint64_t RING = 16384;
struct Box {
    std::unique_ptr<ribbit::Space> sp;
    std::unique_ptr<ribbit::pp::Publisher> pub;   // [API_PLUS_PLUS_V2] its publishes: its ring per topic, its count there
    std::string id;                        // this box's name in the tuple space
    // the other boxes' counts ([FRONT_COUNTS_V1] across boxes), as their sys tuples last said
    std::atomic<int64_t> last_c{-1}, last_n{-1};
    std::atomic<uint64_t> published{0}, applied_msgs{0}, lost{0};
    // the newest claim another box made of each client (its key), as the at cells say: set by the reader, read by the
    // front that gives the session up -- insert-only; a key is replaced whole, never edited
    struct Claim { std::string client; Claim* next = nullptr; std::atomic<const std::string*> key{nullptr}; };
    std::atomic<Claim*> claims[4096] = {};
    Claim* claim(const std::string& client) {
        auto& slot = claims[std::hash<std::string>()(client) % 4096];
        for (;;) { Claim* h = slot.load(std::memory_order_acquire);
            for (Claim* x = h; x; x = x->next) if (x->client == client) return x;
            auto* f = new Claim; f->client = client; f->next = h;
            if (slot.compare_exchange_strong(h, f, std::memory_order_acq_rel)) return f;
            delete f; }
    }
};
std::atomic<Box*> BOX{nullptr};
std::once_flag BOX_once;
std::string BOX_err;

// [BAGS_ARE_JSON_V1] A publish is a regular JSON bag: the payload and the properties are JSON strings, as they are,
// whenever they are valid UTF-8 -- so the semantic wire's templates and SAME/DIFF see them. Bytes that are not UTF-8
// cannot be a JSON string; only then is that field base64, under its own key ("q" for the payload, "prq" for the
// properties), as the Redis intents do ([BINARY_SAFE_INTENTS_V1]).
bool utf8_ok(const unsigned char* p, size_t n) {
    size_t i = 0;
    while (i < n) {
        const unsigned char c = p[i];
        if (c < 0x80) { ++i; continue; }
        size_t k; uint32_t cp;
        if ((c & 0xE0) == 0xC0) { k = 1; cp = c & 0x1F; } else if ((c & 0xF0) == 0xE0) { k = 2; cp = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { k = 3; cp = c & 0x07; } else return false;
        if (i + k >= n) return false;                     // cut short
        for (size_t j = 1; j <= k; ++j) { if ((p[i + j] & 0xC0) != 0x80) return false; cp = cp << 6 | (p[i + j] & 0x3F); }
        if ((k == 1 && cp < 0x80) || (k == 2 && cp < 0x800) || (k == 3 && cp < 0x10000) || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += k + 1;
    }
    return true;
}
void b64_into(std::string& out, const void* data, size_t n) {
    const unsigned char* in = static_cast<const unsigned char*>(data);
    size_t i = 0;
    for (; i + 2 < n; i += 3) {
        uint32_t v = (in[i] << 16) | (in[i + 1] << 8) | in[i + 2];
        const char c[4] = {B64[v >> 18], B64[(v >> 12) & 63], B64[(v >> 6) & 63], B64[v & 63]}; out.append(c, 4);
    }
    if (i + 1 == n) { uint32_t v = in[i] << 16; const char c[4] = {B64[v >> 18], B64[(v >> 12) & 63], '=', '='}; out.append(c, 4); }
    else if (i + 2 == n) { uint32_t v = (in[i] << 16) | (in[i + 1] << 8); const char c[4] = {B64[v >> 18], B64[(v >> 12) & 63], B64[(v >> 6) & 63], '='}; out.append(c, 4); }
}
// [CANONICAL_BODY_V1] strings in the wire's own form (json.dumps, ensure_ascii), keys in sorted order: the engine proves
// the body by comparing bytes with its rebuild instead of parsing both (measured: 17% of the box's CPU was that parse)
void quote_into(std::string& out, const char* s, size_t n) { Json::quote_ascii_into(out, std::string_view(s, n)); }
void quote_into(std::string& out, const char* s) { quote_into(out, s, strlen(s)); }
// [FIELDS_NOT_TEXT_V1] a publish's bag as fields, keys sorted: b e [live] n o p pr prq q qos r si sp [su] t. The box
// has every value already, so nothing is written as text to be parsed again; a retained cell's "live" goes in at 2. The two shapes (with and without the user name) each keep one order vector for the life
// of the process -- the encoder recognises a shape by it.
const std::vector<std::string> FO_MSG = {"e", "o", "p", "pr", "prq", "q", "qos", "r", "si", "sp", "t"};
const std::vector<std::string> FO_MSG_SU = {"e", "o", "p", "pr", "prq", "q", "qos", "r", "si", "sp", "su", "t"};
// the box's own shapes: each always whole, so each write of one is a difference against the last
const std::vector<std::string> FO_AT = {"b", "cs", "f", "k"};
const std::vector<std::string> FO_SUB = {"b", "live", "o", "sid"};
const std::vector<std::string> FO_SYS = {"c", "l", "n"};
const std::vector<std::string> FO_SHAPE = {"s"};
const std::vector<std::string> FO_SESSION = {"b", "f", "k", "s"};   // f: its subscriptions, "<options>\x1f<identifier>\x1f<filter>"
const std::vector<std::string> FO_RET = {"e", "live", "o", "p", "pr", "prq", "q", "qos", "r", "si", "sp", "t"};
const std::vector<std::string> FO_RET_SU = {"e", "live", "o", "p", "pr", "prq", "q", "qos", "r", "si", "sp", "su", "t"};
PendMsg msg_fields(const rgn_msg& m) {
    using pyv::Str; using pyv::Int;
    PendMsg pm; pm.su = m.src_user != nullptr; semcodec::Fields& f = pm.f; f.reserve(14);
    const bool pu = utf8_ok(static_cast<const unsigned char*>(m.payload), m.payloadlen), ru = utf8_ok(static_cast<const unsigned char*>(m.props), m.propslen);
    auto bytes = [](const void* p, uint32_t n) { return std::string(static_cast<const char*>(p), n); };
    auto b64s = [](const void* p, uint32_t n) { std::string o; b64_into(o, p, n); return o; };
    f.emplace_back("e", Int::of(int64_t(m.expiry_ms)));
    f.emplace_back("o", Int::of(m.origin));
    f.emplace_back("p", Str{pu ? bytes(m.payload, m.payloadlen) : std::string()});
    f.emplace_back("pr", Str{ru ? bytes(m.props, m.propslen) : std::string()});
    f.emplace_back("prq", Str{ru ? std::string() : b64s(m.props, m.propslen)});
    f.emplace_back("q", Str{pu ? std::string() : b64s(m.payload, m.payloadlen)});
    f.emplace_back("qos", Int::of(m.qos)); f.emplace_back("r", Int::of(m.retain));
    f.emplace_back("si", Str{m.src_id ? std::string(m.src_id) : std::string()}); f.emplace_back("sp", Int::of(m.src_port));
    if (pm.su) f.emplace_back("su", Str{std::string(m.src_user)});
    f.emplace_back("t", Str{m.topic ? std::string(m.topic) : std::string()});
    pm.bytes = 64 + m.payloadlen + m.propslen;
    return pm;
}
// [DECODE_ONCE_V1] a tuple's values as the session decoded them, read in place: no text, no parse
const std::string& sval(const ribbit::BagArr& a, const char* k) { static const std::string none; const pyv::Obj* v = a.get(k); return v && v->is<pyv::Str>() ? v->as<pyv::Str>().s : none; }
// [BAGS_ARE_JSON_V1] a field's bytes: the JSON string, or the base64 under its other key
std::string bag_bytes(const ribbit::BagArr& g, const char* key, const char* bkey) {
    const std::string& x = sval(g, bkey); return x.empty() ? sval(g, key) : unb64(x);
}
// a message from a tuple's values, as the region keeps one
struct BagMsg { std::string topic, payload, props, src_id, src_user; mqttr::MsgIn in; };
void from_arr(const ribbit::BagArr& g, BagMsg& o) {
    o.topic = sval(g, "t"); o.payload = bag_bytes(g, "p", "q"); o.props = bag_bytes(g, "pr", "prq"); o.src_id = sval(g, "si");
    o.in = mqttr::MsgIn{};
    o.in.topic = o.topic; o.in.payload = o.payload; o.in.props = o.props; o.in.src_id = o.src_id;
    const pyv::Obj* su = g.get("su");
    o.in.has_user = su && su->is<pyv::Str>(); if (o.in.has_user) { o.src_user = su->as<pyv::Str>().s; o.in.src_user = o.src_user; }
    o.in.qos = uint8_t(g.num("qos")); o.in.retain = uint8_t(g.num("r")); o.in.origin = uint8_t(g.num("o"));
    o.in.expiry_ms = g.num("e"); o.in.src_port = int(g.num("sp"));
}
mqttr::Msg* msg_from_arr(mqttr::Region* r, const ribbit::BagArr& g) {
    auto* x = new mqttr::Msg();
    x->id = r->next_id(); x->topic = sval(g, "t"); x->payload = bag_bytes(g, "p", "q"); x->props = bag_bytes(g, "pr", "prq");
    x->qos = uint8_t(g.num("qos")); x->retain = 1; x->origin = uint8_t(g.num("o")); x->expiry_ms = g.num("e");
    x->src_id = sval(g, "si"); x->src_port = int(g.num("sp"));
    const pyv::Obj* su = g.get("su"); if (su && su->is<pyv::Str>()) { x->has_user = true; x->src_user = su->as<pyv::Str>().s; }
    return x;
}
// A read held on a family of the space (ribbit::Space::watch): `from` -1, the family's state first, then every row
// written after; -2, only what is written from now on. `apply(instance, values, initial)` runs on the read's own
// thread, the rows in the family's write order.
using BoxApply = std::function<void(const std::string&, const ribbit::BagArr&, bool)>;
void watch(Box* b, const std::string& variable, int64_t from, BoxApply apply) {
    auto first = std::make_shared<bool>(true);
    b->sp->watch(SVC_BOX, variable, from == -1 ? 0 : from == -2 ? -1 : from, [apply, first](std::vector<ribbit::SpaceRow>& rows) {
        const bool initial = *first; *first = false;
        for (auto& r : rows) if (!r.gone) if (ribbit::BagArrP a = r.fields()) apply(r.instance, *a, initial);   // [GONE_WITH_ITS_WRITER_V1]
    });
}
// [READERS_IN_PARALLEL_V1] FNWP's reader model: each topic this box's clients subscribe to -- a topic filter's topic,
// or each topic the directory names that a wildcard filter matches -- is a read held by a thread of its own, waiting
// for its own answer, all of them in flight on the box's one session at once. One read per topic, however many filters
// match it: what it reads goes into this box's region once, and the region gives it to the box's subscribers.
struct HeldTopic { std::string topic; HeldTopic* next; };
std::atomic<HeldTopic*> HELD{nullptr};
struct WildFilter { std::string filter; WildFilter* next; };
std::atomic<WildFilter*> WILD{nullptr};               // the box's wildcard filters (insert-only: a read too many gives nobody anything)
void hold_topic(Box* b, const std::string& topic, long long from) {
    HeldTopic* h = HELD.load(std::memory_order_acquire);
    for (;;) {
        for (HeldTopic* x = h; x; x = x->next) if (x->topic == topic) return;
        auto* n = new HeldTopic{topic, h};
        if (HELD.compare_exchange_strong(h, n, std::memory_order_acq_rel)) break;
        delete n;                                     // h reloaded: look again
    }
    mqttr::Region* r = resident();
    // [API_PLUS_PLUS_V2] the topic's publishes, every box's, in write order; a ring that moved past this read is said
    ribbit::pp::subscribe(*b->sp, SVC, topic, from, [b, r](const ribbit::pp::Delivery& d) {
        thread_local BagMsg m;
        if (d.writer == b->id) return;                   // this box's own: its clients had it when it was made
        from_arr(*d.fields, m);
        mqttr::Region::store_retained_here(false);       // its retained state is the topic's retained tuple
        r->publish(m.in, FROM_ELSEWHERE, nullptr, nullptr, nullptr);
        b->applied_msgs++;
    }, [b](const std::string& topic, const std::string& writer, uint64_t gap) {   // [LOST_IS_SAID_V1]
        const uint64_t before = b->lost.fetch_add(gap, std::memory_order_relaxed);
        if (before < 1000000 && (before == 0 || before / 100000 != (before + gap) / 100000))
            fprintf(stderr, "region: missed %llu publishes of %s on %s (read too late: its ring had moved on); %llu missed in all\n",
                    (unsigned long long)gap, writer.c_str(), topic.c_str(), (unsigned long long)(before + gap));
    }, [r] { r->flush_wakes(); });
}
bool wild(const std::string& f) { return f.find_first_of("+#") != std::string::npos || f.rfind("$share/", 0) == 0; }
std::string share_free(const std::string& f) {        // $share/<group>/<filter>: the filter
    if (f.rfind("$share/", 0) != 0) return f;
    const size_t s = f.find('/', 7); return s == std::string::npos ? f : f.substr(s + 1);
}
bool matches(const std::string& filter, const std::string& topic) {
    bool hit = false; return mosquitto_topic_matches_sub(filter.c_str(), topic.c_str(), &hit) == 0 && hit;
}
std::string topic_of(const std::string& inst) { return inst.substr(0, inst.find('\x1f')); }
// a subscription's topics: a topic filter's topic, from now on; a wildcard filter's, every topic the directory names
// that it matches -- those there now from now on, and every one that appears, all of it
void hold_filter(Box* b, const std::string& filter0) {
    const std::string filter = share_free(filter0);
    if (!wild(filter)) { hold_topic(b, filter, -1); return; }
    WildFilter* h = WILD.load(std::memory_order_acquire);
    for (;;) { for (WildFilter* x = h; x; x = x->next) if (x->filter == filter) return;   // held already
               auto* n = new WildFilter{filter, h};
               if (WILD.compare_exchange_strong(h, n, std::memory_order_acq_rel)) break;
               delete n; }
    // [API_PLUS_PLUS_V2] every topic in use that the filter matches: those in use now, from now on; one that appears
    // later, all of it (its first publish may have landed before the directory said so)
    ribbit::pp::capable(*b->sp, SVC_TOPICS, [filter](const std::string& t) { return matches(filter, t); },
                        [b](const ribbit::pp::Capable& c) { if (!c.gone) hold_topic(b, c.capability, c.already ? -1 : 0); });
}
// [ONE_MEMORY_V1] the retained messages a filter matches, looked up at their address, (mqtt.retained, <topic>, *) --
// the newest of the boxes' cells (API++ latest) -- and handed to the caller, never stored in this box
std::vector<std::string> topics_matching(Box* b, const std::string& filter) {
    std::vector<std::string> topics;
    if (!wild(filter)) { topics.push_back(filter); return topics; }
    for (auto& row : b->sp->read(SVC_TOPICS, "all")) { const std::string t = topic_of(row.instance);
        if (matches(filter, t) && std::find(topics.begin(), topics.end(), t) == topics.end()) topics.push_back(t); }
    return topics;
}
ribbit::BagArrP retained_of(Box* b, const std::string& topic) {
    ribbit::SpaceRow row; if (!ribbit::pp::latest(*b->sp, SVC_RET, topic, row)) return nullptr;   // [API_PLUS_PLUS_V2]
    ribbit::BagArrP g = row.fields();
    return g && g->num("live") ? g : nullptr;
}
void look_up_retained(Box* b, mqttr::Region* r, const std::string& filter0, std::vector<mqttr::Msg*>& out) {
    for (auto& topic : topics_matching(b, share_free(filter0)))
        if (ribbit::BagArrP g = retained_of(b, topic)) out.push_back(msg_from_arr(r, *g));
}
extern "C" void sub__pace_stats(uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*);   // subs.c [DIAG-PACE]
void claim_key_set(Box* b, const std::string& client, const std::string& key) { b->claim(client)->key.store(new std::string(key), std::memory_order_release); }   // (the old one is let go: a few bytes)
std::string claim_key_of(Box* b, const std::string& client) { const std::string* k = b->claim(client)->key.load(std::memory_order_acquire); return k ? *k : std::string(); }
// [ONE_MEMORY_V1] the box's one standing read of another box's truth: a claim on a client this box holds. Nothing
// another box owns is copied into this box -- its subscriptions, its counts, the retained messages are read where they
// are, when they are needed.
void start_triggers(Box* b) {
    mqttr::Region* r = resident();
    // a client another box connected: if this box holds it, the session goes to that box
    watch(b, "at", -1, [b, r](const std::string& inst, const ribbit::BagArr& g, bool) {
        if (sval(g, "b") == b->id) return;
        const std::string client = topic_of(inst);
        mqttr::Client* cl = r->client(client);
        const int held = cl->front.exchange(REMOTE, std::memory_order_acq_rel);
        claim_key_set(b, client, sval(g, "k"));          // the session this box gives up for it names the claim
        if (held >= 0) r->want_session(client, held, REMOTE, g.num("cs") != 0);
    });
}
Box* the_box(const char* host, int port) {
    std::call_once(BOX_once, [&] {
        try {
            auto* b = new Box();
            b->sp.reset(new ribbit::Space(std::string(host) + ":" + std::to_string(port), "mqtt"));
            char hn[64] = ""; gethostname(hn, sizeof hn - 1);
            std::random_device rd; char tag[24]; snprintf(tag, sizeof tag, "%08x", rd());
            b->id = std::string(hn) + "." + std::to_string(getpid()) + "." + tag;   // this box, for the life of the process
            b->sp->linger(SVC_RET, "", ribbit::Space::STAYS);   // [LINGER_BY_FAMILY_V1] a retained message outlives its box
            b->pub.reset(new ribbit::pp::Publisher(*b->sp, SVC, b->id, {}, RING));   // [API_PLUS_PLUS_V2]
            BOX.store(b, std::memory_order_release);
            b->sp->write_fields(SVC_BOX, "sys", b->id, FO_SYS, {{"c", pyv::Int::of(0)}, {"l", pyv::List(4, pyv::Int::of(0))}, {"n", pyv::Int::of(0)}});
            start_triggers(b);
            if (getenv("MQTT_DIAG")) std::thread([b] {       // [DIAG-QUEUE] once a second: what this box wrote and read
                uint64_t lp = 0, la = 0;
                for (;;) { std::this_thread::sleep_for(std::chrono::seconds(1));
                    const uint64_t p = b->published.load(), a = b->applied_msgs.load();
                    fprintf(stderr, "[DIAG-QUEUE] box %s: published %llu/s, read from others %llu/s, missed %llu\n", b->id.c_str(),
                            (unsigned long long)(p - lp), (unsigned long long)(a - la), (unsigned long long)b->lost.load());
                    fprintf(stderr, "[DIAG-SUBLOOKUP] box %s: lookups %llu reads %llu front_us %llu (cumulative)\n", b->id.c_str(),
                            (unsigned long long)SL_calls.load(), (unsigned long long)SL_reads.load(), (unsigned long long)SL_us.load());
                    lp = p; la = a; } }).detach();
            std::atexit([] {                            // [DIAG-WIRE] what this box's session put on the wire, said at exit
                Box* x = BOX.load(std::memory_order_acquire); if (!x || !x->sp) return;
                uint64_t on, oms, xn, xms, xf; sub__pace_stats(&on, &oms, &xn, &xms, &xf);   // [DIAG-PACE]
                fprintf(stderr, "[DIAG-PACE] box %s: publishers paused for own held %llu times %llu ms\n", x->id.c_str(), (unsigned long long)on, (unsigned long long)oms);
                fprintf(stderr, "[DIAG-SUBLOOKUP] box %s: asked=%llu answered=%llu failed=%llu\n", x->id.c_str(), (unsigned long long)SL_asked.load(),
                        (unsigned long long)SL_answered.load(), (unsigned long long)SL_failed.load());
                fprintf(stderr, "[DIAG-SUBLOOKUP] box %s: lookups=%llu hits=%llu reads=%llu rows=%llu pool_thread_us_total=%llu avg_us=%.1f max_us=%llu\n", x->id.c_str(),
                        (unsigned long long)SL_calls.load(), (unsigned long long)SL_hits.load(), (unsigned long long)SL_reads.load(), (unsigned long long)SL_rows.load(),
                        (unsigned long long)SL_us.load(), SL_calls.load() ? double(SL_us.load()) / double(SL_calls.load()) : 0.0, (unsigned long long)SL_max_us.load());
                fprintf(stderr, "[DIAG-WIRE] box %s: published=%llu read_from_others=%llu missed=%llu %s\n", x->id.c_str(),
                        (unsigned long long)x->published.load(), (unsigned long long)x->applied_msgs.load(), (unsigned long long)x->lost.load(), x->sp->stats().c_str());
            });
            fprintf(stderr, "region: box %s (MQTT on Ribbit %s) on the memory at %s:%d -- producers write, consumers read [SPACE_V1]\n", b->id.c_str(), RIBBIT_MQTT_VERSION, host, port);
        } catch (const std::exception& x) { BOX_err = x.what(); }
    });
    Box* b = BOX.load(std::memory_order_acquire);
    if (!b) throw std::runtime_error("the memory at " + std::string(host) + ":" + std::to_string(port) + " did not answer: " + BOX_err);
    return b;
}
Box* box() { return BOX.load(std::memory_order_acquire); }
// a truth of this box, written; nothing waits for it. The memory gone, this box writes nothing more: said once.
// [DECODE_ONCE_V1] a truth of this box, as values: one order vector per shape, for the life of the process
void put(const char* space, const char* variable, const std::string& instance, const std::vector<std::string>& order, semcodec::Fields f) {
    Box* b = box(); if (!b) return;
    try { b->sp->write_fields(space, variable, instance, order, std::move(f)); }
    catch (const std::exception& x) {
        static std::atomic<bool> said{false};
        if (!said.exchange(true)) fprintf(stderr, "region: the memory is gone (%s): this box's truths are no longer written\n", x.what());
    }
}
void put(const char* variable, const std::string& instance, const std::vector<std::string>& order, semcodec::Fields f) { put(SVC_BOX, variable, instance, order, std::move(f)); }
semcodec::Fields sub_fields(const std::string& box, bool live, int64_t o, int64_t sid) {
    return {{"b", pyv::Str{box}}, {"live", pyv::Int::of(live)}, {"o", pyv::Int::of(o)}, {"sid", pyv::Int::of(sid)}};
}
// [SUBS_BY_FILTER_V1] a subscription of this box's, at its filter: (mqtt.subs, <filter>, <client>␟<box>␟<group>).
// The filter's shape goes in the manifest the first time this box uses it (insert-only: found by CAS, no lock).
struct ShapeSeen { std::string shape; ShapeSeen* next; };
std::atomic<ShapeSeen*> SHAPES{nullptr};
void put_sub(const std::string& client, const std::string& filter0, bool live, int64_t o, int64_t sid) {
    Box* b = box(); if (!b) return;
    std::string group, filter; subaddr::split_share(filter0, group, filter);
    const std::string shape = subaddr::shape_of(filter);
    for (ShapeSeen* h = SHAPES.load(std::memory_order_acquire);;) {
        bool have = false; for (ShapeSeen* x = h; x; x = x->next) if (x->shape == shape) { have = true; break; }
        if (have) break;
        auto* n = new ShapeSeen{shape, h};
        if (SHAPES.compare_exchange_strong(h, n, std::memory_order_acq_rel)) {
            put(SVC_SHAPES, "all", shape + '\x1f' + b->id, FO_SHAPE, {{"s", pyv::Str{shape}}});   // written before the sub below
            break;
        }
        delete n;                                         // h reloaded: look again
    }
    put(SVC_SUBS, filter.c_str(), client + '\x1f' + b->id + '\x1f' + group, FO_SUB, sub_fields(b->id, live, o, sid));
}
// [SUBS_BY_FILTER_V1] MQTT 5's "no matching subscribers", asked of the one memory: for each shape in use, the one filter
// of that shape that matches the topic, read at its address. Nothing is scanned: the reads are the shapes in use.
bool subscriber_elsewhere(Box* b, const std::string& topic) {
    const int64_t t0 = mono_us(); uint64_t reads = 0, rows = 0; bool hit = false;
    const std::vector<std::string_view> tl = subaddr::levels(topic);
    std::vector<std::string> seen; std::string f;
    ++reads; auto shapes = b->sp->read(SVC_SHAPES, "all"); rows += shapes.size();
    for (auto& row : shapes) {
        const std::string shape = row.instance.substr(0, row.instance.find('\x1f'));
        if (std::find(seen.begin(), seen.end(), shape) != seen.end()) continue;
        seen.push_back(shape);
        if (!subaddr::filter_for(tl, shape, f)) continue;
        ++reads; auto subs = b->sp->read(SVC_SUBS, f); rows += subs.size();
        for (auto& s : subs) {
            ribbit::BagArrP g = s.fields();
            if (g && g->num("live") && sval(*g, "b") != b->id) { hit = true; break; }
        }
        if (hit) break;
    }
    const uint64_t us = uint64_t(mono_us() - t0);
    SL_calls++; SL_reads += reads; SL_rows += rows; SL_us += us; SL_hits += hit;
    for (uint64_t m = SL_max_us.load(); us > m && !SL_max_us.compare_exchange_weak(m, us);) {}
    return hit;
}
semcodec::Fields at_fields(const std::string& box, int front, const std::string& key) {
    return {{"b", pyv::Str{box}}, {"cs", pyv::Int::of(0)}, {"f", pyv::Int::of(front)}, {"k", pyv::Str{key}}};
}
// A publish is one cell at its topic, (mqtt, <topic>, <box>.<n mod RING>) (API++ Publisher): a DIFF against the box's
// previous write of that topic, so only what changed travels. A front's publishes of one loop pass are written from the front's own thread
// before its PUBACKs leave, in parallel with every other front.
constexpr size_t PEND_BYTES = 12 * 1024, PEND_MAX = 256;
void write_pending(Front& f) {
    Box* b = box(); if (!b || f.pend.empty()) { f.pend.clear(); f.pend_bytes = 0; return; }
    try {
        for (auto& pm : f.pend) {
            const std::string topic = std::get<pyv::Str>(pm.f.back().second.v).s;
            // [API_PLUS_PLUS_V2] the box's ring on the topic; its first publish there: the directory says the box uses it
            if (b->pub->publish(topic, pm.su ? FO_MSG_SU : FO_MSG, std::move(pm.f)) == 1) ribbit::pp::announce(*b->sp, SVC_TOPICS, topic, b->id);
            b->published.fetch_add(1, std::memory_order_relaxed);
        }
    } catch (const std::exception& x) {
        static std::atomic<bool> said{false};
        if (!said.exchange(true)) fprintf(stderr, "region: the memory is gone (%s): this box's truths are no longer written\n", x.what());
    }
    f.pend.clear(); f.pend_bytes = 0;
}
void put_counts(Front& f) {
    Box* b = box(); if (!b) return;
    int64_t c = 0, n = 0, l[mqttr::Region::COUNTED_LISTENERS] = {};
    f.r->others_counts(-1, &c, &n, -1, nullptr);                       // every front of this box
    for (int k = 0; k < 4; k++) f.r->others_counts(-1, nullptr, nullptr, k, &l[k]);
    if (b->last_c.exchange(c) == c && b->last_n.exchange(n) == n) return;
    pyv::List ls; for (int k = 0; k < 4; k++) ls.push_back(pyv::Int::of(l[k]));
    put("sys", b->id, FO_SYS, {{"c", pyv::Int::of(c)}, {"l", std::move(ls)}, {"n", pyv::Int::of(n)}});
}
void release_ret(Front& f) { for (auto* x : f.ret_refs) mqttr::msg_unref(x); f.ret_refs.clear(); }
}  // namespace

extern "C" {

int rgn_open(const char* host, int port) {
    Front& f = me();
    try {
        f.efd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        if (f.efd < 0) { f.err = "eventfd failed"; return 1; }
        f.r = resident();
        f.front = f.r->hello_local(f.efd);
        if (host) the_box(host, port);                 // [MEMORY_IS_MEMORY_V1] this box in the tuple space
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
bool rgn_is_open(void) { return F && F->r; }
int rgn_front(void) { return F ? F->front : -1; }
const char* rgn_error(void) { return me().err.c_str(); }
void rgn_config(bool upgrade_outgoing_qos, bool allow_duplicate_messages, long max_queued_messages) {
    mqttr::Region* r = resident();
    r->cfg.upgrade_outgoing_qos = upgrade_outgoing_qos; r->cfg.allow_duplicate_messages = allow_duplicate_messages;
    r->cfg.max_queued_messages = max_queued_messages;
}
int rgn_pub_dropped(void) { return me().dropped; }

int rgn_own(const char* client, int protocol) {
    Front& f = me();
    f.r->own(client, f.front, protocol);
    if (Box* b = box()) put("at", std::string(client) + '\x1f' + b->id, FO_AT, at_fields(b->id, f.front, ""));
    return 0;
}
int rgn_sub(const char* client, const char* filter, uint8_t options, uint32_t identifier, bool* is_new) {
    Front& f = me();
    try {
        f.r->client(client)->front.store(f.front, std::memory_order_release);
        *is_new = f.r->subscribe(client, filter, options, identifier);
        if (Box* b = box()) hold_filter(b, filter);   // [READERS_IN_PARALLEL_V1] the topics this subscription reads
        put_sub(client, filter, true, options, identifier);   // [SUBS_BY_FILTER_V1]
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
int rgn_unsub(const char* client, const char* filter, bool* had, bool* root) {
    Front& f = me();
    try {
        *had = f.r->unsubscribe(client, filter, root);
        if (*had) put_sub(client, filter, false, 0, 0);   // withdrawal is a truth
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
int rgn_clean(const char* client) {
    Front& f = me();
    try {
        if (box()) {
            if (mqttr::Client* c = f.r->find_client(client)) {
                std::vector<std::string> fs;
                { ribbit::lf::ebr::Guard g; c->filters.walk_all([&](ribbit::lf::LFMap<std::string, int>::Node* x) { fs.push_back(x->key); return true; }); }
                for (auto& flt : fs) put_sub(client, flt, false, 0, 0);
            }
        }
        f.r->clean(client);
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
// the topic's retained tuple, its high water mark
static void put_retain(const struct rgn_msg* m) {
    Box* b = box(); if (!b) return;
    PendMsg pm = msg_fields(*m);                        // e o ...: "live" goes between e and o
    pm.f.insert(pm.f.begin() + 1, {"live", pyv::Int::of(m->payloadlen ? 1 : 0)});
    // [API_PLUS_PLUS_V2] the box's own cell for the topic's retained message; a reader takes the newest of the boxes'
    try { ribbit::pp::offer(*b->sp, SVC_RET, m->topic, b->id, pm.su ? FO_RET_SU : FO_RET, std::move(pm.f)); }
    catch (const std::exception& x) { static std::atomic<bool> said{false};
        if (!said.exchange(true)) fprintf(stderr, "region: the memory is gone (%s): this box's truths are no longer written\n", x.what()); }
}
int rgn_retain(const struct rgn_msg* m, bool* had_retained) {
    Front& f = me();
    try {
        if (Box* b = box()) {                              // [ONE_MEMORY_V1] the topic's tuple, and nothing here
            if (had_retained) *had_retained = bool(retained_of(b, m->topic));
            put_retain(m);
            return 0;
        }
        mqttr::Msg* x = make(f.r, m); x->retain = 1; bool had = f.r->retain(x); mqttr::msg_unref(x);
        if (had_retained) *had_retained = had;
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
int rgn_pub(const struct rgn_msg* m, bool* has_subscribers, rgn_own_cb own, void* ud, bool* had_retained) {
    Front& f = me();
    try {
        mqttr::MsgIn in;
        in.topic = m->topic; in.payload = std::string_view(static_cast<const char*>(m->payload), m->payloadlen);
        in.props = std::string_view(static_cast<const char*>(m->props), m->propslen);
        in.src_id = m->src_id ? m->src_id : ""; in.has_user = m->src_user != nullptr; if (m->src_user) in.src_user = m->src_user;
        in.qos = m->qos; in.retain = m->retain; in.origin = m->origin; in.expiry_ms = m->expiry_ms; in.src_port = m->src_port;
        struct Pass { rgn_own_cb cb; void* ud; } pass{own, ud};
        bool had = false;
        mqttr::Region::store_retained_here(box() == nullptr);   // [ONE_MEMORY_V1] with a memory, its retained tuple is the copy
        if (m->retain && box() && had_retained) *had_retained = bool(retained_of(box(), m->topic));
        *has_subscribers = f.r->publish(in, f.front,
            [](void* u, const std::string& client, uint8_t qos, uint8_t retain, uint32_t sid) {
                auto* p = static_cast<Pass*>(u); p->cb(client.c_str(), qos, retain, sid, p->ud);
            }, &pass, &had);
        if (had_retained && !box()) *had_retained = had;
        // [SUBS_BY_FILTER_V1] no subscriber on this box: MQTT 5's "no matching subscribers" is answered from the
        // other boxes' subscriptions, looked up at the filters that can match this topic
        // [ASYNC_PUBACK_V1] another box's subscribers are not asked here, on the front thread: the one caller that uses the
        // answer (an MQTT 5 QoS 1 PUBACK) asks with rgn_ask_subscribers and its PUBACK waits for the answer, in order
        f.dropped = mqttr::Region::last_dropped();
        // [MEMORY_IS_MEMORY_V1] written once; another box with subscribers to it is woken by its own trigger
        if (Box* b = box()) {
            f.pend.push_back(msg_fields(*m)); f.pend_bytes += f.pend.back().bytes;
            if (f.pend_bytes >= PEND_BYTES || f.pend.size() >= PEND_MAX) write_pending(f);
            if (m->retain) put_retain(m);
        }
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
// [ASYNC_PUBACK_V1] Asked on the process's pool ([ADAPTIVE_POOL_V1]): each ask is its own task, its reads its own
// requests in flight on the box's one session with every other ask's (FNWP fan-in; identical reads coalesce). The front
// thread never waits for it. The answer goes on this front's list and its eventfd fires.
bool rgn_on_memory(void) { return box() != nullptr; }
int rgn_ask_subscribers(const char* topic, const char* client, uint64_t token, uint64_t seq) {
    Front* fp = &me(); Box* b = box();
    if (!b) { fp->err = "no memory: this box alone answers"; return 1; }
    SL_asked++;
    ribbit::pool_run([fp, b, t = std::string(topic), c = std::string(client), token, seq] {
        auto* a = new Answer; a->client = c; a->token = token; a->seq = seq;
        try { a->has = subscriber_elsewhere(b, t); }
        catch (const std::exception& x) {               // the memory is gone: this box knows only its own (it had none)
            if (SL_failed++ == 0) fprintf(stderr, "region: another box's subscribers to %s could not be read (%s): the PUBACK says what this box knows\n", t.c_str(), x.what());
        }
        fp->answers.push(a);
        const uint64_t one = 1; ssize_t w = write(fp->efd, &one, sizeof one); (void)w;
    });
    return 0;
}
int rgn_answers(rgn_answer_cb cb, void* ud) {
    Front& f = me(); int n = 0;
    while (Answer* a = f.answers.pop()) { cb(a->client.c_str(), a->token, a->seq, a->has, ud); delete a; ++n; SL_answered++; }
    return n;
}
int rgn_pub_async(const struct rgn_msg* m, rgn_own_cb own, rgn_pub_done_cb done, void* ud) {
    Front& f = me();
    bool hs = false, had = false;
    if (rgn_pub(m, &hs, own, ud, &had)) return 1;
    f.last_bp = mqttr::Region::last_backpressure();
    done(ud, hs, had, f.dropped);
    f.last_bp = false;
    return 0;
}
static int retained_view(bool all, const char* filter, const struct rgn_msg** msgs, int* n) {
    Front& f = me();
    release_ret(f); f.ret_m.clear();
    if (Box* b = box()) look_up_retained(b, f.r, all ? std::string("#") : std::string(filter), f.ret_refs);   // where it is
    else if (all) f.r->retained_all(f.ret_refs); else f.r->retained_for(filter, f.ret_refs);
    for (auto* x : f.ret_refs) f.ret_m.push_back(view(x));
    *msgs = f.ret_m.data(); *n = int(f.ret_m.size());
    return 0;
}
int rgn_retained(const char* filter, const struct rgn_msg** msgs, int* n) { return retained_view(false, filter, msgs, n); }
int rgn_retained_all(const struct rgn_msg** msgs, int* n) { return retained_view(true, nullptr, msgs, n); }

int rgn_start_waiter(void) { return 0; }   // the region writes this front's eventfd itself
int rgn_eventfd(void) { return me().efd; }

int rgn_claim(const char* client, int protocol, int* prev) {
    Front& f = me();
    try {
        *prev = f.r->claim(client, f.front, protocol);
        if (Box* b = box()) {
            // the claim's key: the session the box that held it writes for this claim names it -- so this box reads
            // that one, not an older one
            std::random_device rd; char k[24]; snprintf(k, sizeof k, "%08x%08x", rd(), rd()); f.claim_key = k;
            put("at", std::string(client) + '\x1f' + b->id, FO_AT, at_fields(b->id, f.front, f.claim_key));
            if (*prev == REMOTE) *prev = REMOTE_FRONT;      // another box held it: its session comes from there
        } else if (*prev < 0) *prev = -1;
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
// [MEMORY_IS_MEMORY_V1] The session of a client another box held: that box's trigger on `at` hands it over and
// writes it as (session, client); this front reads it from there -- a held read of memory, bounded: a box that is
// gone has no session to give (gone is gone), and the client starts with none.
static void session_from_elsewhere(Box* b, std::string client, int to, std::string key) {
    std::thread([b, client, to, key] {
        mqttr::Region* r = resident();
        std::string blob; bool got_it = false; ribbit::BagArrP sess;
        try {                                           // this front's own blocking read: the session written for its claim
            long long after = 0;
            for (const int64_t until = mono_ms() + 5000; !got_it && mono_ms() < until;)
                for (auto& row : b->sp->read(SVC_BOX, "session." + client, "", after, 1.0)) {
                    if (int64_t(row.id) > after) after = int64_t(row.id);
                    ribbit::BagArrP g = row.fields(); if (!g) continue;
                    if (sval(*g, "k") == key) { blob = unb64(sval(*g, "s")); got_it = true; sess = g; }
                }
            if (!got_it) fprintf(stderr, "region: the box that held %s gave no session within 5 s: it starts without one\n", client.c_str());
        } catch (const std::exception& x) { fprintf(stderr, "region: the session of %s: %s\n", client.c_str(), x.what()); }
        // [ONE_MEMORY_V1] its subscriptions, as the box that held it wrote them in the session: this box's now, written
        // as this box's own tuples
        if (sess) if (const pyv::Obj* l = sess->get("f"); l && l->is<pyv::List>())
            for (const pyv::Obj& e : l->as<pyv::List>()) {
                if (!e.is<pyv::Str>()) continue;
                const std::string& s = e.as<pyv::Str>().s; const size_t a = s.find('\x1f'), z = a == std::string::npos ? a : s.find('\x1f', a + 1);
                if (z == std::string::npos) continue;
                const uint8_t o = uint8_t(std::stoul(s.substr(0, a))); const uint32_t sid = uint32_t(std::stoul(s.substr(a + 1, z - a - 1)));
                const std::string fl = s.substr(z + 1);
                r->subscribe(client, fl, o, sid);
                put_sub(client, fl, true, o, sid);
            }
        r->give_session(client, to, std::move(blob));
        r->flush_wakes();
        if (mqttr::Client* c = r->find_client(client)) {   // its subscriptions came with it: this box reads their families
            std::vector<std::string> fs;
            { ribbit::lf::ebr::Guard g; c->filters.walk_all([&](ribbit::lf::LFMap<std::string, int>::Node* x) { fs.push_back(x->key); return true; }); }
            for (auto& fl : fs) hold_filter(b, fl);
        }
    }).detach();
}
int rgn_want_session(const char* client, int holder, bool clean_start) {
    Front& f = me();
    try {
        if (holder == REMOTE_FRONT) { if (Box* b = box()) session_from_elsewhere(b, client, f.front, f.claim_key); return 0; }
        f.r->want_session(client, holder, f.front, clean_start);
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}
int rgn_give_session(const char* client, int to, const void* blob, uint32_t len) {
    Front& f = me();
    try {
        if (to == REMOTE) {                                 // to the box that took it over: written, read from there
            if (Box* b = box()) {
                // [ONE_MEMORY_V1] the session and its subscriptions, in one tuple of this box's: the box taking the client
                // reads them there. This box's own subscription tuples for it are withdrawn -- it no longer holds them.
                std::vector<std::string> fs;
                if (mqttr::Client* c = f.r->find_client(client)) { ribbit::lf::ebr::Guard g; c->filters.walk_all([&](ribbit::lf::LFMap<std::string, int>::Node* x) { fs.push_back(x->key); return true; }); }
                pyv::List subs;
                for (auto& fl : fs) { uint8_t o = 0; uint32_t sid = 0;
                    if (f.r->sub_of(client, fl, o, sid)) subs.push_back(pyv::Str{std::to_string(o) + '\x1f' + std::to_string(sid) + '\x1f' + fl}); }
                put(("session." + std::string(client)).c_str(), b->id, FO_SESSION, {{"b", pyv::Str{b->id}}, {"f", std::move(subs)}, {"k", pyv::Str{claim_key_of(b, client)}}, {"s", pyv::Str{b64(blob, len)}}});
                for (auto& fl : fs) put_sub(client, fl, false, 0, 0);
                f.r->clean(client);
            }
            return 0;
        }
        f.r->give_session(client, to, std::string(static_cast<const char*>(blob), len));
        return 0;
    } catch (const std::exception& x) { return fail(x); }
}

int rgn_last_backpressure(void) { return F && F->last_bp ? 1 : 0; }
int64_t rgn_others_behind(void) {
    Front& f = me();
    int64_t behind = f.r->most_behind(f.front);
    return behind;
}

void rgn_hold_stats(uint64_t* held, uint64_t* forced, int64_t* now) {
    Front& f = me();
    *held = f.x_held; *forced = f.x_forced; *now = int64_t(f.held.size());
}

bool rgn_arm(void) {
    Front& f = me();
    write_pending(f);                                       // this pass's publishes, before its PUBACKs
    f.r->pace();                                            // [PACED_PUBLISH_V1] let a backlogged front drain first
    f.r->flush_wakes();                                     // [BATCHED_WAKE_V1] what this iteration pushed, before sleeping
    bool waiting = f.r->arm(f.front);
    return waiting || !f.held.empty();                      // [HOLD_FOR_ROOM_V1] offer the held again each pass
}

int rgn_drain(rgn_delivery_cb cb, rgn_session_cb scb, void* ud) {
    Front& f = me();
    uint64_t v; ssize_t rr = read(f.efd, &v, sizeof v); (void)rr;
    int n = 0;
    // [HOLD_FOR_ROOM_V1] what was held first, then what is new; a client that had no room this pass keeps the rest
    // of its deliveries behind the first one held, in order. Past hold_limit held, deliver anyway (Mosquitto's own
    // queue limit then applies, as it does in stock).
    thread_local std::vector<mqttr::Delivery*> work; thread_local std::vector<std::string> full;
    work.assign(f.held.begin(), f.held.end()); f.held.clear(); full.clear();
    size_t fresh_from = work.size();
    for (auto* d : f.r->take(f.front)) work.push_back(d);
    bool force = int64_t(work.size()) > f.r->hold_limit(f.front)
                 || (fresh_from > 0 && mono_ms() - work.front()->held_ms >= RHOLD_MS);   // the oldest held is first
    if (force)                                              // [DIAG-HOLD] the bound reached: delivered whether or not it fits
        fprintf(stderr, "[DIAG-HOLD] front %d: %zu deliveries from other fronts forced (limit %lld, held %zu, oldest waited %lld ms)\n",
                f.front, work.size(), (long long)f.r->hold_limit(f.front), fresh_from, fresh_from ? (long long)(mono_ms() - work.front()->held_ms) : 0LL);
    for (size_t wi = 0; wi < work.size(); wi++) {
        mqttr::Delivery* d = work[wi];
        if (d->kind != mqttr::Kind::Message) {
            scb(int(d->kind), d->client.c_str(), d->from, d->clean_start, d->blob.data(), uint32_t(d->blob.size()), ud);
            delete d; continue;
        }
        bool blocked = false;
        for (auto& c : full) if (c == d->client) { blocked = true; break; }
        if (blocked) { if (wi >= fresh_from) { f.x_held++; d->held_ms = mono_ms(); } f.held.push_back(d); continue; }
        rgn_msg m = view(d->msg);
        if (force && d->qos > 0) f.x_forced++;
        int rc = cb(&m, d->client.c_str(), d->qos, d->retain, d->sid, force ? 1 : 0, ud);
        if (rc == 2) { if (wi >= fresh_from) { f.x_held++; d->held_ms = mono_ms(); } full.push_back(d->client); f.held.push_back(d); continue; }
        if (rc) f.r->forward(d->msg, d->client, d->qos, d->retain, d->sid, f.front);
        mqttr::msg_unref(d->msg); delete d; n++;
    }
    f.r->set_held(f.front, int64_t(f.held.size()));
    f.held_for.clear(); for (auto* d : f.held) ++f.held_for[d->client];   // [ONE_LINE_PER_SUBSCRIBER_V1]
    f.r->flush_wakes();                                 // forwards made while draining
    return n;
}
// [ONE_LINE_PER_SUBSCRIBER_V1] Measured (16 publishers into one subscriber, 2 fronts): deliveries from the other front
// were held for the subscriber while this front's own publishers kept taking every slot its queue freed; after 1 s the
// held were forced in and Mosquitto dropped what did not fit (8,460 of 403,540 acknowledged). A subscriber has one line:
// while deliveries from other fronts are held for it, this front's own deliveries to it wait behind them.
int rgn_held_for(const char* client) {
    Front& f = me(); if (f.held_for.empty()) return 0;
    auto it = f.held_for.find(client); return it == f.held_for.end() ? 0 : it->second;
}

void rgn_counts_publish(int64_t clients, int64_t conns, const int64_t* listener, int n) {
    Front& f = me();
    f.r->publish_counts(f.front, clients, conns, listener, n);
    put_counts(f);
}
int rgn_counts_exchange(int64_t clients, int64_t conns, const int64_t* listener, int n) {
    Front& f = me();
    f.r->publish_counts(f.front, clients, conns, listener, n);
    put_counts(f);
    return 0;
}
void rgn_counts_others(int64_t* clients, int64_t* conns, int listener, int64_t* in_listener) {
    Front& f = me();
    f.r->others_counts(f.front, clients, conns, listener, in_listener);
    if (Box* b = box()) {                               // [ONE_MEMORY_V1] and every other box's, read from its sys tuple now
        try {
            for (auto& row : b->sp->read(SVC_BOX, "sys")) {
                if (row.instance == b->id) continue;
                ribbit::BagArrP g = row.fields(); if (!g) continue;
                if (clients) *clients += g->num("c");
                if (conns) *conns += g->num("n");
                if (in_listener && listener >= 0) if (const pyv::Obj* l = g->get("l"); l && l->is<pyv::List>() && size_t(listener) < l->as<pyv::List>().size()) {
                    const pyv::Obj& e = l->as<pyv::List>()[size_t(listener)]; if (e.is<pyv::Int>()) *in_listener += e.as<pyv::Int>().v; }
            }
        } catch (const std::exception& x) { fail(x); }
    }
}

const char* rgn_stats(void) {
    Front& f = me();
    f.stats = f.r->stats_json();
    if (Box* b = box()) { f.stats.pop_back(); f.stats += ",\"box\":" + q(b->id) + ",\"from_other_boxes\":" + std::to_string(b->applied_msgs.load())
                          + ",\"missed_from_other_writers\":" + std::to_string(b->lost.load()) + "}"; }
    return f.stats.c_str();
}

long rgn_retained_count(void) {
    Box* b = box(); if (!b) return long(me().r->retained_count());
    long n = 0;                                         // [ONE_MEMORY_V1] counted where they are
    try { for (auto& t : topics_matching(b, "#")) if (retained_of(b, t)) ++n; } catch (const std::exception& x) { fail(x); }
    return n;
}

}  // extern "C"
