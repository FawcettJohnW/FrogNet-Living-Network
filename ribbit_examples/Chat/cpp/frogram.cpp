// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "frogram.hpp"

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
#include <sstream>

namespace frogram {
namespace {

const char MAGIC[] = "FNW1";
enum : uint8_t { REQ_REPEAT = 0x02, REQ_RAW = 0x03, RESP_SAME = 0x13, RESP_RAW = 0x14,
                 REQ_MISS = 0x21, OP_ERROR = 0x30, OP_HELLO = 0x50 };
const size_t SAME_MAX = 256;

#ifdef _WIN32
struct WsaOnce { WsaOnce() { WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w) != 0) throw Unreachable("WSAStartup failed"); } };
#endif

int dial(const std::string& host, int port) {
#ifdef _WIN32
    static WsaOnce wsa_once;
#endif
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res) != 0 || !res)
        throw Unreachable(host + ": name does not resolve");
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

// The request hash is a lookup key to the far end and nothing more; any stable
// 16 bytes will do. Two independent 64-bit FNV-1a passes.
std::string hash16(const std::string& s) {
    uint64_t a = 1469598103934665603ULL, b = 0x9E3779B97F4A7C15ULL;
    for (unsigned char c : s) { a = (a ^ c) * 1099511628211ULL; b = (b ^ (c + 0x5bu)) * 0x100000001B3ULL; b ^= b >> 29; }
    return be64(a) + be64(b);
}
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
struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; std::string frame, err; };
struct Session::Impl {
    std::mutex send_m, pend_m, cache_m; uint32_t seq = 0; std::string dead;
    std::map<uint32_t, std::shared_ptr<Pending>> pending;
    std::map<std::string, std::string> seen;                          // req_hash -> same_id
    std::map<std::string, std::pair<Json, std::list<std::string>::iterator>> bodies;   // same_id -> body
    std::list<std::string> lru; Stats st; std::thread reader;
};

