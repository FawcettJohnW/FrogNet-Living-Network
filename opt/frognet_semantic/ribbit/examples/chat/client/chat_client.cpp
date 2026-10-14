// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "chat_client.hpp"
#include <algorithm>
#include <chrono>

namespace chat {

// Seeded from the clock so a sender that restarts moves on through its ring instead of starting over at slot 0.
Client::Client()
    : next_(uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::system_clock::now().time_since_epoch()).count())) {}

void Client::send(const std::string& to, const std::string& from, const std::string& text) {
    const uint64_t slot = next_.fetch_add(1) % RING;
    put(SERVICE, to, from + "#" + std::to_string(slot),
        std::string("{\"from\":") + frogram::Json::quote(from) + ",\"text\":" + frogram::Json::quote(text) + "}", false);
}

uint64_t Client::position(const std::string& me) {
    uint64_t p = 0;
    for (auto& t : get(SERVICE, me)) if (t.id > p) p = t.id;
    return p;
}

std::vector<Message> Client::receive(const std::string& me, uint64_t& after, double wait_s) {
    std::vector<Message> out;
    for (auto& t : get_all(SERVICE, 0, wait_s, 1, me, int64_t(after))) {
        Message m; m.id = t.id; m.from = t.value["from"].s; m.text = t.value["text"].s;
        m.ts = t.value["ts"].type == frogram::Json::Num ? t.value["ts"].n : t.ts_env;
        out.push_back(m);
    }
    std::sort(out.begin(), out.end(), [](const Message& a, const Message& b) { return a.id < b.id; });
    if (!out.empty() && out.back().id > after) after = out.back().id;
    return out;
}

void Client::here(const std::string& me) { put(PRESENCE, "here", me, "{}", true); }

std::vector<std::string> Client::list() {
    std::vector<std::string> who;
    for (auto& t : get(PRESENCE, "here", PRESENT_S)) who.push_back(t.scope);
    std::sort(who.begin(), who.end());
    return who;
}

}  // namespace chat
