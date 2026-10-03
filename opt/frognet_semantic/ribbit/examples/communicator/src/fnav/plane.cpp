// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "plane.hpp"
#include "net.hpp"
#include <chrono>
#include <cstring>
#include <thread>

namespace fnav {

using Clock = std::chrono::steady_clock;
static long ms_since(Clock::time_point t) {
    return long(std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - t).count());
}
static std::string err(const char* what, int e) { return std::string(what) + ": " + net_strerror(e); }
static std::string err(const char* what) { return err(what, net_error()); }

Plane::Plane(int fd) : fd_(fd) {
    if (!net_set_nonblocking(fd_)) throw ConnectionError(err("set non-blocking"));
    int sb = SNDBUF, one = 1;
    setsockopt(fd_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const char*>(&sb), sizeof sb);
    setsockopt(fd_, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&one), sizeof one);
    struct linger lg {0, 0};                               // [SHUTDOWN_NOT_CLOSE_V1] close never parks a thread
    setsockopt(fd_, SOL_SOCKET, SO_LINGER, reinterpret_cast<const char*>(&lg), sizeof lg);
}

Plane::~Plane() { if (fd_ >= 0) net_close(fd_); }

void Plane::shutdown_both() { ::shutdown(fd_, NET_SHUT_RDWR); }

int Plane::connect_to(const std::string& host, int port, int timeout_ms) {
    net_init();
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int rc = getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &res);
    if (rc != 0) throw ConnectionError("cannot resolve " + host + ": " + gai_strerror(rc));
    int fd = int(socket(res->ai_family, SOCK_STREAM, 0));
    if (fd < 0) { freeaddrinfo(res); throw ConnectionError(err("socket")); }
    net_set_nonblocking(fd);
    rc = ::connect(fd, res->ai_addr, int(res->ai_addrlen));
    const int cerr = rc < 0 ? net_error() : 0;
    freeaddrinfo(res);
    if (rc < 0 && !net_in_progress(cerr)) { net_close(fd); throw ConnectionError(err(("connect " + host + ":" + std::to_string(port)).c_str(), cerr)); }
    net_pollfd p{}; p.fd = decltype(p.fd)(fd); p.events = POLLOUT;
    if (rc < 0) {                                          // [A_HANG_IS_THE_WORST_REPORT_V1] bounded
        if (net_poll(&p, 1, timeout_ms) <= 0) { net_close(fd); throw ConnectionError("connect " + host + ":" + std::to_string(port) + ": timed out after " + std::to_string(timeout_ms) + " ms"); }
        int so = 0; socklen_t l = sizeof so;
        getsockopt(fd, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&so), &l);
        if (so != 0) { net_close(fd); throw ConnectionError(err(("connect " + host + ":" + std::to_string(port)).c_str(), so)); }
    }
    return fd;
}

int Plane::listen_on(const std::string& host, int port) {
    net_init();
    int fd = int(socket(AF_INET, SOCK_STREAM, 0)), one = 1;
    if (fd < 0) throw ConnectionError(err("socket"));
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&one), sizeof one);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(uint16_t(port));
    if (inet_pton(AF_INET, host.c_str(), &a.sin_addr) != 1) { net_close(fd); throw ConnectionError("bad listen address " + host); }
    if (bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof a) < 0 || listen(fd, 64) < 0) {
        int e = net_error(); net_close(fd); throw ConnectionError(err(("listen " + host + ":" + std::to_string(port)).c_str(), e));
    }
    net_set_nonblocking(fd);                                // [ALL_NONBLOCKING_V1]
    return fd;
}

bool Plane::wait_writable(int ms) const { net_pollfd p{}; p.fd = decltype(p.fd)(fd_); p.events = POLLOUT; return net_poll(&p, 1, ms) > 0 && (p.revents & POLLOUT); }
bool Plane::wait_readable(int ms) const { net_pollfd p{}; p.fd = decltype(p.fd)(fd_); p.events = POLLIN; return net_poll(&p, 1, ms) > 0; }

long Plane::unsent_bytes() const { return net_unsent(fd_); }   // Linux: what the kernel still holds; Windows: unknown

// [DO_NOT_COMMIT_TO_A_FRAME_THAT_WILL_NOT_FIT_V1] fnav._send_room, exactly: conservative. The cap and the queue are
// not in the same units ([THE_TWO_NUMBERS_ARE_NOT_THE_SAME_UNITS_V1]); a shortage is reported only when the queue is
// over three quarters of the cap, and when the numbers disagree the socket is believed, not the arithmetic.
long Plane::send_room() const {
    int cap = 0; socklen_t l = sizeof cap;
    if (getsockopt(fd_, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<char*>(&cap), &l) < 0 || cap <= 0) return -1;
    long used = unsent_bytes();
    if (used < 0 || used >= cap) return -1;
    if (used * 4 < long(cap) * 3) return cap;
    return cap - used;
}

bool Plane::pay_debt() {
    while (pad_off_ < pad_.size()) {
        net_ssize n = ::send(fd_, reinterpret_cast<const char*>(pad_.data() + pad_off_), int(pad_.size() - pad_off_), NET_SEND_FLAGS);
        if (n > 0) { pad_off_ += size_t(n); continue; }
        if (n < 0 && net_would_block(net_error())) return false;
        throw ConnectionError(err("send"));
    }
    pad_.clear(); pad_off_ = 0;
    return true;
}

