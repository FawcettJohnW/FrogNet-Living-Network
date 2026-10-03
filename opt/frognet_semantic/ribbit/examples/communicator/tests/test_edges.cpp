// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_edges -- every send size, encoded from a source picture and decoded: the rightmost and bottom pixels must
// continue the picture. A size whose width is not a multiple of the codec's block size is where stale buffer memory
// shows (found 2026-10-01: a strip of another stream's colours down the right edge of an 854x480 picture).
#include "comms/call.hpp"
#include "media/transcode.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavutil/frame.h>
}
#include <cstdlib>
#include <iostream>

int main() {
    long fail = 0;
    comms::SyntheticSource src(1280, 720);
    AVFrame* pic = av_frame_alloc();
    pic->format = AV_PIX_FMT_YUV420P; pic->width = 1280; pic->height = 720;
    av_frame_get_buffer(pic, 0);
    for (size_t i = 0; i < media::SIZES.size(); ++i) {
        const media::Size& s = media::SIZES[i];
        media::Encoder enc(s, 24);
        const AVCodec* c = avcodec_find_decoder_by_name("vp8");
        AVCodecContext* d = avcodec_alloc_context3(c); avcodec_open2(d, c, nullptr);
        AVPacket* pkt = av_packet_alloc(); AVFrame* f = av_frame_alloc();
        comms::Picture p;
        for (int n = 0; n < 6; ++n) {
            // poison the decoder's frame pool with a value the picture never has, so unwritten columns show
            src.next(pic);
            for (auto& vp : enc.encode(pic, n == 0)) {
                fnav::Video v = fnav::unpack_video(vp);
                pkt->data = v.packet.data(); pkt->size = int(v.packet.size());
                avcodec_send_packet(d, pkt);
                while (avcodec_receive_frame(d, f) == 0) comms::copy_picture(f, p);
            }
        }
        // the last column against its neighbour, every row (luma); a gradient source changes slowly
        long bad = 0;
        for (int y = 0; y < p.h; ++y) {
            int a = p.y[size_t(y) * size_t(p.w) + size_t(p.w - 1)], b = p.y[size_t(y) * size_t(p.w) + size_t(p.w - 6)];
            if (std::abs(a - b) > 40) ++bad;
        }
        bool ok = p.w == s.w && p.h == s.h && bad < p.h / 20;
        if (!ok) ++fail;
        std::cout << (ok ? "ok   " : "FAIL ") << s.w << "x" << s.h << ": decoded " << p.w << "x" << p.h
                  << ", right edge discontinuous on " << bad << " of " << p.h << " rows\n";
        av_frame_free(&f); av_packet_free(&pkt); avcodec_free_context(&d);
    }
    av_frame_free(&pic);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " picture edges at every size: " << fail << " fail\n";
    return fail ? 1 : 0;
}
