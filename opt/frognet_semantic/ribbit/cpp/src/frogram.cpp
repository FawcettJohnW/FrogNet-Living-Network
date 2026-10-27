// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "frogram.hpp"
#include "fnwp_engine.hpp"
#include "rowcodec.hpp"
#include "cellwire.hpp"
#include "dataplane.hpp"

#ifdef _WIN32
#  ifndef _WIN32_WINNT
#    define _WIN32_WINNT 0x0600
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
   typedef int ssize_t_fr;
#  define FR_CLOSE(fd)      closesocket(fd)
#  define FR_POLL           WSAPoll
#  define FR_SHUT_RDWR      SD_BOTH
#  define FR_NOSIGNAL       0
   typedef char fr_optval_t;
#else
#  include <arpa/inet.h>
#  include <netdb.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
   typedef ssize_t ssize_t_fr;
#  define FR_CLOSE(fd)      ::close(fd)
#  define FR_POLL           ::poll
#  define FR_SHUT_RDWR      SHUT_RDWR
#  ifdef MSG_NOSIGNAL
#    define FR_NOSIGNAL     MSG_NOSIGNAL
#  else
#    define FR_NOSIGNAL     0
#  endif
   typedef int fr_optval_t;
#endif

#include <chrono>
#include <cstring>
#include <list>
#include <random>
#include <fstream>
#include <sstream>

namespace frogram {
namespace {

const char MAGIC[] = "FNW1";
// [READS_AFTER_IN_BINARY_V1] the memory's read verb for "what was written after id N" (ram_host.cpp mem_read_frame)
constexpr uint8_t OP_MEM_READ = 0x7E;
// [FOLLOW_STREAM_V1] a standing trigger: one request, then rows as they are written, for as long as the session lives.
// Request as OP_MEM_READ (the wait and row fields unused); every answer is MAGIC, this op, rows as OP_MEM_READ's.
constexpr uint8_t OP_MEM_FOLLOW = 0x7D;
enum : uint8_t { REQ_REPEAT = 0x02, REQ_RAW = 0x03, RESP_SAME = 0x13, RESP_RAW = 0x14,
                 REQ_MISS = 0x21, OP_ERROR = 0x30, OP_HELLO = 0x50 };

#ifdef _WIN32
struct WsaOnce { WsaOnce() { WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w) != 0) throw Unreachable("WSAStartup failed"); } };
#endif

int dial(const std::string& host, int port) {
#ifdef _WIN32
    static WsaOnce wsa_once;
#endif
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM;
    int gai = getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res);
    if (gai != 0 || !res)                                  // the resolver's own reason, not a paraphrase
        throw Unreachable(host + ": name does not resolve (" + (gai ? gai_strerror(gai) : "no address") + ")");
    int fd = -1;
    for (addrinfo* a = res; a; a = a->ai_next) {
        fd = int(::socket(a->ai_family, a->ai_socktype, a->ai_protocol));
        if (fd < 0) continue;
        if (::connect(fd, a->ai_addr, int(a->ai_addrlen)) == 0) break;
        FR_CLOSE(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) throw Unreachable(host + ":" + std::to_string(port) + " connect failed");
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    { int ka = 1, idle = 2, intvl = 1, cnt = 3, uto = 5000; setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, reinterpret_cast<const fr_optval_t*>(&ka), sizeof ka);
#ifdef TCP_KEEPIDLE
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, reinterpret_cast<const fr_optval_t*>(&idle), sizeof idle);
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, reinterpret_cast<const fr_optval_t*>(&intvl), sizeof intvl);
      setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, reinterpret_cast<const fr_optval_t*>(&cnt), sizeof cnt);
#ifdef TCP_USER_TIMEOUT
      setsockopt(fd, IPPROTO_TCP, TCP_USER_TIMEOUT, reinterpret_cast<const fr_optval_t*>(&uto), sizeof uto);
#endif
#endif
    }   // [KEEPALIVE_5S_V1] (was [KEEPALIVE_20S_V1])
    return fd;
}
void send_all(int fd, const std::string& b) {
    size_t off = 0;
    while (off < b.size()) {
        ssize_t_fr n = ::send(fd, b.data() + off, int(b.size() - off), FR_NOSIGNAL);
        if (n <= 0) throw Unreachable("send failed");
        off += size_t(n);
    }
}
std::string recv_n(int fd, size_t n) {
    std::string d(n, '\0'); size_t off = 0;
    while (off < n) {
        ssize_t_fr r = ::recv(fd, &d[off], int(n - off), 0);
        if (r == 0) throw Unreachable("the far end closed the connection");
#ifdef _WIN32
        if (r < 0) throw Unreachable("recv failed (WSA error " + std::to_string(WSAGetLastError()) + ")");
#else
        if (r < 0) { int e = errno; throw Unreachable(std::string("recv: ") + strerror(e) +
            (e == ETIMEDOUT ? " (no answer from the far end within the 5 s keepalive / user timeout)" : "")); }   // [RECV_SAYS_WHY_V1]
#endif
        off += size_t(r);
    }
    return d;
}
std::string be32(uint32_t v) { uint32_t n = htonl(v); return std::string(reinterpret_cast<char*>(&n), 4); }
std::string be16(uint16_t v) { uint16_t n = htons(v); return std::string(reinterpret_cast<char*>(&n), 2); }
std::string be64(uint64_t v) { std::string s(8, '\0'); for (int i = 7; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
uint32_t rd32(const std::string& s, size_t o) { uint32_t n; std::memcpy(&n, s.data() + o, 4); return ntohl(n); }
uint16_t rd16(const std::string& s, size_t o) { uint16_t n; std::memcpy(&n, s.data() + o, 2); return ntohs(n); }
uint64_t rd64(const std::string& s, size_t o) { uint64_t v = 0; for (int i = 0; i < 8; ++i) v = (v << 8) | uint8_t(s[o + i]); return v; }
void send_frame(int fd, const std::string& f) { send_all(fd, be32(uint32_t(f.size())) + f); }
std::string recv_frame(int fd) { return recv_n(fd, rd32(recv_n(fd, 4), 0)); }
std::string hello(const std::string& t) { return std::string(MAGIC, 4) + char(OP_HELLO) + char(t.size()) + t; }

std::string urlenc(const std::string& s) {
    static const char* hex = "0123456789ABCDEF"; std::string o;
    for (unsigned char c : s) {
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') o += char(c);
        else { o += '%'; o += hex[c >> 4]; o += hex[c & 15]; }
    }
    return o;
}
}  // namespace

// ---------------------------------------------------------------- Json
const Json& Json::operator[](const std::string& k) const {
    static const Json none;
    for (auto& kv : o) if (kv.first == k) return kv.second;
    return none;
}
std::string Json::quote_ascii(const std::string& s) { std::string o; semtpl::detail::dumps_ascii_str(o, s); return o; }   // [CANONICAL_BODY_V1]
void Json::quote_ascii_into(std::string& out, std::string_view s) { semtpl::detail::dumps_ascii_str(out, s); }
std::string Json::quote(const std::string& s) {
    // [JSON_QUOTE_RUNS_V1] runs that need no escape are appended whole; one allocation for the common case
    std::string o; o.reserve(s.size() + 2); o += '"';
    const char* p = s.data(); const char* e = p + s.size();
    while (p < e) {
        const char* r = p;
        while (r < e) { const unsigned char c = uint8_t(*r); if (c == '"' || c == '\\' || c < 0x20) break; ++r; }
        o.append(p, size_t(r - p)); p = r;
        if (p >= e) break;
        const unsigned char c = uint8_t(*p++);
        if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r"; else if (c == '\t') o += "\\t";
        else { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    }
    o += '"';
    return o;
}
namespace {
struct P {
    const std::string& t; size_t i;
    explicit P(const std::string& text) : t(text), i(0) {}
    void ws() { while (i < t.size() && isspace(uint8_t(t[i]))) ++i; }
    [[noreturn]] void bad(const char* w) { throw Refused(std::string("bad JSON: ") + w); }
    Json val() {
        ws(); if (i >= t.size()) bad("eof");
        Json j; char c = t[i];
        if (c == '{') { j.type = Json::Obj; ++i; ws(); if (t[i] == '}') { ++i; return j; }
            for (;;) { ws(); Json k = val(); if (k.type != Json::Str) bad("key"); ws(); if (t[i++] != ':') bad(":");
                       j.o.emplace_back(std::move(k.s), val()); ws(); if (t[i] == ',') { ++i; continue; } if (t[i++] == '}') return j; bad("}"); } }
        if (c == '[') { j.type = Json::Arr; ++i; ws(); if (t[i] == ']') { ++i; return j; }
            for (;;) { j.a.push_back(val()); ws(); if (t[i] == ',') { ++i; continue; } if (t[i++] == ']') return j; bad("]"); } }
        if (c == '"') { j.type = Json::Str; ++i;
            while (i < t.size() && t[i] != '"') {
                if (t[i] == '\\') { char e = t[++i];
                    if (e == 'n') j.s += '\n'; else if (e == 't') j.s += '\t'; else if (e == 'r') j.s += '\r';
                    else if (e == 'b') j.s += '\b'; else if (e == 'f') j.s += '\f';
                    else if (e == 'u') { unsigned cp = std::stoul(t.substr(i + 1, 4), nullptr, 16); i += 4;
                        // [JSON_SURROGATE_PAIRS_V1] \ud83d\ude00 is ONE character (U+1F600, four bytes of UTF-8): the
                        // semantic codec writes every character past U+FFFF as such a pair (json.dumps, ensure_ascii).
                        // Read as two, it became six bytes of CESU-8 -- a publish's payload changed on its way through
                        // the memory (measured: 8-30 of 200,000 messages across two boxes, every one with a 4-byte
                        // sequence in its timestamp bytes).
                        if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 < t.size() && t[i + 1] == '\\' && t[i + 2] == 'u') {
                            const unsigned lo = std::stoul(t.substr(i + 3, 4), nullptr, 16);
                            if (lo >= 0xDC00 && lo <= 0xDFFF) { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); i += 6; }
                        }
                        if (cp < 0x80) j.s += char(cp);
                        else if (cp < 0x800) { j.s += char(0xC0 | (cp >> 6)); j.s += char(0x80 | (cp & 0x3F)); }
                        else if (cp < 0x10000) { j.s += char(0xE0 | (cp >> 12)); j.s += char(0x80 | ((cp >> 6) & 0x3F)); j.s += char(0x80 | (cp & 0x3F)); }
                        else { j.s += char(0xF0 | (cp >> 18)); j.s += char(0x80 | ((cp >> 12) & 0x3F)); j.s += char(0x80 | ((cp >> 6) & 0x3F)); j.s += char(0x80 | (cp & 0x3F)); } }
                    else j.s += e;
                    ++i; }
                else { size_t k = i; while (k < t.size() && t[k] != '"' && t[k] != '\\') ++k; j.s.append(t, i, k - i); i = k; } }   // [JSON_PARSE_LINEAR_V1] a run at once
            ++i; return j; }
        if (!t.compare(i, 4, "true")) { i += 4; j.type = Json::Bool; j.b = true; return j; }
        if (!t.compare(i, 5, "false")) { i += 5; j.type = Json::Bool; return j; }
        if (!t.compare(i, 4, "null")) { i += 4; return j; }
        // [JSON_PARSE_LINEAR_V1] the number read in place: it took a copy of the rest of the text for every number
        // (t.substr(i)), quadratic in the body -- 60% of the memory host's time on a batch of writes, measured
        j.type = Json::Num; const char* b0 = t.c_str() + i; char* e0 = nullptr;
        j.n = std::strtod(b0, &e0); if (e0 == b0) bad("number");
        i += size_t(e0 - b0); return j;
    }
};
}  // namespace
Json Json::parse(const std::string& text) { P p(text); return p.val(); }

