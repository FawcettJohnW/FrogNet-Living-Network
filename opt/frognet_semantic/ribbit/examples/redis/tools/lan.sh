#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# lan.sh -- the Redis region across machines, beside stock Redis on the same machines. No DNS: every address is an IP.
#
#   on the machine that holds the memory (and the stock server):
#       tools/lan.sh host
#   on every other machine, with that machine's IP and a name for this one:
#       tools/lan.sh node <HOST_IP> <NAME>
#   on the host machine, once the nodes are up (N = how many nodes):
#       tools/lan.sh coordinate <N> [SECONDS]
#
# Ports: 7490 the memory, 7491 stock redis-server, 7492 a local box (on every machine, the host's included).
# The coordinated run (tests/coordinated.py) is done twice, the same clients and checks each time:
#   stock   every node's clients talk to stock Redis on the host machine, across the network
#   region  every node's clients talk to the box on their own machine; every box holds the region, on the memory
# Each run checks that no INCR was lost or applied twice and that every node reads every other node's markers, and
# reports each node's operations per second. Then each node runs redis-benchmark against both, REPEAT times, interleaved.
set -u
cd "$(dirname "$0")/.."; ROOT=$PWD
REDIS_SRC=${REDIS_SRC:-$(cd .. && pwd)/redis-7.2.11}
CLI=$REDIS_SRC/src/redis-cli; BENCH=$REDIS_SRC/src/redis-benchmark
STOCK=$REDIS_SRC/src/redis-server.stock; [ -x "$STOCK" ] || STOCK=$REDIS_SRC/src/redis-server
REPEAT=${REPEAT:-5}
echo "== lan.sh $* on $(uname -n) $(uname -m), $(nproc) cores, build $(cat "$ROOT/VERSION" 2>/dev/null || echo unknown)"

up() { for i in $(seq 1 150); do "$CLI" -h "$1" -p "$2" ping >/dev/null 2>&1 && return 0; sleep 0.1; done; echo "FAIL: nothing answers on $1:$2"; exit 1; }
taken() { (exec 3<>/dev/tcp/127.0.0.1/$1) 2>/dev/null; }

case "${1:-}" in
host)
  for p in 7490 7491 7492; do taken $p && { echo "FAIL: port $p is already in use by a server this run did not start"; exit 1; }; done
  mkdir -p /tmp/redis-lan-stock
  build/ribbit-redis-host --listen 0.0.0.0:7490 & H=$!
  "$STOCK" --port 7491 --bind 0.0.0.0 --protected-mode no --save "" --appendonly no --dir /tmp/redis-lan-stock & S=$!
  sleep 0.5
  RIBBIT_RAM=127.0.0.1:7490 RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port 7492 --bind 0.0.0.0 --protected-mode no --save "" & F=$!
  up 127.0.0.1 7491; up 127.0.0.1 7492
  echo "== host up: memory :7490, stock :7491, box :7492. Leave this running; Ctrl-C stops all three."
  trap 'kill $H $S $F 2>/dev/null' INT TERM EXIT
  wait
  ;;
node)
  HOST=${2:?node needs the IP of the host machine}; NAME=${3:?node needs a name for this machine}
  FP=${FRONT_PORT:-7492}                                   # another port only to run two nodes on one machine
  taken $FP && { echo "FAIL: port $FP is already in use by a server this run did not start"; exit 1; }
  up "$HOST" 7491
  RIBBIT_RAM=$HOST:7490 build/ribbit-redis-front --port $FP --save "" & F=$!
  trap 'kill $F 2>/dev/null' INT TERM EXIT
  up 127.0.0.1 $FP
  echo "== $NAME: stock arm (clients -> stock on $HOST:7491)"
  python3 tests/coordinated.py participate --run lan-stock --name "$NAME" --host "$HOST" --port 7491 || echo "RESULT FAIL $NAME stock arm"
  echo "== $NAME: region arm (clients -> the box on this machine, on the memory at $HOST:7490)"
  python3 tests/coordinated.py participate --run lan-region --name "$NAME" --host 127.0.0.1 --port $FP || echo "RESULT FAIL $NAME region arm"
  echo "== $NAME: redis-benchmark -c 50 -P 16 -n 200000, $REPEAT runs interleaved (req/s)"
  for t in set get; do for r in $(seq 1 $REPEAT); do
    s=$("$BENCH" -h "$HOST" -p 7491 -q -c 50 -P 16 -n 200000 -r 100000 -t $t | tr '\r' '\n' | grep -o '[0-9.]* requests per' | tail -1 | cut -d' ' -f1)
    g=$("$BENCH" -h 127.0.0.1 -p $FP -q -c 50 -P 16 -n 200000 -r 100000 -t $t | tr '\r' '\n' | grep -o '[0-9.]* requests per' | tail -1 | cut -d' ' -f1)
    echo "MEASURE $NAME ${t^^} run $r  stock(remote) ${s:-0}  region(local box) ${g:-0}"
  done; done
  echo "== $NAME done"
  ;;
coordinate)
  N=${2:?coordinate needs how many nodes}; SECS=${3:-20}
  echo "== stock arm: $N nodes, $SECS s"
  python3 tests/coordinated.py coordinate --run lan-stock --expect "$N" --seconds "$SECS" --host 127.0.0.1 --port 7491
  echo "== region arm: $N nodes, $SECS s"
  python3 tests/coordinated.py coordinate --run lan-region --expect "$N" --seconds "$SECS" --host 127.0.0.1 --port 7492
  ;;
*) sed -n '4,20p' "$0"; exit 2 ;;
esac
