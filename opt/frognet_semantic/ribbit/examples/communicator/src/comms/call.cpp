// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "call.hpp"
#include <condition_variable>
#include "media/transcode.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <chrono>
#include <cmath>
#include <cstring>
#include <deque>
#include <random>

namespace comms {

using namespace fnav;
using Clock = std::chrono::steady_clock;
static double now_s() { return std::chrono::duration<double>(Clock::now().time_since_epoch()).count(); }

bool SyntheticSource::next(AVFrame* f) {
    for (int y = 0; y < h_; ++y)
        for (int x = 0; x < w_; ++x) {
            int dx = x - (w_ / 2 + int(w_ / 6 * std::sin(n_ / 10.0))), dy = y - h_ / 2, r = h_ / 5;
            f->data[0][y * f->linesize[0] + x] = uint8_t(40 + (x + y) * 64 / (w_ + h_) + (dx * dx + dy * dy < r * r ? 60 : 0) + ((x * 7 + y * 13 + n_) % 5));
        }
    for (int p = 1; p < 3; ++p)
        for (int y = 0; y < h_ / 2; ++y) std::memset(f->data[p] + y * f->linesize[p], p == 1 ? 120 : 136, size_t(w_ / 2));
    ++n_;
    return true;
}

// The upload rung's size: L8..L5 are the 16:9 ladder (media::SIZES 0..3); never larger than the source.
static int size_for_rung(int rung, int src_w, int src_h) {
    if (rung < 5) return -1;                               // L4 and below: no video
    int i = 8 - rung;
    while (i < 3 && (media::SIZES[size_t(i)].w > src_w || media::SIZES[size_t(i)].h > src_h)) ++i;
    return i;
}
static int ceiling_for(int w, int h) {                     // [EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1]
    for (int r = 8; r >= 5; --r) if (media::SIZES[size_t(8 - r)].w <= w && media::SIZES[size_t(8 - r)].h <= h) return r;
    return 5;
}

bool ToneSource::next(Bytes& b) {
    b.resize(BLOCK_BYTES);
    for (int i = 0; i < BLOCK_BYTES / 2; ++i, ++n_) {
        int16_t s = int16_t(amp_ * std::sin(2 * M_PI * hz_ * double(n_) / AUDIO_RATE));
        b[size_t(2 * i)] = uint8_t(uint16_t(s) & 0xFF); b[size_t(2 * i + 1)] = uint8_t(uint16_t(s) >> 8);
    }
    return true;
}

Call::Call(const std::string& host, int port, const std::string& name, const std::string& session,
           std::unique_ptr<Source> source, int fps, std::unique_ptr<AudioSource> mic, std::unique_ptr<AudioSink> speaker,
           int cushion_ms)
    : host_(host), name_(name), session_(session), port_(port), fps_(fps), src_(std::move(source)),
      mic_(std::move(mic)), spk_(std::move(speaker)),
      mixer_(BLOCK_BYTES, size_t(AUDIO_RATE) * size_t(cushion_ms) / 1000 * SAMPLE_BYTES) {
    std::mt19937_64 rng(std::random_device{}());
    Bytes tag;
    for (int i = 0; i < 8; ++i) tag.push_back(uint8_t('a' + rng() % 26));       // one teardown tag, both planes
    auto open = [&](uint8_t kind) {
        auto p = std::make_unique<Plane>(Plane::connect_to(host_, port_, 8000));   // [A_HANG_IS_THE_WORST_REPORT_V1]
        Bytes decl{kind};
        decl.insert(decl.end(), tag.begin(), tag.end());
        decl.insert(decl.end(), session_.begin(), session_.end());
        p->send(pack_typed(KIND_PLANE, name_, decl), SendKind::Control);
        return p;
    };
    vplane_ = open(PLANE_VIDEO);
    aplane_ = open(PLANE_AUDIO);                          // [TWO_PLANES_V1]
}

Call::~Call() { stop(); }

void Call::start() {
    vdl_ = std::make_unique<DelayLine>(vplane_.get(), jitter_ms_, stop_);
    adl_ = std::make_unique<DelayLine>(aplane_.get(), jitter_ms_, stop_);
    if (src_) tx_ = std::thread([this] { video_tx(); });
    rx_ = std::thread([this] { video_rx(); });
    if (mic_) atx_ = std::thread([this] { audio_tx(); });
    arx_ = std::thread([this] { audio_rx(); });
    if (spk_) play_ = std::thread([this] { playout(); });
}

void Call::audio_tx() {                                    // 20 ms blocks, Opus, format byte first
    Opus opus;
    Bytes block;
    auto next = Clock::now();
    try {
        while (!stop_ && mic_->next(block)) {
            Bytes body = pack_typed(KIND_AUDIO, name_, pack_audio(AUDIO_FMT_OPUS, opus.encode(block)));
            if (aplane_->send(body, SendKind::Audio) == Sent::Whole) { stats.a_sent++; stats.a_bytes += body.size(); }
            else { stats.a_shed++; audio_shed_++; }        // [AUDIO_DRIVES_THE_RESOLUTION_V1]
            next += std::chrono::milliseconds(BLOCK_MS);
            if (next < Clock::now()) next = Clock::now();  // [NO_CATCH_UP_BURST_V1]
            std::this_thread::sleep_until(next);
        }
    } catch (const ConnectionError&) {}
}

// [JITTER_IS_THE_LINK_V1] The jitter slider is this participant's link, both directions, like the bandwidth slider.
// The receive side: a reader thread drains the socket continuously (the sender sees no back-pressure) and stamps each
// frame with a release time -- its arrival plus a random 0..N ms, never before the frame ahead of it (TCP does not
// reorder). The receive loop takes frames as they come due. Sleeping in the receive loop instead would ADD each delay
// to the last: the lag would grow without bound, not wobble.
class DelayLine {
public:
    DelayLine(fnav::Plane* p, std::atomic<int>& jitter_ms, std::atomic<bool>& stop)
        : p_(p), jit_(jitter_ms), stop_(stop), th_([this] { read(); }) {}
    ~DelayLine() { if (th_.joinable()) th_.join(); }
    Recv get(Bytes& body, int timeout_ms) {
        std::unique_lock<std::mutex> g(mu_);
        auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
        while (true) {
            if (!q_.empty() && q_.front().first <= Clock::now()) {
                body = std::move(q_.front().second); q_.pop_front();
                return Recv::Frame;
            }
            if (q_.empty() && closed_) throw ConnectionError("closed");
            auto until = q_.empty() ? deadline : std::min(deadline, q_.front().first);
            if (cv_.wait_until(g, until) == std::cv_status::timeout && Clock::now() >= deadline &&
                (q_.empty() || q_.front().first > Clock::now()))
                return Recv::Timeout;
        }
    }
private:
    void read() {
        std::mt19937 rng(std::random_device{}());
        Bytes body;
        Clock::time_point last = Clock::now();
        try {
            while (!stop_) {
                if (p_->recv(body, 200) != Recv::Frame) continue;
                const int j = jit_.load();
                auto release = Clock::now() + std::chrono::milliseconds(j > 0 ? int(rng() % unsigned(j + 1)) : 0);
                if (release < last) release = last;          // TCP does not reorder
                last = release;
                std::lock_guard<std::mutex> g(mu_);
                if (q_.size() < 4000) q_.emplace_back(release, std::move(body));   // bounded: a stalled reader cannot eat memory
                cv_.notify_one();
            }
        } catch (const ConnectionError&) {}
        std::lock_guard<std::mutex> g(mu_);
        closed_ = true;
        cv_.notify_all();
    }
    fnav::Plane* p_;
    std::atomic<int>& jit_;
    std::atomic<bool>& stop_;
    std::mutex mu_;
    std::condition_variable cv_;
    std::deque<std::pair<Clock::time_point, Bytes>> q_;
    bool closed_ = false;
    std::thread th_;
};

void Call::audio_rx() {
    std::map<std::string, std::unique_ptr<Opus>> dec;      // one decoder state per remote stream
    Bytes body;
    try {
        while (!stop_) {
            if (adl_->get(body, 200) != Recv::Frame) continue;   // through the receive-side jitter
            Typed t;
            try { t = unpack_typed(body); } catch (const FrameError&) { continue; }
            if (t.kind == KIND_AUDIO_BACKPRESSURE) { stats.a_bp++; audio_shed_++; continue; }   // [AUDIO_BACKPRESSURE_V1]
            if (t.kind != KIND_AUDIO || t.payload.empty()) continue;
            stats.a_recv++; stats.a_rx_bytes += body.size();   // [RECEIVE_IS_MEASURED_V1] before the mixer
            uint8_t fmt = t.payload[0];
            Bytes data(t.payload.begin() + 1, t.payload.end());
            Bytes pcm;
            if (fmt == AUDIO_FMT_OPUS) {
                auto& d = dec[t.src];
                if (!d) d = std::make_unique<Opus>();
                try { pcm = d->decode(data); } catch (const AudioError&) { stats.a_refused++; continue; }
            } else if (fmt == AUDIO_FMT_PCM) pcm = data;
            else { stats.a_refused++; continue; }          // [AUDIO_IS_OPUS_V1] refused, never guessed
            mixer_.feed(t.src, pcm);
        }
    } catch (const ConnectionError&) {}
}

void Call::playout() {                                     // the speaker pulls one mixed block every 20 ms
    auto next = Clock::now();
    while (!stop_) {
        Bytes b = mixer_.pull_block();
        const float g = gain_.load();
        if (g != 1.0f) {                                   // the listener's volume, clipped, never wrapped
            auto* p = reinterpret_cast<int16_t*>(b.data());
            for (size_t i = 0; i < b.size() / 2; ++i) {
                float v = float(p[i]) * g;
                p[i] = int16_t(v > 32767.f ? 32767.f : v < -32768.f ? -32768.f : v);
            }
        }
        spk_->play(b);
        next += std::chrono::milliseconds(BLOCK_MS);
        if (next < Clock::now()) next = Clock::now();
        std::this_thread::sleep_until(next);
    }
}

void Call::stop() {
    if (stop_.exchange(true)) return;
    if (vplane_) vplane_->shutdown_both();
    if (aplane_) aplane_->shutdown_both();
    for (auto* t : {&tx_, &rx_, &atx_, &arx_, &play_}) if (t->joinable()) t->join();
    vdl_.reset(); adl_.reset();                            // their readers end with the planes' shutdown
}

void Call::set_throttle(long bps) { throttle_bps_ = bps < 0 ? 0 : bps; }
void Call::set_volume(float g) { gain_ = g < 0 ? 0.f : g > 1.5f ? 1.5f : g; }
void Call::set_jitter(int ms) { jitter_ms_ = ms < 0 ? 0 : ms; }

void Call::video_tx() {
    AVFrame* pic = av_frame_alloc();
    pic->format = AV_PIX_FMT_YUV420P; pic->width = src_->width(); pic->height = src_->height();
    av_frame_get_buffer(pic, 0);
    Bearer bearer(ceiling_for(src_->width(), src_->height()), 0, now_s());
    std::unique_ptr<media::Encoder> enc;
    int enc_size = -2;
    std::mt19937 rng(7);
    double bucket = 0, bucket_t = now_s();
    std::deque<double> sent_times;                         // frames actually sent in the last second: fps_sent
    // [SOURCE_CANNOT_SUSTAIN_V1] production signals only once there IS production at this rung: 15 frames and 2 s
    // (fnav WARMUP_FRAMES, WARMUP_S). A cold start, or a rung just changed, reads 0 fps -- not a verdict.
    const int WARMUP_FRAMES = 15; const double WARMUP_S = 2.0;
    int rung_frames = 0; double rung_t0 = now_s();
    double stress_from = -1, clean_from = -1;               // the runs the hysteresis lag is measured from
    // [BOTTOM_RUNG_SHRINKS_V1] Below L5 the picture is not shed: it gets smaller -- 640x360, 480x360, 320x240, 160x120
    // (media::SIZES 3..6) -- and only after 160x120 is refused too does the sender go audio only. "Video is not gone; it
    // is smaller." One step per BOTTOM_STEP_S at most; shrink only when the WIRE refuses frames
    // ([SHRINKING_ONLY_HELPS_IF_THE_WIRE_IS_THE_LIMIT_V1]); grow back one step after BOTTOM_CLEAN_WINDOWS clean
    // windows ([GROWTH_IS_EARNED_V1]); when the walk is back at 640x360 the ladder resumes AT L5
    // ([THE_CLIMB_BACK_IS_A_CLIMB_V1]). fnav.py _bottom_shrink / _bottom_clean / _bottom_grow.
    const int BOTTOM_FIRST = 3, BOTTOM_LAST = 6;
    const double BOTTOM_STEP_S = 2.0, WINDOW_S = 2.0; const int BOTTOM_CLEAN_WINDOWS = 3;
    int bottom = 0, bottom_ok = 0;                          // steps below 640x360
    double bottom_at = 0, win_t0 = now_s();
    int win_refused = 0;                                    // frames the wire refused this window
    bool in_bottom = false;                                 // the ladder is below L5 and video is still going
    bool shed = false;                                      // 160x120 was refused too: audio only until L5 again
    try {
        while (!stop_) {
            auto deadline = Clock::now() + std::chrono::microseconds(1000000 / fps_);   // [NO_CATCH_UP_BURST_V1]
            av_frame_make_writable(pic);
            src_->next(pic);
            { std::lock_guard<std::mutex> g(pmu_); copy_picture(pic, self_); }   // the self-view
            int size;
            if (shed) size = -1;                             // audio only
            else if (in_bottom || bottom > 0) size = BOTTOM_FIRST + bottom;   // the bottom walk owns the size
            else size = size_for_rung(bearer.idx(), src_->width(), src_->height());
            stats.rung = shed ? bearer.idx() : (in_bottom || bottom > 0) ? 5 : bearer.idx();
            int dropped = 0;
            if (size >= 0) {
                if (size != enc_size) { enc = std::make_unique<media::Encoder>(media::SIZES[size_t(size)], fps_); enc_size = size; }
                stats.send_w = media::SIZES[size_t(size)].w; stats.send_h = media::SIZES[size_t(size)].h;
                bool force = want_key_.exchange(false);
                for (auto& vp : enc->encode(pic, force)) {
                    bool key = unpack_video(vp).key;
                    if (int j = jitter_ms_.load()) std::this_thread::sleep_for(std::chrono::milliseconds(rng() % (j + 1)));
                    if (long bps = throttle_bps_.load()) {     // DEMO: a smaller wire -- frames over budget are shed
                        double t = now_s();
                        bucket = std::min(double(bps) / 8.0, bucket + (t - bucket_t) * bps / 8.0); bucket_t = t;
                        if (double(vp.size()) > bucket && !key) { ++dropped; stats.v_dropped++; continue; }
                        bucket -= double(vp.size());
                    }
                    if (vplane_->send_video(name_, vp, key)) {
                        stats.v_sent++; stats.v_bytes += vp.size(); if (key) stats.keys_sent++;
                        sent_times.push_back(now_s()); ++rung_frames;
                    } else { ++dropped; stats.v_dropped++; want_key_ = want_key_ || key; }
                }
            } else { enc.reset(); enc_size = -2; stats.send_w = 0; stats.send_h = 0; }
            double t = now_s();
            while (!sent_times.empty() && t - sent_times.front() > 1.0) sent_times.pop_front();
            std::optional<double> fps_sent;
            if (size >= 0 && rung_frames >= WARMUP_FRAMES && t - rung_t0 >= WARMUP_S) fps_sent = double(sent_times.size());
            win_refused += dropped;
            if (t - win_t0 >= WINDOW_S) {                    // one window's verdict
                if (win_refused > 0) bottom_ok = 0; else ++bottom_ok;
                win_refused = 0; win_t0 = t;
            }
            int before = bearer.idx();
            // a run starts on the first stressed (clean) evidence, BEFORE this sample may step and reset the counters
            if ((dropped > 0 || bearer.bad_reads() > 0 || bearer.sec_drops() > 0) && stress_from < 0) stress_from = t;
            bearer.sample(t, 0, dropped, fps_sent, double(fps_), audio_shed_.exchange(0));
            stats.ceiling = bearer.ceiling(); stats.stressed = bearer.bad_reads(); stats.clean = bearer.clean_secs(); stats.sec_drops = bearer.sec_drops();
            if (bearer.clean_secs() > 0 && clean_from < 0) clean_from = t;
            if (bearer.idx() < before) {                      // a step down: the lag since the stress began
                if (stress_from >= 0) { stats.down_lag_ms += uint64_t((t - stress_from) * 1000); stats.down_steps++; }
                stress_from = clean_from = -1;
            } else if (bearer.idx() > before) {               // a step up: the lag since the clean run began
                if (clean_from >= 0) { stats.up_lag_ms += uint64_t((t - clean_from) * 1000); stats.up_steps++; }
                stress_from = clean_from = -1;
            } else {
                if (dropped == 0 && bearer.bad_reads() == 0 && bearer.sec_drops() == 0) stress_from = -1;   // the stress passed
                if (bearer.clean_secs() == 0) clean_from = -1;
            }
            const int r = bearer.idx();
            if (shed) {
                // audio only until the ladder is back at L5; then video restarts at 160x120 and earns its way up
                if (r >= 5) { shed = false; in_bottom = true; bottom = BOTTOM_LAST - BOTTOM_FIRST; bottom_at = t; bottom_ok = 0; bearer.resume_at(5, t); }
            } else if (r < 5) {
                // below L5: shrink only if the wire is refusing; past 160x120 there is nowhere left: audio only
                if (!in_bottom) { in_bottom = true; bottom_at = t; }
                else if (dropped > 0 && t - bottom_at >= BOTTOM_STEP_S) {
                    if (BOTTOM_FIRST + bottom < BOTTOM_LAST) { ++bottom; bottom_at = t; bottom_ok = 0; }
                    else { shed = true; in_bottom = false; bottom = 0; }   // 160x120 refused too: audio only
                }
            } else if (r > 5 && bottom > 0) {
                // the ladder wants more: the picture grows back first, one earned step at a time
                if (bottom_ok >= BOTTOM_CLEAN_WINDOWS && t - bottom_at >= BOTTOM_STEP_S) {
                    --bottom; bottom_at = t; bottom_ok = 0;
                    if (bottom == 0) { in_bottom = false; bearer.resume_at(5, t); }
                } else bearer.resume_at(5, t);              // hold the ladder at L5 while the picture grows
            } else if (r >= 5 && bottom == 0) in_bottom = false;
            if (bearer.idx() != before) {                  // [RATE_IS_MEASURED_AT_THIS_RUNG_V1] judged afresh at the new rung
                sent_times.clear(); rung_frames = 0; rung_t0 = t;
            }
            std::this_thread::sleep_until(deadline);
        }
    } catch (const ConnectionError&) {}
    av_frame_free(&pic);
}

void Call::video_rx() {
    Reassembler reasm;
    std::map<std::string, AVCodecContext*> decs;
    AVFrame* f = av_frame_alloc();
    AVPacket* pkt = av_packet_alloc();
    Bytes body, vp;
    try {
        while (!stop_) {
            if (vdl_->get(body, 200) != Recv::Frame) continue;   // through the receive-side jitter
            Typed t;
            try { t = unpack_typed(body); } catch (const FrameError&) { continue; }
            if (t.kind == KIND_KEYREQ) { want_key_ = true; stats.keyreqs++; continue; }   // [KEYFRAME_ON_REQUEST_V1]
            const Bytes* payload = nullptr;
            if (t.kind == KIND_VIDEO) payload = &t.payload;
            else if (t.kind == KIND_VSEG && reasm.feed(t.src, t.payload, vp)) payload = &vp;
            if (!payload) continue;
            Video v;
            try { v = unpack_video(*payload); } catch (const FrameError&) { continue; }
            stats.v_recv++; stats.v_rx_bytes += payload->size();   // [RX_IS_PER_SOURCE_V1] counted before decoding
            AVCodecContext*& d = decs[t.src];
            if (!d) { const AVCodec* c = avcodec_find_decoder_by_name("vp8"); d = avcodec_alloc_context3(c); avcodec_open2(d, c, nullptr); }
            pkt->data = v.packet.data(); pkt->size = int(v.packet.size());
            if (avcodec_send_packet(d, pkt) < 0) { stats.decode_errors++; continue; }   // one frame's problem only
            while (avcodec_receive_frame(d, f) == 0) {
                std::lock_guard<std::mutex> g(pmu_);
                Picture& p = pics_[t.src];
                copy_picture(f, p);
                p.level = v.level;
            }
        }
    } catch (const ConnectionError&) {}
    for (auto& d : decs) avcodec_free_context(&d.second);
    av_frame_free(&f); av_packet_free(&pkt);
}

void copy_picture(const AVFrame* f, Picture& p) {
    p.w = f->width; p.h = f->height; p.seq++;
    const int cw = (p.w + 1) / 2, ch = (p.h + 1) / 2;
    p.y.resize(size_t(p.w) * size_t(p.h)); p.u.resize(size_t(cw) * size_t(ch)); p.v.resize(size_t(cw) * size_t(ch));
    for (int y = 0; y < p.h; ++y) std::memcpy(p.y.data() + size_t(y) * size_t(p.w), f->data[0] + y * f->linesize[0], size_t(p.w));
    for (int y = 0; y < ch; ++y) {
        std::memcpy(p.u.data() + size_t(y) * size_t(cw), f->data[1] + y * f->linesize[1], size_t(cw));
        std::memcpy(p.v.data() + size_t(y) * size_t(cw), f->data[2] + y * f->linesize[2], size_t(cw));
    }
}

bool Call::self_view(Picture& out) {
    std::lock_guard<std::mutex> g(pmu_);
    if (!self_.w) return false;
    out = self_;
    return true;
}

bool Call::latest(const std::string& src, Picture& out) {
    std::lock_guard<std::mutex> g(pmu_);
    auto it = pics_.find(src);
    if (it == pics_.end()) return false;
    out = it->second;
    return true;
}

std::vector<std::string> Call::sources() {
    std::lock_guard<std::mutex> g(pmu_);
    std::vector<std::string> v;
    for (auto& p : pics_) v.push_back(p.first);
    return v;
}

}  // namespace comms
