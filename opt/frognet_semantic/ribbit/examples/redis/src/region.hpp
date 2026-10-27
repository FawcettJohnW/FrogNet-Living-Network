// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// region.hpp -- the Redis region, in the memory [ONE_KEYSPACE_V1] (DESIGN.md).
//
// The keyspace is here, once. A front writes its truth -- each connection's pipeline -- as cells, unanswered:
// (redis.ask, <front>, <n>), (redis.ask, <front>, g<n>) when the client hangs up, (redis.ask, <front>, x) its
// configuration. The memory's reader (redis_ram.cpp) is woken by those writes and hands each to request(). The region
// writes its truth -- the replies -- as cells, (redis.answer, <front>, <n>); the front holds one standing read on them.
// Nothing is a request answered by a response. [REPLIES_AS_TRUTH_V1]
#pragma once
#include <cstdint>
#include <string>
#include "bagarr.hpp"

namespace rr {
namespace region {

void init();                                    // the region as Redis starts it; before the first request
// one write to (redis.ask, front, instance), as the memory's reader read it; on that one reader thread
void request(const std::string& front, const std::string& instance, const ribbit::BagArr& bag);   // its fields, decoded once [DECODE_ONCE_V1]
// [GONE_WITH_ITS_WRITER_V1] a front's request cell went (its session ended): that connection leaves the region
void request_gone(const std::string& front, const std::string& instance);
// the region writes its replies here (the host sets it: a write to its own memory)
extern void (*write_reply)(const std::string& front, const std::string& instance, ribbit::BagArrP bag);

}  // namespace region
}  // namespace rr
