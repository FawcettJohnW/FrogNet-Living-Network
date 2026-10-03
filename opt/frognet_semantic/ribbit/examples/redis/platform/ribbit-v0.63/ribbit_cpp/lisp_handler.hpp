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
// lisp_handler.hpp -- LispHandler, a UnRESTHandler for LISP (RFC 9300/9301) on FrogNet. Same shape as
// core/sotf_handler.py: identity + election + lifecycle on the handler; the programs (threads, sockets) in objects
// its factories return, the way open_stream() returns a SotFMediaCodex.
//
//   identity   role "lisp", candidate "LispCandidate", port 4342
//   election   score(): a node can serve the LISP boundary only if its capability says UDP 4342 is open and it has a
//              public address -- two NEW capability fields, lisp_udp_4342 and public_ip, which
//              frognet_capability_probe.sh does not publish yet. Rank on static capability (measured CPU bench,
//              else cores), never on load. evaluate() uses the WAN-inclusive hosts_list: the boundary faces the
//              Internet (mediahost is the opposite: LAN only).
//   codec      a mapping record as JSON {"iid","eid","ttl","rlocs":[{"address","priority","weight"}]}, fields in
//              that order. A body that is not a mapping record raises.
//   open_site()      -> LispSite: an ETR/ITR participant. Holds its own Engine (lisp_engine.hpp) on the RAM; the
//                       per-ETR cells, liveness, governance and held resolution are the engine's.
//   open_boundary()  -> LispBoundary: the interop edge. Holds its OWN Engine (one request thread per Engine, and
//                       participants take no locks: the site and the boundary coordinate through memory) and a
//                       UDP socket. Map-Register (type 3) -> wire.register4_notify, Map-Notify back to the sender;
//                       Map-Request (type 1) -> wire.request4, Map-Reply or negative reply back to the sender.
#pragma once

#include "lisp_engine.hpp"
#include "unrest_handler.hpp"

#include <algorithm>
#include <atomic>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <deque>
#include <functional>
#include <mutex>
#include <cerrno>
#include <cstring>
#include <memory>
#include <thread>

namespace frognet {

class LispSite {
public:
    LispSite(const std::string& ram_host, int ram_port);
    // Any engine operation (the same operations ribbit-lisp takes on stdin); the result is the engine's JSON text.
    std::string call(const std::string& op, const std::string& args_json);
private:
    Engine e_;
};

// [BOUNDARY_NEVER_WAITS_V1] John 2026-09-26: nothing may stop behind a blocking call. The receive thread never
// touches the network: a Map-Request is answered on it from held views (no round trip); a Map-Register is handed to a
// register worker and the receive thread goes straight back to recvfrom. Workers are chosen by the sender's
// address:port, so one xTR's registers stay in the order it sent them while different senders proceed in parallel.
// Every thread has its OWN Engine (participants take no locks; they coordinate through memory): the receive thread's
// answers requests, each worker's registers, and the configuration Engine serves call(). Before this, one thread did
// recvfrom -> the register's RAM round trips -> sendto, and every datagram from every sender queued behind it.
class LispBoundary {
public:
    static const int REGISTER_WORKERS = 8;
    // Binds UDP udp_port on all addresses and starts the receive thread and the register workers.
    LispBoundary(const std::string& ram_host, int ram_port, int udp_port, int register_workers = REGISTER_WORKERS,
                 const std::string& api = "/ram.php");      // api: the vendor's RAM interface endpoint
    ~LispBoundary();
    // Engine operations for the boundary's own configuration (site.add with the site's password, ...), on the
    // configuration Engine. Any time; serialized among themselves.
    std::string call(const std::string& op, const std::string& args_json);

