#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
# test.sh [PORT] -- starts a tuples RAM host on 127.0.0.1:PORT (default 8821), runs the default API's test, stops it.
set -u
cd "$(dirname "$0")"
PORT=${1:-8821}
./build/named-ram tuples --listen 127.0.0.1:$PORT > build/named-ram-test.log 2>&1 & RAM=$!
for i in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
python3 tests/test_tuples.py $PORT; rc=$?
kill $RAM; wait $RAM 2>/dev/null
exit $rc
