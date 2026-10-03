// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms-media, step 1: connections, sessions, forwarding (FNAV-SPEC.md, "The relay ... what the new media server
// keeps"). Transcoding per rung (decision 2) sits between a sender's reassembled frame and the per-viewer fan, and
// comes in step 2; in step 1 every viewer gets the sender's own stream.
//
//   - The first frame on a connection declares it: KIND_PLANE, payload = plane(1) + teardown tag(8) + call session.
//     No session: the connection can neither send nor receive ([FAN_IS_PER_SESSION_V1]).
//   - Audio goes to every other participant's audio plane in the session; it is never shed while video is still
//     being delivered to that viewer ([AUDIO_DRIVES_THE_RESOLUTION_V1]).
//   - Video: a viewer that lost its reference is unanchored and gets no inter frames until a keyframe goes; a keyframe
//     that does not fit is held (newer replaces older; at most 3 s) ([KEYFRAME_ANCHOR_V1],
//     [KEYFRAME_SURVIVES_EWOULDBLOCK_V1], [A_HOLD_IS_NOT_FOREVER_V1]); an unanchored viewer with nothing held asks
//     the sender for a keyframe, at most once per 3 s per sender ([KEYREQ_NEEDS_ROOM_V1]).
//   - Losing one plane shuts down the other plane of the SAME teardown tag only ([TEARDOWN_IS_PER_SESSION_V1]).
#pragma once
#include "fnav/plane.hpp"
#include "transcode.hpp"
#include <atomic>
#include <condition_variable>
#include <deque>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace media {

using fnav::Bytes;
using Clock = std::chrono::steady_clock;

struct Viewer {                               // per video-plane connection, as a receiver
    bool unanchored = true;                   // until its first keyframe goes
    Bytes held_key;                           // a keyframe waiting for room (whole KIND_VIDEO payload)
    std::string held_src;
    Clock::time_point held_at{};
    uint64_t aimed = 0, delivered = 0, inters_shed = 0, keys_held = 0;
    // What this viewer can take: -1 = no limit (the sender's own stream); 0..SIZES-1 = the largest ladder size it may
    // be sent; SIZES.size() = audio only. Set from the viewer's cap (MediaSpeed) and its measured link.
    int target = -1;
    long cap_bps = 0;
    int stream = -2;                          // the stream it is on now: -1 the upload, i a transcoded size, -2 none yet
    // The measured link (FNAV-SPEC.md, "Measuring a viewer"): the largest size this viewer's own link has been
    // taking, -1 = no limit found yet. The target is the smaller of this and the cap.
    int link_target = -1;
    uint64_t w_aimed0 = 0, w_deliv0 = 0;      // counters at the start of the current window
    int bad_windows = 0, good_windows = 0;
    Clock::time_point changed_at{}, judge_after{};
    std::map<int, Clock::time_point> held_off; // size -> not to be retried before ([PROBE_BACKOFF_V1])
    std::map<int, int> fails;                  // size -> consecutive failures
    double last_ratio = -1;                    // delivered/aimed in the last judged window (stats)
    int upload_from = -1;                      // the upload's ladder size, from the rung in its frame headers
};

struct Conn {
    std::unique_ptr<fnav::Plane> plane;
    std::string peer, name, session;
    uint8_t kind = 0;                         // 'A' or 'V'; 0 until declared
    Bytes tag;                                // 8-byte teardown tag
    fnav::Reassembler reasm;                  // this connection's incoming VSEG pieces
    std::mutex vmu;                           // guards view: several senders' pumps deliver to one viewer
    Viewer view;                              // this connection as a video viewer
    uint64_t audio_aimed = 0, audio_delivered = 0;
    Clock::time_point keyreq_at{};            // last keyframe request sent TO this sender
    std::atomic<bool> dead{false};
    std::unique_ptr<SenderTranscoder> tx;     // this connection as a video sender: decode once, encode per size
    // Transcoding never runs on the thread that reads the upload: frames for transcoded viewers go to this sender's
    // worker. It decodes every frame and encodes only the newest; 2 s behind, it skips to the next keyframe.
    std::thread txw;
    std::mutex txmu;
    std::condition_variable txcv;
    std::deque<Bytes> txq;
    bool txstop = false;
};

