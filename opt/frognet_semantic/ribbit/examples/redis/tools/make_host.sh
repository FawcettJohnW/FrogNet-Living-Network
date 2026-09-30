#!/bin/bash
# Produce the RAM host with the Redis region resident: the platform's unmodified ram_server.cpp plus one include and
# one dispatch line (op=redis, run inline: a Redis region operation never waits). Refuses if an anchor is not found
# exactly once, or if the result lacks the region glue.
set -eu
SRC=${1:?platform ram_server.cpp}; OUT=${2:?output}
n1=$(grep -c '^#include "lisp_engine.hpp"$' "$SRC"); n2=$(grep -c '    if (op == "lisp") {' "$SRC")
[ "$n1" = 1 ] && [ "$n2" = 1 ] || { echo "make_host: anchors not found exactly once (include=$n1 dispatch=$n2)" >&2; exit 1; }
awk '
/^#include "lisp_engine.hpp"$/ { print; print "#include \"redis_region.h\""; next }
/^    if \(op == "lisp"\) \{/ {
  print "    if (op == \"redis\") {                              // [REDIS_REGION_V1] the Redis region, resident, inline"
  print "        if (method != \"POST\") { out = bad(405, \"redis is POST\"); return true; }"
  print "        try { out = Reply{200, redis_region_call(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"redis: \") + x.what()); }"
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
grep -q 'REDIS_REGION_V1' "$OUT" || { echo "make_host: generated host lacks the region glue" >&2; exit 1; }