    void stop();
    uint64_t registers() const { return registers_; }
    uint64_t requests() const { return requests_; }
    uint64_t rejected() const { return rejected_; }
private:
    struct Job { std::vector<unsigned char> pkt; sockaddr_in from; socklen_t fl; std::function<void(Engine&)> cfg; };
    std::string configure(const std::string& op, const std::string& args_json);
    // [BOUNDARY_NO_LOCKS_V1] Participants take no locks. A worker's queue is lock-free: many producers (the receive
    // thread, the configuration caller) append with one atomic exchange, the worker alone takes (Vyukov's intrusive
    // MPSC queue); the worker sleeps on a futex word that every append bumps -- a kernel wait queue, not a lock.
    struct JobNode { Job job; std::atomic<JobNode*> next{nullptr}; };
    struct Worker {
        std::unique_ptr<Engine> e; std::thread th;
        JobNode* head = new JobNode;                     // the consumer's stub
        std::atomic<JobNode*> tail{head};
        std::atomic<uint32_t> signal{0};
        void push(Job j) {
            JobNode* n = new JobNode; n->job = std::move(j);
            JobNode* prev = tail.exchange(n, std::memory_order_acq_rel);
            prev->next.store(n, std::memory_order_release);
            signal.fetch_add(1, std::memory_order_release);
            ::syscall(SYS_futex, reinterpret_cast<uint32_t*>(&signal), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0);
        }
        bool pop(Job& out) {                              // the worker only
            JobNode* nx = head->next.load(std::memory_order_acquire);
            if (!nx) return false;
            out = std::move(nx->job); delete head; head = nx; return true;
        }
        void sleep_unless_changed(uint32_t seen) {
            ::syscall(SYS_futex, reinterpret_cast<uint32_t*>(&signal), FUTEX_WAIT_PRIVATE, seen, nullptr, nullptr, 0);
        }
        void wake() { signal.fetch_add(1, std::memory_order_release); ::syscall(SYS_futex, reinterpret_cast<uint32_t*>(&signal), FUTEX_WAKE_PRIVATE, 1, nullptr, nullptr, 0); }
        ~Worker() { Job j; while (pop(j)) {} delete head; }
    };
    // [REGISTER_STRANDS_V1] John 2026-09-28 ("nothing may stop behind a blocking call"): a Map-Register's call to the
    // region blocks for a whole round trip to the RAM server. It used to occupy one of a fixed set of workers, chosen by a
    // hash of the sender, so senders that hashed alike queued behind each other and throughput was capped at workers
    // per round trip. Now each sender has its own strand -- its registers stay in the order they came -- and a strand
    // runs on its own thread only while it has registers to answer: every sender's round trip overlaps every other's.
    struct Strand {
        uint64_t key = 0; Strand* next_in_bucket = nullptr;      // set once, before publication
        JobNode* head = new JobNode;                            // the running thread's stub (Vyukov MPSC, as Worker)
        std::atomic<JobNode*> tail{head};
        std::atomic<bool> running{false};
        void push(Job j) {
            JobNode* n = new JobNode; n->job = std::move(j);
            JobNode* prev = tail.exchange(n, std::memory_order_acq_rel);
            prev->next.store(n, std::memory_order_release);
        }
        bool pop(Job& out) {                                    // the running thread only
            JobNode* nx = head->next.load(std::memory_order_acquire);
            if (!nx) return false;
            out = std::move(nx->job); delete head; head = nx; return true;
        }
        bool pending() const { return head->next.load(std::memory_order_acquire) != nullptr || tail.load(std::memory_order_acquire) != head; }
    };
    static constexpr size_t STRAND_BUCKETS = 4096;
    std::atomic<Strand*> strands_[STRAND_BUCKETS] = {};
    std::atomic<uint32_t> strands_running_{0};
    Strand* strand_for(uint64_t key);
    void run_strand(Strand* s);
    void answer_register(Engine& e, const Job& j);
    void loop();
    void work(Worker& w);
    void reject(const sockaddr_in& from, ssize_t n, int type, const std::string& why);
    static int request_eid_afi(const std::vector<unsigned char>& m);
    // [DDT_REFERRAL_V1] LISP-DDT (RFC 8111) as lispers.net's map-server does it (lisp_ms_send_map_referral): when this
    // front is configured as a DDT authority (front.ddt_authority, ms.authoritative_prefix), a DDT-originated
    // Map-Request (ECM with the D bit) gets its Map-Reply AND a Map-Referral to the ECM's source. Off by default: a
    // co-located map-server + map-resolver (what lispers.net does when both run in one lispers.net) sends no referral.
    std::vector<unsigned char> map_referral(const std::vector<unsigned char>& request, const std::vector<unsigned char>& reply);
    std::atomic<bool> ddt_authority_{false};
    std::atomic<const std::vector<std::string>*> auth_prefixes_{nullptr};    // IPv4 prefixes, instance 0
    std::atomic<const std::vector<std::string>*> ms_peers_{nullptr};         // map-server peers: the referral set
    ribbit::Ebr ebr_;
    Engine req_e_, cfg_e_;
    std::vector<std::unique_ptr<Worker>> workers_;
    Engine reg_e_;                                          // [REGISTER_STRANDS_V1] one session carries every strand's call
    int fd_ = -1;
    std::atomic<bool> stop_{false};
    std::atomic<uint64_t> registers_{0}, requests_{0}, rejected_{0};
    std::thread th_;
};

class LispHandler : public UnRESTHandler {
public:
    const char* role_name() const override { return "lisp"; }
    const char* candidate_type() const override { return "LispCandidate"; }
    int default_port() const override { return 4342; }

    pyv::Dict learn_request_template(std::string_view body) override;
    pyv::Dict learn_reply_template(std::string_view body) override;
    semcodec::Fields extract_request_dynamic(std::string_view body, const pyv::Dict& fragment) override;
    semcodec::Fields extract_reply_dynamic(std::string_view body, const pyv::Dict& fragment) override;
    std::string rebuild_reply(const pyv::Dict& fragment, const pyv::List& values) override;

    double score(const Json& cand) const override;
    const Json* evaluate(const std::vector<Json>& hosts_list, const std::vector<Json>& lan_list) const override;

