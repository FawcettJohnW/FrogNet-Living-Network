#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# test.sh [PORT] -- starts build/comms-ram on 127.0.0.1:PORT (default 8841) and runs every Communicator oracle
# against it through the default Ribbit API. The bar is the node: tests_passing_on_node.txt lists the oracles that pass
# on the untouched 26 September bundle with a FrogNet node's own modules; every one of them must pass here. (The
# others fail on the node too: the tree's tests and code had drifted apart, or they need a display.) Build first.
set -u
cd "$(dirname "$0")"
PORT=${1:-8841}; OUT=$(mktemp -d)
./build/comms-ram --listen 127.0.0.1:$PORT > "$OUT/comms-ram.log" 2>&1 & RAM=$!
for i in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
export FROGNET_TUPLES_RAM=127.0.0.1:$PORT FROGNET_TUPLES_VENDOR=comms PYTHONDONTWRITEBYTECODE=1
fail=0; pass=0
while read -r t; do
  ( cd build && timeout 120 python3 "$t" > "$OUT/$t.log" 2>&1 ) && { pass=$((pass+1)); echo "PASS $t"; } || { fail=$((fail+1)); echo "FAIL $t (log: $OUT/$t.log)"; }
done < tests_passing_on_node.txt
kill $RAM; wait $RAM 2>/dev/null
echo "RESULT $([ $fail = 0 ] && echo PASS || echo FAIL) the Communicator on comms-ram: $pass of $((pass+fail)) oracles that pass on a node"
[ $fail = 0 ]