class Server {
public:
    static constexpr double HELD_KEY_MAX_S = 3.0, KEYREQ_MIN_S = 3.0;
    Server(const std::string& host, int port);
    ~Server();
    void run();                               // accept loop; returns when stop() is called
    void stop();
    std::string snapshot();                   // per-connection accounting, one line each (tests, logs)
    struct Hold {                             // one viewer's video plane, as the server is serving it
        std::string session, viewer;
        bool unanchored = true, key_held = false;
        uint64_t aimed = 0, delivered = 0, inters_shed = 0, keys_held = 0;
        int stream_w = 0, stream_h = 0;       // the size being sent to it; 0 = its sender's own (pass-through) or none
        bool passthrough = false;
        bool audio_only = false;              // the viewer's link is too slow for any video: it is sent audio only
    };
    std::vector<Hold> holds();                // [MEDIAHOLD_IS_MEMORY_V1] published to memory for the viewer's screen
    // A viewer's download cap in bits/s (0 = none), applied to that viewer only (MediaSpeed, August ruling).
    void set_cap(const std::string& viewer, long bps);
    // A MediaSpeed row: the viewer in `session` whose id is `viewer` -- its name, or fnav's name + 4 hex digits.
    // Never matched by address: viewers behind one address (NAT) must not share a cap.
    void set_cap_row(const std::string& session, const std::string& viewer, const std::string& addr, long bps);
    static int target_for_cap(long bps);      // the largest size whose video budget plus audio fits; SIZES.size() = audio only
    static constexpr long AUDIO_BPS = 32000;  // Opus 24 kb/s plus framing
    // The per-viewer link controller: fnav's consumer rules, applied here to each viewer.
    static constexpr double WINDOW_S = 2.0, HAPPY_FRACTION = 0.80, SETTLE_S = 45.0, DROP_SETTLE_S = 4.0,
                            RATE_SETTLE_S = 15.0, HOLD_BASE_S = 15.0, HOLD_MAX_S = 300.0;
    static constexpr int BAD_WINDOWS = 2, HAPPY_WINDOWS = 3;
    void control_tick();                      // one window for every viewer (run on its own thread by run())

private:
    void pump(std::shared_ptr<Conn> c);
    void declare(const std::shared_ptr<Conn>& c, const fnav::Typed& t);
    void fan_audio(const std::shared_ptr<Conn>& from, const Bytes& body);
    void fan_video(const std::shared_ptr<Conn>& from, const Bytes& video_payload, bool key);
    void deliver_video(const std::shared_ptr<Conn>& from, const std::shared_ptr<Conn>& to, const Bytes& vp, bool key, int stream);
    void deliver_video_locked(const std::shared_ptr<Conn>& from, const std::shared_ptr<Conn>& to, const Bytes& vp, bool key, int stream);
    void ask_key(const std::shared_ptr<Conn>& sender);
    void teardown(const std::shared_ptr<Conn>& c);
    void retarget(Viewer& v);
    void tx_worker(std::shared_ptr<Conn> from);
    std::vector<int> wanted_sizes(const std::shared_ptr<Conn>& from, const std::vector<std::shared_ptr<Conn>>& viewers, int up);
    std::vector<std::shared_ptr<Conn>> peers_of(const std::shared_ptr<Conn>& c, uint8_t kind);

    int lfd_;
    std::atomic<bool> stop_{false};
    std::mutex mu_;
    std::vector<std::shared_ptr<Conn>> conns_;
    std::vector<std::thread> threads_;
};

}  // namespace media
