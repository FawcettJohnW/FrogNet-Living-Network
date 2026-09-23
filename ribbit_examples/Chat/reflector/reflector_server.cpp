// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// reflector_server -- the FAST PLANE reflector: one publisher's stream fanned
// out to every subscriber, send-or-drop, over kept connections. In one process.
//
//     reflector_server --listen 0.0.0.0:8789
//
// This is the Communicator's _Hub generalized: a sender-facing process whose
// whole job is the one-to-many reflection of incoming data. It stores nothing.
// A frame that arrives is pushed to every current subscriber of its stream and
// then forgotten. A subscriber that cannot take a frame RIGHT NOW is shed for
// that frame -- the next one is newer anyway -- so a slow subscriber never
// backpressures the publisher or the other subscribers. This is why live state
// (a kart at 60 Hz, a video frame) belongs here and not in queued Memory: for
// real-time data a stale generation is worse than a dropped one.
//
// The slow plane (Memory) holds the descriptor that says where this reflector
// is and what streams exist; this reflector holds nothing and carries the bytes.
//
// THE WIRE.  One permanent TCP connection per participant, which the CLIENT dials.
// Frames are [len:4 BE][op:1]... ; names are UTF-8. A connection is a publisher,
// a subscriber, or both -- roles are per stream, declared by the frames sent.
//   HELLO 0x50 [nlen:2][id]                    who this connection is (for logging)
//   SUB   0x40 [nlen:2][stream]                start receiving this stream's frames
//   UNSUB 0x41 [nlen:2][stream]                stop
//   PUB   0x01 [gen:8][nlen:2][stream][bytes]  reflect this frame to the stream's subscribers (no reply)
// Pushed to each subscriber, as they arrive:
//   FRAME 0x11 [gen:8][nlen:2][stream][blen:4][bytes]
// gen is the publisher's own counter; the reflector passes it through and never
// reorders a single publisher's frames (one publisher, one connection, TCP order).
// Between publishers to one stream there is no global order and none is promised:
// a reflector reflects; it does not serialize. (Use the Memory for anything that
// needs a single order across writers.)
//
// NO FALLBACKS. A malformed frame drops that connection; nothing is guessed.
#include "../cpp/frogram.hpp"

#ifdef _WIN32
#  ifndef _WIN32_WINNT
#    define _WIN32_WINNT 0x0600
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  define FR_CLOSE(fd) closesocket(fd)
#  define FR_SHUT_RDWR SD_BOTH
#  define MSG_NOSIGNAL 0
   typedef char fr_optval_t;
#else
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
#  define FR_CLOSE(fd) ::close(fd)
#  define FR_SHUT_RDWR SHUT_RDWR
   typedef int fr_optval_t;
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <thread>
#include <vector>

