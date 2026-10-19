// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// region.hpp -- the Redis region, in the memory [ONE_KEYSPACE_V1] (DESIGN.md).
//
// The keyspace is here, once. A front's call is one request: a connection opening, its pipeline, its end. A pipeline
// runs on the memory's pool, in parallel with every other connection's; a blocked command keeps its call's answer and
// is run again when a write moves what it waits on.
#pragma once
#include <cstdint>
#include <string>

namespace rr {
namespace region {

void init();                                    // the region as Redis starts it; before the first call
// One call (op=redis). Its body: "<kind> <args>\n" then its bytes -- h <front> | o <n> <addr> <laddr> | p <n> + RESP |
// g <n> | x + the configuration as RESP. A pipeline's answer comes later (ribbit::answer_later): "<authed><close>\n"
// and the replies. Anything else is answered here: "" (nothing awaited) or an error (thrown).
std::string call(const std::string& body);
void session_down(uint64_t session);            // a front's session ended: its connections leave the region
// a connection killed (CLIENT KILL) while its front has no call here: written for its front to read (the host sets it)
extern void (*write_kill)(const std::string& front, uint64_t n);

}  // namespace region
}  // namespace rr
