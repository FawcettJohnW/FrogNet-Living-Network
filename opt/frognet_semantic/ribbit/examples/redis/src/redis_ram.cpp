// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// redis_ram.cpp -- ribbit-redis-host: the memory, holding the Redis region -- the keyspace, once. [ONE_KEYSPACE_V1]
//
// Its one operation is a front's call (op=redis: region.hpp). The calls of one session arrive in wire order and are
// read inline; a pipeline runs on the pool and is answered when it is done. A session that ends takes its front's
// connections with it. A connection killed while its front has no call in the region is written here as a cell,
// (redis.kill, <front>, <n>): the front holds a read on its own family and closes the socket.
//
//     ribbit-redis-host [--listen HOST:PORT] [--announce-fd N]      answers /redis-api
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include "region.hpp"
#include <chrono>
#ifndef REDIS_RAM_VERSION
#define REDIS_RAM_VERSION "2.0"
#endif

class RedisRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "redis"; }
    std::string vendor_version() const override { return REDIS_RAM_VERSION; }
    std::string default_listen() const override { return "0.0.0.0:8886"; }
    bool handles(const std::string& op) const override { return op == "redis"; }
    bool inline_operation(const std::string& op) const override { return op == "redis"; }   // never waits: read in wire order
    ribbit::Reply operation(const std::string&, const std::string& body) override {
        try { return ribbit::Reply{200, rr::region::call(body)}; }
        catch (const std::exception& e) { return ribbit::error_reply(400, e.what()); }
    }
    void session_down(uint64_t id) override { rr::region::session_down(id); }
};

static void write_kill(const std::string& front, uint64_t n) {
    const double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    ribbit::RamHost::memory().write("redis.kill", front, std::to_string(n), "{\"k\":1}", now);
}

int main(int argc, char** argv) {
    rr::region::init();
    rr::region::write_kill = write_kill;
    return RedisRam().run(argc, argv);
}
