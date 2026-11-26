// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include <cstdio>
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
    wthread_ = std::thread([this] { writer(); });          // [ONE_WRITER_PER_PLANE_V1]
}

Plane::~Plane() {
    if (codec_) codec_->when_owing(nullptr);               // the codec may outlive this plane (its Channel holds it weakly)
    stop_ = true;
    waker_.wake();
    if (wthread_.joinable()) wthread_.join();
    if (fd_ >= 0) net_close(fd_);
}

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

void Plane::set_channel(std::shared_ptr<channel_frame::Codec> codec, std::string role, bool by_first_frame) {
    codec_ = std::move(codec); role_ = std::move(role); undecided_ = by_first_frame && codec_ != nullptr;
    if (codec_) codec_->when_owing([this] { owed_pending_ = true; waker_.wake(); });   // a switch goes though nothing else is being sent
}

// [ONE_WRITER_PER_PLANE_V1] Any thread: hand the body to this plane's writer and take its word for what became of it.
Sent Plane::send(const Bytes& body, SendKind kind) {
    Job j{&body, kind};
    std::unique_lock<std::mutex> g(hand_);
    if (!broken_.empty()) throw ConnectionError(broken_);
    jobs_.push_back(&j);
    waker_.wake();
    told_.wait(g, [&j] { return j.done; });
    if (!j.error.empty()) throw ConnectionError(j.error);
    return j.result;
}

// The writer: the one owner of this plane's wire. It waits to be handed a body; while it owes the far end something
// the socket would not take (a call of the frame's, padding in debt mode) it waits for the socket too.
void Plane::writer() {
    bool refused = false;                                  // the socket said writable and still took nothing of what is owed
    bool said_broken = false;
    std::string error;
    while (!stop_) {
        net_pollfd p[2]{};
        p[0].fd = decltype(p[0].fd)(waker_.fd()); p[0].events = POLLIN;
        p[1].fd = decltype(p[1].fd)(fd_); p[1].events = POLLOUT;
        const bool owes = error.empty() && (owed_pending_ || (debt_mode_ && !pad_.empty()));
        net_poll(p, owes && !refused ? 2 : 1, owes && refused ? RETRY_WAIT_MS : -1);
        waker_.drain();
        if (stop_) break;
        if (error.empty()) {
            try {
                bool went = true;
                if (debt_mode_ && !pad_.empty()) went = pay_debt();
                if (went && codec_ && !undecided_ && !plain_) went = write_owed();
                refused = !went;
            } catch (const std::exception& e) { error = e.what(); }
        }
        if (!error.empty() && !said_broken) {
            // [A_BROKEN_PLANE_IS_CLOSED_V1] The writer cannot go on, so the connection is over, and both ends learn it
            // now: this end's reader and the far end's read "closed" instead of waiting on a wire nobody writes.
            said_broken = true;
            { std::lock_guard<std::mutex> g(hand_); broken_ = error; }
            std::fprintf(stderr, "[fnav] a plane's writer stops and the connection is closed: %s\n", error.c_str());
            shutdown_both();
        }
        for (;;) {
            Job* j = nullptr;
            { std::lock_guard<std::mutex> g(hand_); if (!jobs_.empty()) { j = jobs_.front(); jobs_.pop_front(); } if (!error.empty()) broken_ = error; }
            if (!j) break;
            if (error.empty()) {
                try { j->result = write_body(*j->body, j->kind); } catch (const std::exception& e) { error = e.what(); }
                if (!error.empty() && !said_broken) {
                    said_broken = true;
                    std::fprintf(stderr, "[fnav] a plane's writer stops and the connection is closed: %s\n", error.c_str());
                    shutdown_both();
                }
            }
            { std::lock_guard<std::mutex> g(hand_); j->error = error; j->done = true; if (!error.empty()) broken_ = error; }
            told_.notify_all();
        }
    }
    std::lock_guard<std::mutex> g(hand_);                  // nothing handed to a writer that has stopped waits for ever
    broken_ = error.empty() ? "the plane is closed" : error;
    for (Job* j : jobs_) { j->error = broken_; j->done = true; }
    jobs_.clear();
    told_.notify_all();
}

