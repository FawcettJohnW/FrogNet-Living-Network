// dataplane.hpp -- the high-speed data socket, both ends. [HIGH_SPEED_DATA_SOCKET_V1], John 2026-09-26.
//
// A payload that cannot be differenced (REQ_RAW out of the client, RESP_RAW out of the server) and would own the
// semantic socket longer than the ceiling does not go on the semantic socket at all. It goes on the dedicated,
// always-open data socket -- the same FNWP frame, cut into segments, interleaved fairly with every other transfer in
// flight, and reassembled by its tag at the far end. Not for speed: so that every other message keeps getting through
// while large ones transit. There is no head-of-line blocking above TCP's own.
//
// The record on the data socket:   [len:4 BE][seq:4][total:4][offset:4][bytes]     len = 12 + bytes
//   seq     the transfer's tag: the sequence of the frame whose marker announced it on the control socket
//   total   the whole frame's length; offset+bytes <= total. A transfer is complete when every byte has arrived.
//
// [THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1] (fnav.py, 2026-08) The ceiling is how long one write would own the semantic
// socket: the rate that socket is actually achieving times one deadline (20 ms), floored at one segment and capped at
// half the send buffer. With no rate measured yet it is one segment: a frame is assumed big enough to matter.
#pragma once
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <utility>

