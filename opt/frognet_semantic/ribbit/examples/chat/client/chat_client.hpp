// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// chat_client.hpp -- the chat vendor's client: chat as C++ calls on the default Ribbit API (tuples), to chat-ram.
//
// A conversation is tuples in the chat region: what Dave says to Bob is the tuple (ChatServer, Bob, Dave) -- service,
// var = the recipient, scope = the sender -- holding {from, text, ts}. Each sender has their own tuple in each
// recipient's buffer, so two people writing to Bob at the same moment do not overwrite each other. Receiving is a held
// read on one's own buffer: the host answers when anyone writes into it. There is no chat server.
#pragma once
#include <string>
#include <vector>
#include "tuples.hpp"

namespace chat {

struct Message { std::string from, text; double ts = 0; uint64_t id = 0; };

class Client : public ribbit::TuplesClient {
public:
    static constexpr const char* SERVICE = "ChatServer";
    std::string vendor_id() const override { return "chat"; }

    // Say `text` from `from` to `to`: one put. The line outlives its writer (own=false): it is the recipient's.
    void send(const std::string& to, const std::string& from, const std::string& text);
    // Where `me`'s buffer stands now: messages already there are not new to someone arriving.
    uint64_t position(const std::string& me);
    // Messages written into `me`'s buffer after `after`, waiting up to wait_s for the first; `after` moves past them.
    std::vector<Message> receive(const std::string& me, uint64_t& after, double wait_s);
};

}  // namespace chat