// ---------------------------------------------------------------- Session
// [ENGINES_IN_THE_SESSION_V1] One sender thread prepares and sends every frame, so frames leave in the order their
// request references were committed. The return-socket reader applies replies in the order they arrive -- wire order,
// as the Python proxy does. [DATA_MARKER_V1] A reply that arrives as a marker is applied on its own thread once its
// payload has landed on the data socket; the engine holds only LATER replies to that same instance until then.
// A caller waits only for its own answer.
// [SESSION_TRACE_V1] FROGRAM_TRACE=1: every frame out and in, with its sequence, op and request, on stderr.
static const bool TRACE = std::getenv("FROGRAM_TRACE") != nullptr;
// The value is "1" for stderr, or a file path to append to (stderr may be a pipe nobody drains).
static void trace(const std::string& m) {
    if (!TRACE) return;
    static std::mutex tm; static std::ofstream tf; static bool opened = false;
    std::lock_guard<std::mutex> g(tm);
    const std::string v = std::getenv("FROGRAM_TRACE");
    if (!opened) { opened = true; if (v != "1") tf.open(v, std::ios::app); }
    std::ostream& o = tf.is_open() ? static_cast<std::ostream&>(tf) : std::cerr;
    o << "[frogram " << std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count() << "] " << m << "\n" << std::flush;
}
}  // namespace frogram (reopened below)
#include <atomic>
#include "ebr.hpp"
#ifdef _WIN32                                   // [FUTEX_PORTABLE_V1]
#include <windows.h>
#else
#include <linux/futex.h>
#include <sys/syscall.h>
#endif
#include <climits>
namespace frogram {
// [CLIENT_LOCKLESS_V1] a futex wait on a 32-bit word, with a timeout (std::atomic::wait has none)
static inline bool futex_wait_for(std::atomic<uint32_t>& w, uint32_t expected, double timeout_s) {
    if (timeout_s > 1e9) timeout_s = 1e9;
    timespec ts; ts.tv_sec = time_t(timeout_s); ts.tv_nsec = long((timeout_s - double(ts.tv_sec)) * 1e9);
#ifdef _WIN32
    (void)ts; const DWORD ms = timeout_s >= 4.0e6 ? INFINITE : DWORD(timeout_s * 1000.0);
    return WaitOnAddress(reinterpret_cast<volatile void*>(&w), &expected, sizeof expected, ms) || GetLastError() != ERROR_TIMEOUT;
#else
    long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, expected, &ts, nullptr, 0);
    return !(r == -1 && errno == ETIMEDOUT);
#endif
}
// [FUTEX_PORTABLE_V1] an untimed wait: sleeps while *w == expected (spurious wakes are the caller's loop's to handle)
static inline void fr_futex_wait(std::atomic<uint32_t>* w, uint32_t expected) {
#ifdef _WIN32
    WaitOnAddress(reinterpret_cast<volatile void*>(w), &expected, sizeof expected, INFINITE);
#else
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(w), FUTEX_WAIT_PRIVATE, expected, nullptr, nullptr, 0);
#endif
}
static inline void futex_wake_all(std::atomic<uint32_t>& w) {
#ifdef _WIN32
    WakeByAddressAll(reinterpret_cast<void*>(&w));
#else
    syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0);
#endif
}
struct Session::Pending { std::atomic<uint32_t> done{0}; fnwp::Answer answer; std::string err;   // [CLIENT_LOCKLESS_V1]
                          Session::Done cb;                                       // [NOBODY_WAITS_V1] instead of a waiter
                          // [NOBODY_WAITS_V1] [CLIENT_COALESCE_LOCKLESS_V1] async callers that joined this request in flight:
                          // a lock-free stack, sealed when the answer is handed out (a join after that is its own request)
                          struct Joined { Session::Done cb; Joined* next = nullptr; };
                          std::atomic<Joined*> joined{nullptr};
                          Session::Impl* co = nullptr; std::string co_key; const void* co_entry = nullptr;
                          std::shared_ptr<const std::vector<RawCell>> rows; };   // [DECODE_ONCE_V1] a read's rows, decoded once   // [CLIENT_COALESCES_V1] its entry, if any
struct Session::InFlight { std::shared_ptr<fnwp::ClientEngine::Ctx> ctx; std::shared_ptr<Pending> waiter;
                           uint64_t ticket = 0; bool in_turn = false;
                           std::shared_ptr<std::function<void(std::string, std::string)>> hdone;   // the memory's own frame's completion (a read after an id, a trigger)
                           uint32_t seq = 0;                              // [IN_FLIGHT_BY_SEQ_V1] where its reply is found
                           uint8_t hop = 0; };                            // a frame's op, which its answer carries   // [WIRE_ORDER_TURNS_V1]
