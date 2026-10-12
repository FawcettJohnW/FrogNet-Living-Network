#!/bin/bash
# make_host.sh RAM_SERVER_CPP OUT -- the RINA host = the platform's ram_server.cpp + one include + one dispatch block,
# exactly as the Redis host is made (redis-on-ribbit tools/make_host.sh). op=rina runs inline in the region and never
# waits; op=rina_block parks on its own request thread (a read with nothing to read yet, an accept with no request).
set -eu
SRC=${1:?}; OUT=${2:?}
n1=$(grep -c '^#include "lisp_engine.hpp"$' "$SRC"); n2=$(grep -c '    if (op == "lisp") {' "$SRC")
[ "$n1" = 1 ] && [ "$n2" = 1 ] || { echo "make_host: anchors not found exactly once (include=$n1 dispatch=$n2)" >&2; exit 1; }
awk '
/^#include "lisp_engine.hpp"$/ { print; print "#include \"rina_region.h\""; next }
/^    if \(op == "lisp"\) \{/ {
  print "    if (op == \"rina\") {                               // [RINA_REGION_V1] the RINA region, resident, inline"
  print "        if (method != \"POST\") { out = bad(405, \"rina is POST\"); return true; }"
  print "        try { out = Reply{200, rina_region_call(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"rina: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (op == \"rina_block\") {                         // [RINA_REGION_V1] waits on its own request thread"
  print "        if (method != \"POST\") { out = bad(405, \"rina_block is POST\"); return true; }"
  print "        // [DEFERRED_ANSWER_V1] the region never blocks: it answers now, or later through a continuation"
  print "        try { out = Reply{200, rina_region_block(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"rina: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print; next }
/^    auto q = query\(path\); const std::string op = q\["op"\];$/ {
  print "    // [REGION_FAST_PATH_V1] the two region operations are recognized by the exact tail of the path, before the query"
  print "    // string is parsed into a map (std::stringstream + std::map + urldec on every call: ~4.4 k instructions each)."
  print "    if (path.size() == API_PATH.size() + 8 && !path.compare(API_PATH.size(), 8, \"?op=rina\")) {"
  print "        if (method != \"POST\") { out = bad(405, \"rina is POST\"); return true; }"
  print "        try { out = Reply{200, rina_region_call(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"rina: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print "    if (path.size() == API_PATH.size() + 14 && !path.compare(API_PATH.size(), 14, \"?op=rina_block\")) {"
  print "        if (method != \"POST\") { out = bad(405, \"rina_block is POST\"); return true; }"
  print "        // [DEFERRED_ANSWER_V1] the region never blocks: it answers now, or later through a continuation"
  print "        try { out = Reply{200, rina_region_block(body)}; } catch (const std::exception& x) { out = bad(400, std::string(\"rina: \") + x.what()); }"
  print "        return true;"
  print "    }"
  print; next }
{ print }' "$SRC" > "$OUT"
grep -q 'RINA_REGION_V1' "$OUT" || { echo "make_host: generated host lacks the region glue" >&2; exit 1; }
grep -q 'REGION_FAST_PATH_V1' "$OUT" || { echo "make_host: generated host lacks the fast path" >&2; exit 1; }
