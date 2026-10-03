// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// A participant's call (FNAV-SPEC.md, "The call"): two planes to the media server, as fnav does.
//   video plane: sends this participant's video; receives everyone else's video, and keyframe requests
//   audio plane: sends this participant's audio; receives everyone else's audio
// Video: a picture from a Source, encoded at the size the bearer allows (L8..L5; below L5 no video is sent), sent
// whole or segmented; what the wire did is fed back to the bearer every frame. A keyframe request makes the next
// frame a keyframe. Receive: the latest decoded picture per remote source ([RX_IS_PER_SOURCE_V1], newest wins).
// Demo link conditions ([fnav set_throttle / set_jitter]): pace the uplink to N bits/s; inject up to N ms of random
// latency per frame. Both are link conditions, not ladder inputs: the bearer discovers what the smaller wire carries.
#pragma once
#include "fnav/bearer.hpp"
#include "fnav/plane.hpp"
#include "audio.hpp"
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

struct AVFrame;
namespace media { class Encoder; }

namespace comms {

using fnav::Bytes;

// A source of pictures (camera, file, synthetic). next() fills a YUV420P picture of the source's own size.
class Source {
public:
    virtual ~Source() = default;
    virtual int width() const = 0;
    virtual int height() const = 0;
    virtual bool next(AVFrame* yuv420p) = 0;
};

class SyntheticSource : public Source {        // a moving round subject on a room gradient -- camera-like content
public:
    SyntheticSource(int w, int h) : w_(w), h_(h) {}
    int width() const override { return w_; }
    int height() const override { return h_; }
    bool next(AVFrame* f) override;
private:
    int w_, h_, n_ = 0;
};

// A source of 20 ms audio blocks (640 bytes, 16 kHz mono s16le) and a sink that plays them.
class AudioSource { public: virtual ~AudioSource() = default; virtual bool next(Bytes& block) = 0; };
class AudioSink { public: virtual ~AudioSink() = default; virtual void play(const Bytes& block) = 0; };
class ToneSource : public AudioSource {         // a sine tone: for tests and the headless feed's silence check
public:
    explicit ToneSource(double hz, int amp = 8000) : hz_(hz), amp_(amp) {}
    bool next(Bytes& b) override;
private:
    double hz_; int amp_; long n_ = 0;
};
class RecordingSink : public AudioSink {        // keeps what it was asked to play (tests)
public:
    void play(const Bytes& b) override { std::lock_guard<std::mutex> g(mu); data.insert(data.end(), b.begin(), b.end()); }
    std::mutex mu; Bytes data;
};

struct Picture { int w = 0, h = 0; std::vector<uint8_t> y, u, v; uint64_t seq = 0; uint32_t level = 0; };  // yuv420p
void copy_picture(const AVFrame* f, Picture& p);   // an AVFrame (yuv420p) into a Picture, all three planes

struct CallStats {                             // what the screen shows; all cumulative, each reader diffs
    std::atomic<uint64_t> v_sent{0}, v_dropped{0}, v_bytes{0}, keys_sent{0}, keyreqs{0};
    std::atomic<uint64_t> v_recv{0}, v_rx_bytes{0}, decode_errors{0};
    std::atomic<int> rung{0}, send_w{0}, send_h{0};
    std::atomic<uint64_t> a_sent{0}, a_shed{0}, a_recv{0}, a_refused{0}, a_bp{0};
    std::atomic<uint64_t> a_bytes{0}, a_rx_bytes{0};
    std::atomic<int> ceiling{0}, stressed{0}, clean{0}, sec_drops{0};   // the bearer, as it stands
    // the hysteresis lag, measured where the bearer runs: from the start of the stressed (clean) run to the step down (up)
    std::atomic<uint64_t> down_lag_ms{0}, down_steps{0}, up_lag_ms{0}, up_steps{0};
};

class Call {
public:
    Call(const std::string& media_host, int media_port, const std::string& name, const std::string& session,
         std::unique_ptr<Source> source, int fps = 24,
         std::unique_ptr<AudioSource> mic = nullptr, std::unique_ptr<AudioSink> speaker = nullptr, int cushion_ms = 120);
    ~Call();
    void start();
    void stop();

    void set_throttle(long bps);               // DEMO: pace the uplink, 0 = unlimited
    long throttle() const { return throttle_bps_.load(); }
    void set_volume(float gain);               // playback gain, 0 = mute, 1 = as received (up to 1.5)
    void set_jitter(int ms);                   // DEMO: up to ms of random latency per frame, BOTH directions of this link
    bool latest(const std::string& src, Picture& out);   // the newest picture from a remote source
    bool self_view(Picture& out);                        // the newest picture this participant is sending
    std::vector<std::string> sources();
    CallStats stats;
    const std::string& name() const { return name_; }
    AudioSink* speaker() { return spk_.get(); }
    Mixer& mixer() { return mixer_; }

private:
    void video_tx();
    void video_rx();
    void audio_tx();
    void audio_rx();
    void playout();
    std::string host_, name_, session_;
    int port_, fps_;
    std::unique_ptr<Source> src_;
    Picture self_;                                       // guarded by pmu_
    std::unique_ptr<fnav::Plane> vplane_, aplane_;
    std::unique_ptr<AudioSource> mic_;
    std::unique_ptr<AudioSink> spk_;
    Mixer mixer_;
    std::atomic<int> audio_shed_{0};                   // read by the video thread's bearer: the strongest down-signal
    std::thread atx_, arx_, play_;
    std::atomic<bool> stop_{false}, want_key_{false};
    std::atomic<long> throttle_bps_{0};
    std::atomic<int> jitter_ms_{0};
    std::atomic<float> gain_{1.0f};
    std::unique_ptr<class DelayLine> vdl_, adl_;        // the receive side of the jitter
    std::thread tx_, rx_;
    std::mutex pmu_;
    std::map<std::string, Picture> pics_;
};

}  // namespace comms
