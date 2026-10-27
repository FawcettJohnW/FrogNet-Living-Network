// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// mqtt_region.hpp -- the MQTT region: what Mosquitto keeps in its one global `db`, held in the RAM host so that any
// number of fronts, on any machines, are one broker.
//
// No mutex, no condition variable, no lock anywhere in this file. Every shared structure is a lock-free ordered map
// (lf/lfmap.hpp) or a single atomic word; everything a reader may still be looking at is retired to the epoch
// reclamation (lf/ebr.hpp) and freed only when no reader can hold it.
//
//   Msg        one message, immutable once published; an atomic count of the deliveries and retained slot holding it
//   Node       one level of a topic or filter: children by name, each child's place in creation order (Mosquitto
//              walks children in the order they were made, and so must we), the subscriptions made AT this level in
//              subscription order, the share groups in creation order, and the retained message for this topic
//   Client     per client id: which front holds it now, and the filters it is subscribed to
//   Front      per front: the deliveries for its clients that other fronts' publishes produced, as a lock-free stack,
//              and the word its one parked wait sleeps on
#pragma once
#include <atomic>
#include <cstdlib>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>
#include "lf/ebr.hpp"
#include "lf/lfmap.hpp"
#include "lf/primitives.hpp"   // [LF_PRIMITIVES_V1]

