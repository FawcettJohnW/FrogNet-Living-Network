extern "C" {
#include <libavutil/log.h>
}
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms-feed -- requirement 1: an unattended feed. No UI at all; started from the command line or a service, it
// publishes until stopped ([HEADLESS_PUBLISHER_V1]):
//   presence  "*<name>" in the lobby ('*' marks an unattended source), mic reported false
//   call      it originates its own call at the media server, so a viewer has something to join
// Both are re-asserted every 5 s. A failure to publish never stops the stream, and is logged every time: a feed that
// is streaming and invisible looks exactly like one that is not running.
//
//   comms-feed --ram HOST:PORT --media HOST:PORT --name NAME [--video FILE | --camera DEV | --synthetic WxH]
//              [--size WxH] [--mic N] [--tone HZ] [--seconds N] [--bandwidth BPS] [--jitter MS]
//   --bandwidth / --jitter: the feed's own link (demo conditions, as the window's sliders): its upload is paced to BPS
//   and every frame it sends waits a random 0..MS.   (--mic -1: the default microphone)
//   --video loops a file; --camera is /dev/videoN (Linux) or "video=<name>" (Windows); --mic uses a real microphone.
#include "comms/call.hpp"
#include "comms/devices.hpp"
#include <iostream>
#include "comms/room.hpp"
#include <csignal>
#include <cstdio>
#include <random>
#include <thread>

static std::atomic<bool> g_stop{false};
static std::pair<std::string, int> hostport(const std::string& a) { auto c = a.rfind(':'); return {a.substr(0, c), std::stoi(a.substr(c + 1))}; }

static int run_main(int argc, char** argv) {
    av_log_set_level(AV_LOG_ERROR);                        // FFmpeg: errors only
    std::string ram = "127.0.0.1:8800", media = "127.0.0.1:8994", name = "Feed", geo = "1280x720", video, camera; bool mic = false; int mic_dev = -1;
    double tone = 0; int seconds = 0; long bandwidth = 0; int jitter = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string k = argv[i], v = argv[i + 1];
        if (k == "--ram") ram = v; else if (k == "--media") media = v; else if (k == "--name") name = v;
        else if (k == "--synthetic") geo = v; else if (k == "--video") video = v; else if (k == "--camera") camera = v; else if (k == "--size") geo = v;
        else if (k == "--mic") { mic = true; mic_dev = std::stoi(v); } else if (k == "--tone") tone = std::stod(v); else if (k == "--seconds") seconds = std::stoi(v); else if (k == "--bandwidth") bandwidth = std::stol(v); else if (k == "--jitter") jitter = std::stoi(v);
    }
    std::signal(SIGINT, [](int) { g_stop = true; });
    std::signal(SIGTERM, [](int) { g_stop = true; });
    auto [rh, rp] = hostport(ram);
    auto [mh, mp] = hostport(media);
    int w = std::stoi(geo.substr(0, geo.find('x'))), h = std::stoi(geo.substr(geo.find('x') + 1));
    std::string session;
    { std::mt19937 r(std::random_device{}()); for (int i = 0; i < 12; ++i) session += "0123456789abcdef"[r() % 16]; }

    std::unique_ptr<comms::Room> room;
    try {
        room = std::make_unique<comms::Room>(rh, rp, "*" + name, "cam=1,mic=0,unattended=1");
        room->offer_call(session, mh, mp);
        room->join_call(session, mh, mp);
        std::printf("[feed] *%s is live: session=%s media=%s\n", name.c_str(), session.c_str(), media.c_str());
    } catch (const std::exception& e) {
        std::printf("[feed] could not announce (%s) -- streaming anyway, but *%s will NOT appear in the lobby\n", e.what(), name.c_str());
    }
    std::fflush(stdout);
    comms::Call call(mh, mp, name, session, !video.empty() ? std::unique_ptr<comms::Source>(new comms::AvInputSource("file", video, w, h, 24))
                     : !camera.empty() ? std::unique_ptr<comms::Source>(new comms::AvInputSource("camera", camera, w, h, 24))
                     : std::unique_ptr<comms::Source>(new comms::SyntheticSource(w, h)), 24,
                     mic ? std::unique_ptr<comms::AudioSource>(new comms::PaMic(mic_dev))
                         : tone > 0 ? std::unique_ptr<comms::AudioSource>(new comms::ToneSource(tone)) : nullptr, nullptr);
    call.start();
    if (bandwidth > 0) { call.set_throttle(bandwidth); std::cerr << "[feed] link: upload paced to " << bandwidth / 1000 << " kb/s\n"; }
    if (jitter > 0) { call.set_jitter(jitter); std::cerr << "[feed] link: up to " << jitter << " ms of jitter per frame\n"; }
    auto t0 = std::chrono::steady_clock::now();
    while (!g_stop && (seconds <= 0 || std::chrono::steady_clock::now() - t0 < std::chrono::seconds(seconds))) {
        std::this_thread::sleep_for(std::chrono::seconds(5));
        std::printf("[feed] L%d %dx%d sent %lu frames (%lu dropped), %lu audio\n", call.stats.rung.load(), call.stats.send_w.load(),
                    call.stats.send_h.load(), (unsigned long)call.stats.v_sent, (unsigned long)call.stats.v_dropped, (unsigned long)call.stats.a_sent);
        std::fflush(stdout);
    }
    call.stop();
    room.reset();                                          // presence goes "offline"
    return 0;
}

// [A_FAILURE_SAYS_WHAT_FAILED_V1] an unreachable memory or media server, a camera that will not open: the cause, once,
// and a clean exit -- never "terminate called ... Aborted".
int main(int argc, char** argv) {
    try { return run_main(argc, argv); }
    catch (const std::exception& e) { std::fprintf(stderr, "comms-feed: %s\n", e.what()); return 1; }
}
