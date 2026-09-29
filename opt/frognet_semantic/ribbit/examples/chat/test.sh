#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# test.sh [PORT] -- starts build/chat-ram on 127.0.0.1:PORT (default 8811), runs the client tests from C++ and from
# Python against it, and stops it. Build first: ./build.sh
set -u
cd "$(dirname "$0")"
PORT=${1:-8811}
./build/chat-ram --listen 127.0.0.1:$PORT > build/chat-ram-test.log 2>&1 & RAM=$!
for i in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rc=0
./build/test-chat-client $PORT || rc=1
python3 tests/test_chat.py $PORT || rc=1
kill $RAM; wait $RAM 2>/dev/null
exit $rc
