// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "net.hpp"
#include <cstring>
#include <stdexcept>

namespace fnav {

#ifdef _WIN32
void net_init() {
    static struct Once { Once() { WSADATA w; if (WSAStartup(MAKEWORD(2, 2), &w) != 0) throw std::runtime_error("WSAStartup failed"); } } once;
}
int net_error() { return WSAGetLastError(); }
bool net_would_block(int e) { return e == WSAEWOULDBLOCK || e == WSAEINTR; }
bool net_in_progress(int e) { return e == WSAEWOULDBLOCK || e == WSAEINPROGRESS; }
std::string net_strerror(int e) {
    char buf[256] = {0};
    FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, DWORD(e), 0, buf, sizeof buf, nullptr);
    std::string s(buf);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
    return s.empty() ? "socket error " + std::to_string(e) : s;
}
int net_close(int fd) { return closesocket(SOCKET(fd)); }
bool net_set_nonblocking(int fd) { u_long on = 1; return ioctlsocket(SOCKET(fd), FIONBIO, &on) == 0; }
int net_poll(net_pollfd* p, unsigned n, int ms) { return WSAPoll(p, ULONG(n), ms); }
long net_unsent(int) { return -1; }
#else
void net_init() {}
int net_error() { return errno; }
bool net_would_block(int e) { return e == EAGAIN || e == EWOULDBLOCK || e == EINTR; }
bool net_in_progress(int e) { return e == EINPROGRESS; }
std::string net_strerror(int e) { return std::strerror(e); }
int net_close(int fd) { return ::close(fd); }
bool net_set_nonblocking(int fd) { int fl = fcntl(fd, F_GETFL, 0); return fl >= 0 && fcntl(fd, F_SETFL, fl | O_NONBLOCK) == 0; }
int net_poll(net_pollfd* p, unsigned n, int ms) { return ::poll(p, nfds_t(n), ms); }
long net_unsent(int fd) {
#  ifdef SIOCOUTQ
    int n = 0;
    if (ioctl(fd, SIOCOUTQ, &n) < 0) return -1;
    return n;
#  else
    (void)fd; return -1;
#  endif
}
#endif

Waker::Waker() {
    net_init();
#ifdef _WIN32
    SOCKET u = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); int l = sizeof a;
    if (u == INVALID_SOCKET || bind(u, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0 || getsockname(u, reinterpret_cast<sockaddr*>(&a), &l) != 0
        || connect(u, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0)
        throw std::runtime_error("a plane's writer could not make what wakes it: " + net_strerror(net_error()));
    rfd_ = wfd_ = int(u);
#else
    int p[2];
    if (::pipe(p) != 0) throw std::runtime_error("a plane's writer could not make what wakes it: " + net_strerror(net_error()));
    rfd_ = p[0]; wfd_ = p[1];
#endif
    net_set_nonblocking(rfd_); net_set_nonblocking(wfd_);
}
Waker::~Waker() {
    if (rfd_ >= 0) net_close(rfd_);
    if (wfd_ >= 0 && wfd_ != rfd_) net_close(wfd_);
}
void Waker::wake() {
    const char one = 1;
#ifdef _WIN32
    ::send(SOCKET(wfd_), &one, 1, 0);
#else
    if (::write(wfd_, &one, 1) < 0) {}               // full: it is already going to wake
#endif
}
void Waker::drain() {
    char b[64];
#ifdef _WIN32
    while (::recv(SOCKET(rfd_), b, sizeof b, 0) > 0) {}
#else
    while (::read(rfd_, b, sizeof b) > 0) {}
#endif
}

}  // namespace fnav
