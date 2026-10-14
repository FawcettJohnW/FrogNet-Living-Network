#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# Produce the RAM host with the Redis region resident: the platform's unmodified ram_server.cpp plus one include and
# one dispatch line (op=redis, run inline: a Redis region operation never waits). Refuses if an anchor is not found
# exactly once, or if the result lacks the region glue.
set -eu
SRC=${1:?platform ram_server.cpp}; OUT=${2:?output}
n1=$(grep -c '^#include "lisp_engine.hpp"$' "$SRC"); n2=$(grep -c '    if (op == "lisp") {' "$SRC")
n3=$(grep -c '^void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {$' "$SRC")
n4=$(grep -c '^    auto run = \[s, seq\](fnwp::ServerEngine::Work w) {$' "$SRC")
[ "$n1" = 1 ] && [ "$n2" = 1 ] && [ "$n3" = 1 ] && [ "$n4" = 1 ] || { echo "make_host: anchors not found exactly once (include=$n1 dispatch=$n2 engine_frame=$n3 run=$n4)" >&2; exit 1; }
awk '
/^#include "lisp_engine.hpp"$/ { print; print "#include \"redis_region.h\""; next }
/^void engine_frame\(std::shared_ptr<Session> s, uint32_t seq, const std::string& f\) \{$/ {
  print "// [NOBODY_WAITS_V1] a raw Redis batch is handed to the region asynchronously: the socket thread offers it to the"
  print "// region'"'"'s pool and returns to its socket; whichever thread finishes the batch sends the reply. No thread is made."
  print "template <class F> static bool redis_async(const fnwp::ServerEngine::Work& w, F send) {"
  print "    if (w.method != \"POST\" || w.path.compare(0, API_PATH.size(), API_PATH) || query(w.path)[\"op\"] != \"redis_raw\") return false;"
  print "    try { redis_region_batch_raw_async(w.body, [send](std::string a) { send(Reply{200, std::move(a)}); }); }"
  print "    catch (const std::exception& x) { send(bad(400, std::string(\"redis: \") + x.what())); }"
  print "    return true;"
  print "}"
  print; next }
/^    auto run = \[s, seq\]\(fnwp::ServerEngine::Work w\) \{$/ {
  print
  print "        if (redis_async(w, [s, seq, w](const Reply& r) { s->finish_and_send(seq, w, r); })) return;   // [NOBODY_WAITS_V1]"
  next }
/^    if \(op == "lisp"\) \{/ {
  print "    if (op == \"redis\") {                              // [REDIS_REGION_V1] the Redis region, resident, inline"
  print "        if (method != \"POST\") { out = bad(405, \"redis is POST\"); return true; }"
  print "        try { out = Reply{200, redis_region_call(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"redis_batch\") {                        // [FAN_IN_BATCH_V1] many connections, one call, on its own thread"
  print "        if (method != \"POST\") { out = bad(405, \"redis_batch is POST\"); return true; }"
  print "        if (!may_wait) return false;                     // off the socket thread: batches in flight run at once"
  print "        try { out = Reply{200, redis_region_batch(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"redis_raw\") {                          // [RESP_ON_THE_WIRE_V1] the batch as bytes"
  print "        if (method != \"POST\") { out = bad(405, \"redis_raw is POST\"); return true; }"
  print "        if (!may_wait) return false;"
  print "        try { out = Reply{200, redis_region_batch_raw(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"redis_feed_open\") {                    // [READS_AT_THE_FRONT_V1] a front opens its invalidation feed"
  print "        try { out = Reply{200, redis_region_feed_open()}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"redis_feed\") {                         // [READS_AT_THE_FRONT_V1] what it holds that changed; waits when nothing has"
  print "        try { std::string a; if (!redis_region_feed(atoi(q[\"f\"].c_str()), may_wait, a)) return false; out = Reply{200, a}; }"
  print "        catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"redis_block\") {                        // [REDIS_REGION_V1] a blocking command parks on its own thread"
  print "        if (method != \"POST\") { out = bad(405, \"redis_block is POST\"); return true; }"
  print "        if (!may_wait) return false;"
  print "        try { out = Reply{200, redis_region_block(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print; next }
{ print }' "$SRC" > "$OUT"
grep -q 'NOBODY_WAITS_V1' "$OUT" || { echo "make_host: generated host lacks the asynchronous batch" >&2; exit 1; }
grep -q 'REDIS_REGION_V1' "$OUT" || { echo "make_host: generated host lacks the region glue" >&2; exit 1; }
