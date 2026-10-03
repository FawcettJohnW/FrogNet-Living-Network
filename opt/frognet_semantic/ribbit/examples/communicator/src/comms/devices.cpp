// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "devices.hpp"
#include "audio.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavdevice/avdevice.h>
#include <libavformat/avformat.h>
#include <libavutil/imgutils.h>
#include <libswscale/swscale.h>
}
#include <portaudio.h>
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>

namespace comms {

// ---------------------------------------------------------------- video input

AvInputSource::AvInputSource(const std::string& kind, const std::string& url, int w, int h, int fps)
    : kind_(kind == "live-file" ? "file" : kind), url_(url), w_(w), h_(h), fps_(fps) {
    live_ = kind == "camera" || kind == "live-file";
    avdevice_register_all();
    raw_ = av_frame_alloc();
    pkt_ = av_packet_alloc();
    newest_ = av_frame_alloc();
    if (!open()) throw std::runtime_error("cannot open " + kind_ + " " + url_ + ": " + desc_);
    if (live_) th_ = std::thread([this] { capture(); });
}

AvInputSource::~AvInputSource() {
    stop_ = true;
    cv_.notify_all();
    if (th_.joinable()) th_.join();
    if (sws_) sws_freeContext(sws_);
    if (dec_) avcodec_free_context(&dec_);
    if (fmt_) avformat_close_input(&fmt_);
    av_frame_free(&raw_);
    av_frame_free(&newest_);
    av_packet_free(&pkt_);
}

bool AvInputSource::open() {
    // the input format's type follows the installed FFmpeg: non-const in 4.4 (Ubuntu 22.04), const from 5.0
    decltype(av_find_input_format("")) ifmt = nullptr;
    AVDictionary* opts = nullptr;
    if (kind_ == "camera") {
#ifdef _WIN32
        ifmt = av_find_input_format("dshow");
#else
        ifmt = av_find_input_format("v4l2");
#endif
        if (!ifmt) { desc_ = "this FFmpeg has no camera input (libavdevice v4l2/dshow)"; return false; }
        std::string size = std::to_string(w_) + "x" + std::to_string(h_);
        av_dict_set(&opts, "video_size", size.c_str(), 0);
        av_dict_set(&opts, "framerate", std::to_string(fps_).c_str(), 0);
#ifdef _WIN32
        av_dict_set(&opts, "vcodec", "mjpeg", 0);             // [ASK_THE_CAMERA_FOR_MJPEG_V1]
#else
        av_dict_set(&opts, "input_format", "mjpeg", 0);       // [ASK_THE_CAMERA_FOR_MJPEG_V1]
#endif
    }
    // FFmpeg 4.4 (Ubuntu 22.04) returns a const format but takes a non-const one here; 5.0+ takes const. Valid in both.
    int rc = avformat_open_input(&fmt_, url_.c_str(), const_cast<AVInputFormat*>(ifmt), &opts);
    av_dict_free(&opts);
    char eb[256];
    if (rc < 0) { av_strerror(rc, eb, sizeof eb); desc_ = eb; return false; }
    if (avformat_find_stream_info(fmt_, nullptr) < 0) { desc_ = "no stream information"; return false; }
    vstream_ = av_find_best_stream(fmt_, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (vstream_ < 0) { desc_ = "no video stream"; return false; }
    AVCodecParameters* par = fmt_->streams[vstream_]->codecpar;
    const AVCodec* c = avcodec_find_decoder(par->codec_id);
    if (!c) { desc_ = "no decoder for this video"; return false; }
    dec_ = avcodec_alloc_context3(c);
    avcodec_parameters_to_context(dec_, par);
    if (avcodec_open2(dec_, c, nullptr) < 0) { desc_ = "decoder would not open"; return false; }
    desc_ = std::string(kind_ == "camera" ? "camera " : "file ") + url_ + ": " + c->name + " " +
            std::to_string(par->width) + "x" + std::to_string(par->height) + " -> sent as " +
            std::to_string(w_) + "x" + std::to_string(h_);
    std::cerr << "[source] " << desc_ << "\n";                // what was actually agreed
    return true;
}

bool AvInputSource::decode_one(AVFrame* into) {
    for (int guard = 0; guard < 1000 && !stop_; ++guard) {
        int rc = avcodec_receive_frame(dec_, into);
        if (rc == 0) return true;
        if (rc != AVERROR(EAGAIN) && rc != AVERROR_EOF) return false;
        rc = av_read_frame(fmt_, pkt_);
        if (rc == AVERROR_EOF && kind_ == "file") {          // a file loops
            av_seek_frame(fmt_, vstream_, 0, AVSEEK_FLAG_BACKWARD);
            avcodec_flush_buffers(dec_);
            continue;
        }
        if (rc < 0) return false;                            // a camera that stops is a real failure
        if (pkt_->stream_index == vstream_) avcodec_send_packet(dec_, pkt_);
        av_packet_unref(pkt_);
    }
    return false;
}

void AvInputSource::scale(const AVFrame* in, AVFrame* out) {
    // [FULL_RANGE_IS_SAID_NOT_ASSUMED_V1] A camera's MJPEG decodes to the full-range "yuvj" formats. Handing those
    // to the scaler as they are logged a deprecation warning on EVERY frame (found 2026-10-01 on the Pi, eMeet C950)
    // and read 0-255 levels as 16-235: a washed-out picture. Use the plain format and say the source is full range.
    AVPixelFormat pf = AVPixelFormat(in->format);
    bool full = in->color_range == AVCOL_RANGE_JPEG;
    switch (pf) {
        case AV_PIX_FMT_YUVJ420P: pf = AV_PIX_FMT_YUV420P; full = true; break;
        case AV_PIX_FMT_YUVJ422P: pf = AV_PIX_FMT_YUV422P; full = true; break;
        case AV_PIX_FMT_YUVJ444P: pf = AV_PIX_FMT_YUV444P; full = true; break;
        case AV_PIX_FMT_YUVJ440P: pf = AV_PIX_FMT_YUV440P; full = true; break;
        default: break;
    }
    sws_ = sws_getCachedContext(sws_, in->width, in->height, pf, w_, h_, AV_PIX_FMT_YUV420P, SWS_BILINEAR, nullptr, nullptr, nullptr);
    if (!sws_) return;
    if (full) {                                               // full-range in, limited-range (what encoders expect) out
        const int* coef = sws_getCoefficients(SWS_CS_DEFAULT);
        sws_setColorspaceDetails(sws_, coef, 1, coef, 0, 0, 1 << 16, 1 << 16);
    }
    sws_scale(sws_, in->data, in->linesize, 0, in->height, out->data, out->linesize);
    AVRational tb = fmt_->streams[vstream_]->time_base;
    if (in->pts != AV_NOPTS_VALUE) last_pts_ms_ = av_rescale_q(in->pts, tb, AVRational{1, 1000});
}

void AvInputSource::capture() {                              // live: decode everything, keep only the newest
    AVFrame* f = av_frame_alloc();
    while (!stop_) {
        if (!decode_one(f)) { if (!stop_) std::cerr << "[source] " << url_ << " stopped delivering frames\n"; break; }
        {
            std::lock_guard<std::mutex> g(mu_);
            if (newest_seq_ > taken_seq_) superseded_++;     // the previous newest was never sent: that is fine
            av_frame_unref(newest_);
            av_frame_move_ref(newest_, f);
            newest_seq_++;
        }
        cv_.notify_one();
    }
    av_frame_free(&f);
}

bool AvInputSource::next(AVFrame* out) {
    if (!live_) {                                            // a file in order: frame after frame
        if (!decode_one(raw_)) return false;
        scale(raw_, out);
        av_frame_unref(raw_);
        return true;
    }
    std::unique_lock<std::mutex> g(mu_);
    // the newest frame; if none newer than the last one sent has arrived, wait briefly for one
    cv_.wait_for(g, std::chrono::milliseconds(200), [&] { return newest_seq_ > taken_seq_ || stop_; });
    if (newest_seq_ == 0) return false;
    taken_seq_ = newest_seq_;
    scale(newest_, out);
    return true;
}

std::string video_devices() {
    avdevice_register_all();
#ifdef _WIN32
    const char* fmt_name = "dshow"; const char* prefix = "video=";
#else
    const char* fmt_name = "v4l2"; const char* prefix = "";
#endif
    auto fmt = av_find_input_format(fmt_name);
    if (!fmt) return std::string("(this FFmpeg has no ") + fmt_name + " camera input)\n";
    AVDeviceInfoList* list = nullptr;
    int n = avdevice_list_input_sources(fmt, nullptr, nullptr, &list);
    if (n < 0) { char eb[256]; av_strerror(n, eb, sizeof eb); return std::string("(cannot list cameras: ") + eb + ")\n"; }
    std::string s;
    for (int i = 0; i < list->nb_devices; ++i) {
        const AVDeviceInfo* d = list->devices[i];
#if LIBAVDEVICE_VERSION_MAJOR >= 59
        bool video = false;                                  // dshow lists microphones too: keep the cameras
        for (int k = 0; k < d->nb_media_types; ++k) if (d->media_types[k] == AVMEDIA_TYPE_VIDEO) video = true;
        if (d->nb_media_types > 0 && !video) continue;
#endif
        std::string name = std::string(prefix) + (prefix[0] ? d->device_description : d->device_name);
        s += "  --camera \"" + name + "\"   (" + d->device_description + ")\n";
    }
    avdevice_free_list_devices(&list);
    return s.empty() ? std::string("  (no cameras found)\n") : s;
}

// ---------------------------------------------------------------- audio devices

void audio_init() {
    static bool done = false;
    if (done) return;
    PaError e = Pa_Initialize();
    if (e != paNoError) throw std::runtime_error(std::string("PortAudio: ") + Pa_GetErrorText(e));
    done = true;
}

std::string audio_devices() {
    audio_init();
    std::string s;
    int n = Pa_GetDeviceCount();
    for (int i = 0; i < n; ++i) {
        const PaDeviceInfo* d = Pa_GetDeviceInfo(i);
        s += std::to_string(i) + ": " + d->name + "  (in " + std::to_string(d->maxInputChannels) + ", out " +
             std::to_string(d->maxOutputChannels) + ", " + std::to_string(int(d->defaultSampleRate)) + " Hz)\n";
    }
    return s;
}

double true_rate(double measured, double nominal) {
    if (measured <= 0 || nominal <= 0) return nominal;
    if (std::abs(measured - nominal) <= 0.10 * nominal) return nominal;
    static const double standard[] = {8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000};
    double best = standard[0];
    for (double r : standard) if (std::abs(r - measured) < std::abs(best - measured)) best = r;
    return best;
}

void Resampler::push(const int16_t* in, size_t n, std::vector<int16_t>& out) {
    for (size_t i = 0; i < n; ++i) {
        if (!primed_) { last_ = in[i]; primed_ = true; continue; }
        // emit every output sample that falls between last_ (position 0) and in[i] (position 1)
        while (pos_ < 1.0) {
            out.push_back(int16_t(double(last_) + (double(in[i]) - double(last_)) * pos_));
            pos_ += step_;
        }
        pos_ -= 1.0;
        last_ = in[i];
    }
}

// [ASK_THE_DEVICE_NOT_THE_DEFAULT_V1] A device is opened at ITS OWN rate, mono; this program resamples to and from the
// 16 kHz wire rate. A raw ALSA device (hw:N,0, as a USB webcam's microphone is when no sound server is running)
// converts nothing, so asking it for 16 kHz fails. The rate it actually delivers is measured and logged once.
static void* open_stream(int device, bool input, PaStreamCallback* cb, void* user, double& rate) {
    audio_init();
    PaStreamParameters p{};
    p.device = device >= 0 ? device : (input ? Pa_GetDefaultInputDevice() : Pa_GetDefaultOutputDevice());
    if (p.device == paNoDevice || p.device >= Pa_GetDeviceCount())   // [AUDIO_ERROR_NAMES_THE_CAUSE_V1]
        throw std::runtime_error(std::string("no ") + (input ? "input" : "output") + " audio device " +
                                 (device >= 0 ? std::to_string(device) : std::string("(default)")) + " (PortAudio found " +
                                 std::to_string(Pa_GetDeviceCount()) + "; see --list-devices)");
    const PaDeviceInfo* info = Pa_GetDeviceInfo(p.device);
    if ((input ? info->maxInputChannels : info->maxOutputChannels) < 1)
        throw std::runtime_error(std::string(info->name) + " has no " + (input ? "input" : "output") + " channels");
    p.channelCount = 1;
    p.sampleFormat = paInt16;
    p.suggestedLatency = input ? info->defaultLowInputLatency : info->defaultLowOutputLatency;
    rate = info->defaultSampleRate;
    PaStream* s = nullptr;
    PaError e = Pa_OpenStream(&s, input ? &p : nullptr, input ? nullptr : &p, rate,
                              (unsigned long)(rate * BLOCK_MS / 1000), paClipOff, cb, user);
    if (e != paNoError)
        throw std::runtime_error(std::string("cannot open ") + info->name + " at " + std::to_string(int(rate)) + " Hz mono: " + Pa_GetErrorText(e));
    e = Pa_StartStream(s);
    if (e != paNoError) throw std::runtime_error(std::string("cannot start ") + info->name + ": " + Pa_GetErrorText(e));
    std::cerr << "[audio] " << (input ? "mic " : "speaker ") << info->name << " at " << int(rate) << " Hz, resampled to "
              << AUDIO_RATE << " Hz\n";
    return s;
}

PaMic::PaMic(int device) {
    t0_ = std::chrono::steady_clock::now();
    stream_ = open_stream(device, true, &PaMic::cb, this, rate_);
    rs_ = std::make_unique<Resampler>(rate_, AUDIO_RATE);
}
PaMic::~PaMic() { if (stream_) { Pa_StopStream(stream_); Pa_CloseStream(stream_); } }

int PaMic::cb(const void* in, void*, unsigned long frames, const PaStreamCallbackTimeInfo*, unsigned long status, void* user) {
    auto* m = static_cast<PaMic*>(user);
    if (status & paInputOverflow) m->overflowed++;
    std::vector<int16_t> out;
    {
        std::lock_guard<std::mutex> g(m->mu_);
        m->dev_frames_ += frames;
        // What the device ACTUALLY delivers, measured from 0.5 s to 2.5 s after the first callback (skipping the burst a
        // device sends while it starts). If that is not what it claims, resample from what it delivers.
        double el = std::chrono::duration<double>(std::chrono::steady_clock::now() - m->t0_).count();
        if (!m->settled_) {
            if (el >= 0.5) m->win_frames_ += frames;
            if (el >= 2.5) {
                m->settled_ = true;
                double measured = double(m->win_frames_) / (el - 0.5);
                double real = true_rate(measured, m->rate_);
                std::cerr << "[audio] mic measured: " << int(measured) << " samples/s from a device that says " << int(m->rate_);
                if (real != m->rate_) {
                    std::cerr << " -- it does not deliver what it says: resampling from " << int(real) << "\n";
                    m->rs_ = std::make_unique<Resampler>(real, AUDIO_RATE);
                    m->rate_ = real;
                } else std::cerr << " (as it says)\n";
            }
        }
        m->rs_->push(static_cast<const int16_t*>(in), frames, out);   // under the lock: rs_ can be replaced above
        m->acc_.insert(m->acc_.end(), out.begin(), out.end());
        const size_t per = BLOCK_BYTES / SAMPLE_BYTES;
        while (m->acc_.size() >= per) {
            if (m->q_.size() >= 25) { m->q_.pop_front(); m->overflowed++; }   // 0.5 s behind: counted, newest wins
            const auto* b = reinterpret_cast<const uint8_t*>(m->acc_.data());
            m->q_.emplace_back(b, b + BLOCK_BYTES);
            m->acc_.erase(m->acc_.begin(), m->acc_.begin() + long(per));
            m->captured++;
        }
    }
    m->cv_.notify_one();
    return paContinue;
}

bool PaMic::next(Bytes& block) {
    std::unique_lock<std::mutex> g(mu_);
    if (!cv_.wait_for(g, std::chrono::milliseconds(500), [&] { return !q_.empty(); })) return false;
    block = std::move(q_.front());
    q_.pop_front();
    return true;
}

PaSpeaker::PaSpeaker(int device) {
    stream_ = open_stream(device, false, &PaSpeaker::cb, this, rate_);
    rs_ = std::make_unique<Resampler>(AUDIO_RATE, rate_);
}
PaSpeaker::~PaSpeaker() { if (stream_) { Pa_StopStream(stream_); Pa_CloseStream(stream_); } }

void PaSpeaker::play(const Bytes& block) {
    std::vector<int16_t> out;
    rs_->push(reinterpret_cast<const int16_t*>(block.data()), block.size() / SAMPLE_BYTES, out);
    std::lock_guard<std::mutex> g(mu_);
    const size_t cap = size_t(rate_ * 0.5);                  // the mixer already holds the cushion; this is the device's slack
    if (q_.size() + out.size() > cap) { dropped++; return; }
    q_.insert(q_.end(), out.begin(), out.end());
}

int PaSpeaker::cb(const void*, void* out, unsigned long frames, const PaStreamCallbackTimeInfo*, unsigned long, void* user) {
    auto* s = static_cast<PaSpeaker*>(user);
    auto* o = static_cast<int16_t*>(out);
    std::lock_guard<std::mutex> g(s->mu_);
    size_t have = std::min<size_t>(frames, s->q_.size());
    std::copy(s->q_.begin(), s->q_.begin() + long(have), o);
    s->q_.erase(s->q_.begin(), s->q_.begin() + long(have));
    if (have < frames) { std::fill(o + have, o + frames, int16_t(0)); s->underruns++; }
    s->played++;
    return paContinue;
}

}  // namespace comms
