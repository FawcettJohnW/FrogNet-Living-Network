// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// test_plane FNAV_DIR -- the C++ data plane against fnav.py's own send_frame/recv_frame over real TCP.
//   echo:  frames of every kind and size go C++ -> fnav.py -> C++ and must come back byte for byte.
//   abort: fnav.py stops reading; a large frame commits and cannot finish; when it reads again it must see the abort
//          (by fnav.py's own tail check) and the next frame intact.
#include "fnav/plane.hpp"
#include <cstdio>
#include <iostream>
#include <random>
#include <string>
#include <thread>
#include <vector>

using namespace fnav;
static long fail = 0;
static void check(bool ok, const std::string& what) { if (!ok && ++fail <= 10) std::cout << "FAIL " << what << "\n"; }

static FILE* peer(const std::string& dir, int port, const char* mode) {
    std::string cmd = "python3 tools/fnav_peer.py " + dir + " " + std::to_string(port) + " " + mode;
    FILE* f = popen(cmd.c_str(), "r");
    char line[256];
    if (!f || !fgets(line, sizeof line, f)) { std::cout << "peer did not start\n"; exit(2); }
    return f;
}

int main(int argc, char** argv) {
    if (argc < 2) { std::cout << "usage: test_plane FNAV_DIR\n"; return 2; }
    std::string dir = argv[1];
    std::mt19937 rng(7);
    {   // echo
        FILE* f = peer(dir, 18601, "echo");
        Plane p(Plane::connect_to("127.0.0.1", 18601, 2000));
        std::vector<Bytes> sent;
        size_t sizes[] = {0, 1, 7, 60, 1368, 8192, 9000, 60000};
        for (int i = 0; i < 200; ++i) {
            Bytes pay(sizes[i % 8]); for (auto& b : pay) b = uint8_t(rng());
            Bytes body = (i % 3 == 0) ? pack_typed(KIND_VIDEO, "Donna", pack_video(uint32_t(i % 9), pay, CODEC_VP8, i % 5 == 0))
                                      : pack_typed(uint8_t(i % 7), i % 2 ? "名前" : "John", pay);
            Sent r;
            while ((r = p.send(body, SendKind::Keyframe)) == Sent::Dropped) std::this_thread::sleep_for(std::chrono::milliseconds(2));
            check(r == Sent::Whole, "echo send " + std::to_string(i));
            sent.push_back(body);
            Bytes back; Recv rr = p.recv(back, 5000);
            check(rr == Recv::Frame && back == body, "echo frame " + std::to_string(i) + " came back different");
        }
        p.shutdown_both();
        char line[256]; std::string out; while (fgets(line, sizeof line, f)) out += line; pclose(f);
        check(out.find("ECHOED 200") != std::string::npos, "fnav.py echoed: " + out);
        std::cout << "echo: 200 frames C++ -> fnav.py -> C++, " << fail << " fail\n";
    }
    {   // abort: fill the path, commit a big frame that cannot finish, then a small one
        long before = fail;
        FILE* f = peer(dir, 18602, "stall");
        Plane p(Plane::connect_to("127.0.0.1", 18602, 2000));
        Bytes big(200u << 10, 0x5A);                       // 200 KiB: inside the room estimate, beyond what the stalled path takes
        Sent r1 = p.send(pack_typed(KIND_VIDEO, "Donna", pack_video(7, big, CODEC_VP8, true)), SendKind::Video);
        check(r1 == Sent::Aborted, std::string("big frame: expected Aborted, got ") + (r1 == Sent::Whole ? "Whole" : "Dropped"));
        Bytes small = pack_typed(KIND_AUDIO, "Donna", Bytes(60, 0x11));
        Sent r2;
        for (int i = 0; i < 400 && (r2 = p.send(small, SendKind::Audio)) == Sent::Dropped; ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        check(r2 == Sent::Whole, "the frame after the abort went whole");
        std::this_thread::sleep_for(std::chrono::milliseconds(1500));
        p.shutdown_both();
        char line[256]; std::string out; while (fgets(line, sizeof line, f)) out += line; pclose(f);
        check(out.find("ABORTED") != std::string::npos, "fnav.py saw the abort: " + out);
        check(out.find("OK " + std::to_string(small.size())) != std::string::npos, "fnav.py read the next frame intact: " + out);
        std::cout << "abort: big frame " << (r1 == Sent::Aborted ? "aborted" : "NOT aborted") << ", fnav.py reported: "
                  << (out.find("ABORTED") != std::string::npos ? "ABORTED" : "-") << " then "
                  << (out.find("OK " + std::to_string(small.size())) != std::string::npos ? "OK " + std::to_string(small.size()) : std::string("-")) << ", " << (fail - before) << " fail\n";
    }
    std::cout << (fail ? "RESULT FAIL" : "RESULT PASS") << " data plane vs fnav.py: " << fail << " fail\n";
    return fail ? 1 : 0;
}