struct Session::Impl {
    std::unique_ptr<fnwp::ClientEngine> eng;
    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false; int redo = 0;
                 uint64_t ticket = 0; bool in_turn = false;      // [WIRE_ORDER_TURNS_V1] a resend made in its call's turn
                 std::string hframe; std::shared_ptr<std::function<void(std::string, std::string)>> hdone;   // the memory's own frame, as built
                 bool mw = false, stream = false, noreply = false; std::string ws, wv, wi, wbag;    // a non-waiting write: its coordinates and bag; stream: [FOLLOW_STREAM_V1]
                 uint8_t hop = 0; };                              // the op a frame's answer must carry
    // [WIRE_ORDER_TURNS_V1] per templated instance: tickets stamped (arrival order) and applied; replies held for their turn
    std::map<fnwp::detail::IKey, uint64_t> issued, applied;   // [CLIENT_APPLIER_V1] the applier's alone (no gate_m)
    // [CLIENT_APPLIER_V1] the applier's queue: kind 0 a reply as it arrived, 1 a marker (its payload is landing),
    // 2 a marker's full reply once landed
    struct ApplyNode { std::atomic<ApplyNode*> next{nullptr}; int kind = 0; uint32_t seq = 0; Session::InFlight inf; std::string reply; };
    struct ApplyList {
        ApplyNode stub; std::atomic<ApplyNode*> head{&stub}; ApplyNode* tail = &stub;
        void push(ApplyNode* n) { n->next.store(nullptr, std::memory_order_relaxed); ApplyNode* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
        ApplyNode* pop() {
            ApplyNode* t = tail; ApplyNode* next = t->next.load(std::memory_order_acquire);
            if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
            if (next) { tail = next; return t; }
            if (t != head.load(std::memory_order_acquire)) return nullptr;
            stub.next.store(nullptr, std::memory_order_relaxed); ApplyNode* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
            next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
            return nullptr;
        }
    } applyq;
    std::atomic<uint32_t> apply_work{0}; std::atomic<bool> apply_stop{false}; std::thread applier;
    void to_applier(int kind, const Session::InFlight& inf, std::string reply, uint32_t seq = 0) {
        ApplyNode* n = new ApplyNode; n->kind = kind; n->seq = seq; n->inf = inf; n->reply = std::move(reply); applyq.push(n);
        apply_work.fetch_add(1, std::memory_order_release); futex_wake_all(apply_work);
    }
    std::set<fnwp::detail::IKey> landing;                // instances whose RAW answer is still landing
    std::map<uint32_t, Session::InFlight> marked;        // a marker's call, stamped at arrival, by sequence number
    std::map<fnwp::detail::IKey, std::vector<std::pair<Session::InFlight, std::string>>> after_landing;
    std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<Session::InFlight, std::string>>> held;
    // [CLIENT_LOCKLESS_V1] fan-in: two lock-free MPSC lists (resends first), a bounded count, a sleep word for the sender
    struct JobNode { std::atomic<JobNode*> next{nullptr}; Job j; };
    struct JobList {
        JobNode stub; std::atomic<JobNode*> head{&stub}; JobNode* tail = &stub;
        void push(JobNode* n) { n->next.store(nullptr, std::memory_order_relaxed); JobNode* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
        JobNode* pop() {                                   // the sender only
            JobNode* t = tail; JobNode* next = t->next.load(std::memory_order_acquire);
            if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
            if (next) { tail = next; return t; }
            if (t != head.load(std::memory_order_acquire)) return nullptr;
            stub.next.store(nullptr, std::memory_order_relaxed); JobNode* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
            next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
            return nullptr;
        }
    } jobq, resendq;
    std::atomic<uint32_t> work{0};                       // bumped on every push; the sender sleeps on it
    std::atomic<int> sender_sleeping{0};                 // [WAKE_ONLY_A_SLEEPER_V1] set by the sender before it sleeps
    rowcodec::Decoder rowdec;                            // [ROWS_ON_THE_SEMANTIC_WIRE_V1] the reader's only
    rowcodec::RequestEncoder reqenc;                     // [READ_REQUESTS_BY_REFERENCE_V1] the sender's only
    cellwire::Encoder cellenc;                           // [CELL_WRITE_V1] any thread's: each keeps its own references
    std::atomic<uint64_t> c_rows_wire{0}, c_rows_whole{0};   // the rows' bytes as they arrived, and made whole
    std::atomic<uint32_t> queued{0};                     // jobs not yet taken by the sender (QUEUE_MAX)
    std::atomic<bool> stop{false};
    void enqueue(Job&& j, bool resend) {
        JobNode* n = new JobNode; n->j = std::move(j);
        if (!resend) queued.fetch_add(1, std::memory_order_acq_rel);
        (resend ? resendq : jobq).push(n);
        // [WAKE_ONLY_A_SLEEPER_V1] measured: a futex wake per write was 8% of a box's CPU while the sender was already awake
        work.fetch_add(1, std::memory_order_seq_cst); if (sender_sleeping.load(std::memory_order_seq_cst)) futex_wake_all(work);
    }
    using InFlight = Session::InFlight;
    // [CLIENT_LOCKLESS_V1] in flight: a ring indexed by sequence number; the sender stores, the reader takes
    static constexpr uint32_t RING = 1u << 16;
    std::unique_ptr<std::atomic<InFlight*>[]> ring{new std::atomic<InFlight*>[RING]};
    std::atomic<uint32_t> ring_free{0};                  // bumped when a slot is taken; a sender waiting for its slot sleeps on it
    // [IN_FLIGHT_BY_SEQ_V1] A slot is not its sequence number modulo the ring: a request that stays unanswered (a held
    // read with nothing to deliver) would own its slot when the sequence came round again 65,536 requests later, and the
    // sender -- every caller on the wire -- would wait behind it forever. Measured: the box's wire stopped at exactly
    // 65,537 sent, 16 held reads unanswered. A request goes in the first free slot from its home; its reply is found by
    // its sequence number. Only the sender fills a slot; only the reader (and the dead sweep) empties one.
    std::atomic<InFlight*>* ring_put(uint32_t s, InFlight* mine) {
        mine->seq = s;
        for (;;) {
            const uint32_t seen = ring_free.load(std::memory_order_acquire);
            for (uint32_t i = 0; i < RING; ++i) { std::atomic<InFlight*>& sl = ring[(s + i) & (RING - 1)];
                if (!sl.load(std::memory_order_acquire)) { sl.store(mine, std::memory_order_release); return &sl; } }
            if (stop.load()) { return nullptr; }
            fr_futex_wait(&ring_free, seen);             // 65,536 unanswered: wait for any slot to be taken
        }
    }
    // [FOLLOW_STREAM_V1] the standing triggers: a few, each answered many times, so never in the ring
    struct Stream { uint32_t seq; std::shared_ptr<std::function<void(std::string, std::string)>> done; };
    static constexpr int STREAMS = 64;
    std::atomic<Stream*> streams[STREAMS] = {};
    Stream* stream_of(uint32_t s) { for (auto& x : streams) { Stream* p = x.load(std::memory_order_acquire); if (p && p->seq == s) return p; } return nullptr; }
    InFlight* ring_take(uint32_t s) {
        for (uint32_t i = 0; i < RING; ++i) { std::atomic<InFlight*>& sl = ring[(s + i) & (RING - 1)];
            InFlight* p = sl.load(std::memory_order_acquire);
            if (p && p->seq == s && sl.compare_exchange_strong(p, nullptr, std::memory_order_acq_rel)) return p; }
        return nullptr;
    }
    uint32_t seq = 0;                                    // the sender's
    std::atomic<bool> dead_claim{false}, dead_set{false}; std::string dead;   // dead is written once, before dead_set
    std::string dead_why() const { return dead_set.load(std::memory_order_acquire) ? dead : std::string(); }
    // counters: atomics; Stats is assembled from them
    using FrameDone = std::function<void(std::string, std::string)>;
    std::atomic<uint64_t> c_cell_bytes{0}, c_cell_writes{0};   // [CELL_WRITE_V1]
    std::atomic<uint64_t> c_batch_bytes{0};              // bytes of the memory's own frames (a read after an id, a trigger's request)
    std::atomic<uint64_t> c_coalesced{0}, c_posted{0}, c_posts_refused{0}, c_data_frames{0}, c_data_bytes{0};
    std::thread sender, reader, data_reader;
    // [HIGH_SPEED_DATA_SOCKET_V1] a request the wire cannot carry differenced (REQ_RAW) and that would own the request
    // socket longer than the ceiling goes on the data socket in fair segments; replies come back the same way
    fnwp::dataplane::Ceiling ceil; std::unique_ptr<fnwp::dataplane::Sender> dsend; fnwp::dataplane::Reassembler reasm;
    fnwp::dataplane::Landed landed;                      // (request-side Landed bookkeeping stays; the client no longer take()s from it)
    // [CLIENT_LANDING_LOCKLESS_V1] the applier pairs a marker (its wire place) with its payload (its landing), either order
    std::map<uint32_t, std::string> marker_reply, early_payload;
    static const uint32_t QUEUE_MAX = 4096;              // new requests beyond this wait, as TCP would hold them
    // [QUEUE_HYSTERESIS_V1] ...and are woken when the queue is down to half, not at every request taken (measured: a
    // wake per request, sender and writer taking turns one request at a time, was a tenth of a box's CPU)
    // [CLIENT_COALESCES_V1] requests in flight, by the exact request: an identical call waits here for the one answer
    // [CLIENT_COALESCE_LOCKLESS_V1] calls in flight, by the exact request
    struct CoEnt { std::string key; std::shared_ptr<Pending> p; };
    static constexpr size_t CO_N = 4096;
    std::atomic<const CoEnt*> co_slot[CO_N];
    ribbit::Ebr co_ebr;
    static size_t co_home(const std::string& k) { uint64_t h = 1469598103934665603ull; for (unsigned char c : k) { h ^= c; h *= 1099511628211ull; } return size_t(h); }
    void co_remove(const Pending* p) {
        const CoEnt* mine = static_cast<const CoEnt*>(p->co_entry); if (!mine) return;
        const size_t h0 = co_home(mine->key);
        for (size_t i = 0; i < 32; ++i) { const CoEnt* expect = mine; if (co_slot[(h0 + i) & (CO_N - 1)].compare_exchange_strong(expect, nullptr, std::memory_order_acq_rel)) break; }
        co_ebr.retire(mine);
    }
    static Pending::Joined* sealed_joins() { return reinterpret_cast<Pending::Joined*>(uintptr_t(1)); }
    static void finish(const std::shared_ptr<Pending>& p, const fnwp::Answer* a, const std::string& err) {
        if (!p) return;
        if (p->co) {   // out of the table first: a request made after this answer is its own request
            p->co->co_remove(p.get());                    // [CLIENT_COALESCE_LOCKLESS_V1]
        }
        if (p->done.load(std::memory_order_acquire)) return;   // [CLIENT_LOCKLESS_V1] once
        if (a) { p->answer = *a; }
        p->err = err; p->done.store(1, std::memory_order_release); futex_wake_all(p->done);
        Pending::Joined* list = p->joined.exchange(sealed_joins(), std::memory_order_acq_rel);   // [NOBODY_WAITS_V1]
        const int st = err.empty() ? int(p->answer.status) : 0;
        while (list && list != sealed_joins()) { Pending::Joined* nx = list->next; list->cb(st, p->answer.body, err); delete list; list = nx; }
        if (p->cb) { Session::Done cb = std::move(p->cb); cb(st, p->answer.body, err); }
    }
};
Session::Session(const std::string& host, int port, const std::string& api) : host_(host), api_(api), port_(port), d_(new Impl) {
    std::random_device rd; std::ostringstream t; t << "frogram-cpp-" << std::hex << rd() << rd();
    token_ = t.str();
    req_ = dial(host, port);
    // THE BUFFER IS THE LATENCY. A writer that does not wait for acknowledgements can
    // only be held back by TCP, and TCP holds it back when this buffer and the far
    // end's are full. Small buffers keep that backlog to a fraction of a second.
    { int buf = 32 * 1024; setsockopt(req_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&buf), sizeof buf); }
    send_frame(req_, hello(token_));
    ret_ = dial(host, port); send_frame(ret_, hello("RETURN:" + token_));
    std::string f = recv_frame(ret_);
    if (f.size() < 5 || f.compare(0, 4, MAGIC) || uint8_t(f[4]) != OP_HELLO) throw Refused("expected the far end's HELLO");
    dat_ = dial(host, port);
    // THE BUFFER IS THE LATENCY, on the data socket too: the interleaver decides what goes next, and a large kernel
    // buffer would let one transfer's segments queue ahead of every later one anyway. Keep the queue at the wire short.
    { int buf = 32 * 1024; setsockopt(dat_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&buf), sizeof buf); }
    send_frame(dat_, hello("DATA:" + token_));
    f = recv_frame(dat_);
    if (f.size() < 5 || f.compare(0, 4, MAGIC) || uint8_t(f[4]) != OP_HELLO)
        throw Refused("the far end did not accept the data socket (HELLO DATA:) -- it does not speak the full FNWP contract");
    sockaddr_storage me{}; socklen_t ml = sizeof me; char ip[64] = "";
    if (getsockname(req_, reinterpret_cast<sockaddr*>(&me), &ml) == 0 && me.ss_family == AF_INET)
        inet_ntop(AF_INET, &reinterpret_cast<sockaddr_in*>(&me)->sin_addr, ip, sizeof ip);
    d_->eng.reset(new fnwp::ClientEngine(host, ip, api_));
    for (uint32_t i = 0; i < Impl::RING; ++i) d_->ring[i].store(nullptr, std::memory_order_relaxed);   // [CLIENT_LOCKLESS_V1]
    for (auto& x : d_->co_slot) x.store(nullptr, std::memory_order_relaxed);                             // [CLIENT_COALESCE_LOCKLESS_V1]
    { int sb = 0; socklen_t sl = sizeof sb; getsockopt(req_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<fr_optval_t*>(&sb), &sl); d_->ceil.set_hard(size_t(sb)); }
    d_->dsend.reset(new fnwp::dataplane::Sender([this](const std::string& rec) {   // its one writer: no lock
        try { send_all(dat_, rec); }
        catch (const std::exception&) {}                 // the data socket is gone: the reader says so (dead), once
    }));
    d_->dsend->start();
    d_->reader = std::thread([this] { read_loop(ret_, false); });
    d_->data_reader = std::thread([this] { read_loop(dat_, true); });
    d_->sender = std::thread([this] { send_loop(); });
    d_->applier = std::thread([this] { apply_loop(); });    // [CLIENT_APPLIER_V1]
}
Session::~Session() {
    shutdown();
    d_->stop.store(true, std::memory_order_release); d_->work.fetch_add(1); futex_wake_all(d_->work);
    d_->queued.store(0); futex_wake_all(d_->queued); d_->ring_free.fetch_add(1); futex_wake_all(d_->ring_free);
    if (d_->sender.joinable()) d_->sender.join();
    if (d_->dsend) d_->dsend->stop();
    if (d_->reader.joinable()) d_->reader.join();
    d_->apply_stop.store(true, std::memory_order_release); d_->apply_work.fetch_add(1); futex_wake_all(d_->apply_work);
    if (d_->applier.joinable()) d_->applier.join();         // [CLIENT_APPLIER_V1]
    if (d_->data_reader.joinable()) d_->data_reader.join();
    d_->landed.close();
    // [CLIENT_LANDING_LOCKLESS_V1] no lander threads to join
    if (req_ >= 0) FR_CLOSE(req_);
    if (ret_ >= 0) FR_CLOSE(ret_);
    if (dat_ >= 0) FR_CLOSE(dat_);
}
void Session::send_loop() {
    // [EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1] every write is its cell's own request through the engine, in the session's
    // order: no write is packed with others or sent as the memory's binary write verb (that verb is gone)
    // [SENDS_GATHERED_V1] frames go into one buffer and leave in one send() when nothing more is ready (before the sender
    // sleeps) or the buffer passes 32 KB: measured, a send() per frame was a tenth of a box's CPU at 20k writes/s
    std::string ob; ob.reserve(64 * 1024);
    auto flush = [&] { if (!ob.empty()) { std::string b; b.swap(ob); ob.reserve(64 * 1024); send_all(req_, b); } };
    auto out = [&](const std::string& f) { ob += be32(uint32_t(f.size())); ob += f; if (ob.size() >= 32 * 1024) flush(); };
    for (;;) {
        Impl::Job j;
        {   // [CLIENT_LOCKLESS_V1] resends first (they are made in a call's turn), then new requests; sleep on the work word
            Impl::JobNode* n = nullptr; bool resend = false;
            for (;;) {
                const uint32_t seen = d_->work.load(std::memory_order_acquire);
                if ((n = d_->resendq.pop())) { resend = true; break; }
                if ((n = d_->jobq.pop())) break;
                try { flush(); } catch (const std::exception&) { ob.clear(); }   // [SENDS_GATHERED_V1] the reader sees the end
                if (d_->stop.load(std::memory_order_acquire)) return;
                d_->sender_sleeping.store(1, std::memory_order_seq_cst);   // [WAKE_ONLY_A_SLEEPER_V1] then look once more
                if (d_->work.load(std::memory_order_seq_cst) != seen) { d_->sender_sleeping.store(0); continue; }   // a push since `seen`
                fr_futex_wait(&d_->work, seen);
                d_->sender_sleeping.store(0, std::memory_order_seq_cst);
            }
            j = std::move(n->j); delete n;
            if (!resend && d_->queued.fetch_sub(1, std::memory_order_acq_rel) == Impl::QUEUE_MAX / 2 + 1) futex_wake_all(d_->queued);
        }
        if (j.mw) {                                      // [EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1] the cell's own request, through the engine
            // [CANONICAL_BODY_V1] in the wire's own form: keys sorted (bag, instance, service, variable), ASCII
            j.body.reserve(48 + j.wbag.size() + j.ws.size() + j.wv.size() + j.wi.size());
            j.body = "{\"bag\":"; j.body += j.wbag; j.body += ",\"instance\":"; Json::quote_ascii_into(j.body, j.wi);
            j.body += ",\"service\":"; Json::quote_ascii_into(j.body, j.ws); j.body += ",\"variable\":"; Json::quote_ascii_into(j.body, j.wv); j.body += '}';
            d_->c_posted.fetch_add(1, std::memory_order_relaxed);
        }
        if (j.hdone) {                                    // the memory's own frame, as built: no engine, no reference, no marker
            uint32_t seq = 0; std::string why = d_->dead_why();
            if (why.empty()) {
                if (j.noreply) {}                         // [NO_WRITE_ACK_V1] a cell write: not a request, no number, no slot
                else seq = d_->seq++;
                if (j.noreply) {}
                else if (j.stream) {                      // [FOLLOW_STREAM_V1] a standing trigger: its own table
                    auto* st = new Impl::Stream{seq, j.hdone}; bool placed = false;
                    for (auto& x : d_->streams) { Impl::Stream* e = nullptr; if (x.compare_exchange_strong(e, st, std::memory_order_acq_rel)) { placed = true; break; } }
                    if (!placed) { delete st; why = "too many standing triggers on one session"; }
                    else if (!d_->dead_why().empty()) why = d_->dead_why();   // the sweep calls it back
                } else {
                Impl::InFlight* mine = new Impl::InFlight{}; mine->hdone = j.hdone; mine->hop = j.hop;
                std::atomic<Impl::InFlight*>* slot = d_->ring_put(seq, mine);   // [IN_FLIGHT_BY_SEQ_V1]
                if (!slot) { delete mine; why = "session shut down"; }
                else if (!d_->dead_why().empty()) { Impl::InFlight* back = mine; if (slot->compare_exchange_strong(back, nullptr, std::memory_order_acq_rel)) { delete mine; why = d_->dead_why(); } }
                }
            }
            if (why.empty()) {
                // [READ_REQUESTS_BY_REFERENCE_V1] a request for rows names its family by slot after the first time:
                // encoded here, in the order the frames leave
                if (j.hframe.size() > 5 && (uint8_t(j.hframe[4]) == OP_MEM_READ || uint8_t(j.hframe[4]) == OP_MEM_FOLLOW))
                    j.hframe = j.hframe.substr(0, 5) + d_->reqenc.encode(std::string_view(j.hframe).substr(5));
                try { out(j.hframe); d_->ceil.note(j.hframe.size() + 4); if (j.hop == cellwire::OP_CELL_WRITE) { d_->c_cell_bytes.fetch_add(j.hframe.size() + 4, std::memory_order_relaxed); d_->c_cell_writes.fetch_add(1, std::memory_order_relaxed); }
                      else d_->c_batch_bytes.fetch_add(j.hframe.size() + 4, std::memory_order_relaxed); }
                catch (const std::exception& e) { why = e.what(); }
            }
            if (!why.empty()) (*j.hdone)(std::string(), why);
            continue;
        }
        fnwp::ClientEngine::Out o;
        try { o = !j.is_resend ? (j.mw ? d_->eng->prepare_nowait(j.method, j.path, j.body) : d_->eng->prepare(j.method, j.path, j.body))
                  : j.redo == 1 ? d_->eng->reprepare(j.resend.ctx) : j.redo == 2 ? d_->eng->resend_raw(j.resend.ctx) : j.resend; }
        catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); if (!j.waiter) d_->c_posts_refused.fetch_add(1); continue; }
        {   // [DIAG-FRAME] FROGRAM_DIAG_FRAMES=N: after N requests, three request frames as they leave, in hex, with their body
            static const long after = getenv("FROGRAM_DIAG_FRAMES") ? atol(getenv("FROGRAM_DIAG_FRAMES")) : -1;
            static std::atomic<long> seen{0};
            if (after >= 0) { const long k = seen.fetch_add(1); if (k >= after && k < after + 3) {
                std::string h; char x[4]; for (unsigned char c : o.frame) { snprintf(x, sizeof x, "%02x", c); h += x; }
                fprintf(stderr, "[DIAG-FRAME] %zu B op=%u body(%zu B)=%s\n[DIAG-FRAME] hex=%s\n", o.frame.size(), o.frame.size() > 4 ? unsigned(uint8_t(o.frame[4])) : 0u,
                        j.body.size(), j.body.c_str(), h.c_str()); } }
        }
        std::string why;
        // [HIGH_SPEED_DATA_SOCKET_V1] [DATA_MARKER_V1] the frame's path is decided as it leaves: whole on the request
        // socket, or -- when it cannot be differenced and would own the request socket past the ceiling -- its MARKER
        // on the request socket, in order, and its body in segments on the data socket under the same sequence
        std::string frame = o.frame, payload; bool via_data = false;
        // [WRITES_KEEP_THEIR_ORDER_V1] a write's frame stays on the request socket, in the session's order, up to 256 KB:
        // a body on the data socket runs when it lands, after writes made later (a writer's window would see a gap)
        if (uint8_t(o.frame[4]) == semwire::OP_REQ_RAW && o.frame.size() > d_->ceil.whole_max() && !(j.mw && o.frame.size() <= 256 * 1024)) {
            try { frame = fnwp::ClientEngine::request_marker(o.frame, payload); via_data = true; }
            catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); continue; }
        }
        uint32_t seq = 0;
        why = d_->dead_why();
        if (why.empty() && o.ctx && o.ctx->unanswered) seq = d_->seq++;   // [NO_WRITE_ACK_V1] nothing will answer: no slot
        else if (why.empty()) {                           // [CLIENT_LOCKLESS_V1] the slot for this sequence number
            seq = d_->seq++;
            Impl::InFlight* mine = new Impl::InFlight{}; mine->ctx = o.ctx; mine->waiter = j.waiter; mine->ticket = j.ticket; mine->in_turn = j.in_turn;
            std::atomic<Impl::InFlight*>* slot = d_->ring_put(seq, mine);   // [IN_FLIGHT_BY_SEQ_V1]
            if (!slot) { delete mine; why = "session shut down"; }
            else if (!d_->dead_why().empty()) {           // died meanwhile: take it back if the reader's sweep did not
                Impl::InFlight* back = mine;
                if (slot->compare_exchange_strong(back, nullptr, std::memory_order_acq_rel)) { delete mine; why = d_->dead_why(); }
            }
        }
        if (why.empty()) {
            try {
                out(frame); if (via_data) flush(); d_->ceil.note(frame.size() + 4);
                trace("OUT seq=" + std::to_string(seq) + " op=" + std::to_string(uint8_t(frame[4])) + (via_data ? " marker" : "") + " " + j.method + " " + j.path.substr(0, 90) + " body=" + std::to_string(j.body.size()) + "B raw=" + (o.ctx->raw ? "y" : "n") + " " + j.body.substr(0, 300));
                if (via_data) { d_->dsend->submit(seq, payload); d_->c_data_frames.fetch_add(1); d_->c_data_bytes.fetch_add(payload.size()); }
            } catch (const std::exception& e) { why = e.what(); }
        }
        if (!why.empty()) Impl::finish(j.waiter, nullptr, why);   // the connection is gone
    }
}
// [WIRE_ORDER_TURNS_V1] the turn machinery: stamp on arrival; apply in turn or hold; close the turn, apply the held
void Session::stamp(InFlight& inf) {
    if (inf.in_turn || !inf.ctx || inf.ctx->raw) return;          // raw moves no reference: no turn (as in Python)
    inf.ticket = ++d_->issued[inf.ctx->ik];              // [CLIENT_APPLIER_V1] on the applier
}
void Session::ready(const InFlight& inf, const std::string& reply) {
    if (inf.ticket && !inf.in_turn) {                   // [CLIENT_APPLIER_V1] on the applier
        if (d_->applied[inf.ctx->ik] != inf.ticket - 1) { d_->held[inf.ctx->ik].emplace(inf.ticket, std::make_pair(inf, reply)); return; }
    }
    apply_reply(inf, reply);
}
void Session::turn_done(const InFlight& inf) {
    if (!inf.ticket) return;
    std::optional<std::pair<InFlight, std::string>> next;
    {                                                    // [CLIENT_APPLIER_V1] on the applier
      uint64_t a = ++d_->applied[inf.ctx->ik];
      auto h = d_->held.find(inf.ctx->ik);
      if (h != d_->held.end()) { auto t = h->second.find(a + 1); if (t != h->second.end()) { next = std::move(t->second); h->second.erase(t); } } }
    if (next) apply_reply(next->first, next->second);                // the next ticket's turn
}
void Session::apply_reply(const InFlight& inf, const std::string& reply) {
    // [CLIENT_APPLIER_V1] the instance's RAW answer is still landing: this reply is applied after it
    if (inf.ctx && !inf.ctx->raw && !inf.ctx->deferred && d_->landing.count(inf.ctx->ik)) { d_->after_landing[inf.ctx->ik].emplace_back(inf, reply); return; }
    const bool landed = inf.ctx && inf.ctx->deferred && !inf.ctx->raw;
    struct Release { Session* s; const InFlight& inf; bool on; ~Release() {
        if (!on) { return; }
        s->d_->landing.erase(inf.ctx->ik);
        auto h = s->d_->after_landing.find(inf.ctx->ik);
        if (h == s->d_->after_landing.end()) { return; }
        auto held = std::move(h->second); s->d_->after_landing.erase(h);
        for (auto& x : held) s->apply_reply(x.first, x.second); } } release{this, inf, landed};
    try {
        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);
        trace("APPLIED " + inf.ctx->method + " " + inf.ctx->path.substr(0, 90) + (s.done ? " done status=" + std::to_string(s.answer.status) : " resend"));
        if (s.done) {
            if (!inf.waiter && s.answer.status >= 400) d_->c_posts_refused.fetch_add(1);
            Impl::finish(inf.waiter, &s.answer, "");
            turn_done(inf);
            return;
        }
        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.redo = s.redo; j.waiter = inf.waiter;
        j.ticket = inf.ticket; j.in_turn = inf.ticket != 0;          // the round trip happens in this call's turn
        d_->enqueue(std::move(j), true);                 // [CLIENT_LOCKLESS_V1] ahead of new requests
    } catch (const std::exception& e) {
        trace(std::string("APPLY FAILED ") + inf.ctx->method + " " + inf.ctx->path.substr(0, 90) + ": " + e.what());
        if (!inf.waiter) d_->c_posts_refused.fetch_add(1);
        Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what());
        turn_done(inf);
    }
}
thread_local std::shared_ptr<const std::vector<RawCell>> tl_rows;   // [DECODE_ONCE_V1] the answer being handed out
void Session::read_loop(int fd, bool data) {
    std::string why;
    try {
        for (;;) {
            std::string f = recv_frame(fd);
            if (data) {                                  // a segment of a payload; it lands when its last segment does
                d_->c_data_bytes.fetch_add(f.size() + 4);
                auto done = d_->reasm.feed(f);
                if (!done) continue;
                d_->c_data_frames.fetch_add(1);
                d_->to_applier(4, Session::InFlight{}, std::move(done->second), done->first);   // [CLIENT_LANDING_LOCKLESS_V1]
                continue;
            }
            if (f.size() < 9) throw Refused("reply frame too short");
            const uint32_t seq = rd32(f, 0); std::string reply = f.substr(4);
            // [ROWS_ON_THE_SEMANTIC_WIRE_V1] rows arrive as differences against this session's previous rows of their shape:
            // made whole here, in the order they arrive, before anything reads them
            // [DECODE_ONCE_V1] decoded once, into the bags' arrays, handed to whoever asked (tl_rows: the answer's rows)
            tl_rows.reset();
            if (reply.size() >= 5 && (uint8_t(reply[4]) == OP_MEM_READ || uint8_t(reply[4]) == OP_MEM_FOLLOW))
            {   const size_t wire = reply.size();
                auto rows = std::make_shared<std::vector<RawCell>>();
                for (auto& r : d_->rowdec.decode_rows(std::string_view(reply).substr(5))) {
                    RawCell c; c.id = r.id; c.updated = r.updated; c.variable = std::move(r.variable); c.instance = std::move(r.instance); c.arr = std::move(r.bag);
                    c.gone = r.gone;                          // [GONE_WITH_ITS_WRITER_V1]
                    rows->push_back(std::move(c)); }
                tl_rows = std::move(rows); reply.resize(5);
                d_->c_rows_wire.fetch_add(wire, std::memory_order_relaxed); }
            trace("IN  seq=" + std::to_string(seq) + " op=" + std::to_string(uint8_t(reply[4])) + " " + std::to_string(reply.size()) + "B");
            if (Impl::Stream* st = d_->stream_of(seq)) {    // [FOLLOW_STREAM_V1] rows of a standing trigger, or its end
                if (uint8_t(reply[4]) == OP_MEM_FOLLOW) { (*st->done)(reply.substr(5), std::string()); continue; }
                for (auto& x : d_->streams) { Impl::Stream* p = st; if (x.compare_exchange_strong(p, nullptr, std::memory_order_acq_rel)) break; }
                (*st->done)(std::string(), "refused: " + (reply.size() > 7 ? reply.substr(7) : std::string("op ") + std::to_string(uint8_t(reply[4]))));
                continue;                                // (freed with the session: a reader may be inside it)
            }
            Impl::InFlight inf;
            {   // [CLIENT_LOCKLESS_V1] take this sequence number's slot
                Impl::InFlight* got = d_->ring_take(seq);   // [IN_FLIGHT_BY_SEQ_V1]
                // [NO_WRITE_ACK_V1] a write holds no slot: the only answer it can have is its refusal
                if (!got && uint8_t(reply[4]) == OP_ERROR) { d_->c_posts_refused.fetch_add(1, std::memory_order_relaxed);
                    {   const std::string why = reply.size() > 7 ? reply.substr(7) : std::string("?");
                        unsigned long long sl = 0, fm = 0; unsigned c = 0;      // [CELL_WRITE_V1] which chain: its next write goes whole
                        if (sscanf(why.c_str(), "[cell %llu %llu %u]", &sl, &fm, &c) == 3) d_->cellenc.refused(sl, fm);
                        static std::atomic<int> said{0}; if (said.fetch_add(1) < 20) fprintf(stderr, "frogram: the memory refused a write: %s\n", why.c_str()); }
                    trace("refused write: " + (reply.size() > 7 ? reply.substr(7) : std::string())); continue; }
                if (!got && uint8_t(reply[4]) == REQ_MISS)   // [NO_WRITE_ACK_V1] the two ends disagree on a reference: said, not hidden
                    throw Refused("the memory could not decode a write nobody waits for (REQ_MISS on an unanswered write): the two ends disagree on a reference, and the write is lost");
                if (!got) throw Refused("reply tagged a seq that was never sent");
                inf = std::move(*got); delete got;
                d_->ring_free.fetch_add(1, std::memory_order_release); futex_wake_all(d_->ring_free);
            }
            if (inf.hdone) {                             // a frame's reply: MAGIC + its op, or a refusal
                if (uint8_t(reply[4]) == inf.hop) (*inf.hdone)(reply.substr(5), std::string());
                else { (*inf.hdone)(std::string(), "refused: " + (reply.size() > 7 ? reply.substr(7) : std::string("op ") + std::to_string(uint8_t(reply[4])))); }
                continue;
            }
            if (fnwp::ClientEngine::is_reply_marker(reply)) {
                // [DATA_MARKER_V1] the marker holds this reply's place in wire order; the payload lands on the data
                // socket; only later replies to this instance wait for it (the engine holds them)
                d_->to_applier(1, inf, reply, seq);      // [CLIENT_APPLIER_V1] its place is its arrival; the payload comes as kind 4
                continue;
            }
            d_->to_applier(0, inf, reply);               // [CLIENT_APPLIER_V1] stamped and applied on the applier, in this order
        }
    } catch (const std::exception& e) { why = e.what(); }
    {   // [CLIENT_LOCKLESS_V1] dead: one reader claims it and writes the reason, then the flag; the other waits for it
        bool claim = false;
        if (d_->dead_claim.compare_exchange_strong(claim, true, std::memory_order_acq_rel)) {
            d_->dead = why.empty() ? "connection closed" : why; d_->dead_set.store(true, std::memory_order_release);
        } else while (!d_->dead_set.load(std::memory_order_acquire)) std::this_thread::yield();
    }
    d_->landed.close();
    for (uint32_t i = 0; i < Impl::RING; ++i)
        if (Impl::InFlight* got = d_->ring[i].exchange(nullptr, std::memory_order_acq_rel)) {
            if (got->hdone) (*got->hdone)(std::string(), d_->dead); else Impl::finish(got->waiter, nullptr, d_->dead); delete got; }
    d_->ring_free.fetch_add(1); futex_wake_all(d_->ring_free);
    if (!data) for (auto& x : d_->streams) if (Impl::Stream* st = x.exchange(nullptr, std::memory_order_acq_rel)) { (*st->done)(std::string(), d_->dead); delete st; }   // [FOLLOW_STREAM_V1]
    d_->to_applier(3, Session::InFlight{}, "");         // [CLIENT_APPLIER_V1] the applier finishes what it holds
    if (!data) { ::shutdown(req_, FR_SHUT_RDWR); ::shutdown(dat_, FR_SHUT_RDWR); } else { ::shutdown(ret_, FR_SHUT_RDWR); }
}
// [CLIENT_APPLIER_V1] the one thread that applies replies: stamps turns in arrival order, keeps a landing instance's
// later replies until it has landed, and owns everything the engine's completion touches
void Session::apply_loop() {
    for (;;) {
        const uint32_t seen = d_->apply_work.load(std::memory_order_acquire);
        Impl::ApplyNode* n = d_->applyq.pop();
        if (!n) {
            if (d_->apply_stop.load(std::memory_order_acquire)) return;
            fr_futex_wait(&d_->apply_work, seen);
            continue;
        }
        try {
            if (n->kind == 0) { stamp(n->inf); ready(n->inf, n->reply); }
            else if (n->kind == 1) { stamp(n->inf); d_->eng->defer(n->inf.ctx); if (n->inf.ctx && !n->inf.ctx->raw) d_->landing.insert(n->inf.ctx->ik);
                                     d_->marked[n->seq] = n->inf;     // the stamped call: its landed reply is applied as this one
                                     auto e = d_->early_payload.find(n->seq);   // [CLIENT_LANDING_LOCKLESS_V1] payload already here
                                     if (e == d_->early_payload.end()) d_->marker_reply[n->seq] = n->reply;
                                     else { std::string full = fnwp::ClientEngine::reply_from_marker(n->reply, e->second); d_->early_payload.erase(e);
                                            InFlight inf = d_->marked[n->seq]; d_->marked.erase(n->seq); ready(inf, full); } }
            else if (n->kind == 4) { auto mr = d_->marker_reply.find(n->seq);   // [CLIENT_LANDING_LOCKLESS_V1] a payload landed
                                     if (mr == d_->marker_reply.end()) d_->early_payload[n->seq] = std::move(n->reply);
                                     else { std::string full = fnwp::ClientEngine::reply_from_marker(mr->second, n->reply); d_->marker_reply.erase(mr);
                                            auto m = d_->marked.find(n->seq); InFlight inf = m->second; d_->marked.erase(m); ready(inf, full); } }
            else if (n->kind == 2) { auto m = d_->marked.find(n->seq); InFlight inf = m != d_->marked.end() ? m->second : n->inf;
                                     if (m != d_->marked.end()) d_->marked.erase(m);
                                     ready(inf, n->reply); }
            else {                                        // 3: the connection is dead: everything held ends with it
                for (auto& i : d_->held) for (auto& t : i.second) Impl::finish(t.second.first.waiter, nullptr, d_->dead_why());
                d_->held.clear();
                for (auto& i : d_->after_landing) for (auto& t : i.second) Impl::finish(t.first.waiter, nullptr, d_->dead_why());
                d_->after_landing.clear(); d_->landing.clear();
                for (auto& m : d_->marked) Impl::finish(m.second.waiter, nullptr, d_->dead_why());   // a payload that will not land
                d_->marked.clear(); d_->marker_reply.clear(); d_->early_payload.clear();
            }
        } catch (const std::exception& e) { Impl::finish(n->inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
        delete n;
    }
}
// [ROUND_TRIPS_V1] Blocking calls made by THIS thread: each one is a round trip the caller waited out. The LISP engine's
// request thread reads it around one operation (transport.stats "calls"); held reads on their own threads don't count.
static thread_local uint64_t tl_calls = 0;
static thread_local std::vector<std::pair<std::string, uint64_t>>* tl_wlog = nullptr;
void log_writes_to(std::vector<std::pair<std::string, uint64_t>>* sink) { tl_wlog = sink; }
std::vector<std::pair<std::string, uint64_t>>* current_write_log() { return tl_wlog; }
uint64_t calls_this_thread() { return tl_calls; }
static thread_local std::string* tl_text_out = nullptr;   // [CALL_TEXT_V1] set by call_text around call()
std::string Session::call_text(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    std::string out; tl_text_out = &out;
    try { call(method, path, body, timeout_s); } catch (...) { tl_text_out = nullptr; throw; }
    tl_text_out = nullptr; return out;
}
Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    ++tl_calls;
    std::string key; key.reserve(method.size() + path.size() + body.size() + 2);
    key += method; key += '\0'; key += path; key += '\0'; key += body;
    std::shared_ptr<Pending> p; bool joined = false;
    {   // [CLIENT_COALESCE_LOCKLESS_V1] join the identical call in flight, or claim a slot and lead
        auto g = d_->co_ebr.guard(); const size_t h0 = Impl::co_home(key);
        for (size_t i = 0; i < 32 && !p; ++i) {
            std::atomic<const Impl::CoEnt*>& sl = d_->co_slot[(h0 + i) & (Impl::CO_N - 1)];
            const Impl::CoEnt* e = g.protect(sl);
            if (!e) {
                Impl::CoEnt* n = new Impl::CoEnt; n->key = key; n->p = std::make_shared<Pending>(); n->p->co = d_.get(); n->p->co_key = key; n->p->co_entry = n;
                const Impl::CoEnt* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { p = n->p; break; }
                n->p->co_entry = nullptr; delete n; e = expect; if (!e) continue;
            }
            if (e->key == key && !e->p->done.load(std::memory_order_acquire)) { p = e->p; joined = true; }
        }
        if (!p) p = std::make_shared<Pending>();          // no free slot near home: this call runs on its own
    }
    if (joined) d_->c_coalesced.fetch_add(1);
    if (!joined) {                                       // [CLIENT_LOCKLESS_V1]
        { const std::string dead = d_->dead_why(); if (!dead.empty()) { Impl::finish(p, nullptr, dead); throw Unreachable(dead); } }
        for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
                   if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
                   fr_futex_wait(&d_->queued, q); }
        if (d_->stop.load()) { Impl::finish(p, nullptr, "session shut down"); throw Unreachable("session shut down"); }
        Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->enqueue(std::move(j), false);
    }
    {   const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s > 1e9 ? 1e9 : timeout_s);
        while (!p->done.load(std::memory_order_acquire)) {
            const double left = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
            if (left <= 0) throw Unreachable("no reply within the timeout");
            futex_wait_for(p->done, 0, left);
        } }
    if (!p->err.empty()) throw Unreachable(p->err);
    if (tl_text_out) { if (p->answer.status >= 400) throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + p->answer.body);
                       *tl_text_out = p->answer.body; return Json(); }   // [CALL_TEXT_V1]
    Json obj = Json::parse(p->answer.body);
    if (p->answer.status >= 400 || !obj["ok"].b) throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + obj["error"].s + (obj["why"].s.empty() ? std::string() : " (" + obj["why"].s + ")"));   // [REFUSAL_SAYS_WHY_V1]
    return obj;
}
void Session::call_async(const std::string& method, const std::string& path, const std::string& body, Done done) {   // [NOBODY_WAITS_V1]
    ++tl_calls;
    std::string key; key.reserve(method.size() + path.size() + body.size() + 2);
    key += method; key += '\0'; key += path; key += '\0'; key += body;
    std::shared_ptr<Pending> p;
    {   // [CLIENT_COALESCE_LOCKLESS_V1] as call(): the identical request in flight answers this one too -- part of the
        // session, not of the caller. A joiner leaves its completion on the leader's list; one sealed already (its
        // answer is being handed out) cannot be joined, and this call leads a request of its own.
        auto g = d_->co_ebr.guard(); const size_t h0 = Impl::co_home(key);
        for (size_t i = 0; i < 32 && !p; ++i) {
            std::atomic<const Impl::CoEnt*>& sl = d_->co_slot[(h0 + i) & (Impl::CO_N - 1)];
            const Impl::CoEnt* e = g.protect(sl);
            if (!e) {
                Impl::CoEnt* n = new Impl::CoEnt; n->key = key; n->p = std::make_shared<Pending>(); n->p->co = d_.get(); n->p->co_key = key; n->p->co_entry = n;
                const Impl::CoEnt* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { p = n->p; break; }
                n->p->co_entry = nullptr; delete n; e = expect; if (!e) continue;
            }
            if (e->key != key) continue;
            auto* me = new Pending::Joined{done, nullptr};
            Pending::Joined* head = e->p->joined.load(std::memory_order_acquire);
            for (;;) {
                if (head == Impl::sealed_joins()) { delete me; me = nullptr; break; }
                me->next = head;
                if (e->p->joined.compare_exchange_weak(head, me, std::memory_order_acq_rel)) break;
            }
            if (me) { d_->c_coalesced.fetch_add(1); return; }   // answered with the request in flight
            break;                                              // answered already: a request of its own
        }
        if (!p) p = std::make_shared<Pending>();
    }
    p->cb = std::move(done);
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) { Impl::finish(p, nullptr, dead); return; } }
    for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
               if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
               fr_futex_wait(&d_->queued, q); }
    if (d_->stop.load()) { Impl::finish(p, nullptr, "session shut down"); return; }
    Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = std::move(p); d_->enqueue(std::move(j), false);
}
// [READS_AFTER_IN_BINARY_V1] one frame out, its answer back, the calling thread waiting for it (a held read waits
// in the memory, not here); the reply's bytes after MAGIC + op, or Unreachable / Refused
void Session::follow_frame(std::string frame, std::function<void(std::string, std::string)> on) {   // [FOLLOW_STREAM_V1]
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }
    Impl::Job j; j.hframe = std::move(frame); j.hop = uint8_t(j.hframe[4]); j.stream = true;
    j.hdone = std::make_shared<Impl::FrameDone>(std::move(on));
    d_->enqueue(std::move(j), false);
}
// [READS_COALESCE_V1] as call(): readers asking the memory the identical thing while it is in flight (the same family,
// past the same id) do not each send it -- the first sends it, every duplicate blocks on that one request as it is
// found, and the one answer releases them all as a group.
std::string Session::call_frame(std::string frame, double timeout_s, std::shared_ptr<const std::vector<RawCell>>* rows_out) {
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }
    std::string key; key.reserve(frame.size() + 1); key += '\x01'; key += frame;
    std::shared_ptr<Pending> p; bool joined = false;
    {   auto g = d_->co_ebr.guard(); const size_t h0 = Impl::co_home(key);
        for (size_t i = 0; i < 32 && !p; ++i) {
            std::atomic<const Impl::CoEnt*>& sl = d_->co_slot[(h0 + i) & (Impl::CO_N - 1)];
            const Impl::CoEnt* e = g.protect(sl);
            if (!e) {
                Impl::CoEnt* n = new Impl::CoEnt; n->key = key; n->p = std::make_shared<Pending>(); n->p->co = d_.get(); n->p->co_key = key; n->p->co_entry = n;
                const Impl::CoEnt* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { p = n->p; break; }
                n->p->co_entry = nullptr; delete n; e = expect; if (!e) continue;
            }
            if (e->key == key && !e->p->done.load(std::memory_order_acquire)) { p = e->p; joined = true; }
        }
        if (!p) p = std::make_shared<Pending>();          // no free slot near home: this read runs on its own
    }
    if (joined) d_->c_coalesced.fetch_add(1);
    else {
        for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
                   if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
                   fr_futex_wait(&d_->queued, q); }
        if (d_->stop.load()) { Impl::finish(p, nullptr, "session shut down"); throw Unreachable("session shut down"); }
        const uint8_t op = uint8_t(frame[4]);
        Impl::Job j; j.hframe = std::move(frame);
        j.hdone = std::make_shared<Impl::FrameDone>([p](std::string body, std::string err) {
            fnwp::Answer a; a.status = 200; a.body = std::move(body);
            p->rows = tl_rows;                            // before done: every reader it releases reads them
            Impl::finish(p, &a, err); });
        j.hop = op;
        d_->enqueue(std::move(j), false);
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s);
    while (!p->done.load(std::memory_order_acquire)) {
        const double left = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
        if (left <= 0) throw Unreachable("no reply within the timeout");
        futex_wait_for(p->done, 0, left);
    }
    if (!p->err.empty()) throw Unreachable(p->err);
    if (rows_out) *rows_out = p->rows;                // the group shares the one decoded answer
    return p->answer.body;
}
// [CELL_WRITE_V1] a write is encoded here, on the thread that makes it, against that thread's own previous write of the
// family (cellwire.hpp), and handed to the session's writer -- the one thread that owns the FNWP request socket, as the
// node proxy's daemon worker owns its socket. Nothing answers it; a refusal is said ([NO_WRITE_ACK_V1]).
void Session::send_write(std::string frame) {
    static const auto nobody = std::make_shared<Impl::FrameDone>([](std::string, std::string) {});
    for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);   // the session's own bound: as TCP would hold it
               if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
               fr_futex_wait(&d_->queued, q); }
    Impl::Job j; j.hframe = std::move(frame); j.hop = cellwire::OP_CELL_WRITE; j.noreply = true; j.hdone = nobody;
    d_->c_posted.fetch_add(1, std::memory_order_relaxed);
    d_->enqueue(std::move(j), false);
}
void Session::post_write_fields(const std::string& service, const std::string& variable, const std::string& instance,
                                const std::vector<std::string>& order, semcodec::Fields fields) {
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }
    if (d_->stop.load()) throw Unreachable("session shut down");
    send_write(d_->cellenc.encode_fields(std::string(MAGIC, 4), service, variable, instance, order, std::move(fields)));
}
void Session::post_write(const std::string& service, const std::string& variable, const std::string& instance, const std::string& bag) {
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }
    if (d_->stop.load()) throw Unreachable("session shut down");
    send_write(d_->cellenc.encode(std::string(MAGIC, 4), service, variable, instance, bag));
}
bool Session::flush(double timeout_s) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s);
    while (std::chrono::steady_clock::now() < until) {
        if (!d_->dead_why().empty()) return false;
        if (d_->queued.load(std::memory_order_acquire) == 0 && d_->sender_sleeping.load(std::memory_order_seq_cst)) return true;   // it sleeps only once flushed
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return false;
}
bool Session::alive(std::string* why) const {                // [CLIENT_LOCKLESS_V1] the dead flag, no lock
    const std::string dead = d_->dead_why();
    if (why) *why = dead;
    return dead.empty();
}
void Session::post(const std::string& method, const std::string& path, const std::string& body) {
    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }   // [CLIENT_LOCKLESS_V1]
    for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
               if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
               fr_futex_wait(&d_->queued, q); }
    if (d_->stop.load()) throw Unreachable("session shut down");
    Impl::Job j; j.method = method; j.path = path; j.body = body; d_->enqueue(std::move(j), false);
    d_->c_posted.fetch_add(1);
}
Stats Session::stats() const {
    Stats s;                                             // [CLIENT_LOCKLESS_V1] from the atomic counters
    s.coalesced = d_->c_coalesced.load(); s.posted = d_->c_posted.load(); s.posts_refused = d_->c_posts_refused.load();
    s.data_frames = d_->c_data_frames.load(); s.data_bytes = d_->c_data_bytes.load();
    auto e = d_->eng->stats();
    s.raw = e.raw; s.repeat = e.repeat; s.same = e.same; s.miss = e.miss; s.full = e.full; s.diff = e.diff; s.rdiff = e.rdiff; s.rraw = e.rraw;
    s.bytes_out = e.bytes_out; s.bytes_in = e.bytes_in;
    s.rows_wire = d_->c_rows_wire.load(); s.rows_whole = d_->c_rows_whole.load();
    s.batch_bytes = d_->c_batch_bytes.load(); s.cell_bytes = d_->c_cell_bytes.load(); s.cell_writes = d_->c_cell_writes.load(); s.queued_now = d_->queued.load();
    return s;
}
void Session::shutdown() {
    if (req_ >= 0) ::shutdown(req_, FR_SHUT_RDWR);
    if (ret_ >= 0) ::shutdown(ret_, FR_SHUT_RDWR);
    if (dat_ >= 0) ::shutdown(dat_, FR_SHUT_RDWR);
}