    std::unique_ptr<LispSite> open_site(const std::string& ram_host, int ram_port) const;
    std::unique_ptr<LispBoundary> open_boundary(const std::string& ram_host, int ram_port, int udp_port) const;
};

// ---- election (pure) -------------------------------------------------------------------------------------------
namespace lisp_detail {
inline double num(const Json& c, const char* field, double absent) {   // absent -> default; present, not a number -> raise
    const Json& v = c[field];
    if (v.type == Json::Null) return absent;
    if (v.type != Json::Num) throw std::invalid_argument(std::string("lisp capability field ") + field + " is not a number");
    return v.n;
}
inline uint32_t ip4(const std::string& ip) {
    in_addr a{};
    if (inet_pton(AF_INET, ip.c_str(), &a) != 1) throw std::invalid_argument("lisp candidate lan_ip " + ip + " is not IPv4");
    return ntohl(a.s_addr);
}
}  // namespace lisp_detail

inline double LispHandler::score(const Json& c) const {
    const Json& udp = c["lisp_udp_4342"];
    if (udp.type != Json::Null && udp.type != Json::Bool) throw std::invalid_argument("lisp capability field lisp_udp_4342 is not a bool");
    const Json& pub = c["public_ip"];
    if (pub.type != Json::Null && pub.type != Json::Str) throw std::invalid_argument("lisp capability field public_ip is not a string");
    if (!(udp.type == Json::Bool && udp.b) || pub.type != Json::Str || pub.s.empty()) return -1.0;
    // [static rank, as DBHOST_NO_LOAD / MEDIAHOST_STATIC_RANK] measured benchmark, else cores; never load
    double bench = lisp_detail::num(c, "cpu_bench_total", 0.0);
    double cores = lisp_detail::num(c, "cores", 1.0);
    return bench > 0 ? bench / 1000.0 : std::max(1.0, cores) * 0.5;
}

inline const Json* LispHandler::evaluate(const std::vector<Json>& hosts_list, const std::vector<Json>&) const {
    // The boundary faces the Internet: the WAN-inclusive list. lan_list is accepted for signature uniformity, unused.
    const Json* best = nullptr; double best_sc = 0; uint32_t best_ip = 0;
    for (const Json& c : hosts_list) {
        const Json& ipj = c["lan_ip"];
        if (ipj.type == Json::Null || (ipj.type == Json::Str && ipj.s.empty())) continue;   // as the Python: no address, no candidate
        if (ipj.type != Json::Str) throw std::invalid_argument("lisp candidate lan_ip is not a string");
        double sc = score(c);
        if (sc < 0) continue;
        uint32_t ip = lisp_detail::ip4(ipj.s);
        if (!best || sc > best_sc || (sc == best_sc && ip > best_ip)) { best = &c; best_sc = sc; best_ip = ip; }
    }
    return best;
}

// ---- codec: a mapping record ---------------------------------------------------------------------------------------
namespace lisp_detail {
inline const std::vector<std::string>& fields() { static const std::vector<std::string> f{"iid", "eid", "ttl", "rlocs"}; return f; }
inline const pyv::Obj& member(const pyv::Dict& d, const char* k, const char* where) {   // a reference into d
    long i = d.find(k);
    if (i < 0) throw pyv::ValueError(std::string(where) + ": no \"" + k + "\"");
    return d.vals[size_t(i)];
}
inline int64_t small_int(const pyv::Obj& v, const std::string& what, int64_t lo, int64_t hi) {
    if (!v.is<pyv::Int>() || v.as<pyv::Int>().big) throw pyv::TypeError(what + " is not an integer");
    int64_t n = v.as<pyv::Int>().v;
    if (n < lo || n > hi) throw pyv::ValueError(what + " out of range");
    return n;
}
inline void check_value(const std::string& f, const pyv::Obj& v) {   // each field's rule
    if (f == "iid") {
        if (!v.is<pyv::Str>()) throw pyv::TypeError("mapping record iid is not a string");
        const std::string& s = v.as<pyv::Str>().s;
        if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos || std::stoull(s) > 0xFFFFFFu)
            throw pyv::ValueError("mapping record iid " + s + " is not a 24-bit instance ID");
    } else if (f == "eid") {
        if (!v.is<pyv::Str>()) throw pyv::TypeError("mapping record eid is not a string");
        prefix_parse(v.as<pyv::Str>().s);                                  // raises on a malformed prefix
    } else if (f == "ttl") {
        small_int(v, "mapping record ttl", 0, 0xFFFFFFFFll);
    } else {
        if (!v.is<pyv::List>()) throw pyv::TypeError("mapping record rlocs is not a list");
        for (const pyv::Obj& r : v.as<pyv::List>()) {
            if (!r.is<pyv::Dict>()) throw pyv::TypeError("mapping record rloc is not an object");
            const pyv::Dict& d = r.as<pyv::Dict>();
            const pyv::Obj& a = member(d, "address", "rloc");
            if (!a.is<pyv::Str>()) throw pyv::TypeError("rloc address is not a string");
            prefix_parse(a.as<pyv::Str>().s + (a.as<pyv::Str>().s.find(':') == std::string::npos ? "/32" : "/128"));
            small_int(member(d, "priority", "rloc"), "rloc priority", 0, 255);
            small_int(member(d, "weight", "rloc"), "rloc weight", 0, 255);
        }
    }
}
inline pyv::Dict record(std::string_view body) {
    pyv::Obj o = semcodec::json_loads(body);                             // raises on text that is not JSON
    if (!o.is<pyv::Dict>()) throw pyv::TypeError("mapping record is not a JSON object");
    const pyv::Dict& d = o.as<pyv::Dict>();
    for (const std::string& f : fields()) check_value(f, member(d, f.c_str(), "mapping record"));
    return d;
}
inline void check_fragment(const pyv::Dict& frag) {
    const pyv::Obj& m = member(frag, "mode", "template fragment");
    if (!m.is<pyv::Str>() || m.as<pyv::Str>().s != "lisp") throw pyv::ValueError("template fragment is not a lisp mapping-record template");
}
inline pyv::Dict learn(std::string_view body) {
    record(body);
    pyv::Dict frag, tm, tokens;
    frag.set("mode", pyv::Str{"lisp"});
    pyv::List fo; for (const std::string& f : fields()) fo.push_back(pyv::Str{f});
    frag.set("fields", fo); frag.set("field_order", fo);
    tm.set("iid", pyv::Str{"string"}); tm.set("eid", pyv::Str{"string"}); tm.set("ttl", pyv::Str{"int"}); tm.set("rlocs", pyv::Str{"array"});
    frag.set("type_map", tm);
    for (const char* t : {"ip", "host", "str", "enum"}) tokens.set(t, pyv::Dict{});
    frag.set("tokens", tokens);
    return frag;
}
inline semcodec::Fields extract(std::string_view body, const pyv::Dict& frag) {
    check_fragment(frag);
    pyv::Dict d = record(body);
    semcodec::Fields out;
    for (const std::string& f : fields()) out.emplace_back(f, member(d, f.c_str(), "mapping record"));
    return out;
}
}  // namespace lisp_detail

