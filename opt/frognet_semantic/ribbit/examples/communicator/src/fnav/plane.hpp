// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// One fnav plane: a non-blocking TCP connection carrying audio or video (FNAV-SPEC.md, "Send path and data plane").
//
//   send()  -- whole frames only. The room is checked before the first byte; nothing written means the frame is
//              dropped cleanly; a part-written frame is finished within 250 ms or completed with abort padding that
//              ends in the sentinel. Audio is retried twice 8 ms apart, a keyframe three times, other video never.
//   recv()  -- always consumes the declared length, so an aborted frame costs one frame and the stream stays framed.
//
// [THE_CALL_IS_UNDER_THE_FRAME_V1] John, 2026-11-24: "the communicator can use the keys and Cypher and encode the
// video and audio across the wire. They would be able to switch protocols and etc. Without interrupting video."
// [ONE_WRITER_PER_PLANE_V1] John, 2026-11-21: "A connection's wire has one owner. One writer for each connection takes
// messages from the request threads and alone packs, sends switch calls, seals and writes. No request thread touches
// the socket." (2026-11-24, of this plane's write lock: "Why is there a write lock?") Each plane has one writer
// thread. send() hands it the body and returns what the writer says became of it, so its callers, the rate ladder and
// the media server's counts are as they were. The lock that let several threads write one socket is gone. The writer's
// one wait is on being handed something and, while the frame still owes the far end a call, on the socket taking it.
//
// With set_channel() every body this plane sends crosses in the protocol layer's frame (channel_frame.hpp): packed by
// the Role's protocol, sealed under the Role's current key, and a switch of either travels in the stream. Nothing
// above the plane changes, and nothing of the plane's own way of writing does: what goes on the wire is still one
// length and one unit, and a unit is still shed or aborted whole. A unit that did not go is taken back from the
// frame (undo), so the two ends never differ about what was sent.
#pragma once
#include "frame.hpp"
#include "segment.hpp"
#include "channel_frame.hpp"
#include "net.hpp"
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

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
    static constexpr uint8_t WHOLE_UNIT = 0;               // the last byte of a unit under the frame ([A_WHOLE_UNIT_SAYS_SO_V1])

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
    // This plane's end of the frame, and the Role its bodies are for. Before the first send or recv.
    // by_first_frame (the end that accepted the connection): the far end's first unit says whether it speaks the
    // frame. A far end that does not is spoken to as before, and the codec is let go.
    void set_channel(std::shared_ptr<channel_frame::Codec> codec, std::string role, bool by_first_frame = false);
    bool framed() const { return codec_ != nullptr && !undecided_ && !plain_; }
    channel_frame::Codec* channel() const { return codec_.get(); }
    size_t owed() const { return pad_.size() - pad_off_; }

    void shutdown_both();                                  // wakes a reader in another thread ([SHUTDOWN_NOT_CLOSE_V1])
    int fd() const { return fd_; }
    PlaneStats stats;

private:
    // ---- the writer's: only the plane's one writer thread calls these or touches the socket to write
    struct Job { const Bytes* body; SendKind kind; Sent result = Sent::Dropped; bool done = false; std::string error; };
    void writer();
    Sent write_body(const Bytes& body, SendKind kind);     // one body, under the frame or not
    bool write_owed();                                     // the calls and repeats this end's frame owes; false: one did not go
    Sent send_unit(const Bytes& unit, SendKind kind);      // what goes on the wire: one length, one unit
    Waker waker_;
    std::mutex hand_;                                      // the list of bodies handed to the writer; never held while writing
    std::condition_variable told_;
    std::deque<Job*> jobs_;
    std::string broken_;                                   // why the writer could not go on, once it cannot
    std::atomic<bool> stop_{false}, owed_pending_{false};
    std::thread wthread_;
    // ---- the reader's
    Recv recv_unit(Bytes& unit, int timeout_ms);
    std::shared_ptr<channel_frame::Codec> codec_;
    std::string role_;
    std::atomic<bool> undecided_{false}, plain_{false};    // plain_: the far end does not speak the frame
    std::deque<Bytes> ready_;
    bool wait_writable(int ms) const;
    bool wait_readable(int ms) const;
    void read_exact(uint8_t* p, size_t n, bool mid_frame);
    long unsent_bytes() const;
    int fd_;
    bool debt_mode_ = false;
    Bytes pad_;                                            // abort padding still owed
    size_t pad_off_ = 0;
    bool pay_debt();                                       // non-blocking; true when square
    RateMeter rate_;                                       // bytes accepted per second ([THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1])
    uint16_t vseg_id_ = 0;
public:
    std::atomic<uint64_t> vseg_frames_sent{0}, vseg_frames_aborted{0}, frames_shed{0};
};

}  // namespace fnav