namespace {
const uint64_t MAX_FRAME = 1u << 24;      // 16 MiB: a video frame or a big tensor slice, not unbounded
enum : uint8_t { OP_PUB = 0x01, OP_FRAME = 0x11, OP_SUB = 0x40, OP_UNSUB = 0x41, OP_HELLO = 0x50 };
bool g_quiet = false;
void logline(const std::string& m) { if (!g_quiet) { std::time_t t = std::time(nullptr); char b[32]; std::strftime(b, sizeof b, "%F %T", std::localtime(&t)); std::cout << "[REFLECTOR] " << b << " " << m << std::endl; } }

std::string be32(uint32_t v) { std::string s(4, '\0'); for (int i = 3; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
std::string be64(uint64_t v) { std::string s(8, '\0'); for (int i = 7; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
uint16_t rd16(const std::string& s, size_t o) { return uint16_t((uint8_t(s[o]) << 8) | uint8_t(s[o + 1])); }
uint32_t rd32(const std::string& s, size_t o) { uint32_t v = 0; for (int i = 0; i < 4; ++i) v = (v << 8) | uint8_t(s[o + i]); return v; }
uint64_t rd64(const std::string& s, size_t o) { uint64_t v = 0; for (int i = 0; i < 8; ++i) v = (v << 8) | uint8_t(s[o + i]); return v; }

// A subscriber connection. send() is NON-BLOCKING send-or-drop: if the socket's send buffer
// cannot take the whole frame right now, the frame is shed for this subscriber. The buffer is
// the latency; a full buffer means this subscriber is behind, and the newest frame supersedes.
struct Conn {
    int fd; std::mutex w; std::string id; uint64_t sent = 0, shed = 0;
    explicit Conn(int f) : fd(f) {}
    void send_frame(const std::string& frame) {
        std::lock_guard<std::mutex> g(w);
#ifdef _WIN32
        u_long avail = 1; ioctlsocket(fd, FIONBIO, &avail);   // non-blocking
        int n = ::send(fd, frame.data(), int(frame.size()), 0);
        u_long block = 0; ioctlsocket(fd, FIONBIO, &block);
        if (n == int(frame.size())) ++sent; else ++shed;      // partial or would-block: shed whole (framing intact next time)
#else
        pollfd pf; pf.fd = fd; pf.events = POLLOUT; pf.revents = 0;
        if (::poll(&pf, 1, 0) <= 0 || !(pf.revents & POLLOUT)) { ++shed; return; }
        ssize_t n = ::send(fd, frame.data(), frame.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
        if (n == ssize_t(frame.size())) ++sent; else ++shed;
#endif
    }
};

struct Reflector {
    std::mutex m;
    std::map<std::string, std::set<std::shared_ptr<Conn>>> subs;   // stream -> subscribers
    std::atomic<uint64_t> frames_in{0}, frames_out{0};

    void subscribe(const std::string& stream, std::shared_ptr<Conn> c) { std::lock_guard<std::mutex> g(m); subs[stream].insert(c); }
    void unsubscribe(const std::string& stream, std::shared_ptr<Conn> c) { std::lock_guard<std::mutex> g(m); auto it = subs.find(stream); if (it != subs.end()) { it->second.erase(c); if (it->second.empty()) subs.erase(it); } }
    void drop(std::shared_ptr<Conn> c) { std::lock_guard<std::mutex> g(m); for (auto it = subs.begin(); it != subs.end();) { it->second.erase(c); if (it->second.empty()) it = subs.erase(it); else ++it; } }

    void reflect(const std::string& stream, uint64_t gen, const char* body, size_t n) {
        std::string frame; frame.reserve(1 + 8 + 2 + stream.size() + 4 + n);
        frame.push_back(char(OP_FRAME)); frame += be64(gen); frame += std::string(1, char(stream.size() >> 8)) + char(stream.size() & 0xff); frame += stream;
        frame += be32(uint32_t(n)); frame.append(body, n);
        std::string wire = be32(uint32_t(frame.size())) + frame;
        std::vector<std::shared_ptr<Conn>> targets;
        { std::lock_guard<std::mutex> g(m); auto it = subs.find(stream); if (it == subs.end()) return; targets.assign(it->second.begin(), it->second.end()); }
        ++frames_in;
        for (auto& c : targets) { c->send_frame(wire); ++frames_out; }   // send-or-drop per subscriber
    }
};
Reflector R;

bool recv_n(int fd, size_t n, std::string& d) { d.assign(n, '\0'); size_t o = 0; while (o < n) { ssize_t r = ::recv(fd, &d[o], n - o, 0); if (r <= 0) return false; o += size_t(r); } return true; }
bool recv_frame(int fd, std::string& f) { std::string l; if (!recv_n(fd, 4, l)) return false; uint64_t n = rd32(l, 0); if (n < 1 || n > MAX_FRAME) return false; return recv_n(fd, size_t(n), f); }

void serve(int fd, std::string peer) {
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    auto c = std::make_shared<Conn>(fd);
    std::set<std::string> mine;   // streams this connection subscribed to, to clean up on close
    std::string f;
    while (recv_frame(fd, f)) {
        uint8_t op = uint8_t(f[0]);
        if (op == OP_PUB) {
            if (f.size() < 11) { break; }
            uint64_t gen = rd64(f, 1); uint16_t nl = rd16(f, 9); if (f.size() < size_t(11 + nl)) { break; }
            std::string stream = f.substr(11, nl); const char* body = f.data() + 11 + nl; size_t bn = f.size() - 11 - nl;
            R.reflect(stream, gen, body, bn);
        } else if (op == OP_SUB) {
            if (f.size() < 3) { break; } uint16_t nl = rd16(f, 1); if (f.size() < size_t(3 + nl)) { break; }
            std::string stream = f.substr(3, nl); R.subscribe(stream, c); mine.insert(stream);
        } else if (op == OP_UNSUB) {
            if (f.size() < 3) { break; } uint16_t nl = rd16(f, 1); if (f.size() < size_t(3 + nl)) { break; }
            std::string stream = f.substr(3, nl); R.unsubscribe(stream, c); mine.erase(stream);
        } else if (op == OP_HELLO) {
            if (f.size() < 3) { break; } uint16_t nl = rd16(f, 1); if (f.size() >= size_t(3 + nl)) { c->id = f.substr(3, nl); }
            logline("connection from " + peer + (c->id.empty() ? "" : " (" + c->id + ")"));
        } else break;
    }
    R.drop(c);
    logline("connection " + peer + (c->id.empty() ? "" : " (" + c->id + ")") + " closed; sent " + std::to_string(c->sent) + ", shed " + std::to_string(c->shed));
    ::shutdown(fd, FR_SHUT_RDWR); FR_CLOSE(fd);
}
}  // namespace

// Startable in-process (the rendezvous launches it beside ram_server), or standalone via main.
int reflector_start(const std::string& listen_on, bool quiet) {
    g_quiet = quiet; size_t c = listen_on.rfind(':'); if (c == std::string::npos) return EINVAL;
#ifdef _WIN32
    { static bool once = [] { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); return true; }(); (void)once; }
#else
    signal(SIGPIPE, SIG_IGN);
#endif
    int srv = int(::socket(AF_INET, SOCK_STREAM, 0)), one = 1; setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(uint16_t(atoi(listen_on.c_str() + c + 1))); inet_pton(AF_INET, listen_on.substr(0, c).c_str(), &a.sin_addr);
    if (::bind(srv, reinterpret_cast<sockaddr*>(&a), sizeof a) || ::listen(srv, 1024)) { int e = errno; FR_CLOSE(srv); return e ? e : EADDRINUSE; }
    logline("fast-plane reflector on " + listen_on);
    std::thread([srv] { for (;;) { sockaddr_in p{}; socklen_t pl = sizeof p; int fd = int(::accept(srv, reinterpret_cast<sockaddr*>(&p), &pl)); if (fd < 0) continue;
        char ip[64]; inet_ntop(AF_INET, &p.sin_addr, ip, sizeof ip); std::thread(serve, fd, std::string(ip)).detach(); } }).detach();
    return 0;
}

#ifndef REFLECTOR_EMBED
int main(int argc, char** argv) {
    std::string listen_on = "0.0.0.0:8789";
    for (int i = 1; i + 1 < argc; i += 2) if (!strcmp(argv[i], "--listen")) listen_on = argv[i + 1];
    int e = reflector_start(listen_on, false);
    if (e) { std::cerr << "cannot listen on " << listen_on << ": " << std::strerror(e) << "\n"; return 1; }
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
}
#endif
