// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_transcode -- one 1280x720 VP8 upload, transcoded to 640x360 and 160x120 once each; every output decoded by an
// independent decoder: its size, a keyframe first, gray at the bottom size, and its bitrate held near its budget.
#include "media/transcode.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <cmath>
#include <cstdlib>
#include <iostream>

using namespace media;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 10) std::cout << "FAIL " << w << "\n"; }

int main() {
    const int FPS = 24, N = 96;                           // 4 seconds
    Encoder sender(Size{1280, 720, 1200000, false, 7}, FPS);
    SenderTranscoder tx(FPS);
    AVFrame* f = av_frame_alloc();
    f->format = AV_PIX_FMT_YUV420P; f->width = 1280; f->height = 720;
    av_frame_get_buffer(f, 0);
    std::map<int, std::vector<fnav::Bytes>> got;
    size_t up_bytes = 0;
    for (int n = 0; n < N; ++n) {                         // moving, textured picture: something an encoder has to work on
        av_frame_make_writable(f);
        const bool harsh = getenv("HARSH") != nullptr;  // HARSH: full-frame noise, worse than any camera
        for (int y = 0; y < 720; ++y) for (int x = 0; x < 1280; ++x) {
            int dx = x - (640 + int(200 * std::sin(n / 10.0))), dy = y - 360;
            int face = (dx * dx + dy * dy < 150 * 150) ? 60 : 0;         // a moving round "face" on a gradient room
            f->data[0][y * f->linesize[0] + x] = harsh ? uint8_t((x * 3 + y * 2 + n * 7) ^ ((x / 16 + y / 16 + n) & 1 ? 0x55 : 0))
                                                       : uint8_t(40 + (x + y) / 16 + face + ((x * 7 + y * 13 + n) % 5));
        }
        for (int p = 1; p < 3; ++p) for (int y = 0; y < 360; ++y) for (int x = 0; x < 640; ++x)
            f->data[p][y * f->linesize[p] + x] = uint8_t(p == 1 ? 90 + (x + n) % 60 : 160 - (y + n) % 50);
        for (auto& vp : sender.encode(f, false)) {
            up_bytes += vp.size();
            for (auto& [i, out] : tx.feed(vp, {3, 6})) got[i].insert(got[i].end(), out.begin(), out.end());
        }
    }
    check(tx.decoded == N, "every uploaded frame decoded once: " + std::to_string(tx.decoded));
    check(tx.source_w() == 1280 && tx.source_h() == 720, "source is 1280x720");
    for (int i : {3, 6}) {
        const Size& s = SIZES[size_t(i)];
        auto& v = got[i];
        check(v.size() == size_t(N), std::to_string(s.w) + "x" + std::to_string(s.h) + ": one output per input, " + std::to_string(v.size()));
        check(!v.empty() && fnav::unpack_video(v[0]).key, std::to_string(s.w) + ": the first output is a keyframe");
        const AVCodec* c = avcodec_find_decoder_by_name("vp8");
        AVCodecContext* d = avcodec_alloc_context3(c); avcodec_open2(d, c, nullptr);
        AVFrame* pic = av_frame_alloc(); AVPacket* pkt = av_packet_alloc();
        int frames = 0, w = 0, h = 0; double chroma_dev = 0; size_t bytes = 0;
        for (auto& vp : v) {
            auto vv = fnav::unpack_video(vp);
            bytes += vv.packet.size();
            pkt->data = vv.packet.data(); pkt->size = int(vv.packet.size());
            avcodec_send_packet(d, pkt);
            while (avcodec_receive_frame(d, pic) == 0) {
                ++frames; w = pic->width; h = pic->height;
                for (int y = 0; y < h / 2; y += 7) for (int x = 0; x < w / 2; x += 7)
                    chroma_dev = std::max(chroma_dev, std::fabs(double(pic->data[1][y * pic->linesize[1] + x]) - 128.0));
            }
        }
        double kbps = bytes * 8.0 / (N / double(FPS)) / 1000.0, budget = s.bitrate / 1000.0;
        check(frames == N && w == s.w && h == s.h, "decoded " + std::to_string(frames) + " frames at " + std::to_string(w) + "x" + std::to_string(h));
        check(!s.gray || chroma_dev <= 3.0, std::to_string(s.w) + " is gray: chroma off 128 by at most " + std::to_string(chroma_dev));
        check(kbps <= budget * 1.5, std::to_string(s.w) + "x" + std::to_string(s.h) + " held near its budget: " + std::to_string(int(kbps)) + " kb/s vs " + std::to_string(int(budget)));
        std::cout << s.w << "x" << s.h << (s.gray ? " gray" : "") << ": " << frames << " frames, " << int(kbps) << " kb/s (budget " << int(budget) << ")\n";
        av_frame_free(&pic); av_packet_free(&pkt); avcodec_free_context(&d);
    }
    std::cout << "upload 1280x720: " << int(up_bytes * 8.0 / (N / double(FPS)) / 1000.0) << " kb/s (budget 1200)\n";
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " transcoding: " << fail << " fail\n";
    av_frame_free(&f);
    return fail ? 1 : 0;
}
