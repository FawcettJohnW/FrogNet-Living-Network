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

}  // namespace fnav