inline pyv::Dict LispHandler::learn_request_template(std::string_view body) { return lisp_detail::learn(body); }
inline pyv::Dict LispHandler::learn_reply_template(std::string_view body) { return lisp_detail::learn(body); }
inline semcodec::Fields LispHandler::extract_request_dynamic(std::string_view b, const pyv::Dict& f) { return lisp_detail::extract(b, f); }
inline semcodec::Fields LispHandler::extract_reply_dynamic(std::string_view b, const pyv::Dict& f) { return lisp_detail::extract(b, f); }
inline std::string LispHandler::rebuild_reply(const pyv::Dict& frag, const pyv::List& values) {
    lisp_detail::check_fragment(frag);
    const auto& fs = lisp_detail::fields();
    if (values.size() != fs.size())
        throw pyv::ValueError("mapping record rebuild: " + std::to_string(values.size()) + " values for " + std::to_string(fs.size()) + " fields");
    pyv::Dict d;
    for (size_t i = 0; i < fs.size(); ++i) { lisp_detail::check_value(fs[i], values[i]); d.set(fs[i], values[i]); }
    return semcodec::json_dumps(pyv::Obj(d));
}

// ---- factories and the programs they return ------------------------------------------------------------------------
inline std::unique_ptr<LispSite> LispHandler::open_site(const std::string& h, int p) const { return std::make_unique<LispSite>(h, p); }
inline std::unique_ptr<LispBoundary> LispHandler::open_boundary(const std::string& h, int p, int udp) const {
    return std::make_unique<LispBoundary>(h, p, udp);
}

inline LispSite::LispSite(const std::string& h, int p) : e_(h, p) {}
inline std::string LispSite::call(const std::string& op, const std::string& args) { return e_.call(op, Json::parse(args)); }