// One body. Under the frame: what this end owes goes first, then the body as one unit; a unit that did not go on the
// wire whole is taken back from the frame, so the far end's frame and this one stay in step whatever the plane sheds.
Sent Plane::write_body(const Bytes& body, SendKind kind) {
    if (!codec_ || plain_) return send_unit(body, kind);
    if (undecided_) { stats.dropped++; return Sent::Dropped; }   // the far end has not said yet whether it speaks the frame
    try {
        if (!write_owed()) return Sent::Dropped;
        Bytes unit;
        if (!codec_->pack(role_, body, unit)) { stats.dropped++; owed_pending_ = true; return Sent::Dropped; }   // messages are being sent again
        unit.push_back(WHOLE_UNIT);
        const Sent r = send_unit(unit, kind);
        if (r != Sent::Whole) codec_->undo();
        return r;
    } catch (const channel_frame::Gone& e) { throw ConnectionError(std::string("the frame: ") + e.what()); }
}

bool Plane::write_owed() {
    try {
        Bytes unit;
        while (codec_->next(unit)) {
            unit.push_back(WHOLE_UNIT);
            if (send_unit(unit, SendKind::Audio) != Sent::Whole) { codec_->undo(); owed_pending_ = true; return false; }   // the writer waits for the socket
        }
        owed_pending_ = false;
        return true;
    } catch (const channel_frame::Gone& e) { throw ConnectionError(std::string("the frame: ") + e.what()); }
}

// [THE_LENGTH_IS_NEVER_PADDED_V1] What follows a part-written unit that will not be finished. When fewer than its 4
// length bytes went, the rest of the LENGTH goes as it is and only the body is padding: padding written where the
// length belongs gives the far end a length nobody sent, and it reads the rest of the connection as one huge unit.
static Bytes rest_of_aborted(const Bytes& blob, size_t sent) {
    if (sent >= 4) return abort_pad(blob.size() - sent);
    Bytes rest(blob.begin() + long(sent), blob.begin() + 4);
    const Bytes pad = abort_pad(blob.size() - 4);
    rest.insert(rest.end(), pad.begin(), pad.end());
    return rest;
}

Sent Plane::send_unit(const Bytes& body, SendKind kind) {
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
        pad_ = rest_of_aborted(blob, sent); pad_off_ = 0;
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
    const Bytes pad = rest_of_aborted(blob, sent);
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

// One body. Under the frame a unit may hold only calls (a switch, RECEIVED): then the next unit is read.
Recv Plane::recv(Bytes& body, int timeout_ms) {
    for (;;) {
        if (!ready_.empty()) { body = std::move(ready_.front()); ready_.pop_front(); return Recv::Frame; }
        const Recv r = recv_unit(body, timeout_ms);
        if (r != Recv::Frame || !codec_ || plain_) return r;
        if (undecided_) {                                  // the first unit from the far end: an fnav body, or the frame
            undecided_ = false;
            if (!body.empty() && body[0] == KIND_PLANE) { plain_ = true; return r; }
        }
        // [A_WHOLE_UNIT_SAYS_SO_V1] under the frame the far end's frame must be fed exactly the units its sender did not
        // take back. The abort sentinel alone cannot say that: a frame aborted with 1 to 3 bytes left ends in 1 to 3
        // sentinel bytes and passes for whole. So a unit under the frame ends in one byte, 0, which no abort padding
        // can end in; anything else was aborted.
        if (body.empty() || body.back() != WHOLE_UNIT) { stats.aborted_in++; return Recv::Aborted; }
        body.pop_back();
        std::vector<channel_frame::Message> out;
        try { codec_->feed(body.data(), body.size(), out); }
        catch (const channel_frame::Gone& e) { throw ConnectionError(std::string("the frame: ") + e.what()); }
        for (auto& m : out) ready_.push_back(std::move(m.data));
    }
}

Recv Plane::recv_unit(Bytes& body, int timeout_ms) {
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
