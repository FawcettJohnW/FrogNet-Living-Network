// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// One fnav plane: a non-blocking TCP connection carrying audio or video (FNAV-SPEC.md, "Send path and data plane").
//
//   send()  -- whole frames only. The room is checked before the first byte; nothing written means the frame is
//              dropped cleanly; a part-written frame is finished within 250 ms or completed with abort padding that
//              ends in the sentinel. Audio is retried twice 8 ms apart, a keyframe three times, other video never.
//   recv()  -- always consumes the declared length, so an aborted frame costs one frame and the stream stays framed.
#pragma once
#include "frame.hpp"
#include "segment.hpp"
#include <atomic>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>

namespace fnav {

// The connection is finished: closed, reset, or desynchronised beyond repair. Never retried silently.
struct ConnectionError : std::runtime_error { using std::runtime_error::runtime_error; };

enum class SendKind { Audio, Keyframe, Video, Control };
enum class Sent { Whole, Dropped, Aborted };   // Aborted: committed, could not finish, padded; the stream is intact

enum class Recv { Frame, Aborted, Closed, Timeout };  // Closed: the peer closed cleanly BETWEEN frames

struct PlaneStats {                            // cumulative; each reader diffs ([READ_A_COUNTER_NOBODY_ELSE_DRAINS_V1])
    std::atomic<uint64_t> frames_sent{0}, bytes_sent{0}, dropped{0}, aborted_out{0};
    std::atomic<uint64_t> frames_recv{0}, bytes_recv{0}, aborted_in{0};
};

class Plane {
public:
#ifdef _WIN32
    // Windows has no measure of what the send buffer holds (no SIOCOUTQ), so the room check cannot run there; a
    // smaller buffer bounds how much stale picture can wait in it ([THE_BUFFER_IS_THE_LATENCY_V1]).
    static constexpr int SNDBUF = 1 << 16;                 // 64 KiB
#else
    static constexpr int SNDBUF = 1 << 17;                 // 128 KiB ([THE_BUFFER_IS_THE_LATENCY_V1])
#endif
    static constexpr int AUDIO_RETRIES = 2, KEY_RETRIES = 3;
    static constexpr int RETRY_WAIT_MS = 8;
    static constexpr int FINISH_MS = 250;

    explicit Plane(int fd);                                // takes ownership; makes it non-blocking
    ~Plane();
    Plane(const Plane&) = delete;
    Plane& operator=(const Plane&) = delete;

    static int connect_to(const std::string& host, int port, int timeout_ms);   // throws ConnectionError
    static int listen_on(const std::string& host, int port);                    // non-blocking listener

    Sent send(const Bytes& body, SendKind kind);           // one whole frame; throws ConnectionError if desynced
    Recv recv(Bytes& body, int timeout_ms);                // one whole frame; throws ConnectionError mid-frame

    // A VIDEO payload from `src`: one whole frame if it fits whole_frame_max() at the rate this socket has been
    // accepting, else VSEG pieces. Returns false if the frame was dropped or abandoned (counted once).
    bool send_video(const std::string& src, const Bytes& video_payload, bool is_key);
    // fnav uses the CONFIGURED buffer (self._sndbuf = SNDBUF), not Linux's doubled read-back: hard cap 64 KiB.
    size_t whole_max() const { return whole_frame_max(rate_.bytes_per_s(), SNDBUF); }
    long send_room() const;                                // bytes it can still take, -1 where unknowable

    // [DROP_THE_FRAME_NOT_THE_CALLER_V1] Debt mode, for a media server writing to viewers on another sender's thread:
    // never wait. A part-written frame becomes debt -- its remaining bytes, owed as abort padding -- paid on later
    // attempts as the socket takes it; while debt stands, new frames are shed.
    void set_debt_mode(bool on) { debt_mode_ = on; }
    size_t owed() const { return pad_.size() - pad_off_; }

    void shutdown_both();                                  // wakes a reader in another thread ([SHUTDOWN_NOT_CLOSE_V1])
    int fd() const { return fd_; }
    PlaneStats stats;

private:
    bool wait_writable(int ms) const;
    bool wait_readable(int ms) const;
    void read_exact(uint8_t* p, size_t n, bool mid_frame);
    long unsent_bytes() const;
    int fd_;
    std::mutex wlock_;
    bool debt_mode_ = false;
    Bytes pad_;                                            // abort padding still owed
    size_t pad_off_ = 0;
    bool pay_debt();                                       // non-blocking; true when square
    RateMeter rate_;                                       // bytes accepted per second ([THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1])
    uint16_t vseg_id_ = 0;
public:
    std::atomic<uint64_t> vseg_frames_sent{0}, vseg_frames_aborted{0}, frames_shed{0};                                     // one writer at a time ([SOCKET_WRITE_LOCK_V1])
};

}  // namespace fnav