Sent Plane::send(const Bytes& body, SendKind kind) {
    std::lock_guard<std::mutex> g(wlock_);
    if (debt_mode_ && !pad_.empty() && !pay_debt()) { stats.dropped++; return Sent::Dropped; }   // in debt: shed
    const Bytes blob = with_length(body);
    int tries = kind == SendKind::Audio ? 1 + AUDIO_RETRIES : kind == SendKind::Keyframe ? KEY_RETRIES : 1;
    size_t sent = 0;
    for (int t = 0; t < tries && sent == 0; ++t) {
        if (t > 0 && !wait_writable(RETRY_WAIT_MS)) continue;   // [ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]
        long room = send_room();
        if (room >= 0 && size_t(room) < blob.size()) continue;   // not committed: nothing written
        net_ssize n = ::send(fd_, reinterpret_cast<const char*>(blob.data()), int(blob.size()), NET_SEND_FLAGS);
        if (n > 0) sent = size_t(n);
        else if (n < 0 && !net_would_block(net_error()))
            throw ConnectionError(err("send"));
    }
    if (sent == 0) { stats.dropped++; return Sent::Dropped; }
    if (debt_mode_ && sent < blob.size()) {                // never wait: the rest is owed as padding
        pad_ = abort_pad(blob.size() - sent); pad_off_ = 0;
        stats.aborted_out++;
        pay_debt();
        return Sent::Aborted;
    }
    // [WHOLE_FRAME_SEND_V1] committed: the receiver has been told the length. Finish it, bounded.
    auto t0 = Clock::now();
    while (sent < blob.size() && ms_since(t0) < FINISH_MS) {
        if (!wait_writable(10)) continue;
        net_ssize n = ::send(fd_, reinterpret_cast<const char*>(blob.data() + sent), int(blob.size() - sent), NET_SEND_FLAGS);
        if (n > 0) sent += size_t(n);
        else if (n < 0 && !net_would_block(net_error())) throw ConnectionError(err("send"));
    }
    if (sent == blob.size()) {
        stats.frames_sent++; stats.bytes_sent += blob.size();
        rate_.on_accepted(blob.size(), std::chrono::duration<double>(Clock::now().time_since_epoch()).count());
        return Sent::Whole;
    }
    // [ABORT_SENTINEL_V1] cannot finish: pad the rest so it ENDS with the sentinel; the stream stays framed.
    const Bytes pad = abort_pad(blob.size() - sent);
    size_t off = 0; auto t1 = Clock::now();
    while (off < pad.size()) {
        if (ms_since(t1) >= FINISH_MS)
            throw ConnectionError("a " + std::to_string(body.size()) + " byte frame: " + std::to_string(sent) + "/" +
                                  std::to_string(blob.size()) + " written and the abort padding would not go either; the stream is desynced");
        if (!wait_writable(10)) continue;
        net_ssize n = ::send(fd_, reinterpret_cast<const char*>(pad.data() + off), int(pad.size() - off), NET_SEND_FLAGS);
        if (n > 0) off += size_t(n);
        else if (n < 0 && !net_would_block(net_error())) throw ConnectionError(err("send"));
    }
    stats.aborted_out++;
    return Sent::Aborted;
}

void Plane::read_exact(uint8_t* p, size_t n, bool mid_frame) {
    size_t got = 0;
    while (got < n) {
        net_ssize r = ::recv(fd_, reinterpret_cast<char*>(p + got), int(n - got), 0);
        if (r > 0) { got += size_t(r); continue; }
        if (r == 0) {                                      // [RX_SAYS_WHERE_V1] between frames vs mid-frame
            if (!mid_frame && got == 0) throw ConnectionError("closed");
            throw ConnectionError("peer closed mid-frame: " + std::to_string(got) + "/" + std::to_string(n) + " bytes (truncation, not a hang-up)");
        }
        if (net_would_block(net_error())) { wait_readable(100); continue; }
        throw ConnectionError(err("recv"));
    }
}

Recv Plane::recv(Bytes& body, int timeout_ms) {
    if (!wait_readable(timeout_ms)) { body.clear(); return Recv::Timeout; }
    uint8_t len[4];
    try { read_exact(len, 4, false); }
    catch (const ConnectionError& e) { if (std::string(e.what()) == "closed") return Recv::Closed; throw; }
    uint32_t n = uint32_t(len[0]) << 24 | uint32_t(len[1]) << 16 | uint32_t(len[2]) << 8 | len[3];
    body.resize(n);
    read_exact(body.data(), n, true);                      // the declared length is ALWAYS consumed
    if (is_abort(body)) { stats.aborted_in++; return Recv::Aborted; }
    stats.frames_recv++; stats.bytes_recv += 4 + n;
    return Recv::Frame;
}

bool Plane::send_video(const std::string& src, const Bytes& vp, bool is_key) {
    const SendKind k = is_key ? SendKind::Keyframe : SendKind::Video;
    if (vp.size() <= whole_max()) {
        if (send(pack_typed(KIND_VIDEO, src, vp), k) == Sent::Whole) return true;
        frames_shed++;
        return false;
    }
    const uint16_t fid = ++vseg_id_;
    uint16_t idx = 0;
    for (size_t off = 0; off < vp.size(); ++idx) {
        size_t n = std::min(VSEG_BYTES, vp.size() - off);
        Bytes piece(vp.begin() + long(off), vp.begin() + long(off + n));
        off += n;
        uint8_t flags = off >= vp.size() ? VSEG_LAST : 0;
        if (send(pack_typed(KIND_VSEG, src, pack_seg(fid, idx, flags, piece)), k) != Sent::Whole) {
            // A refused or aborted piece abandons the WHOLE frame; tell the receiver to let go. One shed.
            send(pack_typed(KIND_VSEG, src, pack_seg(fid, idx, VSEG_ABORT, Bytes())), SendKind::Audio);
            vseg_frames_aborted++; frames_shed++;
            return false;
        }
    }
    vseg_frames_sent++;
    return true;
}

}  // namespace fnav
