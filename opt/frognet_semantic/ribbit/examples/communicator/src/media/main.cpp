extern "C" {
#include <libavutil/log.h>
}
// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// comms-media [--listen HOST:PORT] [--ram HOST:PORT] -- the Communicator's media server. With --ram it reads every
// viewer's MediaSpeed from the Communicator's memory every 2 s and applies each to that viewer only.
#include "server.hpp"
#include "comms/channel.hpp"
#include "comms/fatal.hpp"
#include "comms/room.hpp"
#include "media/speed.hpp"
#include <memory>
#include <cstdio>
#include <csignal>
#include <string>

static media::Server* g_server = nullptr;
static int run_main(int argc, char** argv) {
    av_log_set_level(AV_LOG_ERROR);                        // FFmpeg: errors only
    std::string host = "0.0.0.0", ram, channel = comms::CHANNEL_ROLE; int port = 8994;
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--listen") { std::string a = argv[i + 1]; auto c = a.rfind(':'); host = a.substr(0, c); port = std::stoi(a.substr(c + 1)); }
        if (std::string(argv[i]) == "--ram") ram = argv[i + 1];
        if (std::string(argv[i]) == "--channel") channel = argv[i + 1];
    }
    try {
        // [THE_CALL_IS_UNDER_THE_FRAME_V1] the server speaks the frame to every far end that does, for the Role named
        // by --channel (mediahost); its setting is read from the memory before anyone is let in
        const auto rc = ram.rfind(':');
        auto under = comms::channel_for_program(channel, "media", ram.empty() ? "" : ram.substr(0, rc), ram.empty() ? 0 : std::stoi(ram.substr(rc + 1)));
        media::Server s(host, port);
        g_server = &s;
        std::signal(SIGINT, [](int) { if (g_server) g_server->stop(); });
        std::signal(SIGTERM, [](int) { if (g_server) g_server->stop(); });
        std::unique_ptr<comms::SpeedPoller> poll;
        std::unique_ptr<comms::HoldPublisher> holds;
        if (!ram.empty()) {
            auto c = ram.rfind(':');
            poll = std::make_unique<comms::SpeedPoller>(s, ram.substr(0, c), std::stoi(ram.substr(c + 1)));
            holds = std::make_unique<comms::HoldPublisher>(s, ram.substr(0, c), std::stoi(ram.substr(c + 1)));
        }
        s.run();
    } catch (const std::exception& e) { std::fprintf(stderr, "comms-media: %s\n", e.what()); return 1; }
    return 0;
}

// [A_FAILURE_SAYS_WHAT_FAILED_V1] an unreachable memory or media server, a camera that will not open: the cause, once,
// and a clean exit -- never "terminate called ... Aborted".
int main(int argc, char** argv) {
    // said first, so a console or a journal shows which server runs: before 2026-11-24 it did not speak the frame
    std::fprintf(stderr, "[media] build comms-media-2026-11-27-r7 (speaks the protocol layer's frame to every end that does; says why it ended)\n");
    comms::say_why_it_ended("[media] comms-media-2026-11-27-r7");
    try { return run_main(argc, argv); }
    catch (const std::exception& e) { std::fprintf(stderr, "comms-media: %s\n", e.what()); return 1; }
}
