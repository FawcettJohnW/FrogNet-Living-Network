// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "transcode.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>
#include <libavutil/opt.h>
#include <libswscale/swscale.h>
}
#include <algorithm>
#include <cstring>

namespace media {

using fnav::Bytes;

const std::vector<Size> SIZES = {
    {1920, 1080, 3000000, false, 8}, {1280, 720, 1200000, false, 7}, {854, 480, 600000, false, 6},
    {640, 360, 300000, true, 5},     {480, 360, 250000, true, 5},    {320, 240, 150000, true, 5},
    {160, 120, 60000, true, 5},
};

int size_index(int w, int h) {
    for (size_t i = 0; i < SIZES.size(); ++i) if (SIZES[i].w == w && SIZES[i].h == h) return int(i);
    return -1;
}

static std::string averr(int e) { char b[128]; av_strerror(e, b, sizeof b); return b; }

Encoder::Encoder(const Size& s, int fps) : s_(s), fps_(fps) {
    const AVCodec* c = avcodec_find_encoder_by_name("libvpx");
    if (!c) throw TranscodeError("no libvpx encoder in this libavcodec");
    ctx_ = avcodec_alloc_context3(c);
    ctx_->width = s.w; ctx_->height = s.h;
    ctx_->pix_fmt = AV_PIX_FMT_YUV420P;
    ctx_->time_base = AVRational{1, fps};
    ctx_->framerate = AVRational{fps, 1};
    ctx_->bit_rate = s.bitrate;
    ctx_->rc_max_rate = s.bitrate;                        // [VBV_OR_THE_BUDGET_IS_A_WISH_V1]
    ctx_->rc_buffer_size = s.bitrate / 2;
    ctx_->gop_size = fps * 2;                             // [KEYFRAME_INTERVAL_IS_BOUNDED_V1]
    av_opt_set(ctx_->priv_data, "deadline", "realtime", 0);
    av_opt_set(ctx_->priv_data, "cpu-used", "8", 0);
    av_opt_set(ctx_->priv_data, "lag-in-frames", "0", 0);
    int e = avcodec_open2(ctx_, c, nullptr);
    if (e < 0) throw TranscodeError("open VP8 encoder " + std::to_string(s.w) + "x" + std::to_string(s.h) + ": " + averr(e));
    frame_ = av_frame_alloc();
    frame_->format = AV_PIX_FMT_YUV420P; frame_->width = s.w; frame_->height = s.h;
    if (av_frame_get_buffer(frame_, 0) < 0) throw TranscodeError("frame buffer");
}

Encoder::~Encoder() {
    if (sws_) sws_freeContext(sws_);
    av_frame_free(&frame_);
    avcodec_free_context(&ctx_);
}

std::vector<Bytes> Encoder::encode(const AVFrame* src, bool force_key) {
    if (!sws_ || sws_w_ != src->width || sws_h_ != src->height || sws_fmt_ != src->format) {
        if (sws_) sws_freeContext(sws_);
        sws_ = sws_getContext(src->width, src->height, AVPixelFormat(src->format), s_.w, s_.h, AV_PIX_FMT_YUV420P,
                              SWS_FAST_BILINEAR, nullptr, nullptr, nullptr);
        if (!sws_) throw TranscodeError("no scaler for this picture");
        sws_w_ = src->width; sws_h_ = src->height; sws_fmt_ = src->format;
    }
    av_frame_make_writable(frame_);
    sws_scale(sws_, src->data, src->linesize, 0, src->height, frame_->data, frame_->linesize);
    if (s_.gray)                                          // [SMALLEST_WIRE_WINS_V1] flat chroma at the bottom sizes
        for (int p = 1; p < 3; ++p)
            for (int y = 0; y < s_.h / 2; ++y) std::memset(frame_->data[p] + y * frame_->linesize[p], 128, size_t(s_.w / 2));
    frame_->pts = pts_++;
    frame_->pict_type = (force_key || first_) ? AV_PICTURE_TYPE_I : AV_PICTURE_TYPE_NONE;
    first_ = false;
    std::vector<Bytes> out;
    if (avcodec_send_frame(ctx_, frame_) < 0) return out;
    AVPacket* pkt = av_packet_alloc();
    while (avcodec_receive_packet(ctx_, pkt) == 0) {
        Bytes data(pkt->data, pkt->data + pkt->size);
        bool key = (pkt->flags & AV_PKT_FLAG_KEY) != 0;   // read what the encoder did; never assume
        out.push_back(fnav::pack_video(s_.level, data, fnav::CODEC_VP8, key));
        av_packet_unref(pkt);
    }
    av_packet_free(&pkt);
    return out;
}

int SenderTranscoder::upload_index() const {
    if (src_w_ <= 0) return -1;
    int exact = size_index(src_w_, src_h_);
    if (exact >= 0) return exact;
    for (size_t i = 0; i < SIZES.size(); ++i)            // a non-ladder size: the largest ladder size not above it
        if (SIZES[i].w * SIZES[i].h <= src_w_ * src_h_) return int(i);
    return int(SIZES.size()) - 1;
}

SenderTranscoder::SenderTranscoder(int fps) : fps_(fps) { pic_ = av_frame_alloc(); latest_ = av_frame_alloc(); }
SenderTranscoder::~SenderTranscoder() { av_frame_free(&pic_); av_frame_free(&latest_); avcodec_free_context(&dec_); }

bool SenderTranscoder::decode(const Bytes& vp) {
    fnav::Video v = fnav::unpack_video(vp);
    if (!dec_ || dec_codec_ != v.codec) {
        avcodec_free_context(&dec_);
        const char* name = v.codec == fnav::CODEC_VP8 ? "vp8" : "h264";
        const AVCodec* c = avcodec_find_decoder_by_name(name);
        if (!c) throw TranscodeError(std::string("no decoder ") + name);
        dec_ = avcodec_alloc_context3(c);
        if (avcodec_open2(dec_, c, nullptr) < 0) throw TranscodeError(std::string("open decoder ") + name);
        dec_codec_ = v.codec;
    }
    AVPacket* pkt = av_packet_alloc();
    pkt->data = const_cast<uint8_t*>(v.packet.data());
    pkt->size = int(v.packet.size());
    if (v.key) pkt->flags |= AV_PKT_FLAG_KEY;
    int e = avcodec_send_packet(dec_, pkt);
    av_packet_free(&pkt);
    if (e < 0) { decode_errors++; return false; }
    bool got = false;
    while (avcodec_receive_frame(dec_, pic_) == 0) {
        decoded++;
        src_w_ = pic_->width; src_h_ = pic_->height;
        av_frame_unref(latest_);
        av_frame_move_ref(latest_, pic_);
        latest_new_ = got = true;
    }
    return got;
}

std::map<int, std::vector<Bytes>> SenderTranscoder::encode_latest(const std::vector<int>& want) {
    std::map<int, std::vector<Bytes>> out;
    for (auto it = enc_.begin(); it != enc_.end();)
        if (std::find(want.begin(), want.end(), it->first) == want.end()) it = enc_.erase(it); else ++it;
    if (!latest_new_) return out;
    latest_new_ = false;
    for (int i : want) {
        if (i < 0 || i >= int(SIZES.size())) continue;
        bool fresh = !enc_.count(i);
        if (fresh) enc_[i] = std::make_unique<Encoder>(SIZES[size_t(i)], fps_);
        bool forced = std::find(force_.begin(), force_.end(), i) != force_.end();
        out[i] = enc_[i]->encode(latest_, fresh || forced);
        if (forced) force_.erase(std::remove(force_.begin(), force_.end(), i), force_.end());
    }
    return out;
}

std::map<int, std::vector<Bytes>> SenderTranscoder::feed(const Bytes& vp, const std::vector<int>& want) {
    std::map<int, std::vector<Bytes>> out;
    fnav::Video v = fnav::unpack_video(vp);
    if (!dec_ || dec_codec_ != v.codec) {                  // the sender's codec decides the decoder
        avcodec_free_context(&dec_);
        const char* name = v.codec == fnav::CODEC_VP8 ? "vp8" : "h264";
        const AVCodec* c = avcodec_find_decoder_by_name(name);
        if (!c) throw TranscodeError(std::string("no decoder ") + name);
        dec_ = avcodec_alloc_context3(c);
        if (avcodec_open2(dec_, c, nullptr) < 0) throw TranscodeError(std::string("open decoder ") + name);
        dec_codec_ = v.codec;
    }
    AVPacket* pkt = av_packet_alloc();
    pkt->data = const_cast<uint8_t*>(v.packet.data());
    pkt->size = int(v.packet.size());
    if (v.key) pkt->flags |= AV_PKT_FLAG_KEY;
    int e = avcodec_send_packet(dec_, pkt);
    av_packet_free(&pkt);
    if (e < 0) { decode_errors++; return out; }
    // Drop encoders no viewer wants any more.
    for (auto it = enc_.begin(); it != enc_.end();)
        if (std::find(want.begin(), want.end(), it->first) == want.end()) it = enc_.erase(it); else ++it;
    while (avcodec_receive_frame(dec_, pic_) == 0) {
        decoded++;
        src_w_ = pic_->width; src_h_ = pic_->height;
        for (int i : want) {
            if (i < 0 || i >= int(SIZES.size())) continue;
            bool fresh = !enc_.count(i);
            if (fresh) enc_[i] = std::make_unique<Encoder>(SIZES[size_t(i)], fps_);
            bool forced = std::find(force_.begin(), force_.end(), i) != force_.end();
            auto pkts = enc_[i]->encode(pic_, fresh || forced);
            if (forced) force_.erase(std::remove(force_.begin(), force_.end(), i), force_.end());
            auto& dst = out[i];
            dst.insert(dst.end(), pkts.begin(), pkts.end());
        }
        av_frame_unref(pic_);
    }
    return out;
}

}  // namespace media
