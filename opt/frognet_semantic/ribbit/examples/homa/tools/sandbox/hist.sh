#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# hist.sh: what a client is told about a server that was there before and is not now, and about one that came back
H=/home/claude/src/frognet/opt/frognet_semantic/ribbit/examples/homa; HOMA=/home/claude/src/homa; L=/home/claude/logs/fast
export SOFTHOMA_RAM=127.0.0.1:9161 SOFTHOMA_NODE1=127.0.0.1
srv() { env LD_PRELOAD=$H/build/libsofthoma.so $HOMA/util/cp_node server --protocol homa --first-port 4000 --port-threads 2 >$L/h-srv$1.log 2>&1 & S=$!; sleep 1; }
cli() { env SOFTHOMA_DEBUG=1 LD_PRELOAD=$H/build/libsofthoma.so timeout $2 $HOMA/util/cp_node client --protocol homa --workload 100 --client-max 8 --ports 1 --first-port 4000 --first-server 1 --server-nodes 1 >$L/h-cli$1.log 2>&1
  echo "--- client $1: $3"; grep "GONE\|NOT THERE\|FATAL\|can row" $L/h-cli$1.log | cut -c1-150 | head -6; grep "clients:" $L/h-cli$1.log | tail -1 | sed 's/.*clients: //' | cut -c1-60; }
$H/build/softhoma-ram 127.0.0.1:9161 >$L/h-ram.log 2>&1 & RAM=$!; sleep 0.5
srv 1; cli 1 4 "server running"
kill -KILL $S; sleep 1
cli 2 5 "server stopped a second ago"
sleep ${1:-13}
cli 3 5 "server stopped ${1:-13} s ago"
srv 2; cli 4 5 "server started again"
kill $S $RAM 2>/dev/null
