// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "audio.hpp"
#include <algorithm>
#include <cmath>
#include <opus/opus.h>

namespace comms {

static int16_t s16(const uint8_t* p) { return int16_t(uint16_t(p[0]) | uint16_t(p[1]) << 8); }

Bytes mix_s16le(const std::vector<Bytes>& chunks, size_t nbytes) {
    if (chunks.empty()) return Bytes(nbytes, 0);
    if (chunks.size() == 1 && chunks[0].size() == nbytes) return chunks[0];
    size_t n = nbytes / SAMPLE_BYTES;
    std::vector<long> acc(n, 0);
    for (auto& c : chunks) {
        size_t m = std::min(n, c.size() / SAMPLE_BYTES);
        for (size_t i = 0; i < m; ++i) acc[i] += s16(&c[2 * i]);
    }
    Bytes out(nbytes, 0);
    for (size_t i = 0; i < n; ++i) {
        long v = std::clamp(acc[i], -32768L, 32767L);
        out[2 * i] = uint8_t(uint16_t(int16_t(v)) & 0xFF);
        out[2 * i + 1] = uint8_t(uint16_t(int16_t(v)) >> 8);
    }
    return out;
}

Mixer::Mixer(size_t block_bytes, size_t jitter_bytes, size_t max_buffer_bytes) : block_(block_bytes), jitter_(jitter_bytes) {
    size_t floor = size_t(AUDIO_RATE) * 240 / 1000 * AUDIO_CH * SAMPLE_BYTES;   // ~240 ms
    max_ = max_buffer_bytes ? max_buffer_bytes : std::max(floor, jitter_bytes * 2);
}

void Mixer::feed(const std::string& src, const Bytes& pcm) {
    std::lock_guard<std::mutex> g(mu_);
    auto it = bufs_.find(src);
    if (it == bufs_.end()) { it = bufs_.emplace(src, Bytes()).first; started_[src] = false; order_.push_back(src); }
    Bytes& b = it->second;
    b.insert(b.end(), pcm.begin(), pcm.end());
    if (b.size() > max_) {
        size_t drop = b.size() - max_;
        b.erase(b.begin(), b.begin() + long(drop));
        trimmed += drop; trims++;
    }
}

Bytes Mixer::pull_block() {
    std::vector<Bytes> chunks;
    {
        std::lock_guard<std::mutex> g(mu_);
        size_t shortb = 0;
        for (auto& src : order_) {
            Bytes& b = bufs_[src];
            if (!started_[src]) {
                if (b.size() < jitter_) continue;            // still filling the cushion -> silence
                started_[src] = true;
            }
            if (b.size() >= block_) {
                chunks.emplace_back(b.begin(), b.begin() + long(block_));
                b.erase(b.begin(), b.begin() + long(block_));
            } else if (!b.empty()) {
                shortb += block_ - b.size();
                chunks.push_back(b); b.clear();
            } else {
                dry++;                                       // source contributed nothing at all
            }
        }
        if (shortb) { pads++; pad_bytes += shortb; }
    }
    Bytes out = mix_s16le(chunks, block_);
    out_bytes += out.size();
    int peak = 0;
    for (size_t i = 0; i + 1 < out.size(); i += 2) peak = std::max(peak, std::abs(int(s16(&out[i]))));
    last_peak = peak;
    active = int(chunks.size());
    return out;
}

double EchoDucker::mic_gain(int speaker_peak) {
    if (!enabled_) return 1.0;
    if (speaker_peak > env_) env_ = 0.5 * env_ + 0.5 * speaker_peak;     // fast attack
    else env_ = 0.92 * env_ + 0.08 * speaker_peak;                     // slow release
    if (env_ <= 30) return 1.0;                                        // far side effectively silent
    double frac = std::min(1.0, env_ / thresh_);
    return std::max(floor_, 1.0 - (1.0 - floor_) * frac);
}

Opus::Opus() {
    int e = 0;
    enc_ = opus_encoder_create(AUDIO_RATE, AUDIO_CH, OPUS_APPLICATION_AUDIO, &e);   // libav's default application
    if (e != OPUS_OK) throw AudioError(std::string("opus encoder: ") + opus_strerror(e));
    opus_encoder_ctl(enc_, OPUS_SET_BITRATE(OPUS_BITRATE));
    dec_ = opus_decoder_create(AUDIO_RATE, AUDIO_CH, &e);
    if (e != OPUS_OK) throw AudioError(std::string("opus decoder: ") + opus_strerror(e));
}
Opus::~Opus() { if (enc_) opus_encoder_destroy(enc_); if (dec_) opus_decoder_destroy(dec_); }

Bytes Opus::encode(const Bytes& pcm) {
    if (pcm.size() != size_t(BLOCK_BYTES)) throw AudioError("opus encode wants one 20 ms block (640 bytes), got " + std::to_string(pcm.size()));
    std::vector<opus_int16> s(pcm.size() / 2);
    for (size_t i = 0; i < s.size(); ++i) s[i] = s16(&pcm[2 * i]);
    Bytes out(1500);
    int n = opus_encode(enc_, s.data(), int(s.size()), out.data(), int(out.size()));
    if (n < 0) throw AudioError(std::string("opus encode: ") + opus_strerror(n));
    out.resize(size_t(n));
    return out;
}

Bytes Opus::decode(const Bytes& pkt) {
    std::vector<opus_int16> s(AUDIO_RATE * 120 / 1000);      // the longest Opus frame, 120 ms
    int n = opus_decode(dec_, pkt.data(), int(pkt.size()), s.data(), int(s.size()), 0);
    if (n < 0) throw AudioError(std::string("opus decode: ") + opus_strerror(n));
    Bytes out(size_t(n) * 2);
    for (int i = 0; i < n; ++i) { out[size_t(2 * i)] = uint8_t(uint16_t(s[size_t(i)]) & 0xFF); out[size_t(2 * i + 1)] = uint8_t(uint16_t(s[size_t(i)]) >> 8); }
    return out;
}

Bytes pack_audio(uint8_t fmt, const Bytes& data) { Bytes b{fmt}; b.insert(b.end(), data.begin(), data.end()); return b; }

double opus_packet_ms(const Bytes& p) {
    static const double MS[32] = {10, 20, 40, 60, 10, 20, 40, 60, 10, 20, 40, 60, 10, 20, 10, 20,
                                  2.5, 5, 10, 20, 2.5, 5, 10, 20, 2.5, 5, 10, 20, 2.5, 5, 10, 20};
    if (p.empty()) throw AudioError("empty Opus packet");
    int n = opus_packet_get_nb_frames(p.data(), int(p.size()));
    if (n < 0) throw AudioError(std::string("unreadable Opus packet: ") + opus_strerror(n));
    return MS[p[0] >> 3] * n;
}

}  // namespace comms
