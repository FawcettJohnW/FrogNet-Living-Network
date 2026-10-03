// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "frogram.hpp"
#include "fnwp_engine.hpp"
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
        if (r <= 0) throw Unreachable("the far end closed the connection");
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
std::string Json::quote(const std::string& s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c == '\n') o += "\\n"; else if (c == '\r') o += "\\r"; else if (c == '\t') o += "\\t";
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += char(c);
    }
    return o + "\"";
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
                       j.o.emplace_back(k.s, val()); ws(); if (t[i] == ',') { ++i; continue; } if (t[i++] == '}') return j; bad("}"); } }
        if (c == '[') { j.type = Json::Arr; ++i; ws(); if (t[i] == ']') { ++i; return j; }
            for (;;) { j.a.push_back(val()); ws(); if (t[i] == ',') { ++i; continue; } if (t[i++] == ']') return j; bad("]"); } }
        if (c == '"') { j.type = Json::Str; ++i;
            while (i < t.size() && t[i] != '"') {
                if (t[i] == '\\') { char e = t[++i];
                    if (e == 'n') j.s += '\n'; else if (e == 't') j.s += '\t'; else if (e == 'r') j.s += '\r';
                    else if (e == 'b') j.s += '\b'; else if (e == 'f') j.s += '\f';
                    else if (e == 'u') { unsigned cp = std::stoul(t.substr(i + 1, 4), nullptr, 16); i += 4;
                        if (cp < 0x80) j.s += char(cp);
                        else if (cp < 0x800) { j.s += char(0xC0 | (cp >> 6)); j.s += char(0x80 | (cp & 0x3F)); }
                        else { j.s += char(0xE0 | (cp >> 12)); j.s += char(0x80 | ((cp >> 6) & 0x3F)); j.s += char(0x80 | (cp & 0x3F)); } }
                    else j.s += e;
                    ++i; }
                else j.s += t[i++]; }
            ++i; return j; }
        if (!t.compare(i, 4, "true")) { i += 4; j.type = Json::Bool; j.b = true; return j; }
        if (!t.compare(i, 5, "false")) { i += 5; j.type = Json::Bool; return j; }
        if (!t.compare(i, 4, "null")) { i += 4; return j; }
        size_t used = 0; j.type = Json::Num;
        try { j.n = std::stod(t.substr(i), &used); } catch (...) { bad("number"); }
        i += used; return j;
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
struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err;
                          Session::Impl* co = nullptr; std::string co_key; };   // [CLIENT_COALESCES_V1] its entry, if any
struct Session::InFlight { std::shared_ptr<fnwp::ClientEngine::Ctx> ctx; std::shared_ptr<Pending> waiter;
                           uint64_t ticket = 0; bool in_turn = false; };   // [WIRE_ORDER_TURNS_V1]
