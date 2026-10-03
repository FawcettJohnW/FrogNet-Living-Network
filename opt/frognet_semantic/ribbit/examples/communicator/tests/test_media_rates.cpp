// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_media_rates -- decision 2 end to end: one 1280x720 VP8 upload, three viewers with different download caps.
//   Donna  no cap      -> the upload itself, byte for byte
//   Julie  120 kb/s    -> 160x120, starting on a keyframe; then 1 Mb/s -> 854x480 (keyframe first);
//                         then 5 Mb/s -> above the upload, so the upload itself (never larger than the upload)
//   Daniel 20 kb/s     -> no video (audio only)
#include "media/server.hpp"
extern "C" {
#include <libavcodec/avcodec.h>
}
#include <algorithm>
#include <cmath>
#include <iostream>
#include <thread>

using namespace media;
using namespace fnav;
static long fail = 0;
static void check(bool ok, const std::string& w) { if (!ok && ++fail <= 12) std::cout << "FAIL " << w << "\n"; }

static std::unique_ptr<Plane> plane(int port, const std::string& name, uint8_t kind, const std::string& tag) {
    auto p = std::make_unique<Plane>(Plane::connect_to("127.0.0.1", port, 2000));
    Bytes decl{kind}; decl.insert(decl.end(), tag.begin(), tag.end()); decl.insert(decl.end(), {'S', '1'});
    p->send(pack_typed(KIND_PLANE, name, decl), SendKind::Control);
    return p;
}

struct Got { std::vector<Video> v; };
static Got drain(Plane& p, int ms) {                       // every VIDEO frame (reassembled) within ms
    Got g; Reassembler r; Bytes body, vp;
    auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
    while (std::chrono::steady_clock::now() < end) {
        if (p.recv(body, 20) != Recv::Frame) continue;
        Typed t = unpack_typed(body);
        if (t.kind == KIND_VIDEO) g.v.push_back(unpack_video(t.payload));
        else if (t.kind == KIND_VSEG && r.feed(t.src, t.payload, vp)) g.v.push_back(unpack_video(vp));
    }
    return g;
}

static void dims(const std::vector<Video>& v, int& w, int& h, int& n) {   // decode and report the picture size
    const AVCodec* c = avcodec_find_decoder_by_name("vp8");
    AVCodecContext* d = avcodec_alloc_context3(c); avcodec_open2(d, c, nullptr);
    AVFrame* f = av_frame_alloc(); AVPacket* pkt = av_packet_alloc(); w = h = n = 0;
    for (auto& x : v) {
        pkt->data = const_cast<uint8_t*>(x.packet.data()); pkt->size = int(x.packet.size());
        avcodec_send_packet(d, pkt);
        while (avcodec_receive_frame(d, f) == 0) { ++n; w = f->width; h = f->height; }
    }
    av_frame_free(&f); av_packet_free(&pkt); avcodec_free_context(&d);
}

