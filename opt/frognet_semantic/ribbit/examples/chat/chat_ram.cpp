// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// chat_ram.cpp -- chat-ram: the chat region's shared memory, one executable.
//
// Chat needs nothing from its host but the memory: every conversation is cells -- ChatServer.<recipient>.<sender>,
// one per sender in each recipient's buffer -- written by one participant and read, held open, by another. There is
// no chat server logic anywhere, so this host has no operations of its own.
//
//     chat-ram [--listen HOST:PORT]        answers /chat-api
#include "ram_host.hpp"
#define CHAT_VERSION "chat 1.0"

class ChatRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "chat"; }
    std::string vendor_version() const override { return CHAT_VERSION; }
};

int main(int argc, char** argv) { return ChatRam().run(argc, argv); }
