// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "reflector_client.hpp"
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
#  include <netdb.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
#  define FR_CLOSE(fd) ::close(fd)
#  define FR_SHUT_RDWR SHUT_RDWR
   typedef int fr_optval_t;
#endif
#include <cstring>
#include <vector>

namespace reflector {
namespace {
enum : uint8_t { OP_PUB = 0x01, OP_FRAME = 0x11, OP_SUB = 0x40, OP_UNSUB = 0x41, OP_HELLO = 0x50 };
const int SNDBUF = 32 * 1024;   // the buffer is the latency
std::string be32(uint32_t v) { std::string s(4, '\0'); for (int i = 3; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
std::string be64(uint64_t v) { std::string s(8, '\0'); for (int i = 7; i >= 0; --i) { s[i] = char(v & 0xff); v >>= 8; } return s; }
std::string be16(uint16_t v) { std::string s(2, '\0'); s[0] = char(v >> 8); s[1] = char(v & 0xff); return s; }
uint16_t rd16(const std::string& s, size_t o) { return uint16_t((uint8_t(s[o]) << 8) | uint8_t(s[o + 1])); }
uint32_t rd32(const std::string& s, size_t o) { uint32_t v = 0; for (int i = 0; i < 4; ++i) v = (v << 8) | uint8_t(s[o + i]); return v; }
uint64_t rd64(const std::string& s, size_t o) { uint64_t v = 0; for (int i = 0; i < 8; ++i) v = (v << 8) | uint8_t(s[o + i]); return v; }
int dial(const std::string& hp, double) {
#ifdef _WIN32
    { static bool once = [] { WSADATA w; WSAStartup(MAKEWORD(2, 2), &w); return true; }(); (void)once; }
#endif
    size_t c = hp.rfind(':'); std::string host = hp.substr(0, c); std::string port = hp.substr(c + 1);
    addrinfo hints{}, *res = nullptr; hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host.c_str(), port.c_str(), &hints, &res) || !res) throw Gone("cannot resolve " + host);
    int fd = -1; for (addrinfo* a = res; a; a = a->ai_next) { fd = int(::socket(a->ai_family, a->ai_socktype, a->ai_protocol)); if (fd < 0) continue; if (!::connect(fd, a->ai_addr, int(a->ai_addrlen))) break; FR_CLOSE(fd); fd = -1; }
    freeaddrinfo(res); if (fd < 0) throw Gone("cannot reach reflector at " + hp);
    int one = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const fr_optval_t*>(&one), sizeof one);
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const fr_optval_t*>(&SNDBUF), sizeof SNDBUF);
    return fd;
}
bool send_all(int fd, const std::string& b) { size_t o = 0; while (o < b.size()) { ssize_t n = ::send(fd, b.data() + o, b.size() - o, MSG_NOSIGNAL); if (n <= 0) return false; o += size_t(n); } return true; }
bool recv_n(int fd, size_t n, std::string& d) { d.assign(n, '\0'); size_t o = 0; while (o < n) { ssize_t r = ::recv(fd, &d[o], n - o, 0); if (r <= 0) return false; o += size_t(r); } return true; }
}  // namespace

Reflector::Reflector(const std::string& hostport, const std::string& id, double t) : id_(id) {
    fd_ = dial(hostport, t);
    std::string h; h.push_back(char(OP_HELLO)); h += be16(uint16_t(id.size())) + id;
    send_all(fd_, be32(uint32_t(h.size())) + h);
    reader_ = std::thread([this] { reader_loop(); });
}
Reflector::~Reflector() { dead_ = true; if (fd_ >= 0) ::shutdown(fd_, FR_SHUT_RDWR); if (reader_.joinable()) reader_.join(); if (fd_ >= 0) FR_CLOSE(fd_); }

void Reflector::subscribe(const std::string& stream, OnFrame cb) {
    { std::lock_guard<std::mutex> g(cb_m_); cbs_[stream] = std::move(cb); }
    std::string f; f.push_back(char(OP_SUB)); f += be16(uint16_t(stream.size())) + stream;
    std::lock_guard<std::mutex> g(w_); send_all(fd_, be32(uint32_t(f.size())) + f);
}
void Reflector::unsubscribe(const std::string& stream) {
    { std::lock_guard<std::mutex> g(cb_m_); cbs_.erase(stream); }
    std::string f; f.push_back(char(OP_UNSUB)); f += be16(uint16_t(stream.size())) + stream;
    std::lock_guard<std::mutex> g(w_); send_all(fd_, be32(uint32_t(f.size())) + f);
}
bool Reflector::publish(const std::string& stream, uint64_t gen, const std::string& body) {
    if (dead_) throw Gone("reflector connection lost");
    std::string f; f.reserve(1 + 8 + 2 + stream.size() + body.size());
    f.push_back(char(OP_PUB)); f += be64(gen) + be16(uint16_t(stream.size())) + stream + body;
    std::string wire = be32(uint32_t(f.size())) + f;
    std::lock_guard<std::mutex> g(w_);
    pollfd pf; pf.fd = fd_; pf.events = POLLOUT; pf.revents = 0;
#ifdef _WIN32
    if (WSAPoll(&pf, 1, 0) <= 0 || !(pf.revents & POLLOUT)) { ++shed; return false; }
#else
    if (::poll(&pf, 1, 0) <= 0 || !(pf.revents & POLLOUT)) { ++shed; return false; }
#endif
    if (!send_all(fd_, wire)) { dead_ = true; throw Gone("reflector send failed"); }
    ++sent; return true;
}
void Reflector::reader_loop() {
    std::string lenbuf, f;
    while (!dead_) {
        if (!recv_n(fd_, 4, lenbuf)) { break; }
        uint32_t n = rd32(lenbuf, 0); if (n < 11 || n > (1u << 24)) { break; }
        if (!recv_n(fd_, n, f)) { break; }
        if (uint8_t(f[0]) != OP_FRAME) continue;
        uint64_t gen = rd64(f, 1); uint16_t nl = rd16(f, 9); if (f.size() < size_t(11 + nl + 4)) continue;
        std::string stream = f.substr(11, nl); uint32_t bl = rd32(f, 11 + nl); std::string body = f.substr(11 + nl + 4, bl);
        ++received; OnFrame cb;
        { std::lock_guard<std::mutex> g(cb_m_); auto it = cbs_.find(stream); if (it != cbs_.end()) { cb = it->second; } }
        if (cb) { cb(gen, body); }
    }
    dead_ = true;
}
}  // namespace reflector
