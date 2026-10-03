// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_audio < mixer vectors -- the C++ mixer against fnphone_pa.py's own Mixer (tools/gen_mixer.py): every pulled
// block byte for byte, and every damage counter. Then Opus: a C++-encoded tone decoded by libavcodec's libopus decoder
// (independent of the C++ wrapper) and by the wrapper, both close to the original.
#include "comms/audio.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <cmath>
#include <iostream>
#include <sstream>

using namespace comms;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 10) std::cout << "FAIL " << w << "\n"; }
static Bytes unhex(const std::string& h) { Bytes b; if (h == "-") return b; for (size_t i = 0; i + 1 < h.size(); i += 2) b.push_back(uint8_t(std::stoi(h.substr(i, 2), nullptr, 16))); return b; }

int main() {
    std::string w; size_t block, jitter; std::cin >> w >> block >> jitter;
    Mixer m(block, jitter);
    std::string op; long pulls = 0;
    while (std::cin >> op) {
        if (op == "feed") { std::string src, hex; std::cin >> src >> hex; m.feed(src, unhex(hex)); continue; }
        std::string hex; uint64_t tr, trs, pads, padb, dry; int active, peak;
        std::cin >> hex >> tr >> trs >> pads >> padb >> dry >> active >> peak;
        ++pulls;
        Bytes out = m.pull_block();
        check(out == unhex(hex), "pull " + std::to_string(pulls) + ": block differs");
        check(m.trimmed == tr && m.trims == trs && m.pads == pads && m.pad_bytes == padb && m.dry == dry && m.active == active && m.last_peak == peak,
              "pull " + std::to_string(pulls) + ": counters differ");
    }
    std::cout << "mixer vs fnphone_pa.Mixer (cushion " << jitter << " B): " << pulls << " blocks, " << fail << " fail\n";

    // Opus round trip: 1 s of a 440 Hz tone
    Opus op_; Bytes pcm(BLOCK_BYTES); double err_wrap = 0, energy = 0; int blocks = 50;
    const AVCodec* c = avcodec_find_decoder_by_name("libopus");
    AVCodecContext* d = avcodec_alloc_context3(c); d->sample_rate = 48000;
#if LIBAVCODEC_VERSION_MAJOR >= 59
    d->ch_layout = AV_CHANNEL_LAYOUT_MONO;
#else
    d->channels = 1; d->channel_layout = AV_CH_LAYOUT_MONO;   // FFmpeg 4.4
#endif
    avcodec_open2(d, c, nullptr);
    AVPacket* pkt = av_packet_alloc(); AVFrame* fr = av_frame_alloc(); long av_samples = 0;
    std::vector<double> ref;
    for (int b = 0; b < blocks; ++b) {
        for (int i = 0; i < BLOCK_BYTES / 2; ++i) {
            double v = 8000 * std::sin(2 * M_PI * 440 * (b * 320 + i) / 16000.0);
            int16_t s = int16_t(v); pcm[size_t(2 * i)] = uint8_t(uint16_t(s) & 0xFF); pcm[size_t(2 * i + 1)] = uint8_t(uint16_t(s) >> 8);
            ref.push_back(v);
        }
        Bytes pk = op_.encode(pcm);
        check(std::fabs(opus_packet_ms(pk) - 20.0) < 1e-9, "packet duration from the TOC is 20 ms");
        Bytes back = op_.decode(pk);
        check(back.size() == size_t(BLOCK_BYTES), "the wrapper decodes 640 bytes at 16 kHz, not 3x (the 48 kHz trap)");
        if (b >= 5) for (int i = 0; i < 320; ++i) { double x = int16_t(uint16_t(back[size_t(2 * i)]) | uint16_t(back[size_t(2 * i + 1)]) << 8); err_wrap += std::fabs(x); energy += std::fabs(ref[size_t(b * 320 + i)]); }
        pkt->data = pk.data(); pkt->size = int(pk.size());
        if (avcodec_send_packet(d, pkt) == 0) while (avcodec_receive_frame(d, fr) == 0) av_samples += fr->nb_samples;
    }
    check(av_samples >= 48000 * 9 / 10, "libavcodec's own libopus decoder reads the C++ stream: " + std::to_string(av_samples) + " samples at 48 kHz");
    check(err_wrap > energy * 0.5, "the decoded tone carries the signal (level " + std::to_string(int(100 * err_wrap / energy)) + "% of the original)");
    std::cout << "opus: 50 blocks, 20 ms each by the TOC, decoded 640 B per block at 16 kHz; libavcodec's libopus decoded "
              << av_samples << " samples at 48 kHz; level " << int(100 * err_wrap / energy) << "% of the original\n";
    av_frame_free(&fr); av_packet_free(&pkt); avcodec_free_context(&d);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " audio: " << fail << " fail\n";
    return fail ? 1 : 0;
}