int main() {
    const int PORT = 18800, FPS = 24;
    Server srv("127.0.0.1", PORT);
    std::thread th([&] { srv.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    auto up = plane(PORT, "John", PLANE_VIDEO, "jjjjjjjj");
    auto donna = plane(PORT, "Donna", PLANE_VIDEO, "dddddddd");
    auto julie = plane(PORT, "Julie", PLANE_VIDEO, "uuuuuuuu");
    auto daniel = plane(PORT, "Daniel", PLANE_VIDEO, "nnnnnnnn");
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    srv.set_cap("Julie", 120000);
    srv.set_cap("Daniel", 20000);

    Encoder enc(Size{1280, 720, 1200000, false, 7}, FPS);
    AVFrame* f = av_frame_alloc();
    f->format = AV_PIX_FMT_YUV420P; f->width = 1280; f->height = 720; av_frame_get_buffer(f, 0);
    std::vector<Bytes> uploaded;
    int n = 0;
    auto send_frames = [&](int count) {
        for (int k = 0; k < count; ++k, ++n) {
            av_frame_make_writable(f);
            for (int y = 0; y < 720; ++y) for (int x = 0; x < 1280; ++x) {
                int dx = x - (640 + int(200 * std::sin(n / 10.0))), dy = y - 360;
                f->data[0][y * f->linesize[0] + x] = uint8_t(40 + (x + y) / 16 + (dx * dx + dy * dy < 22500 ? 60 : 0));
            }
            for (int p = 1; p < 3; ++p) for (int y = 0; y < 360; ++y) std::fill_n(f->data[p] + y * f->linesize[p], 640, uint8_t(128));
            for (auto& vp : enc.encode(f, false)) {
                uploaded.push_back(vp);
                while (!up->send_video("John", vp, unpack_video(vp).key)) std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1000 / FPS));
        }
    };
    std::thread rx_d, rx_j, rx_n; Got gd, gj, gn;
    rx_d = std::thread([&] { gd = drain(*donna, 4000); });
    rx_j = std::thread([&] { gj = drain(*julie, 4000); });
    rx_n = std::thread([&] { gn = drain(*daniel, 4000); });
    send_frames(48);                                       // 2 s at 24 fps
    rx_d.join(); rx_j.join(); rx_n.join();

    // Donna: the upload itself
    size_t same = 0;
    for (size_t i = 0; i < gd.v.size() && i < uploaded.size(); ++i)
        if (pack_video(gd.v[i].level, gd.v[i].packet, gd.v[i].codec, gd.v[i].key) == uploaded[i]) ++same;
    check(gd.v.size() == uploaded.size() && same == uploaded.size(), "Donna receives the upload byte for byte: " + std::to_string(same) + "/" + std::to_string(uploaded.size()));
    // Julie at 120 kb/s: 160x120, keyframe first
    int w, h, dn; dims(gj.v, w, h, dn);
    check(!gj.v.empty() && gj.v[0].key, "Julie's first frame is a keyframe");
    check(w == 160 && h == 120 && dn >= 40, "Julie at 120 kb/s gets 160x120: " + std::to_string(w) + "x" + std::to_string(h) + ", " + std::to_string(dn) + " frames");
    // Daniel at 20 kb/s: no video
    check(gn.v.empty(), "Daniel at 20 kb/s gets no video: " + std::to_string(gn.v.size()) + " frames");
    std::cout << "Donna " << same << "/" << uploaded.size() << " upload frames byte for byte; Julie " << w << "x" << h << " x" << dn
              << "; Daniel " << gn.v.size() << " video frames\n";

    // Julie's cap rises to 1 Mb/s: 854x480, starting on a keyframe
    srv.set_cap("Julie", 1000000);
    rx_j = std::thread([&] { gj = drain(*julie, 3000); });
    send_frames(36);
    rx_j.join();
    dims(gj.v, w, h, dn);
    check(!gj.v.empty() && gj.v[0].key, "after the change Julie's first frame is a keyframe");
    check(w == 854 && h == 480, "Julie at 1 Mb/s gets 854x480: " + std::to_string(w) + "x" + std::to_string(h));
    std::cout << "Julie at 1 Mb/s: " << w << "x" << h << " x" << dn << ", first frame " << (gj.v.empty() ? "-" : gj.v[0].key ? "key" : "inter") << "\n";

    // 5 Mb/s: above the upload -> the upload itself, never larger
    srv.set_cap("Julie", 5000000);
    size_t mark = uploaded.size();
    rx_j = std::thread([&] { gj = drain(*julie, 3000); });
    send_frames(60);
    rx_j.join();
    dims(gj.v, w, h, dn);
    check(w == 1280 && h == 720, "Julie at 5 Mb/s gets the 1280x720 upload, never larger: " + std::to_string(w) + "x" + std::to_string(h));
    bool all_upload = !gj.v.empty();
    for (auto& x : gj.v) {
        Bytes b = pack_video(x.level, x.packet, x.codec, x.key);
        if (std::find(uploaded.begin() + long(mark), uploaded.end(), b) == uploaded.end()) all_upload = false;
    }
    check(all_upload, "every frame Julie gets at 5 Mb/s is an upload frame, passed through");
    std::cout << "Julie at 5 Mb/s: " << w << "x" << h << " x" << dn << ", passed through: " << (all_upload ? "yes" : "no") << "\n";
    std::cout << srv.snapshot();
    srv.stop(); th.join();
    av_frame_free(&f);
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " per-viewer rates (decision 2): " << fail << " fail\n";
    return fail ? 1 : 0;
}
