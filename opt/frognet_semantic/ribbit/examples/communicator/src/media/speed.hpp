// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// The media server's reader of MediaSpeed (each viewer's link, from shared memory). Part of the media server only, so
// the participant programs (comms-app, comms-feed) build without it -- on Windows too.
#pragma once
#include "comms/room.hpp"
#include <atomic>
#include <thread>
#include <vector>

namespace media { class Server; }

namespace comms {

class SpeedPoller {
public:
    SpeedPoller(media::Server& srv, const std::string& ram_host, int ram_port);
    ~SpeedPoller();
private:
    CommsClient c_;
    std::atomic<bool> stop_{false};
    std::thread th_;
};
struct SpeedRow { std::string session, viewer, addr; long bps = 0; };
std::vector<SpeedRow> read_media_speeds(CommsClient& c, int fresh_s = 120);

// [MEDIAHOLD_IS_MEMORY_V1] Every 2 s, per viewer, what the media server is holding for it and why -- written to
// memory, where the viewer's own screen reads it from its copy of the region.
class HoldPublisher {
public:
    HoldPublisher(media::Server& srv, const std::string& ram_host, int ram_port);
    ~HoldPublisher();
private:
    CommsClient c_;
    std::atomic<bool> stop_{false};
    std::thread th_;
};

}  // namespace comms
