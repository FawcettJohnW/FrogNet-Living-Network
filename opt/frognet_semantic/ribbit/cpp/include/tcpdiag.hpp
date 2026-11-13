// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// tcpdiag.hpp -- [SOCKET_SAYS_WHAT_HELD_IT_V1] one socket's TCP_INFO as one line of text.
//
// The lesson from the PyTorch link (revive-september-20261112): what a socket was held back by is measured by the
// kernel, so read it instead of arguing about it. busy_ms is the time the socket had data to send; rwnd_limited_ms the
// part of that the receiver's window held it; sndbuf_limited_ms the part this end's SO_SNDBUF held it (the 32 KB "the
// buffer is the latency" setting). On a path with a real round trip, sndbuf_limited_ms near busy_ms says the buffer,
// not the network, set the rate. Linux only; elsewhere the line says so.
#pragma once
#include <string>
#ifdef __linux__
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <cstdio>
#include <cstdint>
#endif

namespace tcpdiag {
#ifdef __linux__
// The kernel's struct tcp_info (linux/tcp.h) as far as tcpi_sndbuf_limited: glibc's netinet/tcp.h stops before the
// fields this needs, and the two headers cannot both be included. The layout is kernel ABI; a kernel that fills
// fewer bytes leaves the rest zero, and the line says how many it filled.
struct Info {
    uint8_t state, ca_state, retransmits, probes, backoff, options, wscale, flags;
    uint32_t rto, ato, snd_mss, rcv_mss, unacked, sacked, lost, retrans, fackets;
    uint32_t last_data_sent, last_ack_sent, last_data_recv, last_ack_recv;
    uint32_t pmtu, rcv_ssthresh, rtt, rttvar, snd_ssthresh, snd_cwnd, advmss, reordering, rcv_rtt, rcv_space, total_retrans;
    uint64_t pacing_rate, max_pacing_rate, bytes_acked, bytes_received;
    uint32_t segs_out, segs_in, notsent_bytes, min_rtt, data_segs_in, data_segs_out;
    uint64_t delivery_rate, busy_time, rwnd_limited, sndbuf_limited;
};
#endif
inline std::string line(int fd) {
#ifdef __linux__
    if (fd < 0) return "fd=-1";
    Info ti{}; socklen_t n = sizeof ti;
    if (getsockopt(fd, IPPROTO_TCP, TCP_INFO, &ti, &n) != 0) return "tcp_info=unavailable";
    int sb = 0; socklen_t sl = sizeof sb; getsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sb, &sl);
    char b[512];
    std::snprintf(b, sizeof b,
                  "rtt_ms=%.1f rttvar_ms=%.1f cwnd=%u retrans=%u lost=%u sndbuf=%d notsent=%u "
                  "busy_ms=%llu rwnd_limited_ms=%llu sndbuf_limited_ms=%llu acked_B=%llu recv_B=%llu%s",
                  ti.rtt / 1000.0, ti.rttvar / 1000.0, ti.snd_cwnd, ti.total_retrans, ti.lost, sb, ti.notsent_bytes,
                  (unsigned long long)(ti.busy_time / 1000), (unsigned long long)(ti.rwnd_limited / 1000),
                  (unsigned long long)(ti.sndbuf_limited / 1000), (unsigned long long)ti.bytes_acked,
                  (unsigned long long)ti.bytes_received, n < sizeof ti ? " (kernel: no busy/limited times)" : "");
    return b;
#else
    (void)fd; return "tcp_info=not-linux";
#endif
}
}  // namespace tcpdiag
