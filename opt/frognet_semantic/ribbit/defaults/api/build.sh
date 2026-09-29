#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- builds the default API into build/: libtuples.so (ribbit::TuplesClient, C interface tuples.h), and the
# test host named-ram. The Ribbit platform comes from ../../cpp (RIBBIT).
set -eu
cd "$(dirname "$0")"
RIBBIT=${RIBBIT:-$(cd ../../cpp && pwd)}
CXX=${CXX:-g++}
mkdir -p build
F="-std=c++17 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I$RIBBIT/include -I."
echo "== libtuples.so"; $CXX $F -shared -fPIC tuples_c.cpp tuples.cpp "$RIBBIT/src/ram_client.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/libtuples.so
echo "== named-ram";    $CXX $F tests/named_ram.cpp "$RIBBIT/src/ram_host.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/named-ram
echo "built: build/libtuples.so build/named-ram"
