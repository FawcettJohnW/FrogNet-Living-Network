// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "speed.hpp"
#include "server.hpp"
#include <chrono>
#include <cstdio>

namespace comms {

using frogram::Json;
static std::string field(const Json& v, const char* k) {
    if (v.type != Json::Obj) return "";
    for (auto& kv : v.o) if (kv.first == k) return kv.second.type == Json::Str ? kv.second.s : ribbit::RamClient::to_json(kv.second);
    return "";
}

std::vector<SpeedRow> read_media_speeds(CommsClient& c, int fresh_s) {
    std::vector<SpeedRow> out;
    for (auto& t : c.get(SERVICE, "MediaSpeed", fresh_s)) {
        std::string b = field(t.value, "bps");
        out.push_back(SpeedRow{field(t.value, "session"), field(t.value, "viewer"), field(t.value, "addr"), b.empty() ? 0 : std::stol(b)});
    }
    return out;
}

// [HOLD_THE_READ_V1] The media server holds a read on MediaSpeed: a viewer's cap applies the moment it is written,
// not at the next poll. Quiet for HOLD_S: one full read, so an aged-out cap is lifted.
SpeedPoller::SpeedPoller(media::Server& srv, const std::string& host, int port) {
    th_ = std::thread([this, &srv, host, port] {
        CommsClient& c = c_;
        bool up = false;
        int64_t after = -1;
        while (!stop_) {
            try {
                if (!up) { c.connect(host, port); up = true; after = -1; }
                auto ts = after < 0 ? c.get(SERVICE, "MediaSpeed", 120)
                                    : c.get_all(SERVICE, 120, 15.0, 1, "MediaSpeed", after);
                if (after >= 0 && ts.empty()) { after = -1; continue; }      // quiet: resync next pass
                for (auto& t : ts) {
                    if (int64_t(t.id) > after) after = int64_t(t.id);
                    std::string b = field(t.value, "bps");
                    srv.set_cap_row(field(t.value, "session"), field(t.value, "viewer"), field(t.value, "addr"), b.empty() ? 0 : std::stol(b));
                }
                if (after < 0) after = 0;
            } catch (const std::exception& e) {
                if (stop_) break;
                std::fprintf(stderr, "[media] MediaSpeed read failed: %s (caps unchanged)\n", e.what());   // a failed read is not a lifted cap
                up = false;
                for (int i = 0; i < 10 && !stop_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
        }
        try { c.close(); } catch (const std::exception&) {}
    });
}
SpeedPoller::~SpeedPoller() {
    stop_ = true;
    try { c_.session().shutdown(); } catch (const std::exception&) {}   // [A_HELD_READ_ENDS_AT_ONCE_V1]
    if (th_.joinable()) th_.join();
}

HoldPublisher::HoldPublisher(media::Server& srv, const std::string& host, int port) {
    th_ = std::thread([this, &srv, host, port] {
        bool up = false;
        while (!stop_) {
            try {
                if (!up) { c_.connect(host, port); up = true; }
                for (auto& h : srv.holds()) {
                    std::string v = "{\"session\":\"" + h.session + "\",\"viewer\":\"" + h.viewer + "\",\"unanchored\":" +
                        (h.unanchored ? "true" : "false") + ",\"key_held\":" + (h.key_held ? "true" : "false") +
                        ",\"aimed\":" + std::to_string(h.aimed) + ",\"delivered\":" + std::to_string(h.delivered) +
                        ",\"inters_shed\":" + std::to_string(h.inters_shed) + ",\"keys_held\":" + std::to_string(h.keys_held) +
                        ",\"passthrough\":" + (h.passthrough ? "true" : "false") + ",\"audio_only\":" + (h.audio_only ? "true" : "false") +
                        ",\"w\":" + std::to_string(h.stream_w) + ",\"h\":" + std::to_string(h.stream_h) + "}";
                    c_.put(SERVICE, "MediaHold", "session:" + h.session + ":viewer:" + h.viewer, v, false);
                }
            } catch (const std::exception& e) {
                if (stop_) break;
                std::fprintf(stderr, "[media] MediaHold write failed: %s (retrying)\n", e.what());
                up = false;
            }
            for (int i = 0; i < 20 && !stop_; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        try { c_.close(); } catch (const std::exception&) {}
    });
}

HoldPublisher::~HoldPublisher() {
    stop_ = true;
    if (th_.joinable()) th_.join();
}

}  // namespace comms