namespace fnwp {
namespace dataplane {

static const size_t SEG_BYTES = 1368;                     // one segment: what fits one MTU after framing (fnav.py VSEG_BYTES)
// [DATA_MARKER_V1] John 2026-09-26: the control plane carries the request (compressed) with a MARKER for the payload;
// the payload goes over the data link. The marker rides inside the FNWP frame the control socket would have carried
// anyway -- a REQ_RAW whose request JSON has "data_bytes" and no body, a RESP_RAW whose headers JSON has
// X-FrogNet-Data-Bytes and no body -- so no new frame is spoken, and the marker takes the frame's place in wire order.
// The payload is the frame's body, alone, on the data socket under the same sequence. References move in wire order
// on both sides, as the Python proxy and daemon move them; nothing waits for anything but its own bytes.
static const char* const MARKER_HEADER = "X-FrogNet-Data-Bytes";
static const char* const MARKER_KEY = "data_bytes";

inline std::string be32(uint32_t v) { return {char(v >> 24), char(v >> 16), char(v >> 8), char(v)}; }
inline uint32_t rd32(const std::string& s, size_t o) {
    return (uint32_t(uint8_t(s[o])) << 24) | (uint32_t(uint8_t(s[o + 1])) << 16) | (uint32_t(uint8_t(s[o + 2])) << 8) | uint32_t(uint8_t(s[o + 3]));
}
inline double wall() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }

struct Ceiling {
    static constexpr double WHOLE_FRAME_MS = 20.0, RATE_HALFLIFE_S = 2.0;
    std::mutex m; double rate = 0, last = 0; size_t hard = SEG_BYTES;
    void set_hard(size_t sndbuf) { std::lock_guard<std::mutex> g(m); hard = sndbuf / 2 > SEG_BYTES ? sndbuf / 2 : SEG_BYTES; }
    void note(size_t n) {                                  // bytes the semantic socket accepted
        std::lock_guard<std::mutex> g(m); double now = wall();
        if (last == 0) { last = now; return; }
        double dt = now - last; last = now; if (dt <= 0) return;
        double a = 1.0 - std::exp(-dt / RATE_HALFLIFE_S); rate = (1.0 - a) * rate + a * (double(n) / dt);
    }
    size_t whole_max() {                                   // the largest frame allowed to go whole on the semantic socket
        std::lock_guard<std::mutex> g(m); if (rate <= 0) return SEG_BYTES;
        size_t by_time = size_t(rate * (WHOLE_FRAME_MS / 1000.0));
        return by_time < SEG_BYTES ? SEG_BYTES : (by_time > hard ? hard : by_time);
    }
};

// The sending end: transfers are queued whole and a writer thread sends them one segment at a time, round robin, so
// N transfers in flight each get every Nth segment. `write` puts one complete record on the socket.
class Sender {
public:
    explicit Sender(std::function<void(const std::string&)> write) : write_(std::move(write)) {}
    ~Sender() { stop(); }
    void start() { thread_ = std::thread([this] { run(); }); }
    void stop() {
        { std::lock_guard<std::mutex> g(m_); if (stop_) return; stop_ = true; } cv_.notify_all();
        if (thread_.joinable()) thread_.join();
    }
    void submit(uint32_t seq, std::string frame) {
        { std::lock_guard<std::mutex> g(m_); active_.push_back(Transfer{seq, std::move(frame), 0}); ++transfers_; } cv_.notify_one();
    }
    uint64_t transfers() const { std::lock_guard<std::mutex> g(m_); return transfers_; }
    uint64_t segments() const { std::lock_guard<std::mutex> g(m_); return segments_; }
    uint64_t bytes() const { std::lock_guard<std::mutex> g(m_); return bytes_; }
    size_t max_in_flight() const { std::lock_guard<std::mutex> g(m_); return max_active_; }
private:
    struct Transfer { uint32_t seq; std::string frame; size_t offset; };
    void run() {
        for (;;) {
            Transfer t; bool last_segment = false;
            { std::unique_lock<std::mutex> l(m_); cv_.wait(l, [&] { return stop_ || !active_.empty(); });
              if (stop_) return;
              if (active_.size() > max_active_) max_active_ = active_.size();
              t = std::move(active_.front()); active_.pop_front(); }
            const size_t n = t.frame.size() - t.offset < SEG_BYTES ? t.frame.size() - t.offset : SEG_BYTES;
            std::string rec = be32(uint32_t(12 + n)) + be32(t.seq) + be32(uint32_t(t.frame.size())) + be32(uint32_t(t.offset)) + t.frame.substr(t.offset, n);
            t.offset += n; last_segment = t.offset >= t.frame.size();
            write_(rec);                                   // outside the lock: the socket's pace is TCP's business
            { std::lock_guard<std::mutex> g(m_); ++segments_; bytes_ += rec.size();
              if (!last_segment) active_.push_back(std::move(t)); }   // to the back: every other transfer goes first
        }
    }
    std::function<void(const std::string&)> write_;
    mutable std::mutex m_; std::condition_variable cv_; std::deque<Transfer> active_; bool stop_ = false; std::thread thread_;
    uint64_t transfers_ = 0, segments_ = 0, bytes_ = 0; size_t max_active_ = 0;
};

// The receiving end: records arrive interleaved; a frame is handed back the moment its last byte lands.
class Reassembler {
public:
    // `rec` is one record without its 4-byte length prefix: [seq][total][offset][bytes]. Returns the complete frame
    // and its seq when this record finished it. A record that does not fit its transfer is a protocol error.
    std::optional<std::pair<uint32_t, std::string>> feed(const std::string& rec) {
        if (rec.size() < 12) throw std::runtime_error("data socket: record shorter than its header");
        const uint32_t seq = rd32(rec, 0), total = rd32(rec, 4), offset = rd32(rec, 8); const size_t n = rec.size() - 12;
        std::lock_guard<std::mutex> g(m_);
        auto& p = parts_[seq];
        if (p.buf.empty()) { p.buf.assign(total, '\0'); p.total = total; p.got = 0; }
        if (p.total != total || size_t(offset) + n > p.total)
            throw std::runtime_error("data socket: segment does not fit its transfer (seq " + std::to_string(seq) + ")");
        p.buf.replace(offset, n, rec, 12, n); p.got += n;
        if (p.got < p.total) return std::nullopt;
        std::string frame = std::move(p.buf); parts_.erase(seq);
        return std::make_pair(seq, std::move(frame));
    }
private:
    struct Part { std::string buf; size_t total = 0, got = 0; };
    std::mutex m_; std::map<uint32_t, Part> parts_;
};

// Landed payloads, by sequence: the data reader puts them, the frame that carries their marker takes them -- on its
// own thread, so nothing else on the session waits for them.
class Landed {
public:
    void put(uint32_t seq, std::string bytes) { { std::lock_guard<std::mutex> g(m_); by_[seq] = std::move(bytes); } cv_.notify_all(); }
    // Blocks until the payload for seq has landed, or the session is closed (then throws).
    std::string take(uint32_t seq) {
        std::unique_lock<std::mutex> l(m_);
        cv_.wait(l, [&] { return closed_ || by_.count(seq); });
        if (!by_.count(seq)) throw std::runtime_error("data socket closed before the payload of seq " + std::to_string(seq) + " landed");
        std::string b = std::move(by_[seq]); by_.erase(seq); return b;
    }
    void close() { { std::lock_guard<std::mutex> g(m_); closed_ = true; } cv_.notify_all(); }
private:
    std::mutex m_; std::condition_variable cv_; std::map<uint32_t, std::string> by_; bool closed_ = false;
};

}  // namespace dataplane
}  // namespace fnwp
