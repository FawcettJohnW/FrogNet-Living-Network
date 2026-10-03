// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// The socket differences between POSIX and Windows, in one place (as frogram.cpp does for the platform).
#pragma once
#include <string>

#ifdef _WIN32
#  ifndef _WIN32_WINNT
#    define _WIN32_WINNT 0x0600                  // Vista+: WSAPoll, inet_pton
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  include <mstcpip.h>
using net_ssize = long long;
#  define NET_SHUT_RDWR SD_BOTH
#  define NET_SEND_FLAGS 0                       // no SIGPIPE on Windows
using net_pollfd = WSAPOLLFD;
#else
#  include <arpa/inet.h>
#  include <cerrno>
#  include <fcntl.h>
#  include <netdb.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <poll.h>
#  include <sys/ioctl.h>
#  include <sys/socket.h>
#  include <unistd.h>
#  ifdef __linux__
#    include <linux/sockios.h>
#  endif
using net_ssize = ssize_t;
#  define NET_SHUT_RDWR SHUT_RDWR
#  define NET_SEND_FLAGS MSG_NOSIGNAL
using net_pollfd = pollfd;
#endif

namespace fnav {

void net_init();                                 // WSAStartup once on Windows; nothing elsewhere
int net_error();                                 // the last socket error (errno / WSAGetLastError)
bool net_would_block(int e);                     // EAGAIN/EWOULDBLOCK/EINTR, WSAEWOULDBLOCK/WSAEINTR
bool net_in_progress(int e);                     // a non-blocking connect under way
std::string net_strerror(int e);
int net_close(int fd);
bool net_set_nonblocking(int fd);
int net_poll(net_pollfd* p, unsigned n, int ms);
// Bytes the kernel still holds for this socket, or -1 where that cannot be known.
// Linux: SIOCOUTQ. Windows: -1 -- no equivalent; the socket's own "would block" decides
// ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]: a known gap, see STATUS.md).
long net_unsent(int fd);

}  // namespace fnav
