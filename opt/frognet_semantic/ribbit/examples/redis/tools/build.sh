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
# Build Redis on the memory [ONE_KEYSPACE_V1]: ribbit-redis-host (the memory, holding the Redis region -- the keyspace,
# once) and ribbit-redis-front (a front: RESP to clients, one call per pipeline).
# Incremental and parallel: a file is recompiled only if it, or a header it includes, changed since its object was
# built (the compiler's own dependency files, -MMD, compared by CONTENT); everything compiles at once, one job per core
# (JOBS=N to override). Compiler output goes to the terminal as it happens.
#   bash tools/build.sh           incremental
#   CLEAN=1 bash tools/build.sh   from scratch
set -e -o pipefail
cd "$(dirname "$0")/.."
P=$(cd ../../cpp && pwd)                    # the platform: ribbit/cpp, linked as it is
JOBS=${JOBS:-$(nproc)}
CXX="${CXX_BIN:-g++} -O2 -pthread"          # CXX_BIN=g++-11 etc.; GCC 11 and newer
# [SAME_ALLOCATOR_AS_STOCK_V1] stock Redis is built with jemalloc; the boxes and the memory are too.
source "$P/ensure_build_deps.sh"; RIBBIT_EXTRA_DEPS="jemalloc/jemalloc.h:libjemalloc-dev" CXX="${CXX_BIN:-g++}" ribbit_ensure_build_deps   # [AUTO_INSTALL_BUILD_DEPS_V1]
W="-Wall -Wextra -Wno-unused-parameter -Wno-free-nonheap-object"   # GCC 12 aarch64 false positive in libstdc++; see ribbit/cpp/qualify.sh
[ "${CLEAN:-0}" = 1 ] && rm -rf build
mkdir -p build/obj

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
  [ "$(content_of "$o")" = "$(cat "$o.hash")" ] && [ "$(cat "$o.cmd" 2>/dev/null)" = "$CMD" ] && return 1
  return 0
}
PIDS=(); NAMES=(); CMDS=()
job() {     # $1 object, rest: the compile command (without -o)
  local o=$1; shift
  CMD="$*"; stale "$o" || return 0                 # [BUILT_FROM_THIS_V1] the flags count too: the version is one
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n || true; done
  echo "   compiling ${o#build/}"
  rm -f "$o.cmd"; "$@" -MMD -MF "${o%.o}.d" -o "$o" & PIDS+=($!); NAMES+=("$o"); CMDS+=("$CMD")
}
settle() {  # wait for every compile; any failure fails the build (and its object is removed so it is retried)
  local i rc=0
  for i in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$i]}"; then rc=1; rm -f "${NAMES[$i]}" "${NAMES[$i]}.hash"; echo "FAILED: ${NAMES[$i]}"
    else content_of "${NAMES[$i]}" > "${NAMES[$i]}.hash"; printf '%s' "${CMDS[$i]}" > "${NAMES[$i]}.cmd"; fi
  done
  PIDS=(); NAMES=(); CMDS=()
  [ $rc = 0 ] || { echo "BUILD FAILED"; exit 1; }
}

echo "== compiling (up to $JOBS at once; only what changed)"
job build/obj/frogram.o $CXX -std=c++20 -Wno-free-nonheap-object -I$P/include -c $P/src/frogram.cpp
job build/obj/ram_host.o $CXX -std=c++20 $W -Werror -I$P/include -c $P/src/ram_host.cpp
job build/obj/lf_ebr.o $CXX -std=c++20 $W -Werror -I$P/include -c $P/src/lf_ebr.cpp
VER=$(cat VERSION 2>/dev/null || echo unversioned)
job build/obj/redis_ram.o $CXX -std=c++20 $W -Werror -DREDIS_RAM_VERSION="\"$VER\"" -Isrc -I$P/include -c src/redis_ram.cpp
job build/obj/ribbit_space.o $CXX -std=c++20 $W -Werror -I$P/include -c $P/src/ribbit_space.cpp   # [SPACE_V1]
# the box's sources, named: a tree unpacked over the RAM-host design keeps its old files (src/redis_region.cpp,
# src/coherence.cpp ...), and those are not part of this build
SRCS="region front server resp util ebr memory db cmd_conn cmd_geo cmd_hash cmd_hll cmd_keyspace cmd_list cmd_multi cmd_server cmd_set cmd_sort cmd_stream cmd_stream_groups cmd_string cmd_zset"
for b in $SRCS; do
  f=src/$b.cpp
  E=""; case "$b" in region|front) E="-Werror -DRIBBIT_REDIS_VERSION=\"$VER\"";; esac   # the new code builds warning-free, always
  job build/obj/$b.o $CXX -std=c++20 $W $E -Isrc -I$P/include -c "$f"
done
settle

echo "== linking"
ENGINE=$(for b in $SRCS; do case $b in front|region) ;; *) echo build/obj/$b.o;; esac; done)
$CXX -o build/ribbit-redis-host build/obj/redis_ram.o build/obj/region.o $ENGINE build/obj/ram_host.o build/obj/lf_ebr.o build/obj/frogram.o -llz4 -lcrypto -ljemalloc
$CXX -o build/ribbit-redis-front build/obj/front.o $ENGINE build/obj/ribbit_space.o build/obj/ram_host.o build/obj/lf_ebr.o build/obj/frogram.o -llz4 -lcrypto -ljemalloc
cat VERSION > build/BUILT_FROM 2>/dev/null || echo unversioned > build/BUILT_FROM   # [BUILT_FROM_THIS_V1] run.sh checks it
echo "BUILD OK $(build/ribbit-redis-host --version 2>&1 | head -1), box $(cat VERSION 2>/dev/null || echo unversioned)"