namespace mqttr {
using ribbit::lf::LFMap;

struct Msg {
    std::atomic<int> refs{1};
    uint64_t id = 0;
    std::string topic, payload, props;      // props: the MQTT v5 property bytes Mosquitto forwards, wire-encoded
    std::string src_id, src_user;           // the publisher (src_user empty and has_user false: none)
    bool has_user = false;
    uint8_t qos = 0, retain = 0, origin = 0;   // origin: 0 a client, 1 the broker
    int64_t expiry_ms = 0;                  // absolute (unix ms), 0 = never
    int src_port = 0;                       // the publisher's listener port (check_retain_source needs its listener)
};
bool msg_ref(Msg* m);                       // take a reference unless the message is already on its way out
void msg_unref(Msg* m);                     // drop one; the last one retires it

struct Client;
struct Sub {                                // immutable: a re-subscribe replaces it, keeping its place
    std::string client, filter;             // filter as the client gave it, $share/... included
    Client* who = nullptr;                  // the client's record: records are never freed, so the pointer stays good
    uint8_t options = 0;                    // MQTT v5 subscription options byte (qos | nl | rap | rh)
    uint32_t identifier = 0;
    uint64_t seq = 0;                       // its place in subscription order
};
struct SubSlot { std::atomic<Sub*> sub{nullptr}; };

struct Group;
struct Node {
    std::string name;
    uint64_t seq = 0;
    LFMap<std::string, Node*> kids;                 // by name
    LFMap<uint64_t, Node*> kids_order;              // by creation (Mosquitto's HASH_ITER order)
    LFMap<std::string, SubSlot*> subs;              // normal subscriptions, by client
    LFMap<uint64_t, SubSlot*> subs_order;           // ... in subscription order
    LFMap<std::string, Group*> groups;              // share groups, by name
    LFMap<uint64_t, Group*> groups_order;           // ... in creation order
    std::atomic<Msg*> retained{nullptr};
    std::atomic<Node*> plus{nullptr}, hash{nullptr};  // the "+" and "#" children, reached without a lookup
    Node* child(std::string_view n) const;           // null if absent
    Node* child_or_make(const std::string& n);
};
struct Group {
    std::string name;
    LFMap<std::string, SubSlot*> members;           // by client
    LFMap<uint64_t, SubSlot*> order;                // join order: the rotation's ring
    std::atomic<uint64_t> turn{0};
};

struct Client {
    std::string id;
    std::atomic<int> front{-1};                     // the front holding this client now
    std::atomic<int> protocol{4};                   // 3, 4 or 5 (MQTT 3.1, 3.1.1, 5)
    LFMap<std::string, int> filters;                // what it is subscribed to
};

// [SESSION_HANDOVER_V1] what travels to a front: a message for one of its clients, or a session moving between fronts
// [POSTED_PIPELINE_V1] PipelineResult: a posted pipeline's results, queued among the front's deliveries in the order the
// host ran it (blob: the results' text)
// [MQTT_STREAM_V1] Reply: a CALL's answer for the front that made it (blob: the encoded entry)
enum class Kind : uint8_t { Message = 0, HandoverWanted = 1, HandoverDone = 2, PipelineResult = 3, Reply = 4, Counts = 5 };
struct Delivery {                                   // one message for one client, bound for that client's front
    Delivery(Msg* m, std::string c, uint8_t q, uint8_t r, uint32_t s) : msg(m), client(std::move(c)), qos(q), retain(r), sid(s) {}
    Msg* msg; std::string client; uint8_t qos; uint8_t retain; uint32_t sid; Delivery* next = nullptr;
    Kind kind = Kind::Message;
    int from = -1;                                  // HandoverWanted: the front that wants the session
    bool clean_start = false;                       // HandoverWanted: the new connection's clean start
    std::string blob;                               // HandoverDone: the session, as its last front wrote it
    int64_t held_ms = 0;                            // [HOLD_FOR_ROOM_V1] when its front first held it (front's own use)
};
struct Front {
    ribbit::lf::TakeStack<Delivery> deliveries;     // [LF_PRIMITIVES_V1] pushed by any thread, taken whole by the front
    ribbit::lf::WaitWord word;                      // a remote front's held wait parks on it
    std::atomic<bool> live{false};
    // [CO_RESIDENT_FRONT_V1] a front in the host's own process sleeps in its event loop, not on the futex: a push
    // writes its eventfd, and only when the front has said it is about to sleep (armed) -- one write per sleep
    std::atomic<int> efd{-1};
    alignas(64) std::atomic<bool> armed{false};
    alignas(64) std::atomic<uint64_t> taken{0};     // [QOS0_BACKLOG_V1] deliveries this front has taken; its own writer
    alignas(64) std::atomic<void*> parked{nullptr}; // [HELD_WAIT_V1] a remote front's held read, parked as a continuation
    std::atomic<int64_t> held{0};                   // [HOLD_FOR_ROOM_V1] of those, still held for a client with no room
                                                    // (its own writer): still undrained for pacing and the QoS 0 bound
};

struct Match { std::string client; uint8_t qos; uint8_t retain; uint32_t sid; };
struct Hit { const Sub* sub; uint8_t qos; uint8_t retain; };   // inside one publish, valid under its epoch guard

// [LAZY_MESSAGE_V1] A publish as it arrives: views into the publisher's own buffers. The region makes a Msg of it only
// when something must keep it -- a delivery bound for another front, or the retained slot. A message whose subscribers
// are all on the publishing front is never copied.
struct MsgIn {
    std::string_view topic, payload, props, src_id, src_user;
    bool has_user = false;
    uint8_t qos = 0, retain = 0, origin = 0;
    int64_t expiry_ms = 0;
    int src_port = 0;
};
// a delivery for one of the publishing front's own clients, handed over while the publish runs, in Mosquitto's order
using OwnFn = void (*)(void* ud, const std::string& client, uint8_t qos, uint8_t retain, uint32_t sid);

struct Config {                                     // the broker options the region itself applies (operator's)
    bool upgrade_outgoing_qos = false;
    bool allow_duplicate_messages = true;
    // [QOS0_BACKLOG_V1] Mosquitto drops a QoS 0 message for a subscriber whose outgoing packets already number
    // max_queued_messages (database.c, db__ready_for_flight). That check still runs in the receiving front; this one
    // stops the backlog building *before* it: a QoS 0 delivery to a front whose undrained deliveries exceed
    // max_queued_messages x its clients is dropped and counted. 0: no limit, as in Mosquitto.
    int64_t max_queued_messages = 0;
};

class Region {
public:
    static const int MAX_FRONTS = 1024;
    Config cfg;

