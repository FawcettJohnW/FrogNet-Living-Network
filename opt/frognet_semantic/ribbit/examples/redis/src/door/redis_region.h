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
// The Redis region's one entry point for the RAM host: a JSON body in, a JSON body out. No Redis type crosses it.
#pragma once
#include <functional>
#include <string>
std::string redis_region_call(const std::string& body);
// A blocking command (BLPOP, XREAD BLOCK, DEBUG SLEEP ...): parks, on the caller's own request thread.
std::string redis_region_block(const std::string& body);
// [FAN_IN_BATCH_V1] Many connections' pipelines in one call: {"batch":[<a redis_region_call body, as JSON text>, ...]}
// -> {"ok":true,"b":[<each one's reply, as JSON text>, ...]}, in order. The items are independent connections: they run
// fanned out across the host's cores and are gathered back in order.
std::string redis_region_batch(const std::string& body);
// [RESP_ON_THE_WIRE_V1] the batch as bytes: no JSON, no base64 (format in redis_region.cpp)
std::string redis_region_batch_raw(const std::string& body);
// [NOBODY_WAITS_V1] the same raw batch, answered asynchronously: returns at once; done(answer) is called by whichever
// thread finishes the batch's last item (possibly this one, before it returns). Throws only if the body is malformed.
void redis_region_batch_raw_async(const std::string& body, std::function<void(std::string)> done);