inline LispBoundary::LispBoundary(const std::string& h, int p, int udp_port, int nworkers, const std::string& api) : req_e_(h, p, api), cfg_e_(h, p, api), reg_e_(h, p, api) {
    if (nworkers < 1) throw std::runtime_error("lisp boundary: register_workers must be >= 1, got " + std::to_string(nworkers));
    // [ONE_SET_OF_VIEWS_PER_PARTICIPANT_V1] the boundary is one participant: every Engine reads the request Engine's views
    cfg_e_.share_views_of(req_e_);
    reg_e_.share_views_of(req_e_);
    for (int i = 0; i < nworkers; ++i) { workers_.emplace_back(new Worker); workers_.back()->e.reset(new Engine(h, p, api)); workers_.back()->e->share_views_of(req_e_); }
    fd_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd_ < 0) throw std::runtime_error(std::string("lisp boundary: socket: ") + std::strerror(errno));
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons((uint16_t)udp_port); a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (::bind(fd_, (sockaddr*)&a, sizeof a) != 0) {
        int e = errno; ::close(fd_); fd_ = -1;
        throw std::runtime_error("lisp boundary: bind UDP " + std::to_string(udp_port) + ": " + std::strerror(e));
    }
    timeval tv{0, 200000};                                                 // the stop flag is honoured within 200 ms
    if (::setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv) != 0) {
        int e = errno; ::close(fd_); fd_ = -1;
        throw std::runtime_error(std::string("lisp boundary: SO_RCVTIMEO: ") + std::strerror(e));
    }
    for (auto& w : workers_) { Worker* wp = w.get(); w->th = std::thread([this, wp] { work(*wp); }); }
    th_ = std::thread([this] { loop(); });
}
inline LispBoundary::~LispBoundary() {
    stop();
    for (auto& b : strands_)                                   // strands are never unlinked while running: freed here
        for (Strand* s = b.load(std::memory_order_acquire); s;) { Strand* n = s->next_in_bucket; Job j; while (s->pop(j)) {} delete s->head; delete s; s = n; }
}
inline std::string LispBoundary::call(const std::string& op, const std::string& args) { return configure(op, args); }
inline std::vector<unsigned char> LispBoundary::map_referral(const std::vector<unsigned char>& request, const std::vector<unsigned char>& reply) {
    // As lispers.net 0.643's map-server answers, observed on the running control as a map-server-only DDT authority
    // (tools/make_hw_configs.py lispers-auth): the referral set is the map-server's peers (priority 0, weight 0,
    // m-priority 255, m-weight 0, R); authoritative always;
    //   registered                 -> MS-ACK, TTL 1440, the registered prefix, incomplete only when there are no peers
    //   in a site, not registered  -> MS-NOT-REGISTERED, TTL 1, the requested EID /32
    //   in no site                 -> NOT-AUTHORITATIVE, TTL 0, incomplete; outside every authoritative prefix the
    //                                 requested EID /32 and no referral set; inside one, the negative prefix
    //                                 lisp_find_negative_mask_len computes (the INDEX of the first bit where the EID
    //                                 differs from a site -- one bit shorter than a prefix excluding it; kept for byte
    //                                 parity, FINDINGS-FOR-DINO.md) and the peers
    auto be16 = [](const unsigned char* p) { return uint16_t(p[0] << 8 | p[1]); };
    auto alen = [](int afi) { return afi == 1 ? 4 : afi == 2 ? 16 : 0; };
    size_t o = 12; o += 2 + alen(be16(request.data() + o)); int irc = (request[1] & 0x1f) + 1;
    for (int i = 0; i < irc; ++i) o += 2 + alen(be16(request.data() + o));
    if (request.size() < o + 8 || be16(request.data() + o + 2) != 1) return {};
    uint32_t target = uint32_t(request[o + 4]) << 24 | uint32_t(request[o + 5]) << 16 | uint32_t(request[o + 6]) << 8 | request[o + 7];
    auto g = ebr_.guard();
    const std::vector<std::string>* peers = ms_peers_.load(std::memory_order_acquire);
    const bool have_peers = peers && !peers->empty();
    int action, ttl; bool incomplete, with_peers; uint32_t eid; int mask;
    const bool positive = reply.size() > 16 && reply[16] > 0;
    const uint32_t reply_ttl = reply.size() >= 16 ? uint32_t(reply[12]) << 24 | uint32_t(reply[13]) << 16 | uint32_t(reply[14]) << 8 | reply[15] : 0;
    if (positive) {
        action = 2; ttl = 1440; incomplete = !have_peers; with_peers = true;
        eid = uint32_t(reply[24]) << 24 | uint32_t(reply[25]) << 16 | uint32_t(reply[26]) << 8 | reply[27]; mask = reply[17];
    } else if (reply_ttl == 1) {
        action = 3; ttl = 1; incomplete = false; with_peers = true; eid = target; mask = 32;
    } else {
        const std::vector<std::string>* auth = auth_prefixes_.load(std::memory_order_acquire);
        bool inside = false;
        std::string ts = std::to_string(target >> 24) + "." + std::to_string((target >> 16) & 255) + "." + std::to_string((target >> 8) & 255) + "." + std::to_string(target & 255) + "/32";
        if (auth) for (auto& ap : *auth) { Prefix a = prefix_parse(ap), t = prefix_parse(ts); if (a.family == t.family && contains(a, t)) inside = true; }
        action = 5; ttl = 0; incomplete = true;
        if (!inside) { with_peers = false; eid = target; mask = 32; }
        else {
            with_peers = true; mask = 0;
            for (auto& sp : req_e_.site_prefixes("0")) {
                Prefix p = prefix_parse(sp);
                if (p.family != AF_INET) continue;
                uint32_t sa = uint32_t(p.a[0]) << 24 | uint32_t(p.a[1]) << 16 | uint32_t(p.a[2]) << 8 | p.a[3], diff = sa ^ target;
                int m = 0; for (; m < 32; ++m) if (diff & (1u << (31 - m))) break;
                if (m > mask) mask = m;
            }
            eid = mask ? target & (0xffffffffu << (32 - mask)) : 0;
        }
    }
    std::vector<std::array<unsigned char, 4>> set;
    if (with_peers && have_peers) for (auto& pa : *peers) { std::array<unsigned char, 4> ad{}; if (inet_pton(AF_INET, pa.c_str(), ad.data()) == 1) set.push_back(ad); }
    std::vector<unsigned char> r = {0x60, 0, 0, 1};
    r.insert(r.end(), request.begin() + 4, request.begin() + 12);       // nonce, as it came
    auto p32 = [&](uint32_t v) { for (int k = 3; k >= 0; --k) r.push_back(uint8_t(v >> (8 * k))); };
    auto p16 = [&](uint16_t v) { r.push_back(uint8_t(v >> 8)); r.push_back(uint8_t(v)); };
    p32(uint32_t(ttl)); r.push_back(uint8_t(set.size())); r.push_back(uint8_t(mask));
    p16(uint16_t(action << 13 | 0x1000 | (incomplete ? 0x0800 : 0))); p16(0); p16(1); p32(eid);
    for (auto& ad : set) { r.push_back(0); r.push_back(0); r.push_back(255); r.push_back(0); p16(1); p16(1); r.insert(r.end(), ad.begin(), ad.end()); }
    return r;
}
// [VENDOR_API_V1] Configuration that the Map-Server's decisions depend on -- the sites and their keys -- goes to the
// LISP region's own API, so the RAM host's Engines, which now decide every Map-Register, hold it. Anything else is the
// configuration Engine's own. One configuration caller at a time, by contract.
inline std::string LispBoundary::configure(const std::string& op, const std::string& args_json) {
    if (op == "site.add" || op == "site.delete" || op == "ms.encryption_key") return cfg_e_.remote_call(op, args_json);
    if (op == "ms.peer") {                                 // [DDT_REFERRAL_V1] a map-server peer (lisp map-server-peer)
        Json a = Json::parse(args_json); std::string ad = a["address"].s; unsigned char z[4];
        if (inet_pton(AF_INET, ad.c_str(), z) != 1) throw std::runtime_error("ms.peer: an IPv4 address");
        const std::vector<std::string>* old = ms_peers_.load(std::memory_order_acquire);
        auto* next = new std::vector<std::string>(old ? *old : std::vector<std::string>{}); next->push_back(ad);
        ms_peers_.store(next, std::memory_order_release); ebr_.retire(old);
        return "\"good\"";
    }
    if (op == "front.ddt_authority") { ddt_authority_ = Json::parse(args_json)["enabled"].b; return "\"good\""; }
    if (op == "ms.authoritative_prefix") {                 // [DDT_REFERRAL_V1] IPv4, instance 0 (lispers.net's common case)
        Json a = Json::parse(args_json); prefix_parse(a["prefix"].s);
        const std::vector<std::string>* old = auth_prefixes_.load(std::memory_order_acquire);
        auto* next = new std::vector<std::string>(old ? *old : std::vector<std::string>{}); next->push_back(a["prefix"].s);
        auth_prefixes_.store(next, std::memory_order_release); ebr_.retire(old);
        return "\"good\"";
    }
    return cfg_e_.call(op, Json::parse(args_json));
}
inline void LispBoundary::stop() {
    stop_ = true;
    if (th_.joinable()) th_.join();                         // no receive, so no new strand
    for (uint32_t n; (n = strands_running_.load(std::memory_order_seq_cst)) != 0;)
        ::syscall(SYS_futex, reinterpret_cast<uint32_t*>(&strands_running_), FUTEX_WAIT_PRIVATE, n, nullptr, nullptr, 0);
    for (auto& w : workers_) w->wake();
    for (auto& w : workers_) if (w->th.joinable()) w->th.join();
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
}
inline void LispBoundary::loop() {
    std::vector<unsigned char> buf(65535);
    while (!stop_) {
        sockaddr_in from{}; socklen_t fl = sizeof from;
        ssize_t n = ::recvfrom(fd_, buf.data(), buf.size(), 0, (sockaddr*)&from, &fl);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue;   // the 200 ms stop-flag tick
            throw std::runtime_error(std::string("lisp boundary: recvfrom: ") + std::strerror(errno));   // the socket itself failed: stop
        }
        char pa[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &from.sin_addr, pa, sizeof pa);
        const std::vector<unsigned char> pkt(buf.begin(), buf.begin() + n);
        const int type = n > 0 ? pkt[0] >> 4 : -1;
        // One datagram from the Internet. What it causes is answered or it is REJECTED -- logged with peer, size,
        // type and exception, and counted -- and the next datagram is served: a malformed packet from one sender must
        // not stop the boundary for every other sender. [BOUNDARY_NEVER_WAITS_V1] Nor may a slow one.
        if (type == 3) {
            ++registers_;
            // [REGISTER_STRANDS_V1] the sender's own strand; started if it is not running
            Strand* s = strand_for((uint64_t(from.sin_addr.s_addr) << 16) | from.sin_port);
            s->push(Job{pkt, from, fl, {}});
            bool idle = false;
            if (s->running.compare_exchange_strong(idle, true, std::memory_order_acq_rel)) {
                strands_running_.fetch_add(1, std::memory_order_seq_cst);
                std::thread([this, s] { run_strand(s); }).detach();
            }
            continue;
        }
        try {
            std::vector<unsigned char> answer;
            if (type == 7) {
                // [INFO_REQUEST_V1] NAT traversal: an Info-Request (type 7, R clear) is answered with an Info-Reply
                // telling the sender the address and port its request arrived from -- what lispers.net's map-server
                // does (lisp_process_info_request; encoding lisp_info.encode): first long with R set, the same nonce,
                // three zero longwords, then a NAT-traversal LCAF (type 7): MS port 0, ETR port = the source port,
                // global ETR RLOC = the source address, the private ETR RLOC (the hostname the request carried, AFI
                // 17, or none), and an empty RTR list. The P5 surface inventory found the entry point never driven.
                if (pkt.size() < 4 + 8 + 12 + 2 || (pkt[0] & 0x08)) throw std::runtime_error("Info-Request: truncated, or a reply");
                std::string host; uint16_t afi = uint16_t(pkt[24] << 8 | pkt[25]);
                if (afi == 17) { for (size_t k = 26; k < pkt.size() && pkt[k]; ++k) host += char(pkt[k]); }
                else if (afi != 0) throw std::runtime_error("Info-Request: unsupported EID AFI " + std::to_string(afi));
                std::vector<unsigned char> r = {0x78, 0, 0, 0};
                r.insert(r.end(), pkt.begin() + 4, pkt.begin() + 12);                 // nonce
                r.insert(r.end(), 12, 0);                                           // key-id/auth-len, TTL, mask/AFI
                auto p16 = [&](uint16_t v) { r.push_back(uint8_t(v >> 8)); r.push_back(uint8_t(v)); };
                p16(16387); p16(0); r.push_back(7); r.push_back(0); p16(16);         // LCAF NAT-traversal
                p16(0); p16(ntohs(from.sin_port)); p16(1);                          // MS port, ETR port, global AFI
                const unsigned char* ga = reinterpret_cast<const unsigned char*>(&from.sin_addr.s_addr); r.insert(r.end(), ga, ga + 4);
                p16(0);
                if (!host.empty()) { p16(17); r.insert(r.end(), host.begin(), host.end()); r.push_back(0); } else p16(0);
                p16(0);                                                             // no RTRs
                ++requests_;
                if (::sendto(fd_, r.data(), r.size(), 0, (sockaddr*)&from, fl) != (ssize_t)r.size())
                    throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
                continue;
            }
            if (type == 8) {
                // [ECM_MAP_REQUEST_V1] An ITR reaches a map-resolver with its Map-Request inside an Encapsulated
                // Control Message (RFC 9301 5.8): 4-byte ECM header, inner IPv4 header, inner UDP header, then the
                // Map-Request. lispers.net takes nothing else on a map-resolver. Strip it, answer the Map-Request, and
                // send the Map-Reply to the inner UDP source port, as the ITR expects.
                // the inner header is IPv4 (IHL words) or IPv6 (40 bytes, next header UDP) -- the EID's family
                const int ver = pkt.size() > 4 ? pkt[4] >> 4 : 0;
                const size_t ihl = ver == 4 ? size_t(pkt[4] & 0x0f) * 4 : ver == 6 ? 40 : 0;
                if (ihl < 20 || pkt.size() < 4 + ihl + 8 + 4 || (ver == 6 && pkt[4 + 6] != 17))
                    throw std::runtime_error("ECM: not an IPv4/IPv6 UDP-encapsulated Map-Request");
                const uint16_t sport = uint16_t(pkt[4 + ihl] << 8 | pkt[4 + ihl + 1]);
                std::vector<unsigned char> inner(pkt.begin() + 4 + ihl + 8, pkt.end());
                if ((inner[0] >> 4) != 1) throw std::runtime_error("ECM: inner message is not a Map-Request");
                ++requests_;
                answer = unhex(Json::parse(req_e_.call(request_eid_afi(inner) == 2 ? "wire.request6" : "wire.request4",
                                                        Json::parse("{\"hex\":" + Json::quote(hex(inner)) + "}"))).s);
                sockaddr_in to = from; to.sin_port = htons(sport);
                if (!answer.empty() && ::sendto(fd_, answer.data(), answer.size(), 0, (sockaddr*)&to, fl) != (ssize_t)answer.size())
                    throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
                if ((pkt[0] & 0x04) && ddt_authority_) {                  // [DDT_REFERRAL_V1] D bit: DDT-originated
                    auto ref = map_referral(inner, answer);
                    sockaddr_in rto = from; rto.sin_port = htons(4342);      // lispers.net: to the requester's 4342
                    if (!ref.empty() && ::sendto(fd_, ref.data(), ref.size(), 0, (sockaddr*)&rto, fl) != (ssize_t)ref.size())
                        throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
                }
                continue;
            }
            if (type == 1) {
                ++requests_;
                answer = unhex(Json::parse(req_e_.call("wire.request4", Json::parse("{\"hex\":" + Json::quote(hex(pkt)) + "}"))).s);
            } else {
                throw std::runtime_error("unsupported LISP message type " + std::to_string(type));
            }
            if (!answer.empty() && ::sendto(fd_, answer.data(), answer.size(), 0, (sockaddr*)&from, fl) != (ssize_t)answer.size())
                throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
        } catch (const std::exception& x) { reject(from, n, type, x.what()); }
    }
}
// The first EID record's AFI of a Map-Request (RFC 9301 5.3): after the 12-byte header, the source EID (AFI + address)
// and the ITR-RLOCs (count + 1, each AFI + address), then a record: reserved, mask length, AFI.
inline int LispBoundary::request_eid_afi(const std::vector<unsigned char>& m) {
    auto alen = [](int afi) { return afi == 1 ? 4 : afi == 2 ? 16 : 0; };
    if (m.size() < 14) return 0;
    size_t o = 12; int safi = m[o] << 8 | m[o + 1]; o += 2 + alen(safi);
    int irc = (m[1] & 0x1f) + 1;
    for (int i = 0; i < irc; ++i) { if (m.size() < o + 2) return 0; int afi = m[o] << 8 | m[o + 1]; o += 2 + alen(afi); }
    if (m.size() < o + 4) return 0;
    int afi = m[o + 2] << 8 | m[o + 3];
    if (afi == 16387 && m.size() >= o + 2 + 14) afi = m[o + 2 + 12] << 8 | m[o + 2 + 13];   // LCAF instance-id: the inner AFI
    return afi;
}
inline void LispBoundary::reject(const sockaddr_in& from, ssize_t n, int type, const std::string& why) {
    ++rejected_;
    char pa[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &from.sin_addr, pa, sizeof pa);
    std::cerr << "[LISP-BOUNDARY] REJECTED peer=" << pa << ":" << ntohs(from.sin_port) << " size=" << n << " type=" << type
              << " err=" << why << "\n" << std::flush;
}
inline LispBoundary::Strand* LispBoundary::strand_for(uint64_t key) {
    auto& b = strands_[(key * 0x9e3779b97f4a7c15ull >> 32) % STRAND_BUCKETS];
    for (;;) {
        Strand* head = b.load(std::memory_order_acquire);
        for (Strand* s = head; s; s = s->next_in_bucket) if (s->key == key) return s;
        Strand* fresh = new Strand; fresh->key = key; fresh->next_in_bucket = head;
        if (b.compare_exchange_strong(head, fresh, std::memory_order_acq_rel)) return fresh;
        delete fresh->head; delete fresh;                    // another receive made it first: look again
    }
}
inline void LispBoundary::run_strand(Strand* s) {
    for (;;) {
        Job j;
        while (s->pop(j)) answer_register(reg_e_, j);
        s->running.store(false, std::memory_order_seq_cst);
        bool idle = false;                                   // a register pushed while we were stopping is ours to answer
        if (!s->pending() || !s->running.compare_exchange_strong(idle, true, std::memory_order_acq_rel)) break;
    }
    if (strands_running_.fetch_sub(1, std::memory_order_seq_cst) == 1)
        ::syscall(SYS_futex, reinterpret_cast<uint32_t*>(&strands_running_), FUTEX_WAKE_PRIVATE, INT32_MAX, nullptr, nullptr, 0);
}
inline void LispBoundary::answer_register(Engine& e, const Job& j) {
    // [VENDOR_API_V1] the whole Map-Register -- authentication, site policy, the registration, its governance, the
    // Map-Notify -- is ONE call to the LISP region's API; the RAM server does it in the memory itself
    char pa[INET_ADDRSTRLEN]; inet_ntop(AF_INET, &j.from.sin_addr, pa, sizeof pa);
    try {
        Json r = Json::parse(e.remote_call("wire.register_notify", "{\"hex\":" + Json::quote(hex(j.pkt)) +
                                                                  ",\"source\":" + Json::quote(pa) + "}"));
        if (!r["notify_hex"].s.empty()) {
            auto answer = unhex(r["notify_hex"].s);
            if (::sendto(fd_, answer.data(), answer.size(), 0, (sockaddr*)&j.from, j.fl) != (ssize_t)answer.size())
                throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
        }
    } catch (const std::exception& x) { reject(j.from, (ssize_t)j.pkt.size(), 3, x.what()); }
}
inline void LispBoundary::work(Worker& w) {
    for (;;) {
        Job j;
        for (;;) {
            const uint32_t seen = w.signal.load(std::memory_order_acquire);
            if (w.pop(j)) break;
            if (stop_) return;                              // stopping, nothing left
            w.sleep_unless_changed(seen);                  // returns at once if anything was appended since `seen`
        }
        if (j.cfg) { j.cfg(*w.e); continue; }
        answer_register(*w.e, j);
    }
}

}  // namespace frognet
