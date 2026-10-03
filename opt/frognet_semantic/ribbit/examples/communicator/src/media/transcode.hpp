// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Transcoding in the media server (decision 2): the sender uploads ONE stream; a viewer whose download cannot take it
// gets a smaller size made here -- decoded once per sender, encoded once per size in use, shared by every viewer at
// that size. Never larger than the upload.
//
// The sizes are fnav's geometry ladder for 16:9 (FNAV-SPEC.md, "Rungs and codecs"), largest first; the bottom three
// are 4:3 and gray, as fnav does below L5:
//     0 1920x1080 3.0M   1 1280x720 1.2M   2 854x480 600k   3 640x360 300k gray
//     4 480x360 250k gray   5 320x240 150k gray   6 160x120 60k gray (the floor)
// Encoder: VP8 with fnav's settings -- deadline=realtime, cpu-used=8, lag-in-frames=0, g=fps*2, and the VBV that makes
// the budget real: maxrate = bitrate, bufsize = bitrate/2 ([VBV_OR_THE_BUDGET_IS_A_WISH_V1]). A size that has just been
// started opens on a keyframe ([A_NEW_SIZE_NEEDS_A_NEW_KEYFRAME_V1]).
#pragma once
#include "fnav/frame.hpp"
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct AVCodecContext;
struct AVFrame;
struct SwsContext;

namespace media {

struct Size { int w, h, bitrate; bool gray; uint32_t level; };
extern const std::vector<Size> SIZES;          // index 0 = largest
int size_index(int w, int h);                  // -1 if not a ladder size

struct TranscodeError : std::runtime_error { using std::runtime_error::runtime_error; };

class Encoder {                                // one output size
public:
    Encoder(const Size& s, int fps);
    ~Encoder();
    // Encode one decoded picture; returns the VIDEO payloads produced (usually one).
    std::vector<fnav::Bytes> encode(const AVFrame* src, bool force_key);
    const Size& size() const { return s_; }
private:
    Size s_;
    int fps_;
    AVCodecContext* ctx_ = nullptr;
    SwsContext* sws_ = nullptr;
    AVFrame* frame_ = nullptr;
    int64_t pts_ = 0;
    bool first_ = true;
    int sws_w_ = 0, sws_h_ = 0, sws_fmt_ = -1;
};

class SenderTranscoder {                       // one per sender: one decoder, encoders per size in use
public:
    explicit SenderTranscoder(int fps = 24);
    ~SenderTranscoder();
    // Feed the sender's VIDEO payload. For every size in `want`, return that size's payloads (may be empty).
    std::map<int, std::vector<fnav::Bytes>> feed(const fnav::Bytes& video_payload, const std::vector<int>& want);
    // The two halves of feed(), for a worker that must decode EVERY upload frame (the decoder's references need them)
    // but encode only the NEWEST picture when it is behind (newest wins: fewer frames, never more delay).
    bool decode(const fnav::Bytes& video_payload);          // true if a new picture was produced
    std::map<int, std::vector<fnav::Bytes>> encode_latest(const std::vector<int>& want);
    void force_key(int size) { force_.push_back(size); }   // a viewer joined this size's stream mid-way
    int upload_index() const;                              // the ladder size of the upload, -1 until decoded
    int source_w() const { return src_w_; }
    int source_h() const { return src_h_; }
    uint64_t decoded = 0, decode_errors = 0;
private:
    int fps_;
    AVCodecContext* dec_ = nullptr;
    int dec_codec_ = -1;
    AVFrame* pic_ = nullptr;
    AVFrame* latest_ = nullptr;                // the newest decoded picture, not yet encoded
    bool latest_new_ = false;
    int src_w_ = 0, src_h_ = 0;
    std::map<int, std::unique_ptr<Encoder>> enc_;
    std::vector<int> force_;
};

}  // namespace media