// ---------------------------------------------------------------- Memory
uint64_t Memory::write(const std::string& sv, const std::string& var, const std::string& inst, const std::string& bag) {
    Json o = s_.call("POST", api_ + "?op=write", "{\"service\":" + Json::quote(sv) + ",\"variable\":" + Json::quote(var) +
                     ",\"instance\":" + Json::quote(inst) + ",\"bag\":" + bag + "}");
    const uint64_t wid_ = uint64_t(o["id"].n); if (tl_wlog) tl_wlog->emplace_back(var, wid_); return wid_;
}
void Memory::write_nowait(const std::string& sv, const std::string& var, const std::string& inst, const std::string& bag) {
    s_.post_write(sv, var, inst, bag);                   // [EVERY_WRITE_ON_THE_SEMANTIC_WIRE_V1]
}
std::vector<Cell> Memory::read(const std::string& sv, const std::string& var, const std::string& inst,
                               int64_t after, double wait_s, int fresh_s) {
    if (after >= 0) {                                    // [READS_AFTER_IN_BINARY_V1] new rows every time: in binary
        std::string f; f.reserve(32 + sv.size() + var.size() + inst.size()); f.append(MAGIC, 4); f += char(OP_MEM_READ);
        auto put = [&](uint64_t v, int w) { for (int k = w - 1; k >= 0; --k) f += char(v >> (8 * k)); };
        put(sv.size(), 2); f += sv; put(var.size(), 2); f += var; put(inst.size(), 4); f += inst;
        put(uint64_t(after), 8); put(uint64_t(wait_s > 0 ? wait_s * 1000 : 0), 4); put(uint64_t(fresh_s > 0 ? fresh_s : 0), 4); put(0, 4);
        std::shared_ptr<const std::vector<RawCell>> rows;
        s_.call_frame(std::move(f), 15.0 + wait_s, &rows);
        std::vector<Cell> out;
        if (rows) for (auto& r : *rows) { Cell c; c.id = r.id; c.updated = r.updated; c.service = sv; c.variable = r.variable; c.instance = r.instance;
                                          c.gone = r.gone; if (!r.gone) c.bag = Json::parse(r.text()); out.push_back(std::move(c)); }
        return out;
    }
    std::string q = api_ + "?op=read&service=" + urlenc(sv) + "&variable=" + urlenc(var);
    if (!inst.empty()) q += "&instance=" + urlenc(inst);
    if (after >= 0) q += "&after=" + std::to_string(after);
    if (wait_s > 0) { char b[32]; snprintf(b, sizeof b, "&wait_s=%.3f", wait_s); q += b; }
    if (fresh_s > 0) q += "&fresh_s=" + std::to_string(fresh_s);
    Json o = s_.call("GET", q, "", 15.0 + wait_s); std::vector<Cell> out;
    for (auto& r : o["rows"].a) { Cell c; c.id = uint64_t(r["id"].n); c.service = r["service"].s; c.variable = r["variable"].s;
        c.instance = r["instance"].s; c.bag = r["bag"]; c.updated = r["updated_epoch"].n; c.gone = r["gone"].b; out.push_back(c); }
    return out;
}
void Memory::remove(uint64_t id) { s_.call("DELETE", api_ + "?op=remove&id=" + std::to_string(id)); }
void Memory::follow(const std::string& sv, const std::string& var, const std::string& inst, int64_t after,
                    std::function<void(std::vector<RawCell>&, const std::string&)> on) {
    std::string f; f.append(MAGIC, 4); f += char(OP_MEM_FOLLOW);
    auto put = [&](uint64_t v, int w) { for (int k = w - 1; k >= 0; --k) f += char(v >> (8 * k)); };
    put(sv.size(), 2); f += sv; put(var.size(), 2); f += var; put(inst.size(), 4); f += inst;
    put(uint64_t(after), 8); put(0, 4); put(0, 4); put(0, 4);   // [FAMILY_HOOKS_V1] after < 0: from now on
    s_.follow_frame(std::move(f), [on](std::string, std::string err) {
        std::vector<RawCell> rows;
        if (err.empty() && tl_rows) rows = *tl_rows;      // [DECODE_ONCE_V1] as the session's reader decoded them
        on(rows, err);
    });
}
uint64_t Memory::newest_id(const std::string& sv, const std::string& var) {   // [NEWEST_ID_V1]
    std::string f; f.append(MAGIC, 4); f += char(0x7B);
    auto put = [&](uint64_t v, int w) { for (int k = w - 1; k >= 0; --k) f += char(v >> (8 * k)); };
    put(sv.size(), 2); f += sv; put(var.size(), 2); f += var;
    const std::string r = s_.call_frame(std::move(f), 15.0);
    if (r.size() < 8) return 0;
    uint64_t v = 0; for (int k = 0; k < 8; ++k) v = v << 8 | uint8_t(r[k]); return v;
}
std::vector<RawCell> Memory::read_raw(const std::string& sv, const std::string& var, const std::string& inst, int64_t after, double wait_s, int fresh_s) {
    if (after < 0) throw Refused("read_raw: a held read needs after >= 0");
    std::string f; f.reserve(32 + sv.size() + var.size() + inst.size()); f.append(MAGIC, 4); f += char(OP_MEM_READ);
    auto put = [&](uint64_t v, int w) { for (int k = w - 1; k >= 0; --k) f += char(v >> (8 * k)); };
    put(sv.size(), 2); f += sv; put(var.size(), 2); f += var; put(inst.size(), 4); f += inst;
    put(uint64_t(after), 8); put(uint64_t(wait_s > 0 ? wait_s * 1000 : 0), 4); put(uint64_t(fresh_s > 0 ? fresh_s : 0), 4); put(0, 4);
    std::shared_ptr<const std::vector<RawCell>> rows;
    s_.call_frame(std::move(f), 15.0 + wait_s, &rows);
    return rows ? *rows : std::vector<RawCell>();          // [DECODE_ONCE_V1] the bags as decoded, once
}

