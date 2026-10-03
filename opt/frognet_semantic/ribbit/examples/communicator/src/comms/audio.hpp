// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Audio (FNAV-SPEC.md, "The audio engine"; fnphone_pa.py and fnav.py's OpusCodec).
//   Wire: 16 kHz mono s16le, 20 ms blocks (640 bytes), Opus at 24 kb/s. An AUDIO payload is one format byte
//   (1 Opus, 0 PCM -- an operator decision) then the data; any other byte is REFUSED, never decoded as something.
//   Mixer: one playout buffer per remote source. A source waits for its cushion (default 40 ms) the first time, then
//   one block is pulled from each and summed with clipping; a source that runs dry gives silence, never a stall, and
//   is not made to re-buffer. The backlog is capped at max(240 ms, 2x cushion); bytes cut to hold it are counted, as
//   are blocks completed with silence ([MIXER_DAMAGE_IS_COUNTED_V1]). Ported line for line from fnphone_pa.Mixer.
#pragma once
#include "fnav/frame.hpp"
#include <cstdint>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

struct OpusEncoder;
struct OpusDecoder;

namespace comms {

using fnav::Bytes;
constexpr int AUDIO_RATE = 16000, AUDIO_CH = 1, SAMPLE_BYTES = 2, BLOCK_MS = 20;
constexpr int BLOCK_BYTES = AUDIO_RATE * BLOCK_MS / 1000 * AUDIO_CH * SAMPLE_BYTES;   // 640
constexpr uint8_t AUDIO_FMT_PCM = 0, AUDIO_FMT_OPUS = 1;
constexpr int OPUS_BITRATE = 24000;

Bytes mix_s16le(const std::vector<Bytes>& chunks, size_t nbytes);

class Mixer {
public:
    Mixer(size_t block_bytes, size_t jitter_bytes, size_t max_buffer_bytes = 0);
    void feed(const std::string& src, const Bytes& pcm);
    Bytes pull_block();
    uint64_t trimmed = 0, trims = 0, pads = 0, pad_bytes = 0, dry = 0, out_bytes = 0;
    int last_peak = 0, active = 0;
    size_t max_buffer_bytes() const { return max_; }
private:
    size_t block_, jitter_, max_;
    std::vector<std::string> order_;                 // insertion order, as a Python dict iterates
    std::map<std::string, Bytes> bufs_;
    std::map<std::string, bool> started_;
    std::mutex mu_;
};

class EchoDucker {                                   // soft suppression, not cancellation (fnphone_pa.EchoDucker)
public:
    explicit EchoDucker(bool enabled = true, double floor = 0.15, int speaker_thresh = 400)
        : enabled_(enabled), floor_(floor), thresh_(speaker_thresh) {}
    double mic_gain(int speaker_peak);
private:
    bool enabled_; double floor_; int thresh_; double env_ = 0.0;
};

struct AudioError : std::runtime_error { using std::runtime_error::runtime_error; };

class Opus {                                         // one per direction, as fnav does
public:
    Opus();
    ~Opus();
    Bytes encode(const Bytes& pcm_block);            // 640 bytes of 16 kHz mono s16le -> one packet
    Bytes decode(const Bytes& packet);               // one packet -> PCM at 16 kHz (no 48 kHz trap: the C API decodes at the rate asked)
private:
    OpusEncoder* enc_ = nullptr;
    OpusDecoder* dec_ = nullptr;
};

Bytes pack_audio(uint8_t fmt, const Bytes& data);    // [fmt][data]
double opus_packet_ms(const Bytes& packet);          // from the TOC byte; throws on an unreadable packet ([NO_FALLBACK_V1])

}  // namespace comms
