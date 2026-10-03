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
# Build the RAM host with the Redis region resident, and the RESP front.
# Incremental and parallel: a file is recompiled only if it, or a header it includes, changed since its object was
# built (the compiler's own dependency files, -MMD); the platform is re-copied and re-patched only if the platform or
# a patch script changed; everything compiles at once, one job per core (JOBS=N to override). Compiler output goes to
# the terminal as it happens (never into a pipe that can close early, never into a file alone).
#   bash tools/build.sh           incremental
#   CLEAN=1 bash tools/build.sh   from scratch
set -e -o pipefail
cd "$(dirname "$0")/.."
PLAT=${PLAT:-platform/ribbit-v0.63}
JOBS=${JOBS:-$(nproc)}
CXX="${CXX_BIN:-g++} -O2 -pthread"          # CXX_BIN=g++-11 etc.; GCC 11 and newer
source "$(dirname "$(readlink -f "$0")")/../../../cpp/ensure_build_deps.sh"; CXX="${CXX_BIN:-g++}" ribbit_ensure_build_deps   # [AUTO_INSTALL_BUILD_DEPS_V1]
W="-Wall -Wextra -Wno-unused-parameter -Wno-free-nonheap-object"   # GCC 12 aarch64 false positive in libstdc++; see ribbit/cpp/qualify.sh
[ "${CLEAN:-0}" = 1 ] && rm -rf build
mkdir -p build/obj
P=build/platform

# ---- the platform, patched: redone only when the platform or a patch script changed
PATCHES="tools/apply_reply_proof.py tools/apply_same_miss.py tools/apply_coalesce.py tools/apply_wire_order.py tools/apply_row_scan_bound.py"
STAMP=$( (find "$PLAT" -type f -print0 | sort -z | xargs -0 sha256sum; sha256sum $PATCHES tools/make_host.sh) | sha256sum | cut -c1-16)
if [ "$(cat build/platform.stamp 2>/dev/null)" != "$STAMP" ]; then
  echo "== platform: copying and patching"
  rm -rf "$P" && cp -r "$PLAT" "$P"
  python3 tools/apply_reply_proof.py $P/ribbit_cpp/fnwp_engine.hpp        # [REPLY_EXTRACTION_PROVEN_V1]
  python3 tools/apply_same_miss.py $P/ribbit_cpp                          # [SAME_MISS_FROM_CACHE_V1]
  python3 tools/apply_coalesce.py $P/ribbit_cpp                           # [CLIENT_COALESCES_V1]
  python3 tools/apply_wire_order.py $P/ribbit_cpp                         # [WIRE_ORDER_TURNS_V1]
  python3 tools/apply_row_scan_bound.py $P/ribbit_cpp                     # [ROW_SCAN_BOUND_V1]
  tools/make_host.sh $P/ramsrv/ram_server.cpp build/ram_server_redis.cpp
  echo "$STAMP" > build/platform.stamp
fi

# ---- one compile: rebuilt when the object is missing, or when the CONTENT of its source or of any header it includes
# changed. Content, not timestamps: files copied or unpacked keep the time they were written, which can be older than
# the objects already built here -- a timestamp test would skip them and build the old code.
content_of() {   # $1 object: a hash of the source and every header it includes (from the compiler's dependency file)
  local d=${1%.o}.d
  sed -e 's/^[^:]*://' -e 's/\\$//' "$d" | tr ' ' '\n' | grep -v '^$' | sort -u | xargs cat 2>/dev/null | sha256sum | cut -c1-32
}
stale() {   # $1 object
  local o=$1
  [ -f "$o" ] && [ -f "${o%.o}.d" ] && [ -f "$o.hash" ] || return 0
  [ "$(content_of "$o")" = "$(cat "$o.hash")" ] && return 1
  return 0
}
PIDS=(); NAMES=()
job() {     # $1 object, rest: the compile command (without -o)
  local o=$1; shift
  stale "$o" || return 0
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n || true; done
  echo "   compiling ${o#build/}"
  "$@" -MMD -MF "${o%.o}.d" -o "$o" & PIDS+=($!); NAMES+=("$o")
}
settle() {  # wait for every compile; any failure fails the build (and its object is removed so it is retried)
  local i rc=0
  for i in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$i]}"; then rc=1; rm -f "${NAMES[$i]}" "${NAMES[$i]}.hash"; echo "FAILED: ${NAMES[$i]}"
    else content_of "${NAMES[$i]}" > "${NAMES[$i]}.hash"; fi
  done
  PIDS=(); NAMES=()
  [ $rc = 0 ] || { echo "BUILD FAILED"; exit 1; }
}

echo "== compiling (up to $JOBS at once; only what changed)"
job build/obj/frogram.o $CXX -std=c++17 -c $P/ribbit_cpp/frogram.cpp
for f in src/*.cpp; do
  b=$(basename "${f%.cpp}")
  E=""; case "$b" in redis_region|front) E="-Werror";; esac        # the new code builds warning-free, always
  job build/obj/$b.o $CXX -std=c++20 $W $E -Isrc -I$P/ribbit_cpp -c "$f"
done
job build/ram_server_redis.o $CXX -std=c++20 -I$P/ribbit_cpp -I$P/ramsrv -Isrc/door -c build/ram_server_redis.cpp
settle

echo "== linking"
ENGINE=$(ls build/obj/*.o | grep -v -e frogram.o -e front.o)
$CXX -o build/ribbit-redis-host build/ram_server_redis.o $ENGINE build/obj/frogram.o -llz4 -lcrypto
$CXX -o build/ribbit-redis-front build/obj/front.o $ENGINE build/obj/frogram.o -llz4 -lcrypto
$CXX -std=c++17 $W -Werror -I$P/ribbit_cpp tests/raw_call.cpp build/obj/frogram.o -o build/raw-call -llz4 -lcrypto   # the trust test's attacker
echo "BUILD OK $(build/ribbit-redis-host --version 2>&1 | head -1)"
