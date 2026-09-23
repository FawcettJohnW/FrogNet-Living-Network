#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# scale.sh -- stock Redis on one core, then Ribbit on one core, two cores, ...
#
# Every row uses the same redis-benchmark line on the same machine. The server
# and the benchmark client are pinned to different cores so they don't compete.
# Each server is benchmarked twice: keys spread over a million-key range, and
# 100 hot keys (maximum contention on the same cells).
#
#   bench/scale.sh | tee scale.log
#   bench/summarize.py scale.log
#
# Settings (environment variables):
#   REDIS         Redis 7.2.11 source tree, built       (default: ~/redis-7.2.11)
#   RIBBIT        the ribbit-redis binary               (default: ../ribbit-redis next to this script)
#   SERVER_CORES  cores given to the server, in the order threads are added
#   CLIENT_CORES  cores the benchmark client runs on (taskset list)
#   N C P         requests, connections, pipeline depth (default 1000000 200 16)
#   TESTS         redis-benchmark -t list               (default set,get,lpush,sadd)
#
# Pick cores so every server core is a REAL physical core and the client
# doesn't share one with the server. Hyperthread siblings are not extra cores.
#   Raspberry Pi 5 (4 cores):          SERVER_CORES="0 1 2"  CLIENT_CORES="3"
#   2 cores / 4 threads (siblings 0-2, 1-3):
#                                      SERVER_CORES="0 1"    CLIENT_CORES="2,3"
#   8+ physical cores:                 SERVER_CORES="0 1 2 3" CLIENT_CORES="4-7"
# `lscpu -e` shows which logical CPUs share a physical core.

HERE="$(cd "$(dirname "$0")" && pwd)"
REDIS=${REDIS:-$HOME/redis-7.2.11}
RIBBIT=${RIBBIT:-$HERE/../ribbit-redis}
SERVER_CORES=${SERVER_CORES:-"0 1"}
CLIENT_CORES=${CLIENT_CORES:-"2,3"}
N=${N:-1000000}; C=${C:-200}; P=${P:-16}
TESTS=${TESTS:-set,get,lpush,sadd}
STOCK_PORT=${STOCK_PORT:-7703}; RIBBIT_PORT=${RIBBIT_PORT:-7701}

BENCH=$REDIS/src/redis-benchmark
CLI=$REDIS/src/redis-cli
STOCK=$REDIS/src/redis-server.stock
[ -x "$STOCK" ] || STOCK=$REDIS/src/redis-server
for f in "$BENCH" "$CLI" "$STOCK" "$RIBBIT"; do [ -x "$f" ] || { echo "not found or not executable: $f" >&2; exit 1; }; done
if head -c 2 "$STOCK" | grep -q '#!'; then echo "$STOCK is the Ribbit wrapper, not stock Redis; expected src/redis-server.stock" >&2; exit 1; fi
command -v taskset >/dev/null || { echo "taskset not found (util-linux)" >&2; exit 1; }

set -- $SERVER_CORES; first=$1

echo "# machine: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')$(tr -d "\0" < /proc/device-tree/model 2>/dev/null)"
echo "# cores: $(nproc) logical; server cores: $SERVER_CORES; client cores: $CLIENT_CORES"
echo "# redis-benchmark -q -n $N -c $C -P $P -t $TESTS"
echo "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

bench() { # port label
  echo "=== $2"
  echo "--- spread keys (-r 1000000)"
  taskset -c $CLIENT_CORES "$BENCH" -p $1 -q -n $N -c $C -P $P -t $TESTS -r 1000000 2>&1 | tr '\r' '\n' | grep 'requests per second'
  echo "--- 100 hot keys (-r 100)"
  taskset -c $CLIENT_CORES "$BENCH" -p $1 -q -n $N -c $C -P $P -t $TESTS -r 100 2>&1 | tr '\r' '\n' | grep 'requests per second'
}
start() { # port cmd...
  local port=$1; shift
  "$@" > /dev/null 2>&1 & pid=$!
  for i in $(seq 1 50); do "$CLI" -p $port ping 2>/dev/null | grep -q PONG && return; sleep 0.1; done
  echo "server did not come up: $*" >&2; kill $pid 2>/dev/null; exit 1
}
stop() { kill $pid 2>/dev/null; wait $pid 2>/dev/null; }

start $STOCK_PORT taskset -c $first "$STOCK" --port $STOCK_PORT --save "" --appendonly no
bench $STOCK_PORT "stock, 1 core ($first)"; stop

cores=""; n=0
for c in $SERVER_CORES; do
  cores=${cores:+$cores,}$c; n=$((n+1))
  start $RIBBIT_PORT taskset -c $cores "$RIBBIT" --port $RIBBIT_PORT --save "" --io-threads $n
  bench $RIBBIT_PORT "ribbit, $n core$([ $n -gt 1 ] && echo s) ($cores)"
  "$CLI" -p $RIBBIT_PORT info ribbit | tr -d '\r' | grep -E '^(rss_kb|live_objects_total|ebr_retired_pending):' | sed 's/^/# /'
  stop
done
