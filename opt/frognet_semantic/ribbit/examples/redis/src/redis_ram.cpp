// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// redis_ram.cpp -- ribbit-redis-host: the memory, holding the Redis region -- the keyspace, once. [ONE_KEYSPACE_V1]
//
// Fronts write their requests as cells, (redis.req, <front>, ...), unanswered; one reader here, woken by those writes
// (the memory's family hook), reads them in write order and hands each to the region (region.hpp). The region writes
// its replies as cells, (redis.rep, <front>, <n>), into this same memory; each front holds one standing read on its
// family. A front's one call (op=redis, "h <front>") ties its session to it, so the session's end takes its
// connections. [REPLIES_AS_TRUTH_V1]
//
//     ribbit-redis-host [--listen HOST:PORT] [--announce-fd N]      answers /redis-api
#include "ram_host.hpp"
#include "ram_memory.hpp"
#include "region.hpp"
#include <chrono>
#include <thread>
#ifndef REDIS_RAM_VERSION
#define REDIS_RAM_VERSION "2.0"
#endif

class RedisRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "redis"; }
    std::string vendor_version() const override { return REDIS_RAM_VERSION; }
    std::string default_listen() const override { return "0.0.0.0:8886"; }
    bool handles(const std::string& op) const override { return op == "redis"; }
    bool inline_operation(const std::string& op) const override { return op == "redis"; }
    ribbit::Reply operation(const std::string&, const std::string& body) override {
        if (body.compare(0, 2, "h ") != 0 || body.size() < 3) return ribbit::error_reply(400, "redis region: the one call is \"h <front>\"");
        rr::region::bind(body.substr(2));
        return ribbit::Reply{200, "{\"ok\":true}"};
    }
    void session_down(uint64_t id) override { rr::region::session_down(id); }
};

static double wall_s() { return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static void write_reply(const std::string& front, const std::string& instance, ribbit::BagArrP bag) {
    ribbit::RamHost::memory().write("redis.rep", front, instance, std::move(bag), wall_s());
}
// the one reader of every front's requests: woken by their writes, reads what is new, in each front's write order
static void requests() {
    ramm::Memory& M = ribbit::RamHost::memory();
    ramm::WakeWord* w = M.wake_word();
    ramm::Memory::Cursor c = M.cursor("redis.req", "", "", -1);
    ramm::Memory::hook(c, w);
    std::vector<ramm::Cell> rows;
    for (;;) {
        const uint32_t seen = w->n.load(std::memory_order_acquire);
        rows.clear(); M.read(c, rows);
        for (auto& r : rows) {                       // a front writes fields: decoded once, by the session [DECODE_ONCE_V1]
            ribbit::BagArrP a = r.arr ? r.arr : ribbit::BagArr::parse(r.bag);
            if (!a) { fprintf(stderr, "redis region: a request %s/%s that is not a bag\n", r.variable.c_str(), r.instance.c_str()); continue; }
            rr::region::request(r.variable, r.instance, *a);
        }
        if (rows.empty()) w->n.wait(seen, std::memory_order_acquire);
    }
}

int main(int argc, char** argv) {
    rr::region::init();
    rr::region::write_reply = write_reply;
    std::thread(requests).detach();
    return RedisRam().run(argc, argv);
}
