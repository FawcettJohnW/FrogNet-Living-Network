/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// [READS_AT_THE_FRONT_V1] John 2026-10-14: "You have lots of ways to make this thing faster that are not available to
// the message passing model. I expect you to take full advantage of them."
//
// The memory is shared: a front keeps the replies it has read and answers the same read again itself, with no trip to
// the RAM host. What keeps those copies true is here, in the host:
//   holders   which fronts have read a key (a bit per front). A read registers BEFORE it reads; nothing is ever
//             unregistered (a front that forgot a key gets an invalidation it ignores).
//   seq       one counter, bumped by every change a holder must hear about.
//   feeds     one per front: the invalidations for keys it holds, in seq order. The front reads its feed as a held read.
// A change is announced by the memory itself (Region::on_change) after it is made, from the thread that made it, so no
// command and no path can change a key without its holders hearing.
//
// The ordering argument. A reader: register (seq_cst RMW), fence, r = seq. A writer: change, fence, read the holders.
// Either the writer sees the registration (and sends an invalidation numbered above r, which the front applies over its
// copy), or the registration came after the writer's fence and the read sees the change. The front drops a copy read
// at r if it has applied an invalidation of that key numbered above r -- in whichever order the two arrive.
//
// The writer's own front gets the invalidations for its batch IN the batch's answer, applied before any reply of it is
// written: a client always reads its own writes, and anything written before it was told OK.
#pragma once
#include <string>
#include <vector>
#include <cstdint>
namespace rr { class Region; }
namespace rr::coh {
constexpr int FEEDS = 64;                     // fronts reading at once (a bit each in a holder's mask)
struct Inval { uint64_t seq = 0; int db = 0; bool flush = false; std::string key; };
void install();                               // the host: follow every change in the memory
// A cacheable read by `front`, of `key` in `db`: register first, then read. Returns the seq the read is numbered with.
uint64_t register_read(int front, int db, const std::string& key);
// The batch item being run on this thread is `front`'s: invalidations for that front made while it runs go to `mine`.
void begin_item(int front, std::vector<Inval>* mine);
void end_item();
int feed_open();                              // a feed for a new front; -1 if every one is in use by a live front
// Take what is waiting on feed f. When there is nothing and wait_ms > 0, waits up to wait_ms for something.
void feed_take(int f, std::vector<Inval>& out, int wait_ms);
bool cacheable(const std::string& lname, size_t argc);   // a read whose reply depends on its one key's value alone
}  // namespace rr::coh