    int hello();                                    // a front arrives: its number
    // [HELD_WAIT_V1] a push to a remote front whose held read is parked calls this (set once by the host, before the
    // first front arrives): the host answers the read with what is waiting. No thread waits for it.
    static void (*remote_wake)(int front);
    Front& front_slot(int front) { return fronts_[front]; }
    void own(const std::string& client, int front, int protocol);
    // [SESSION_HANDOVER_V1] A connecting client is claimed by its front: the front that held it before (-1: none) is
    // returned. The claim is the client's owner word, exchanged -- the newer claim wins, nobody arbitrates.
    int claim(const std::string& client, int front, int protocol);
    // the claimer asks the previous front for the session; that front answers with the session written whole
    void want_session(const std::string& client, int holder, int wanter, bool clean_start);
    void give_session(const std::string& client, int to, std::string blob);
    // [POSTED_PIPELINE_V1] a posted pipeline's results, for the front that posted it, behind what is already queued
    void post_result(int front, std::string text);
    // [MQTT_STREAM_V1] any entry for a front, queued behind what is already queued for it
    void post_entry(int front, Kind kind, std::string blob);
    // a delivery that reached a front no longer holding its client goes on to the client's front now (false: none)
    bool forward(Msg* m, const std::string& client, uint8_t qos, uint8_t retain, uint32_t sid, int from);
    // subscribe: true if new, false if it replaced an existing subscription of this client to this filter
    bool subscribe(const std::string& client, const std::string& filter, uint8_t options, uint32_t identifier);
    // [ONE_MEMORY_V1] a client's subscription as held: its options and identifier (false: none)
    bool sub_of(const std::string& client, const std::string& filter, uint8_t& options, uint32_t& identifier);
    // false: there was none. *root: the filter's first level exists (Mosquitto pre-makes "" and "$SYS", and "" for
    // shared): when it does not, Mosquitto leaves the UNSUBACK reason as it was
    bool unsubscribe(const std::string& client, const std::string& filter, bool* root = nullptr);
    void clean(const std::string& client);          // every subscription of this client goes
    // publish: fan out (Mosquitto's walk and rules), then retain. Deliveries for `front`'s own clients come back in
    // `own`, in Mosquitto's order; the rest go to their fronts. Returns whether anyone was subscribed.
    bool publish(Msg* m, int front, std::vector<Match>& own, bool* had_retained = nullptr);
    // [LAZY_MESSAGE_V1] the same publish from views: the message is made only if kept, and own deliveries are handed
    // to `own` as the fan-out finds them (no copy of the client id, no list)
    bool publish(const MsgIn& in, int front, OwnFn own, void* ud, bool* had_retained = nullptr);
    void retained_for(const std::string& filter, std::vector<Msg*>& out);       // Mosquitto's retain__search order
    // set (or, empty payload, clear) the retained message, no fan-out; true if there was one before
    bool retain(Msg* m);
    void retained_all(std::vector<Msg*>& out);
    int64_t retained_count();                      // derived: slots filled minus slots emptied, every thread's counts summed      // every retained message, depth first in creation order
    // the deliveries waiting for this front, oldest first; waits up to wait_ms for one to arrive
    std::vector<Delivery*> wait(int front, int64_t wait_ms);
    // [PIPELINE_V1] Park until deliveries are waiting for this front, or wait_ms passes; take nothing. The front takes
    // them itself on its next pipeline, so a front has exactly one taker and its deliveries one order.
    bool wait_ready(int front, int64_t wait_ms);
    // [REMOTE_PACING_V1] how far behind the most-behind *other* front is: its undrained deliveries (every thread's
    // pushes to it, less what it took -- single-writer counts, summed here). A remote front paces on it.
    int64_t most_behind(int except_front);
    // [HOLD_FOR_ROOM_V1] the front says how many it has taken but is holding (no room in the client's queue yet)
    int64_t hold_limit(int front) {   // beyond this many held, a front delivers anyway and Mosquitto's own limit applies
        int64_t c = front >= 0 && front < MAX_FRONTS ? counts_[front].clients.load(std::memory_order_relaxed) : 1;
        int64_t l = int64_t(cfg.max_queued_messages) * (c > 1 ? c : 1);
        static const int64_t floor = [] { const char* v = getenv("RIBBIT_HOLD_MAX"); long long n = v ? atoll(v) : 0; return int64_t(n > 0 ? n : 16384); }();
        if (floor != 16384) return floor;                     // set to test the bound: exactly that
        return l > floor ? l : floor;
    }
    void set_held(int front, int64_t n) { if (front >= 0 && front < MAX_FRONTS) fronts_[front].held.store(n, std::memory_order_release); }
    // [CO_RESIDENT_FRONT_V1] a front in this process: it is woken through efd, takes without waiting, and arms before
    // it sleeps (arm returns true if deliveries are already waiting: take them instead of sleeping)
    int hello_local(int efd);
    std::vector<Delivery*> take(int front);
    bool arm(int front);
    // [BATCHED_WAKE_V1] A front thread's pushes to a sleeping co-resident front only mark it; the wake (one eventfd
    // write) goes out here, once per front per loop iteration, instead of one write and one epoll wake per message.
    // Called by the front before it sleeps and after a drain. Threads that are not fronts wake at once, as before.
    void flush_wakes();
    void wake_all();                                // [PUBLISHER_PACED_BY_READERS_V1] every front of this box looks again
    // [PACED_PUBLISH_V1] Before a front reads more input: while a front it delivered to this iteration has more than
    // PACE_PENDING deliveries undrained, give up the core (up to PACE_MAX_US) so that front can drain. Stock Mosquitto
    // is paced the same way by construction -- the thread that reads a publish also delivers it. Returns microseconds
    // spent waiting.
    static const int64_t PACE_PENDING = 1024;
    static const int64_t PACE_MAX_US = 2000;
    static const int64_t PACE_HARD_US = 50000;     // [HOLD_FOR_ROOM_V1] a target past half its hold limit
    int64_t pace();
    void flush_wakes_list(const std::vector<int>& fronts);
    // [DIAG-FRONTS] every front's backlog terms, one line (MQTT_DIAG=1 in mqtt-ram samples it every 50 ms)
    std::string diag_fronts();
    static const int TRACKED_FRONTS = 64;          // fronts whose backlog is bounded (front threads in one process)
    // QoS 0 deliveries the last publish on this thread dropped ([QOS0_BACKLOG_V1])
    static int last_dropped();
    static void store_retained_here(bool on);       // [MEMORY_IS_MEMORY_V1] see mqtt_region.cpp
    // [HOLD_FOR_ROOM_V1] the last publish on this thread sent a QoS 1/2 delivery into a front more than PACE_PENDING behind
    static bool last_backpressure();