Session::Session(const std::string& host, int port) : host_(host), port_(port), d_(new Impl) {
    std::random_device rd; std::ostringstream t; t << "frogram-cpp-" << std::hex << rd() << rd();
    token_ = t.str();
    req_ = dial(host, port);
    // THE BUFFER IS THE LATENCY. A writer that does not wait for acknowledgements can
    // only be held back by TCP, and TCP holds it back when this buffer and the far
    // end's are full. Everything sitting in them is a write that has not been applied
    // yet -- and, since writing replaces, mostly writes that are already out of date.
    // Small buffers keep that backlog to a fraction of a second instead of many.
    { int buf = 32 * 1024; setsockopt(req_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&buf), sizeof buf); }
    send_frame(req_, hello(token_));
    ret_ = dial(host, port); send_frame(ret_, hello("RETURN:" + token_));
    std::string f = recv_frame(ret_);
    if (f.size() < 5 || f.compare(0, 4, MAGIC) || uint8_t(f[4]) != OP_HELLO) throw Refused("expected the far end's HELLO");
    d_->reader = std::thread([this] { read_loop(); });
}
Session::~Session() {
    if (req_ >= 0) ::shutdown(req_, FR_SHUT_RDWR);
    if (ret_ >= 0) ::shutdown(ret_, FR_SHUT_RDWR);
    if (d_->reader.joinable()) d_->reader.join();
    if (req_ >= 0) FR_CLOSE(req_);
    if (ret_ >= 0) FR_CLOSE(ret_);
}
void Session::read_loop() {
    std::string why;
    try {
        for (;;) {
            std::string f = recv_frame(ret_);
            uint32_t seq = rd32(f, 0); std::shared_ptr<Pending> p;
            { std::lock_guard<std::mutex> g(d_->pend_m); auto it = d_->pending.find(seq);
              if (it == d_->pending.end()) { throw Refused("reply tagged a seq that was never sent"); }
              p = it->second; d_->pending.erase(it); }
            if (!p) {                                                    // a post(): nobody is waiting for this one
                bool refused = f.size() > 8 && (uint8_t(f[8]) == OP_ERROR || (uint8_t(f[8]) == RESP_RAW && f.size() >= 31 && rd16(f, 29) >= 400));
                if (refused) ++d_->st.posts_refused;
                continue;
            }
            { std::lock_guard<std::mutex> g(p->m); p->frame = f.substr(4); p->done = true; } p->cv.notify_all();
        }
    } catch (const std::exception& e) { why = e.what(); }
    std::lock_guard<std::mutex> g(d_->pend_m); d_->dead = why;
    for (auto& kv : d_->pending) { if (!kv.second) continue; { std::lock_guard<std::mutex> h(kv.second->m); kv.second->err = why; kv.second->done = true; } kv.second->cv.notify_all(); }
    d_->pending.clear();
}
std::string Session::exchange(const std::string& frame, double timeout_s) {
    auto p = std::make_shared<Pending>();
    { std::lock_guard<std::mutex> g(d_->send_m);                       // the seq IS the order frames leave in
      { std::lock_guard<std::mutex> h(d_->pend_m); if (!d_->dead.empty()) throw Unreachable(d_->dead); d_->pending[d_->seq++] = p; }
      send_frame(req_, frame); }
    std::unique_lock<std::mutex> l(p->m);
    if (!p->cv.wait_for(l, std::chrono::duration<double>(timeout_s), [&] { return p->done; })) throw Unreachable("no reply within the timeout");
    if (!p->err.empty()) throw Unreachable(p->err);
    d_->st.bytes_out += frame.size(); d_->st.bytes_in += p->frame.size();
    if (p->frame.size() < 5 || p->frame.compare(0, 4, MAGIC)) throw Refused("reply is not FNW1");
    return p->frame;
}
Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    std::string http = "{\"method\":" + Json::quote(method) + ",\"path\":" + Json::quote(path) + ",\"headers\":" +
        (body.empty() ? "{}" : "{\"Content-Type\":\"application/json\"}") + ",\"body\":" + Json::quote(body) +
        ",\"host\":\"127.0.0.1\",\"port\":8080}";
    std::string h = hash16(http); bool repeat;
    { std::lock_guard<std::mutex> g(d_->cache_m); auto it = d_->seen.find(h); repeat = it != d_->seen.end() && d_->bodies.count(it->second); }
    std::string f; uint8_t op = 0;
    for (int attempt = 0; attempt < 2; ++attempt) {
        if (repeat) { ++d_->st.repeat; f = exchange(std::string(MAGIC, 4) + char(REQ_REPEAT) + h, timeout_s); }
        else        { ++d_->st.raw;    f = exchange(std::string(MAGIC, 4) + char(REQ_RAW) + h + be32(uint32_t(http.size())) + http, timeout_s); }
        op = uint8_t(f[4]);
        if (op == REQ_MISS) { ++d_->st.miss; std::lock_guard<std::mutex> g(d_->cache_m); d_->seen.erase(h); repeat = false; continue; }
        if (op == RESP_SAME) {
            std::lock_guard<std::mutex> g(d_->cache_m); auto it = d_->bodies.find(f.substr(5, 16));
            if (it == d_->bodies.end()) { d_->seen.erase(h); repeat = false; continue; }      // ask the same source again, in full
            d_->lru.splice(d_->lru.end(), d_->lru, it->second.second); ++d_->st.same; return it->second.first;
        }
        break;
    }
    if (op == OP_ERROR) throw Refused("far end: " + std::to_string(rd16(f, 5)) + " " + f.substr(7));
    if (op != RESP_RAW) throw Refused("far end answered an op this client does not speak");
    std::string same_id = f.substr(5, 16); uint32_t plen = rd32(f, 21); std::string pl = f.substr(25, plen);
    uint16_t status = rd16(pl, 0), hlen = rd16(pl, 2); Json obj = Json::parse(pl.substr(4 + hlen));
    if (status >= 400 || !obj["ok"].b) throw Refused("HTTP " + std::to_string(status) + ": " + obj["error"].s);
    std::lock_guard<std::mutex> g(d_->cache_m);
    d_->seen[h] = same_id;
    auto old = d_->bodies.find(same_id); if (old != d_->bodies.end()) { d_->lru.erase(old->second.second); d_->bodies.erase(old); }
    d_->lru.push_back(same_id); d_->bodies[same_id] = { obj, std::prev(d_->lru.end()) };
    while (d_->bodies.size() > SAME_MAX) { d_->bodies.erase(d_->lru.front()); d_->lru.pop_front(); }
    return obj;
}
void Session::post(const std::string& method, const std::string& path, const std::string& body) {
    std::string http = "{\"method\":" + Json::quote(method) + ",\"path\":" + Json::quote(path) + ",\"headers\":" +
        (body.empty() ? "{}" : "{\"Content-Type\":\"application/json\"}") + ",\"body\":" + Json::quote(body) +
        ",\"host\":\"127.0.0.1\",\"port\":8080}";
    std::string frame = std::string(MAGIC, 4) + char(REQ_RAW) + hash16(http) + be32(uint32_t(http.size())) + http;
    std::lock_guard<std::mutex> g(d_->send_m);
    { std::lock_guard<std::mutex> h(d_->pend_m); if (!d_->dead.empty()) throw Unreachable(d_->dead); d_->pending[d_->seq++] = nullptr; }
    send_frame(req_, frame); ++d_->st.posted;
}
Stats Session::stats() const { return d_->st; }
void Session::shutdown() {
    if (req_ >= 0) ::shutdown(req_, FR_SHUT_RDWR);
    if (ret_ >= 0) ::shutdown(ret_, FR_SHUT_RDWR);
}

// ---------------------------------------------------------------- Memory
uint64_t Memory::write(const std::string& sv, const std::string& var, const std::string& inst, const std::string& bag) {
    Json o = s_.call("POST", api_ + "?op=write", "{\"service\":" + Json::quote(sv) + ",\"variable\":" + Json::quote(var) +
                     ",\"instance\":" + Json::quote(inst) + ",\"bag\":" + bag + "}");
    return uint64_t(o["id"].n);
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
