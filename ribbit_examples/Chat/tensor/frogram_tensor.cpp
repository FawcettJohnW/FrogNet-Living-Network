// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// frogram_tensor -- the FrogNet TENSOR PLANE in C++, behind a C ABI.
//
// EXPERIMENT A. This is tensor_plane.py's wire contract and nothing else: the
// same frames, the same request, the same three replies, the same digest, the
// same wait. A C++ client reads from a Python server and a Python client reads
// from a C++ server, so the algorithm above it (psychedelic_backend.py) does not
// change by one line. Nothing learned today about choreography is in here.
//
//   frame    [len:8 BE][payload]
//   request  JSON {"name","gen","mode":"by_identity"|"current","want","inc","have","verify","wait_s","from"}
//   reply    "GONE" + JSON {"reason":"never_published"|"superseded"|"wait_expired", "gen","digest","inc","waited_s"}
//            "SAME" + JSON {"gen","digest","inc"}
//            "DATA" + [headlen:8 BE] + JSON {"gen","digest","inc"} + the tensor's bytes
//   wait     the request is held until the name's generation >= gen (or exists, if gen is
//            null), parked on a condition the publish signals; expiry is its own answer.
//   digest   crc32 of each half of the bytes, as 16 hex characters; computed only when a
//            request needs it (want / have / verify), then kept for that state.
//
// ZERO COPY where it counts: a DATA reply is received straight into the caller's buffer
// (a torch tensor's data_ptr()), and publish(copy=0) serves the caller's own memory.
// NO FALLBACKS: FROGNET_PLANE_DIGEST other than crc32 is refused, not approximated.
#include "../cpp/frogram.hpp"

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#include <zlib.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

