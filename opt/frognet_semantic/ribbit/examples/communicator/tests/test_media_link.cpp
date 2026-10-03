// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_media_link -- requirement 3 from the MEASURED link: a 720p upload; one viewer reads freely, one reads only
// ~40 KB/s (~320 kb/s). The server must step the slow viewer down until its link carries what it is sent, and leave
// the fast viewer on the upload.
#include "media/server.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <cmath>
#include <iostream>
#include <sys/socket.h>
#include <thread>

using namespace media;
using namespace fnav;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 12) std::cout << "FAIL " << w << "\n"; }

static std::unique_ptr<Plane> plane(int port, const std::string& name, const std::string& tag, int rcvbuf = 0) {
    int fd = Plane::connect_to("127.0.0.1", port, 2000);
    if (rcvbuf) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);
    auto p = std::make_unique<Plane>(fd);
    Bytes decl{PLANE_VIDEO}; decl.insert(decl.end(), tag.begin(), tag.end()); decl.insert(decl.end(), {'S', '1'});
    p->send(pack_typed(KIND_PLANE, name, decl), SendKind::Control);
    return p;
}

int main() {
    const int PORT = 18900, FPS = 24, SECONDS = 30;
    Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    auto up = plane(PORT, "John", "jjjjjjjj");
    auto fast = plane(PORT, "Donna", "dddddddd");
    auto slow = plane(PORT, "Julie", "uuuuuuuu", 8192);
    std::atomic<bool> done{false};
    std::atomic<long> slow_bytes{0};
    std::thread rf([&] { Bytes b; while (!done) fast->recv(b, 50); });
    std::thread rs([&] {                                   // ~40 KB/s, however much is offered
        Bytes b; auto t0 = std::chrono::steady_clock::now();
        while (!done) {
            if (slow->recv(b, 50) == Recv::Frame) slow_bytes += long(b.size()) + 4;
            double el = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            double ahead = slow_bytes / 40000.0 - el;
            if (ahead > 0) std::this_thread::sleep_for(std::chrono::milliseconds(long(ahead * 1000)));
        }
    });
    Encoder enc(Size{1280, 720, 1200000, false, 7}, FPS);
    AVFrame* f = av_frame_alloc();
    f->format = AV_PIX_FMT_YUV420P; f->width = 1280; f->height = 720; av_frame_get_buffer(f, 0);
    auto t0 = std::chrono::steady_clock::now();
    for (int n = 0; std::chrono::steady_clock::now() - t0 < std::chrono::seconds(SECONDS); ++n) {
        av_frame_make_writable(f);
        for (int y = 0; y < 720; ++y) for (int x = 0; x < 1280; ++x) {
            int dx = x - (640 + int(200 * std::sin(n / 10.0))), dy = y - 360;
            f->data[0][y * f->linesize[0] + x] = uint8_t(40 + (x + y) / 16 + (dx * dx + dy * dy < 22500 ? 60 : 0) + ((x * 7 + y * 13 + n) % 5));
        }
        for (int p = 1; p < 3; ++p) for (int y = 0; y < 360; ++y) std::fill_n(f->data[p] + y * f->linesize[p], 640, uint8_t(128));
        for (auto& vp : enc.encode(f, n % 48 == 0)) up->send_video("John", vp, unpack_video(vp).key);
        std::this_thread::sleep_for(std::chrono::milliseconds(1000 / FPS));
    }
    std::string snap = srv.snapshot();
    done = true; rf.join(); rs.join();
    std::cout << snap;
    auto field = [&](const std::string& who, const std::string& key) {
        auto p = snap.find(who + " V"); if (p == std::string::npos) return std::string("?");
        auto q = snap.find(key + "=", p); auto e = snap.find_first_of(" \n", q);
        return snap.substr(q + key.size() + 1, e - q - key.size() - 1);
    };
    int jt = std::stoi(field("Julie", "target")), dt = std::stoi(field("Donna", "target"));
    double jr = std::stod(field("Julie", "ratio"));
    check(jt >= 3, "the slow viewer stepped down to 640x360 or smaller: target " + std::to_string(jt));
    check(jr >= Server::HAPPY_FRACTION, "and its link now carries what it is sent: delivered " + std::to_string(int(jr * 100)) + "%");
    check(dt == -1, "the fast viewer stays on the upload: target " + std::to_string(dt));
    srv.stop(); th.join(); av_frame_free(&f);
    std::cout << "slow viewer: " << (jt >= 0 && jt < int(SIZES.size()) ? std::to_string(SIZES[size_t(jt)].w) + "x" + std::to_string(SIZES[size_t(jt)].h) : std::string("?"))
              << " at " << int(jr * 100) << "% delivered; fast viewer: upload\n";
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " per-viewer rate from the measured link: " << fail << " fail\n";
    return fail ? 1 : 0;
}
