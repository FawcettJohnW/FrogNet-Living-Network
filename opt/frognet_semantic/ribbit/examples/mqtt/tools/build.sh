#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- build MQTT on Ribbit: the platform pieces it links, mqtt-ram (the region's host), the front (Mosquitto
# 2.1.2's broker with its shared state moved into the region) and its plugins, the test plugins, and the harness's
# load driver. Compiler output goes to the terminal as it happens.
#
#   tools/build.sh            from examples/mqtt
#   JOBS=4 tools/build.sh
#
# Results: front/src/mosquitto (the front; with MQTT_REGION unset it starts front/src/mqtt-ram itself),
# front/src/mqtt-ram, harness/mqtt-load. The front tree doubles as the BUILD_ROOT Mosquitto's own tests run against.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
P=$(cd "$HERE/../../cpp" && pwd)
JOBS=${JOBS:-$(nproc)}
CXX=${CXX:-g++}
source "$P/ensure_build_deps.sh"
# the platform's lz4 and OpenSSL, and what Mosquitto 2.1.2 (front and stock) and its suite's plugins link
ribbit_ensure_build_deps "cjson/cJSON.h:libcjson-dev" "microhttpd.h:libmicrohttpd-dev" "sqlite3.h:libsqlite3-dev" \
  "CUnit/CUnit.h:libcunit1-dev" "argon2.h:libargon2-dev" "jemalloc/jemalloc.h:libjemalloc-dev" "editline/readline.h:libedit-dev"
W="-Wall -Wextra -Werror -Wno-free-nonheap-object"
mkdir -p "$P/bin"

# the version the box says in its log: VERSION, baked in at build time
# written with its licence notice: this file is tracked, and the build must not strip what the licence check requires
printf '// Copyright (C) 2016-2026 Fawcett Innovations LLC\n// SPDX-License-Identifier: GPL-2.0-only\n// written by tools/build.sh from VERSION\n#define RIBBIT_MQTT_VERSION "%s"\n' "$(cat "$HERE/VERSION")" > "$HERE/region/ribbit_mqtt_version.h"
echo "== platform objects"
$CXX -std=c++20 -Wno-free-nonheap-object -O2 -pthread -I"$P/include" -c "$P/src/frogram.cpp" -o "$P/bin/frogram.o"
$CXX -std=c++20 -O2 -pthread $W -I"$P/include" -c "$P/src/ram_client.cpp" -o "$P/bin/ram_client.o"
$CXX -std=c++20 -O2 -pthread $W -I"$P/include" -c "$P/src/ribbit_space.cpp" -o "$P/bin/ribbit_space.o"   # [SPACE_V1]
$CXX -std=c++20 -O2 -pthread $W -I"$P/include" -c "$P/src/ribbit_pp.cpp" -o "$P/bin/ribbit_pp.o"   # [API_PLUS_PLUS_V2]
$CXX -std=c++20 -O2 -pthread $W -I"$P/include" -c "$P/src/ram_host.cpp" -o "$P/bin/ram_host.o"

echo "== mqtt-ram"
$CXX -std=c++20 -O2 -pthread $W -I"$P/include" -I"$HERE/region" \
  "$HERE/region/mqtt_ram.cpp" "$P/src/ram_host.cpp" "$P/src/lf_ebr.cpp" "$P/bin/frogram.o" \
  -llz4 -lcrypto -ljemalloc -o "$HERE/front/src/mqtt-ram"   # [JEMALLOC_V1] malloc was 15% of the front, measured

echo "== front (Mosquitto 2.1.2 over the region) and its plugins"
# Mosquitto's makefiles do not make every object depend on the headers it includes: a changed struct would leave old
# objects with the old layout. Every object is rebuilt.
rm -f "$HERE"/front/src/*.o "$HERE"/front/lib/*.o "$HERE"/front/lib/*.so* "$HERE"/front/libcommon/*.o "$HERE"/front/libcommon/*.a "$HERE"/front/plugins/*/*.o "$HERE"/front/plugins/*/*.so
make -C "$HERE/front" -j"$JOBS" WITH_DOCS=no WITH_JEMALLOC=yes binary   # [JEMALLOC_V1] Mosquitto's own option
echo "== test plugins"
make -C "$HERE/front/test/broker/c" -j"$JOBS"

echo "== harness load driver"
$CXX -std=c++17 -O2 -pthread -Wall -Wextra -Werror -o "$HERE/harness/mqtt-load" "$HERE/harness/mqtt_load.cpp"

echo "build PASS"
