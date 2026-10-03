#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- builds the chat example into build/: chat-ram (the chat region's RAM host), libchat.so (the chat client
# library, its C interface in client/chat.h) and the client test. The Ribbit platform comes from ../../cpp (RIBBIT).
set -eu
cd "$(dirname "$0")"
RIBBIT=${RIBBIT:-$(cd ../../cpp && pwd)}
DEFAULTS=${DEFAULTS:-$(cd ../../defaults/api && pwd)}   # the default API (tuples), which chat is written on
CXX=${CXX:-g++}
source "$(dirname "$(readlink -f "$0")")/../../cpp/ensure_build_deps.sh"; ribbit_ensure_build_deps   # [AUTO_INSTALL_BUILD_DEPS_V1]
mkdir -p build
F="-std=c++17 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I$RIBBIT/include -I$DEFAULTS -Iclient"
echo "== chat-ram";          $CXX $F chat_ram.cpp "$RIBBIT/src/ram_host.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/chat-ram
echo "== libchat.so";        $CXX $F -shared -fPIC client/chat_c.cpp client/chat_client.cpp "$DEFAULTS/tuples.cpp" "$RIBBIT/src/ram_client.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/libchat.so
echo "== test-chat-client";  $CXX $F tests/test_chat_client.cpp client/chat_client.cpp "$DEFAULTS/tuples.cpp" "$RIBBIT/src/ram_client.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/test-chat-client
cp client/chat.py client/frogchat.py build/
echo "built: build/chat-ram build/libchat.so build/chat.py build/frogchat.py"
