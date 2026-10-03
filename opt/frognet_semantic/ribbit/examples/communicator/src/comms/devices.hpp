// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Real inputs and outputs for a call.
//   AvInputSource -- any input libavformat/libavdevice opens, decoded and scaled to the send size (yuv420p):
//                    a video file (looped at its end) or a camera (v4l2 on Linux, dshow on Windows).
//                    [ASK_THE_CAMERA_FOR_MJPEG_V1] a camera is asked for MJPEG at the requested size and rate, and what
//                    it actually agreed is reported, not assumed.
//   PaMic / PaSpeaker -- PortAudio at the wire rate (16 kHz mono s16le, 20 ms blocks). A block the consumer could not
//                    take is counted, never dropped silently ([AUDIO_DROP_IS_LOUD_V1]).
#pragma once
#include "call.hpp"
#include <condition_variable>
#include <chrono>
#include <deque>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <string>

struct AVFormatContext; struct AVCodecContext; struct SwsContext; struct AVPacket;
struct PaStreamCallbackTimeInfo;

namespace comms {

class AvInputSource : public Source {
public:
    // kind: "file" (url = path), "camera" (url = /dev/videoN on Linux, "video=<name>" on Windows)
    AvInputSource(const std::string& kind, const std::string& url, int w, int h, int fps);
    ~AvInputSource() override;
    int width() const override { return w_; }
    int height() const override { return h_; }
    bool next(AVFrame* yuv420p) override;
    std::string describe() const { return desc_; }          // what was actually opened
    uint64_t superseded() const { return superseded_; }     // live input: frames decoded but never sent (newer arrived)
    int64_t last_pts_ms() const { return last_pts_ms_; }    // the presentation time of the frame next() last returned
    // A live input (a camera, or kind "live-file" for testing) is read by its own thread; next() takes the NEWEST
    // decoded frame. [A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]: reading frames in order let a backlog build in the
    // driver and FFmpeg whenever the camera ran faster than the sender -- ~2 s of video delay behind the audio on the
    // eMeet C950 (found 2026-10-01).
private:
    bool open();
    bool decode_one(AVFrame* into);                          // read and decode until one frame comes out
    void scale(const AVFrame* in, AVFrame* out);
    void capture();                                          // the live reader's thread
    std::string kind_, url_, desc_;
    bool live_ = false;
    std::thread th_;
    std::atomic<bool> stop_{false};
    std::mutex mu_;
    std::condition_variable cv_;
    AVFrame* newest_ = nullptr;                              // guarded by mu_
    uint64_t newest_seq_ = 0, taken_seq_ = 0;
    std::atomic<uint64_t> superseded_{0};
    std::atomic<int64_t> last_pts_ms_{-1};
    int w_, h_, fps_, vstream_ = -1;
    AVFormatContext* fmt_ = nullptr;
    AVCodecContext* dec_ = nullptr;
    SwsContext* sws_ = nullptr;
    AVFrame* raw_ = nullptr;
    AVPacket* pkt_ = nullptr;
};

// A streaming linear resampler for mono s16: device rate <-> the 16 kHz wire rate.
class Resampler {
public:
    Resampler(double in_rate, double out_rate) : step_(in_rate / out_rate) {}
    void push(const int16_t* in, size_t n, std::vector<int16_t>& out);
private:
    double step_, pos_ = 0.0;                                // position in input samples, relative to last_
    int16_t last_ = 0;
    bool primed_ = false;
};

// [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] The rate a microphone really delivers, from what it was measured delivering:
// the nominal rate if the measurement is within 10% of it, else the nearest standard rate. The eMeet C950 says 44100
// and delivers ~16000 (fnav 2026-08; measured again 2026-10-01: 13678 over a 5 s window that included the start).
double true_rate(double measured, double nominal);

class PaMic : public AudioSource {
public:
    explicit PaMic(int device = -1);                         // -1: the system default input
    ~PaMic() override;
    bool next(Bytes& block) override;                        // blocks until one 20 ms block is captured
    uint64_t captured = 0, overflowed = 0;
private:
    static int cb(const void*, void*, unsigned long, const PaStreamCallbackTimeInfo*, unsigned long, void*);
    void* stream_ = nullptr;
    std::mutex mu_; std::condition_variable cv_; std::deque<Bytes> q_;
    std::unique_ptr<Resampler> rs_;
    std::vector<int16_t> acc_;                               // 16 kHz samples not yet a whole block
    double rate_ = 0;                                        // the device's own rate
    uint64_t dev_frames_ = 0, win_frames_ = 0; std::chrono::steady_clock::time_point t0_; bool reported_ = false, settled_ = false;
};

class PaSpeaker : public AudioSink {
public:
    explicit PaSpeaker(int device = -1);                     // -1: the system default output
    ~PaSpeaker() override;
    void play(const Bytes& block) override;                  // queued; the device pulls at its own clock
    uint64_t played = 0, underruns = 0, dropped = 0;
private:
    static int cb(const void*, void*, unsigned long, const PaStreamCallbackTimeInfo*, unsigned long, void*);
    void* stream_ = nullptr;
    std::mutex mu_; std::deque<int16_t> q_;                  // at the device's rate
    std::unique_ptr<Resampler> rs_;
    double rate_ = 0;
};

void audio_init();                                           // Pa_Initialize once per process; throws with the cause
std::string audio_devices();                                 // a listing, for --list-devices
std::string video_devices();                                 // cameras, with the exact --camera value for each

}  // namespace comms
