// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// redis_ram.cpp -- ribbit-redis-host: the memory, holding the Redis region -- the keyspace, once. [ONE_KEYSPACE_V1]
//
// Fronts ask (API++ Asker): each writes its requests as cells of its own, (redis.ask, <front>, ...), unanswered. The
// region answers (API++ Answerer): one read held on every front's requests, here, in each front's write order, each
// handed to the region (region.hpp); the region's replies are cells it writes, (redis.answer, <front>, <n>), which each
// front reads by its one standing read. Nothing is called. When a front's session ends its request cells go with it
// ([GONE_WITH_ITS_WRITER_V1]): the region is handed each as gone, and that connection leaves. [REPLIES_AS_TRUTH_V1]
//
//     ribbit-redis-host [--listen HOST:PORT] [--announce-fd N]      answers /redis-api
#include "ram_host.hpp"
#include "ribbit_space.hpp"
#include "ribbit_pp.hpp"
#include "region.hpp"
#include <memory>
#ifndef REDIS_RAM_VERSION
#define REDIS_RAM_VERSION "2.0"
#endif

class RedisRam : public ribbit::RamHost {
public:
    std::string vendor_id() const override { return "redis"; }
    std::string vendor_version() const override { return REDIS_RAM_VERSION; }
    std::string default_listen() const override { return "0.0.0.0:8886"; }
};

static ribbit::Space* HERE = nullptr;              // the memory this process holds
static ribbit::pp::Answerer* ANS = nullptr;
// a reply: the region's fields, as its own cell for that front
static void write_reply(const std::string& front, const std::string& instance, ribbit::BagArrP bag) {
    semcodec::Fields f; f.reserve(bag->vals.size());
    const std::vector<std::string>& names = *bag->names;  // the region's shape names: kept for its life (ChunkNames)
    for (size_t k = 0; k < bag->vals.size(); ++k) f.emplace_back(names[k], std::move(bag->vals[k]));
    ANS->answer(front, instance, names, std::move(f));
}

int main(int argc, char** argv) {
    rr::region::init();
    HERE = new ribbit::Space("here", "redis");
    rr::region::write_reply = write_reply;
    // every front's every request, from the first: a front may ask before this read is held
    ANS = new ribbit::pp::Answerer(*HERE, "redis", [](const std::string& front, const ribbit::SpaceRow& r) {
        if (r.gone) { rr::region::request_gone(front, r.instance); return; }
        ribbit::BagArrP a = r.fields();                // a front writes fields: decoded once, by the session [DECODE_ONCE_V1]
        if (!a) { fprintf(stderr, "redis region: a request %s/%s that is not a bag\n", front.c_str(), r.instance.c_str()); return; }
        rr::region::request(front, r.instance, *a);
    });
    return RedisRam().run(argc, argv);
}
