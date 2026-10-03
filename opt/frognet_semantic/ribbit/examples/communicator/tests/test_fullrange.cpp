// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_fullrange WHITE.avi BLACK.avi -- [FULL_RANGE_IS_SAID_NOT_ASSUMED_V1]: MJPEG in the full-range yuvj422p format
// (what a USB webcam such as the eMeet C950 delivers) arrives at the encoder as limited-range yuv420p: white at
// luma 235, black at 16.
#include "comms/devices.hpp"
extern "C" {
#include <libavutil/frame.h>
}
#include <iostream>

int main(int argc, char** argv) {
    if (argc < 3) { std::cout << "usage: test_fullrange WHITE.avi BLACK.avi\n"; return 2; }
    int fail = 0;
    const int want[2] = {235, 16};
    for (int k = 0; k < 2; ++k) {
        comms::AvInputSource src("file", argv[1 + k], 320, 240, 24);
        AVFrame* f = av_frame_alloc();
        f->format = AV_PIX_FMT_YUV420P; f->width = 320; f->height = 240; av_frame_get_buffer(f, 0);
        src.next(f);
        int y = f->data[0][120 * f->linesize[0] + 160];
        bool ok = std::abs(y - want[k]) <= 2;
        if (!ok) ++fail;
        std::cout << (ok ? "ok   " : "FAIL ") << (k ? "black" : "white") << ": luma " << y << " (want " << want[k] << ")\n";
        av_frame_free(&f);
    }
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " full-range camera input: " << fail << " fail\n";
    return fail;
}