struct Session::Impl {
    std::unique_ptr<fnwp::ClientEngine> eng;
    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false;
                 uint64_t ticket = 0; bool in_turn = false; };   // [WIRE_ORDER_TURNS_V1] a resend made in its call's turn
    // [WIRE_ORDER_TURNS_V1] per templated instance: tickets stamped (arrival order) and applied; replies held for their turn
    std::mutex gate_m; std::map<fnwp::detail::IKey, uint64_t> issued, applied;
    std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<Session::InFlight, std::string>>> held;
    std::mutex q_m; std::condition_variable q_cv, room_cv; std::deque<Job> jobs; bool stop = false;
    using InFlight = Session::InFlight;
    std::mutex pend_m; uint32_t seq = 0; std::map<uint32_t, InFlight> inflight; std::string dead;
    std::mutex st_m; Stats st; std::thread sender, reader, data_reader;
    // [HIGH_SPEED_DATA_SOCKET_V1] a request the wire cannot carry differenced (REQ_RAW) and that would own the request
    // socket longer than the ceiling goes on the data socket in fair segments; replies come back the same way
    fnwp::dataplane::Ceiling ceil; std::unique_ptr<fnwp::dataplane::Sender> dsend; fnwp::dataplane::Reassembler reasm;
    fnwp::dataplane::Landed landed; std::mutex dat_m;
    std::mutex land_m; std::vector<std::thread> landers;   // threads applying marker replies as their payloads land
    static const size_t QUEUE_MAX = 4096;                // new requests beyond this wait, as TCP would hold them
    // [CLIENT_COALESCES_V1] requests in flight, by the exact request: an identical call waits here for the one answer
    std::mutex co_m; std::map<std::string, std::shared_ptr<Pending>> coalesce;
    static void finish(const std::shared_ptr<Pending>& p, const fnwp::Answer* a, const std::string& err) {
        if (!p) return;
        if (p->co) {   // out of the table first: a request made after this answer is its own request
            std::lock_guard<std::mutex> g(p->co->co_m);
            auto it = p->co->coalesce.find(p->co_key); if (it != p->co->coalesce.end() && it->second == p) p->co->coalesce.erase(it);
        }
        { std::lock_guard<std::mutex> g(p->m); if (a) p->answer = *a; p->err = err; p->done = true; } p->cv.notify_all();
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
    { int sb = 0; socklen_t sl = sizeof sb; getsockopt(req_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<fr_optval_t*>(&sb), &sl); d_->ceil.set_hard(size_t(sb)); }
    d_->dsend.reset(new fnwp::dataplane::Sender([this](const std::string& rec) { std::lock_guard<std::mutex> g(d_->dat_m); send_all(dat_, rec); }));
    d_->dsend->start();
    d_->reader = std::thread([this] { read_loop(ret_, false); });
    d_->data_reader = std::thread([this] { read_loop(dat_, true); });
    d_->sender = std::thread([this] { send_loop(); });
}
Session::~Session() {
    shutdown();
    { std::lock_guard<std::mutex> g(d_->q_m); d_->stop = true; } d_->q_cv.notify_all(); d_->room_cv.notify_all();
    if (d_->sender.joinable()) d_->sender.join();
    if (d_->dsend) d_->dsend->stop();
    if (d_->reader.joinable()) d_->reader.join();
    if (d_->data_reader.joinable()) d_->data_reader.join();
    d_->landed.close();
    { std::lock_guard<std::mutex> g(d_->land_m); for (auto& t : d_->landers) if (t.joinable()) t.join(); }
    if (req_ >= 0) FR_CLOSE(req_);
    if (ret_ >= 0) FR_CLOSE(ret_);
    if (dat_ >= 0) FR_CLOSE(dat_);
}
void Session::send_loop() {
    for (;;) {
        Impl::Job j;
        { std::unique_lock<std::mutex> l(d_->q_m); d_->q_cv.wait(l, [&] { return d_->stop || !d_->jobs.empty(); });
          if (d_->jobs.empty()) return;
          j = std::move(d_->jobs.front()); d_->jobs.pop_front(); }
        d_->room_cv.notify_one();
        fnwp::ClientEngine::Out o;
        try { o = j.is_resend ? j.resend : d_->eng->prepare(j.method, j.path, j.body); }
        catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); if (!j.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; } continue; }
        std::string why;
        // [HIGH_SPEED_DATA_SOCKET_V1] [DATA_MARKER_V1] the frame's path is decided as it leaves: whole on the request
        // socket, or -- when it cannot be differenced and would own the request socket past the ceiling -- its MARKER
        // on the request socket, in order, and its body in segments on the data socket under the same sequence
        std::string frame = o.frame, payload; bool via_data = false;
        if (uint8_t(o.frame[4]) == semwire::OP_REQ_RAW && o.frame.size() > d_->ceil.whole_max()) {
            try { frame = fnwp::ClientEngine::request_marker(o.frame, payload); via_data = true; }
            catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); continue; }
        }
        uint32_t seq = 0;
        { std::lock_guard<std::mutex> g(d_->pend_m);
          if (!d_->dead.empty()) why = d_->dead;
          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn}; } }
        if (why.empty()) {
            try {
                send_frame(req_, frame); d_->ceil.note(frame.size() + 4);
                trace("OUT seq=" + std::to_string(seq) + " op=" + std::to_string(uint8_t(frame[4])) + (via_data ? " marker" : "") + " " + j.method + " " + j.path.substr(0, 90) + " body=" + std::to_string(j.body.size()) + "B raw=" + (o.ctx->raw ? "y" : "n") + " " + j.body.substr(0, 300));
                if (via_data) { d_->dsend->submit(seq, payload); std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.data_frames; d_->st.data_bytes += payload.size(); }
            } catch (const std::exception& e) { why = e.what(); }
        }
        if (!why.empty()) Impl::finish(j.waiter, nullptr, why);   // the connection is gone
    }
}
// [WIRE_ORDER_TURNS_V1] the turn machinery: stamp on arrival; apply in turn or hold; close the turn, apply the held
void Session::stamp(InFlight& inf) {
    if (inf.in_turn || !inf.ctx || inf.ctx->raw) return;          // raw moves no reference: no turn (as in Python)
    std::lock_guard<std::mutex> g(d_->gate_m); inf.ticket = ++d_->issued[inf.ctx->ik];
}
void Session::ready(const InFlight& inf, const std::string& reply) {
    if (inf.ticket && !inf.in_turn) {
        std::lock_guard<std::mutex> g(d_->gate_m);
        if (d_->applied[inf.ctx->ik] != inf.ticket - 1) { d_->held[inf.ctx->ik].emplace(inf.ticket, std::make_pair(inf, reply)); return; }
    }
    apply_reply(inf, reply);
}
void Session::turn_done(const InFlight& inf) {
    if (!inf.ticket) return;
    std::optional<std::pair<InFlight, std::string>> next;
    { std::lock_guard<std::mutex> g(d_->gate_m);
      uint64_t a = ++d_->applied[inf.ctx->ik];
      auto h = d_->held.find(inf.ctx->ik);
      if (h != d_->held.end()) { auto t = h->second.find(a + 1); if (t != h->second.end()) { next = std::move(t->second); h->second.erase(t); } } }
    if (next) apply_reply(next->first, next->second);                // the next ticket's turn
}
void Session::apply_reply(const InFlight& inf, const std::string& reply) {
    try {
        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);
        trace("APPLIED " + inf.ctx->method + " " + inf.ctx->path.substr(0, 90) + (s.done ? " done status=" + std::to_string(s.answer.status) : " resend"));
        if (s.done) {
            if (!inf.waiter) { std::lock_guard<std::mutex> g(d_->st_m); if (s.answer.status >= 400) ++d_->st.posts_refused; }
            Impl::finish(inf.waiter, &s.answer, "");
            turn_done(inf);
            return;
        }
        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.waiter = inf.waiter;
        j.ticket = inf.ticket; j.in_turn = inf.ticket != 0;          // the round trip happens in this call's turn
        { std::lock_guard<std::mutex> g(d_->q_m); d_->jobs.push_front(std::move(j)); } d_->q_cv.notify_one();
    } catch (const std::exception& e) {
        trace(std::string("APPLY FAILED ") + inf.ctx->method + " " + inf.ctx->path.substr(0, 90) + ": " + e.what());
        if (!inf.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; }
        Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what());
        turn_done(inf);
    }
}
void Session::read_loop(int fd, bool data) {
    std::string why;
    try {
        for (;;) {
            std::string f = recv_frame(fd);
            if (data) {                                  // a segment of a payload; it lands when its last segment does
                { std::lock_guard<std::mutex> g(d_->st_m); d_->st.data_bytes += f.size() + 4; }
                auto done = d_->reasm.feed(f);
                if (!done) continue;
                { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.data_frames; }
                d_->landed.put(done->first, std::move(done->second));
                continue;
            }
            if (f.size() < 9) throw Refused("reply frame too short");
            const uint32_t seq = rd32(f, 0); std::string reply = f.substr(4);
            trace("IN  seq=" + std::to_string(seq) + " op=" + std::to_string(uint8_t(reply[4])) + " " + std::to_string(reply.size()) + "B");
            Impl::InFlight inf;
            { std::lock_guard<std::mutex> g(d_->pend_m); auto it = d_->inflight.find(seq);
              if (it == d_->inflight.end()) throw Refused("reply tagged a seq that was never sent");
              inf = it->second; d_->inflight.erase(it); }
            if (fnwp::ClientEngine::is_reply_marker(reply)) {
                // [DATA_MARKER_V1] the marker holds this reply's place in wire order; the payload lands on the data
                // socket; only later replies to this instance wait for it (the engine holds them)
                stamp(inf);                               // [WIRE_ORDER_TURNS_V1] its place is its arrival
                std::lock_guard<std::mutex> g(d_->land_m);
                d_->landers.emplace_back([this, inf, reply, seq] {
                    try { ready(inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq))); }
                    catch (const std::exception& e) { Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
                });
                continue;
            }
            stamp(inf); ready(inf, reply);               // [WIRE_ORDER_TURNS_V1] in its turn, or held for it
        }
    } catch (const std::exception& e) { why = e.what(); }
    std::map<uint32_t, Impl::InFlight> lost;
    { std::lock_guard<std::mutex> g(d_->pend_m); if (d_->dead.empty()) d_->dead = why.empty() ? "connection closed" : why; lost.swap(d_->inflight); }
    d_->landed.close();
    for (auto& kv : lost) Impl::finish(kv.second.waiter, nullptr, d_->dead);
    { std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<InFlight, std::string>>> h;
      { std::lock_guard<std::mutex> g(d_->gate_m); h.swap(d_->held); }
      for (auto& i : h) for (auto& t : i.second) Impl::finish(t.second.first.waiter, nullptr, d_->dead); }
    if (!data) { ::shutdown(req_, FR_SHUT_RDWR); ::shutdown(dat_, FR_SHUT_RDWR); } else { ::shutdown(ret_, FR_SHUT_RDWR); }
}
// [ROUND_TRIPS_V1] Blocking calls made by THIS thread: each one is a round trip the caller waited out. The LISP engine's
// request thread reads it around one operation (transport.stats "calls"); held reads on their own threads don't count.
static thread_local uint64_t tl_calls = 0;
static thread_local std::vector<std::pair<std::string, uint64_t>>* tl_wlog = nullptr;
void log_writes_to(std::vector<std::pair<std::string, uint64_t>>* sink) { tl_wlog = sink; }
std::vector<std::pair<std::string, uint64_t>>* current_write_log() { return tl_wlog; }
uint64_t calls_this_thread() { return tl_calls; }
Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    ++tl_calls;
    std::string key; key.reserve(method.size() + path.size() + body.size() + 2);
    key += method; key += '\0'; key += path; key += '\0'; key += body;
    std::shared_ptr<Pending> p; bool joined = false;
    {   std::lock_guard<std::mutex> g(d_->co_m);
        auto it = d_->coalesce.find(key);
        if (it != d_->coalesce.end()) { p = it->second; joined = true; }
        else { p = std::make_shared<Pending>(); p->co = d_.get(); p->co_key = key; d_->coalesce.emplace(key, p); }
    }
    if (joined) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.coalesced; }
    if (!joined)
    { std::unique_lock<std::mutex> l(d_->q_m);
      { std::string dead; { std::lock_guard<std::mutex> g(d_->pend_m); dead = d_->dead; }
        if (!dead.empty()) { Impl::finish(p, nullptr, dead); throw Unreachable(dead); } }
      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });
      if (d_->stop) { Impl::finish(p, nullptr, "session shut down"); throw Unreachable("session shut down"); }
      Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->jobs.push_back(std::move(j)); }
    d_->q_cv.notify_one();
    std::unique_lock<std::mutex> l(p->m);
    if (!p->cv.wait_for(l, std::chrono::duration<double>(timeout_s), [&] { return p->done; })) throw Unreachable("no reply within the timeout");
    if (!p->err.empty()) throw Unreachable(p->err);
    Json obj = Json::parse(p->answer.body);
    if (p->answer.status >= 400 || !obj["ok"].b) throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + obj["error"].s);
    return obj;
}
void Session::post(const std::string& method, const std::string& path, const std::string& body) {
    { std::unique_lock<std::mutex> l(d_->q_m);
      { std::lock_guard<std::mutex> g(d_->pend_m); if (!d_->dead.empty()) throw Unreachable(d_->dead); }
      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });
      if (d_->stop) throw Unreachable("session shut down");
      Impl::Job j; j.method = method; j.path = path; j.body = body; d_->jobs.push_back(std::move(j)); }
    d_->q_cv.notify_one();
    std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posted;
}
Stats Session::stats() const {
    Stats s; { std::lock_guard<std::mutex> g(d_->st_m); s = d_->st; }
    auto e = d_->eng->stats();
    s.raw = e.raw; s.repeat = e.repeat; s.same = e.same; s.miss = e.miss; s.full = e.full; s.diff = e.diff; s.rdiff = e.rdiff; s.rraw = e.rraw;
    s.bytes_out = e.bytes_out; s.bytes_in = e.bytes_in;
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
    s_.post("POST", api_ + "?op=write", "{\"service\":" + Json::quote(sv) + ",\"variable\":" + Json::quote(var) +
            ",\"instance\":" + Json::quote(inst) + ",\"bag\":" + bag + "}");
}
std::vector<Cell> Memory::read(const std::string& sv, const std::string& var, const std::string& inst,
                               int64_t after, double wait_s, int fresh_s) {
    std::string q = api_ + "?op=read&service=" + urlenc(sv) + "&variable=" + urlenc(var);
    if (!inst.empty()) q += "&instance=" + urlenc(inst);
    if (after >= 0) q += "&after=" + std::to_string(after);
    if (wait_s > 0) { char b[32]; snprintf(b, sizeof b, "&wait_s=%.3f", wait_s); q += b; }
    if (fresh_s > 0) q += "&fresh_s=" + std::to_string(fresh_s);
    Json o = s_.call("GET", q, "", 15.0 + wait_s); std::vector<Cell> out;
    for (auto& r : o["rows"].a) { Cell c; c.id = uint64_t(r["id"].n); c.service = r["service"].s; c.variable = r["variable"].s;
        c.instance = r["instance"].s; c.bag = r["bag"]; c.updated = r["updated_epoch"].n; out.push_back(c); }
    return out;
}
void Memory::remove(uint64_t id) { s_.call("DELETE", api_ + "?op=remove&id=" + std::to_string(id)); }

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