    // [FRONT_COUNTS_V1] what each front has, published by that front alone (one writer per slot); a limit that is
    // the whole broker's (global_max_clients, max_connections) reads the other fronts' slots and adds its own count
    static const int COUNTED_LISTENERS = 32;
    void publish_counts(int front, int64_t clients, int64_t conns, const int64_t* listener, int n);
    void others_counts(int front, int64_t* clients, int64_t* conns, int listener, int64_t* in_listener) const;

    uint64_t next_id() { return id_.fetch_add(1, std::memory_order_relaxed); }
    Client* client(const std::string& id);          // made on first use
    Client* find_client(const std::string& id) const;

    // counters: derived where possible, per-thread where not (telemetry, never a decision)
    struct Count { std::atomic<uint64_t> pubs{0}, deliveries_own{0}, deliveries_pushed{0}, wakes{0}, wakes_batched{0}, waits{0}, subs{0}, unsubs{0}, retained_set{0}, retained_cleared{0}, messages_made{0}, retained_added{0}, retained_removed{0}, qos0_dropped{0}, paced{0}, paced_us{0};
        std::atomic<uint64_t> pushed_to[64] = {};      // [QOS0_BACKLOG_V1] deliveries this thread pushed to fronts 0..63
    };
    Count& count();
    std::string stats_json();

private:
    alignas(64) std::atomic<uint64_t> id_{1};
    alignas(64) std::atomic<uint64_t> seq_{1};
    std::atomic<int> fronts_n_{0};
    Front fronts_[MAX_FRONTS];
    struct alignas(64) FrontCounts { std::atomic<int64_t> clients{0}, conns{0}; std::atomic<int64_t> listener[COUNTED_LISTENERS] = {}; };
    FrontCounts counts_[MAX_FRONTS];
    Node normal_, shared_, retains_;                // roots: children are the first token ("" or "$SYS" ...)
    LFMap<std::string, Client*> clients_;

    void search(Node* n, const std::vector<std::string_view>& t, size_t i, std::string_view source, uint8_t qos,
                uint8_t retain, std::vector<Hit>& out, bool& have);
    void process(Node* n, std::string_view source, uint8_t qos, uint8_t retain, std::vector<Hit>& out, bool& have);
    void send(const Sub* s, uint8_t qos, uint8_t retain, std::vector<Hit>& out);
    bool fan_out(const MsgIn& in, Msg* kept, int front, OwnFn own, void* ud, std::vector<Match>* own_list, bool* had_retained);
    int retain_search(Node* n, const std::vector<std::string>& t, size_t i, int level, std::vector<Msg*>& out);
    void retain_take(Node* n, std::vector<Msg*>& out);
    void retain_walk(Node* n, std::vector<Msg*>& out);
    void push(int front, Delivery* d);
    int backlogged(int front);          // [QOS0_PACED_V1] 0: not behind; 1: behind (pause the publisher); 2: far behind (drop)
    int64_t pending(int front);
};

// Mosquitto's sub__topic_tokenise: "" in front of a topic not starting with '$'; $share/<name>/ removed (and the
// share name returned) for a subscription. False: not a valid filter.
bool tokenise(const std::string& topic, std::vector<std::string>& out, std::string* share);
// the same for a publish topic, as views into it (no copies: the hot path)
void tokenise_view(std::string_view topic, std::vector<std::string_view>& out);

}  // namespace mqttr
