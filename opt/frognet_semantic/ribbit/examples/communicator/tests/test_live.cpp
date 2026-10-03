// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_live VIDEO -- [A_SWALLOWED_SETTING_IS_A_MYSTERY_LATER_V1]: a live input hands the sender its NEWEST frame. A file
// read as "live-file" decodes faster than real time -- a camera outrunning its sender -- so after a pause the next
// frame must be from far into the video, not the next in line. Found 2026-10-01: in-order reading put the eMeet's
// video ~2 s behind its audio.
#include "comms/devices.hpp"
extern "C" {
#include <libavutil/frame.h>
}
#include <chrono>
#include <iostream>
#include <thread>

int main(int argc, char** argv) {
    if (argc < 2) { std::cout << "usage: test_live VIDEO\n"; return 2; }
    int fail = 0;
    AVFrame* f = av_frame_alloc();
    f->format = AV_PIX_FMT_YUV420P; f->width = 640; f->height = 360; av_frame_get_buffer(f, 0);
    {   // in order (a file): the second frame follows the first, whatever the pause
        comms::AvInputSource file("file", argv[1], 640, 360, 24);
        file.next(f); std::this_thread::sleep_for(std::chrono::milliseconds(800)); file.next(f);
        std::cout << "file, after an 800 ms pause: frame at " << file.last_pts_ms() << " ms (the next in line)\n";
        if (file.last_pts_ms() > 200) { ++fail; std::cout << "FAIL a file must play in order\n"; }
    }
    {   // live: after the same pause, the newest frame, far ahead; the frames in between were superseded
        comms::AvInputSource live("live-file", argv[1], 640, 360, 24);
        live.next(f); int64_t first = live.last_pts_ms();
        std::this_thread::sleep_for(std::chrono::milliseconds(800));
        live.next(f); int64_t second = live.last_pts_ms();
        std::cout << "live, after an 800 ms pause: frame at " << second << " ms (first was " << first << "), "
                  << live.superseded() << " frames superseded\n";
        if (second - first < 1000 || live.superseded() == 0) { ++fail; std::cout << "FAIL a live input must hand over its newest frame\n"; }
    }
    av_frame_free(&f);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " newest frame from a live input: " << fail << " fail\n";
    return fail;
}
