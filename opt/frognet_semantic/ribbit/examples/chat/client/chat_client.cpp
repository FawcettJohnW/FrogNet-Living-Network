// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
#include "chat_client.hpp"

namespace chat {

void Client::send(const std::string& to, const std::string& from, const std::string& text) {
    put(SERVICE, to, from, std::string("{\"from\":") + frogram::Json::quote(from) + ",\"text\":" + frogram::Json::quote(text) + "}", false);
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
        if (t.id > after) after = t.id;
        out.push_back(m);
    }
    return out;
}

}  // namespace chat
