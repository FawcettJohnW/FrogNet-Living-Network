// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// chat_client.hpp -- the chat vendor's client: chat as C++ calls on the default Ribbit API (tuples), to chat-ram.
//
// A conversation is tuples in the chat region. What Dave says to Bob goes into Bob's buffer, (ChatServer, Bob, *), in
// a ring of RING cells that belongs to Dave: scope "Dave#<slot>", holding {from, text, ts}. Each line Dave writes takes
// the next slot, so lines Dave writes faster than Bob reads are all still there when Bob reads, in write order, and the
// ring keeps Dave's share of Bob's buffer bounded. Each sender has their own ring in each recipient's buffer, so two
// people writing to Bob at the same moment do not overwrite each other. Receiving is a held read on one's own buffer:
// the host answers when anyone writes into it. Presence is one cell per person, (ChatPresence, here, Dave), refreshed
// by the person's own client and believed only while fresh. There is no chat server.
#pragma once
#include <atomic>
#include <string>
#include <vector>
#include "tuples.hpp"

namespace chat {

struct Message { std::string from, text; double ts = 0; uint64_t id = 0; };

class Client : public ribbit::TuplesClient {
public:
    static constexpr const char* SERVICE = "ChatServer";
    static constexpr const char* PRESENCE = "ChatPresence";
    static constexpr unsigned RING = 64;        // slots per sender in each recipient's buffer
    static constexpr int PRESENT_S = 60;        // a presence cell older than this is not believed

    Client();
    std::string vendor_id() const override { return "chat"; }

    // Say `text` from `from` to `to`: one put, into the next slot of `from`'s ring in `to`'s buffer. The line outlives
    // its writer (own=false): it is the recipient's.
    void send(const std::string& to, const std::string& from, const std::string& text);
    // Where `me`'s buffer stands now: messages already there are not new to someone arriving.
    uint64_t position(const std::string& me);
    // Messages written into `me`'s buffer after `after`, in write order, waiting up to wait_s for the first; `after`
    // moves past them.
    std::vector<Message> receive(const std::string& me, uint64_t& after, double wait_s);
    // `me` is here: writes (refreshes) me's presence cell. Removed when this client closes.
    void here(const std::string& me);
    // Who is here: everyone whose presence cell was written within PRESENT_S seconds, sorted.
    std::vector<std::string> list();

private:
    std::atomic<uint64_t> next_;                 // this client's next ring position; seeded from the clock
};

}  // namespace chat
