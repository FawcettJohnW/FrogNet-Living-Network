// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// apply.hpp -- the Redis region as every box holds it [ONE_ORDER_V1] (docs/CONVERTING-TO-RIBBIT.md §4b).
//
// A box's truth is what its clients asked: each pipeline, each connection opening and ending, each blocking command
// whose time ran out, written once as an intent in the memory. Every box reads every box's intents in write order and
// applies each one to its own copy of the keyspace, with the intent's time as the clock and its write id as the seed:
// every copy decides the same. The box that wrote an intent answers its client from applying it. A blocked command is
// state like any other -- parked in the order, tried again when what it waits on changes -- so every box parks and
// serves it the same way, and the box holding the client answers it.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace rr {
namespace sm {

// one intent, as read from the memory
struct Intent {
    uint64_t id = 0;            // its write id: the one order
    std::string box;            // the box that wrote it
    uint64_t n = 0;             // its place among that box's intents (1, 2, ...)
    int64_t t = 0;              // the writer's clock when it was written, unix ms
    char kind = 0;              // 'o' a connection opens, 'p' its pipeline, 'g' it ends, 'u' its parked command's time
                                // ran out, 'x' the region's configuration (from the box that started the memory);
                                // ('P', a piece of a larger intent of the same connection, is gathered by the box)
    uint64_t conn = 0;          // the writing box's number for the connection
    uint64_t park = 0;          // 'u': which parking of it
    std::string p;              // 'p': the commands' RESP; 'o': the addresses (addr \n laddr); 'x': name, value ... as RESP
};

// what applying says to the box that holds a connection
struct Out {
    enum What { DONE, PARKED, CLOSE, FREED } what = DONE;
    uint64_t conn = 0;          // the box's number for the connection
    std::string bytes;          // replies to write now
    bool authed = false;
    int64_t timeout_ms = 0;     // PARKED: how long it may wait (0: for ever)
    uint64_t park = 0;          // PARKED: which parking (a 'u' intent names it)
};

void init();                    // the region as Redis starts it (before any intent)
// apply one intent; outs for `me`'s connections are appended, in the order their replies are due
void apply(const Intent& in, const std::string& me, std::vector<Out>& outs);
bool is_blocking_class(const std::vector<std::string>& a);
// [JOIN_BY_COPY_V1] the region as this box holds it -- keyspace, connections, MULTI queues and watches, parked
// commands, configuration, ids -- as bytes, and back into a box that holds nothing yet (on its applier)
void dump(std::string& out);
void load(const std::string& in, size_t& at);
size_t connections();           // live connections in the region
size_t parked();                // parked commands in the region

}  // namespace sm
}  // namespace rr
