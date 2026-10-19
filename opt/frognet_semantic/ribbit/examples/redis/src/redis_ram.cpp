// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// redis_ram.cpp -- ribbit-redis-host: the memory Redis's boxes share. [ONE_ORDER_V1]
//
// It has no operations of its own. Every box writes its truth (its clients' intents: service redis, cmd and applied --
// front.cpp) and reads every box's from there with held reads; the memory, the wire and the triggers are the
// platform's. The keyspace is not here: every box holds it, derived from the intents in write order.
//
//     ribbit-redis-host [--listen HOST:PORT] [--announce-fd N]      answers /redis-api
#include "ram_host.hpp"
#ifndef REDIS_RAM_VERSION
#define REDIS_RAM_VERSION "1.0"
#endif

class RedisRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "redis"; }
    std::string vendor_version() const override { return REDIS_RAM_VERSION; }
    std::string default_listen() const override { return "0.0.0.0:8886"; }
};

int main(int argc, char** argv) { return RedisRam().run(argc, argv); }
