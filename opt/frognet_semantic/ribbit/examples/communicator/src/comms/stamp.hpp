// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// stamp.hpp -- [STAMP_THE_PICTURE_V1] burns "#<frame> HH:MM:SS.mmm" (the sender's frame count and its wall clock at
// capture) into the bottom-left of a yuv420p picture, before it is encoded. It is part of the picture, so it survives
// every hop (the media server's transcode included) and any viewer can read the lag off the screen: its own clock
// (shown in the viewer's overlay) minus the stamped one. COMMS_STAMP=0 turns it off.
#pragma once
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
extern "C" {
#include <libavutil/frame.h>
}

namespace comms {

// the participant programs' build, printed at start and shown in the window: what is running, never a guess
constexpr const char* COMMS_APP_BUILD = "comms-app-2026-10-10-r3";   // r3: rings only while offered; leave withdraws; stamped picture

inline bool stamp_enabled() { const char* e = getenv("COMMS_STAMP"); return !(e && e[0] == '0'); }

inline std::string wall_clock_ms() {                       // local HH:MM:SS.mmm
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    int ms = int(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() % 1000);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char b[32]; snprintf(b, sizeof b, "%02d:%02d:%02d.%03d", tm.tm_hour, tm.tm_min, tm.tm_sec, ms);
    return b;
}

inline void stamp_picture(AVFrame* f, uint64_t frame_no) {
    static const uint8_t G[14][7] = {                       // 5x7, rows top to bottom, bit 4 = leftmost
        {14,17,19,21,25,17,14}, {4,12,4,4,4,4,14}, {14,17,1,2,4,8,31}, {31,2,4,2,1,17,14}, {2,6,10,18,31,2,2},
        {31,16,30,1,1,17,14}, {6,8,16,30,17,17,14}, {31,1,2,4,8,8,8}, {14,17,17,14,17,17,14}, {14,17,17,15,1,2,12},
        {0,12,12,0,12,12,0}, {0,0,0,0,0,12,12}, {10,10,31,10,31,10,10}, {0,0,0,0,0,0,0}};   // 0-9 : . # space
    char txt[48]; snprintf(txt, sizeof txt, "#%llu %s", (unsigned long long)frame_no, wall_clock_ms().c_str());
    const int s = f->height >= 120 ? f->height / 120 : 1;   // 6 px per dot at 720p
    const int cw = 6 * s, chh = 9 * s, n = int(strlen(txt));
    const int bw = (n * cw + 2 * s) < f->width ? (n * cw + 2 * s) : f->width, bh = (chh + s) < f->height ? (chh + s) : f->height;   // no std::min: windows.h
    const int top = (f->height - bh) & ~1;                  // bottom-left: the viewer's stats overlay is top-left
    for (int y = top; y < top + bh; ++y) memset(f->data[0] + y * f->linesize[0], 16, size_t(bw));          // black box
    for (int y = top / 2; y < (top + bh) / 2; ++y) { memset(f->data[1] + y * f->linesize[1], 128, size_t(bw / 2)); memset(f->data[2] + y * f->linesize[2], 128, size_t(bw / 2)); }
    for (int i = 0; i < n; ++i) {
        char c = txt[i]; int g = c >= '0' && c <= '9' ? c - '0' : c == ':' ? 10 : c == '.' ? 11 : c == '#' ? 12 : 13;
        int x0 = s + i * cw, y0 = top + s;
        for (int r = 0; r < 7; ++r) for (int b = 0; b < 5; ++b) if (G[g][r] & (16 >> b))
            for (int dy = 0; dy < s; ++dy) {
                int y = y0 + r * s + dy, x = x0 + b * s;
                if (y < f->height && x + s <= f->width) memset(f->data[0] + y * f->linesize[0] + x, 235, size_t(s));
            }
    }
}

}  // namespace comms