using frogram::Json;
namespace {
const uint64_t MAX_FRAME = 1ULL << 30; const double MAX_SERVER_WAIT_S = 300.0;

// zlib's own crc32 -- the very function tensor_plane.py's zlib.crc32 calls, so the digests are identical by
// construction and as fast. (A bytewise table CRC was tried first: correct, and 10x slower -- 35 ms per pass
// over a 16.9 MB gradient, which made this plane SLOWER than the Python one. Measured, then replaced.)
uint32_t crc32z(const unsigned char* p, size_t n) { uLong c = crc32(0L, Z_NULL, 0); while (n) { uInt k = n > 0x40000000u ? 0x40000000u : uInt(n); c = crc32(c, p, k); p += k; n -= k; } return uint32_t(c); }
std::string digest_of(const char* p, size_t n) { size_t h = n / 2; char b[24];
    snprintf(b, sizeof b, "%08x%08x", crc32z(reinterpret_cast<const unsigned char*>(p), h), crc32z(reinterpret_cast<const unsigned char*>(p) + h, n - h)); return b; }

bool send_all(int fd, const char* p, size_t n) { while (n) { ssize_t r = ::send(fd, p, n, MSG_NOSIGNAL); if (r <= 0) return false; p += r; n -= size_t(r); } return true; }
bool recv_all(int fd, char* p, size_t n) { while (n) { ssize_t r = ::recv(fd, p, n, 0); if (r <= 0) return false; p += r; n -= size_t(r); } return true; }
std::string be64(uint64_t v) { std::string s(8, '\0'); for (int i = 7; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
bool recv_len(int fd, uint64_t& n) { unsigned char b[8]; if (!recv_all(fd, reinterpret_cast<char*>(b), 8)) return false; n = 0; for (int i = 0; i < 8; ++i) n = (n << 8) | b[i]; return n <= MAX_FRAME; }
std::string jstr(const std::string& s) { return Json::quote(s); }
std::string jopt(const std::string& s, bool has) { return has ? jstr(s) : "null"; }
double mono() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct State { long long gen = 0; std::shared_ptr<std::string> owned; const char* ptr = nullptr; size_t n = 0; std::string inc; bool has_inc = false; std::string digest; };
}  // namespace

struct ft_server {
    int fd = -1, port = 0; std::atomic<bool> stop{false}; std::thread acceptor;
    std::mutex m; std::condition_variable cv; std::map<std::string, State> cur;
    uint64_t same_replies = 0, data_replies = 0, wait_expired = 0, bytes_sent = 0, states_offered = 0;

    void serve(int c) {
        int one = 1; setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        for (;;) {
            uint64_t n; if (!recv_len(c, n)) break; std::string raw(n, '\0'); if (!recv_all(c, &raw[0], n)) break;
            Json q; try { q = Json::parse(raw); } catch (const std::exception&) { break; }
            std::string name = q["name"].s, mode = q["mode"].type == Json::Str ? q["mode"].s : "by_identity";
            bool has_gen = q["gen"].type == Json::Num, has_want = q["want"].type == Json::Str, has_inc = q["inc"].type == Json::Str, has_have = q["have"].type == Json::Str;
            long long want_gen = has_gen ? (long long)q["gen"].n : 0; double wait_s = q["wait_s"].type == Json::Num ? q["wait_s"].n : 0.0;
            bool verify = q["verify"].type == Json::Bool ? q["verify"].b : true;
            std::unique_lock<std::mutex> l(m);
            if (wait_s > 0) {
                double t0 = mono(), dl = t0 + std::min(wait_s, MAX_SERVER_WAIT_S);
                auto ok = [&] { auto it = cur.find(name); return it != cur.end() && (!has_gen || it->second.gen >= want_gen); };
                while (!ok() && mono() < dl && !stop) cv.wait_for(l, std::chrono::duration<double>(std::min(dl - mono(), 0.05)));
                if (!ok()) { ++wait_expired; auto it = cur.find(name); char w[32]; snprintf(w, sizeof w, "%.3f", mono() - t0);
                    std::string j = std::string("{\"reason\": \"wait_expired\", \"gen\": ") + (it == cur.end() ? "null" : std::to_string(it->second.gen)) + ", \"waited_s\": " + w + "}";
                    l.unlock(); std::string f = be64(4 + j.size()) + "GONE" + j; if (!send_all(c, f.data(), f.size())) break; continue; }
            }
            auto it = cur.find(name);
            if (it == cur.end()) { l.unlock(); std::string j = "{\"reason\": \"never_published\"}", f = be64(4 + j.size()) + "GONE" + j; if (!send_all(c, f.data(), f.size())) break; continue; }
            State& s = it->second;
            if ((has_want || has_have || verify) && s.digest.empty()) s.digest = digest_of(s.ptr, s.n);      // on demand, then kept for this state
            std::string idj = "{\"gen\": " + std::to_string(s.gen) + ", \"digest\": " + jopt(s.digest, !s.digest.empty()) + ", \"inc\": " + jopt(s.inc, s.has_inc) + "}";
            bool by_id = mode == "by_identity";
            bool gone = by_id && ((has_inc && s.has_inc && q["inc"].s != s.inc) || (has_want && q["want"].s != s.digest) || (has_gen && want_gen != s.gen));
            if (gone) { std::string j = "{\"reason\": \"superseded\", \"gen\": " + std::to_string(s.gen) + ", \"digest\": " + jopt(s.digest, !s.digest.empty()) + ", \"inc\": " + jopt(s.inc, s.has_inc) + "}";
                l.unlock(); std::string f = be64(4 + j.size()) + "GONE" + j; if (!send_all(c, f.data(), f.size())) break; continue; }
            if (has_have && q["have"].s == s.digest) { ++same_replies; l.unlock(); std::string f = be64(4 + idj.size()) + "SAME" + idj; if (!send_all(c, f.data(), f.size())) break; continue; }
            ++data_replies; bytes_sent += s.n; std::shared_ptr<std::string> keep = s.owned; const char* p = s.ptr; size_t bn = s.n; l.unlock();   // the state stays alive while it is sent
            std::string head = be64(4 + 8 + idj.size() + bn) + "DATA" + be64(idj.size()) + idj;
            if (!send_all(c, head.data(), head.size()) || !send_all(c, p, bn)) break;
        }
        ::close(c);
    }
};
struct ft_client {
    std::string me; std::mutex m;
    struct Link { int fd = -1; std::mutex m; }; std::map<std::string, std::shared_ptr<Link>> links;
    std::map<std::string, std::string> have;                 // host:port/name -> digest of the copy the caller already holds
    uint64_t reads = 0, same = 0, data = 0, bytes = 0;
};

extern "C" {
struct ft_result { int status; unsigned long long nbytes; long long gen; int has_gen; char digest[24]; char inc[128]; char reason[32]; double waited_s; char error[256]; };
enum { FT_DATA = 0, FT_SAME = 1, FT_GONE = 2, FT_ERROR = 3 };

ft_server* ft_server_create(const char* bind_ip, int port, char* err, int errlen) {
    const char* algo = getenv("FROGNET_PLANE_DIGEST");
    if (algo && strcmp(algo, "crc32")) { snprintf(err, size_t(errlen), "FROGNET_PLANE_DIGEST=%s: this build speaks crc32 only", algo); return nullptr; }
    ft_server* s = new ft_server; s->fd = ::socket(AF_INET, SOCK_STREAM, 0); int one = 1; setsockopt(s->fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(port)); inet_pton(AF_INET, bind_ip, &a.sin_addr);
    if (::bind(s->fd, reinterpret_cast<sockaddr*>(&a), sizeof a) || ::listen(s->fd, 256)) { snprintf(err, size_t(errlen), "cannot listen on %s:%d: %s", bind_ip, port, strerror(errno)); ::close(s->fd); delete s; return nullptr; }
    socklen_t al = sizeof a; getsockname(s->fd, reinterpret_cast<sockaddr*>(&a), &al); s->port = ntohs(a.sin_port);
    s->acceptor = std::thread([s] { for (;;) { int c = ::accept(s->fd, nullptr, nullptr); if (c < 0) { if (s->stop) return; continue; } std::thread([s, c] { s->serve(c); }).detach(); } });
    return s;
}
int ft_server_port(ft_server* s) { return s->port; }
// copy=1 freezes the state (one copy, as tensor_bytes does). copy=0 serves the caller's memory: the caller keeps it alive
// and unchanged until it publishes that name again.
void ft_server_publish(ft_server* s, const char* name, long long gen, const void* data, unsigned long long n, const char* inc, int copy) {
    State st; st.gen = gen; st.n = size_t(n); if (inc) { st.inc = inc; st.has_inc = true; }
    if (copy) { st.owned = std::make_shared<std::string>(static_cast<const char*>(data), size_t(n)); st.ptr = st.owned->data(); } else st.ptr = static_cast<const char*>(data);
    { std::lock_guard<std::mutex> g(s->m); s->cur[name] = std::move(st); ++s->states_offered; } s->cv.notify_all();
}
void ft_server_stats(ft_server* s, unsigned long long* out5) { std::lock_guard<std::mutex> g(s->m); out5[0] = s->states_offered; out5[1] = s->same_replies; out5[2] = s->data_replies; out5[3] = s->bytes_sent; out5[4] = s->wait_expired; }
void ft_server_close(ft_server* s) { s->stop = true; ::shutdown(s->fd, SHUT_RDWR); ::close(s->fd); s->cv.notify_all(); if (s->acceptor.joinable()) s->acceptor.join(); /* serving threads end with their sockets */ }

ft_client* ft_client_create(const char* me) { ft_client* c = new ft_client; c->me = me ? me : "cpp"; return c; }
static int dial(const char* host, int port, std::string& err) {
    addrinfo hints{}, *res = nullptr; hints.ai_socktype = SOCK_STREAM; if (getaddrinfo(host, std::to_string(port).c_str(), &hints, &res) || !res) { err = "name does not resolve"; return -1; }
    int fd = -1; for (addrinfo* a = res; a; a = a->ai_next) { fd = ::socket(a->ai_family, a->ai_socktype, a->ai_protocol); if (fd < 0) continue; if (!::connect(fd, a->ai_addr, a->ai_addrlen)) break; ::close(fd); fd = -1; }
    freeaddrinfo(res); if (fd < 0) { err = std::string("connect failed: ") + strerror(errno); return -1; } int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one); return fd;
}
// gen < 0 means "no generation asked for". want_digest / want_inc may be NULL. use_cache: offer the digest of the copy last
// read from this peer under this name, so an unchanged state is answered SAME and dst is left as it is.
int ft_client_read_into(ft_client* c, const char* host, int port, const char* name, long long gen, const char* mode, const char* want_digest, const char* want_inc,
                        int verify, int use_cache, double wait_s, void* dst, unsigned long long cap, ft_result* out) {
    memset(out, 0, sizeof *out); std::string key = std::string(host) + ":" + std::to_string(port), ck = key + "/" + name, have; std::shared_ptr<ft_client::Link> link;
    { std::lock_guard<std::mutex> g(c->m); auto& l = c->links[key]; if (!l) l = std::make_shared<ft_client::Link>(); link = l; if (use_cache) { auto h = c->have.find(ck); if (h != c->have.end()) have = h->second; } ++c->reads; }
    char w[32]; snprintf(w, sizeof w, "%.3f", wait_s);
    std::string req = "{\"name\": " + jstr(name) + ", \"gen\": " + (gen < 0 ? "null" : std::to_string(gen)) + ", \"mode\": " + jstr(mode) + ", \"wait_s\": " + w + ", \"want\": " + jopt(want_digest ? want_digest : "", want_digest != nullptr) +
                      ", \"inc\": " + jopt(want_inc ? want_inc : "", want_inc != nullptr) + ", \"verify\": " + ((verify || want_digest || !have.empty()) ? "true" : "false") + ", \"have\": " + jopt(have, !have.empty()) + ", \"from\": " + jstr(c->me) + "}";
    std::string frame = be64(req.size()) + req, err; std::lock_guard<std::mutex> lg(link->m);            // one request/reply at a time per peer
    for (int attempt = 0; attempt < 2; ++attempt) {
        bool fresh = link->fd < 0; if (fresh) { link->fd = dial(host, port, err); if (link->fd < 0) break; }
        timeval tv; tv.tv_sec = long(std::max(30.0, wait_s + 60.0)); tv.tv_usec = 0; setsockopt(link->fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        uint64_t n = 0; char tag[4];
        if (!send_all(link->fd, frame.data(), frame.size()) || !recv_len(link->fd, n) || n < 4 || !recv_all(link->fd, tag, 4)) { ::close(link->fd); link->fd = -1; err = "link dropped"; if (fresh) break; continue; }
        if (!memcmp(tag, "DATA", 4)) {
            uint64_t hl = 0; if (!recv_len(link->fd, hl) || hl > n) { ::close(link->fd); link->fd = -1; err = "bad DATA header"; break; }
            std::string head(hl, '\0'); uint64_t bn = n - 4 - 8 - hl;
            if (!recv_all(link->fd, &head[0], hl)) { ::close(link->fd); link->fd = -1; err = "link dropped in header"; break; }
            if (bn > cap) { ::close(link->fd); link->fd = -1; snprintf(out->error, sizeof out->error, "the state is %llu bytes and the destination holds %llu", (unsigned long long)bn, cap); out->status = FT_ERROR; return FT_ERROR; }
            if (!recv_all(link->fd, static_cast<char*>(dst), bn)) { ::close(link->fd); link->fd = -1; err = "link dropped in the tensor"; break; }      // straight into the caller's storage
            Json h = Json::parse(head); out->gen = (long long)h["gen"].n; out->has_gen = 1; out->nbytes = bn; snprintf(out->digest, sizeof out->digest, "%s", h["digest"].s.c_str()); snprintf(out->inc, sizeof out->inc, "%s", h["inc"].s.c_str());
            if (verify && !h["digest"].s.empty() && digest_of(static_cast<const char*>(dst), bn) != h["digest"].s) { snprintf(out->error, sizeof out->error, "digest mismatch: the bytes did not survive the wire"); out->status = FT_ERROR; return FT_ERROR; }
            { std::lock_guard<std::mutex> g(c->m); if (use_cache && !h["digest"].s.empty()) c->have[ck] = h["digest"].s; ++c->data; c->bytes += bn; }
            out->status = FT_DATA; return FT_DATA;
        }
        std::string body(n - 4, '\0'); if (!recv_all(link->fd, &body[0], n - 4)) { ::close(link->fd); link->fd = -1; err = "link dropped in reply"; break; }
        Json j; try { j = Json::parse(body.empty() ? "{}" : body); } catch (const std::exception&) { err = "reply is not JSON"; break; }
        if (j["gen"].type == Json::Num) { out->gen = (long long)j["gen"].n; out->has_gen = 1; } snprintf(out->digest, sizeof out->digest, "%s", j["digest"].s.c_str()); snprintf(out->inc, sizeof out->inc, "%s", j["inc"].s.c_str());
        if (!memcmp(tag, "SAME", 4)) { std::lock_guard<std::mutex> g(c->m); ++c->same; out->status = FT_SAME; return FT_SAME; }
        if (!memcmp(tag, "GONE", 4)) { snprintf(out->reason, sizeof out->reason, "%s", j["reason"].type == Json::Str ? j["reason"].s.c_str() : "never_published"); out->waited_s = j["waited_s"].n; out->status = FT_GONE; return FT_GONE; }
        err = "unknown reply tag"; ::close(link->fd); link->fd = -1; break;
    }
    snprintf(out->error, sizeof out->error, "%s:%d %s", host, port, err.c_str()); out->status = FT_ERROR; return FT_ERROR;
}
void ft_client_forget(ft_client* c, const char* host, int port, const char* name) { std::lock_guard<std::mutex> g(c->m); c->have.erase(std::string(host) + ":" + std::to_string(port) + "/" + name); }
void ft_client_close(ft_client* c) { std::lock_guard<std::mutex> g(c->m); for (auto& kv : c->links) if (kv.second->fd >= 0) { ::close(kv.second->fd); kv.second->fd = -1; } }
}