// ---------------------------------------------------------------- Plane
struct Plane::Impl { std::mutex w, p; uint32_t rid = 0; std::string dead; std::thread reader;
    struct Slot { std::mutex m; std::condition_variable cv; bool done = false; std::string f; };
    std::map<uint32_t, std::shared_ptr<Slot>> pending; };
Plane::Plane(const std::string& host, int port) : d_(new Impl) {
    fd_ = dial(host, port); int buf = 16 * 1024; setsockopt(fd_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&buf), sizeof buf);   // the buffer IS the latency
    d_->reader = std::thread([this] { read_loop(); });
}
Plane::~Plane() { if (fd_ >= 0) ::shutdown(fd_, FR_SHUT_RDWR); if (d_->reader.joinable()) d_->reader.join(); if (fd_ >= 0) FR_CLOSE(fd_); }
void Plane::read_loop() {
    std::string why;
    try { for (;;) { std::string f = recv_frame(fd_); uint32_t rid = rd32(f, 1); std::shared_ptr<Impl::Slot> s;
            { std::lock_guard<std::mutex> g(d_->p); auto it = d_->pending.find(rid);
              if (it == d_->pending.end()) { continue; }
              s = it->second; d_->pending.erase(it); }
            { std::lock_guard<std::mutex> g(s->m); s->f = f; s->done = true; } s->cv.notify_all(); } }
    catch (const std::exception& e) { why = e.what(); }
    std::lock_guard<std::mutex> g(d_->p); d_->dead = why;
    for (auto& kv : d_->pending) { { std::lock_guard<std::mutex> h(kv.second->m); kv.second->done = true; } kv.second->cv.notify_all(); }
}
bool Plane::publish(const std::string& name, uint64_t gen, const std::string& bytes) {
    std::string body = std::string(1, char(0x01)) + be64(gen) + be16(uint16_t(name.size())) + name + bytes;
    std::lock_guard<std::mutex> g(d_->w);
    pollfd pf; pf.fd = fd_; pf.events = POLLOUT; pf.revents = 0;
    if (FR_POLL(&pf, 1, 0) <= 0 || !(pf.revents & POLLOUT)) { ++shed; return false; }     // not writable NOW: shed it
    send_frame(fd_, body); ++sent; return true;                                          // started, so finished
}
void Plane::drop(const std::string& name) { std::lock_guard<std::mutex> g(d_->w); send_frame(fd_, std::string(1, char(0x03)) + be16(uint16_t(name.size())) + name); }
std::vector<PlaneRow> Plane::read(const std::string& prefix, uint64_t& after, uint32_t wait_ms) {
    auto s = std::make_shared<Impl::Slot>(); uint32_t rid;
    { std::lock_guard<std::mutex> g(d_->p); if (!d_->dead.empty()) throw Unreachable(d_->dead); rid = ++d_->rid; d_->pending[rid] = s; }
    { std::lock_guard<std::mutex> g(d_->w); send_frame(fd_, std::string(1, char(0x02)) + be32(rid) + be64(after) + be32(wait_ms) + be16(uint16_t(prefix.size())) + prefix); }
    std::unique_lock<std::mutex> l(s->m);
    if (!s->cv.wait_for(l, std::chrono::milliseconds(wait_ms + 10000), [&] { return s->done; }) || s->f.empty()) throw Unreachable("no reply from the plane");
    const std::string& f = s->f; std::vector<PlaneRow> out;
    if (uint8_t(f[0]) == 0x12) {
        if (f[5] == 2) { throw std::out_of_range("nothing has been published under " + prefix); }
        return out;
    }
    uint16_t count = rd16(f, 5); size_t off = 7;
    for (uint16_t i = 0; i < count; ++i) { uint64_t seq = rd64(f, off), gen = rd64(f, off + 8); uint16_t nl = rd16(f, off + 16); off += 18;
        PlaneRow r; r.gen = gen; r.name = f.substr(off, nl); off += nl; uint32_t bl = rd32(f, off); off += 4; r.bytes = f.substr(off, bl); off += bl;
        if (seq > after) { after = seq; }
        out.push_back(r); }
    return out;
}

}  // namespace frogram
