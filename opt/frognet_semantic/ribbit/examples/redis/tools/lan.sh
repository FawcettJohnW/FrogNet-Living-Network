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
# reports each node's operations per second. Then the benchmark: on the host's word, every node runs the same
# redis-benchmark at the same moment (INCR on one key, SET, GET; -P 1 and -P 16), stock then region, REPEAT rounds
# interleaved; the host prints the total req/s of all nodes together and the median table. REQS (per node, -P 1;
# x5 at -P 16), CLIENTS (per node), REPEAT.
set -u
cd "$(dirname "$0")/.."; ROOT=$PWD
REDIS_SRC=${REDIS_SRC:-$(cd .. && pwd)/redis-7.2.11}
CLI=$REDIS_SRC/src/redis-cli; BENCH=$REDIS_SRC/src/redis-benchmark
STOCK=$REDIS_SRC/src/redis-server.stock; [ -x "$STOCK" ] || STOCK=$REDIS_SRC/src/redis-server
REPEAT=${REPEAT:-3}
echo "== lan.sh $* on $(uname -n) $(uname -m), $(nproc) cores, build $(cat "$ROOT/VERSION" 2>/dev/null || echo unknown)"

# [SAYS_WHY_V1] what the connection got: refused (nothing listening there), timed out (dropped on the way), or the reply
up() { local e; for i in $(seq 1 150); do e=$(timeout 3 "$CLI" -h "$1" -p "$2" ping 2>&1) && [ "$e" = PONG ] && return 0; sleep 0.1; done
       [ -z "$e" ] && e="no reply in 3 s (timed out: the SYN or its answer is dropped on the way)"
       echo "FAIL: nothing answers on $1:$2 -- $e"; exit 1; }
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
  # [LAN_BENCH_TOGETHER_V1] the benchmark, on the host's word: every node starts the same run at once
  echo "== $NAME: ready for the benchmark (the host runs it: lan.sh coordinate)"
  B="$CLI -h $HOST -p 7491"; last=""
  # [RUNS_SIZED_BY_THE_LINK_V1] this node's round trip to the host, measured: the host sizes every run from it
  rtt=$(python3 -c "
import socket, time
s = socket.create_connection(('$HOST', 7491)); s.sendall(b'PING\\r\\n'); s.recv(64); t = time.time()
for i in range(20): s.sendall(b'PING\\r\\n'); s.recv(64)
print('%.6f' % ((time.time() - t) / 20))")
  echo "== $NAME: round trip to $HOST measured $(python3 -c "print('%.1f' % ($rtt*1000))") ms"
  $B hset lanbench:rtt "$NAME" "$rtt" >/dev/null
  $B sadd lanbench:ready "$NAME" >/dev/null
  while :; do
    spec=$($B get lanbench:go)
    [ "$spec" = end ] && break
    set -- $spec
    if [ -n "${1:-}" ] && [ "$1" != "$last" ]; then
      last=$1; arm=$2; t=$3; P=$4; n=$5; R=""; [ "$t" != incr ] && R="-r 100000"
      if [ "$arm" = stock ]; then th=$HOST; tp=7491; else th=127.0.0.1; tp=$FP; fi
      r=$("$BENCH" -h $th -p $tp -q -c ${CLIENTS:-50} -P $P -n $n $R -t $t | tr '\r' '\n' | grep -o '[0-9.]* requests per' | tail -1 | cut -d' ' -f1)
      echo "MEASURE $NAME run $1 $arm ${t^^} -P $P: ${r:-0} req/s"
      $B rpush lanbench:res:$1 "$NAME ${r:-0}" >/dev/null
    fi
    sleep 0.05
  done
  echo "== $NAME done"
  ;;
coordinate)
  N=${2:?coordinate needs how many nodes}; SECS=${3:-20}; REQS=${REQS:-200000}
  B="$CLI -h 127.0.0.1 -p 7491"
  $B del lanbench:go lanbench:rtt lanbench:ready >/dev/null
  echo "== stock arm: $N nodes, $SECS s"
  python3 tests/coordinated.py coordinate --run lan-stock --expect "$N" --seconds "$SECS" --host 127.0.0.1 --port 7491
  echo "== region arm: $N nodes, $SECS s"
  python3 tests/coordinated.py coordinate --run lan-region --expect "$N" --seconds "$SECS" --host 127.0.0.1 --port 7492
  # [LAN_BENCH_TOGETHER_V1] every node runs the same redis-benchmark at the same moment; the total is the requests of
  # all nodes over the time from GO until the last node reports, on this machine's clock
  echo "== benchmark: $N nodes at once, redis-benchmark -c ${CLIENTS:-50} per node, $REPEAT rounds interleaved"
  echo "   stock: every node -> stock redis-server on this machine.  region: every node -> its own box -> the memory on this machine"
  for i in $(seq 1 600); do [ "$($B scard lanbench:ready)" -ge "$N" ] && break; sleep 0.5; done
  [ "$($B scard lanbench:ready)" -ge "$N" ] || { echo "FAIL: only $($B scard lanbench:ready) of $N nodes reached the benchmark"; exit 1; }
  # [RUNS_SIZED_BY_THE_LINK_V1] requests per node per run: about RUN_SECS at the slowest node's measured round trip,
  # one request per client per round trip (x16 pipelined), at most REQS (x5 pipelined)
  RTT=$($B hvals lanbench:rtt | sort -g | tail -1); RUN=${RUN_SECS:-10}; C=${CLIENTS:-50}
  N1=$(python3 -c "print(int(max(1000, min($REQS, $C*$RUN/$RTT))))"); N16=$(python3 -c "print(int(max(1000, min($REQS*5, $C*16*$RUN/$RTT))))")
  echo "== slowest round trip to this machine: $(python3 -c "print('%.1f' % ($RTT*1000))") ms; per node per run: $N1 requests at -P 1, $N16 at -P 16"
  seq=0; OUTL=""
  for r in $(seq 1 $REPEAT); do for t in incr set get; do for P in 1 16; do for arm in stock region; do
    seq=$((seq+1)); n=$N1; [ $P = 16 ] && n=$N16
    $B del lanbench:res:$seq >/dev/null
    t0=$(date +%s.%N); $B set lanbench:go "$seq $arm $t $P $n" >/dev/null
    for i in $(seq 1 12000); do [ "$($B llen lanbench:res:$seq)" -ge "$N" ] && break; sleep 0.05; done
    t1=$(date +%s.%N); got=$($B llen lanbench:res:$seq)
    # the total is the sum of every node's own rate: nodes on faster links finish their share sooner, and dividing
    # all requests by the slowest node's time would charge them its link [SUM_OF_THE_NODES_V1]
    per=$($B lrange lanbench:res:$seq 0 -1 | tr '\n' ' ')
    tot=$(python3 -c "v='$per'.split(); print('%.0f' % sum(float(x) for x in v[1::2]))")
    line="MEASURE round $r ${t^^} -P $P $arm: $tot req/s total, the sum of $got nodes ($per; $n requests each, last done at $(python3 -c "print('%.1f' % ($t1-$t0))") s)"
    [ "$got" -lt "$N" ] && line="$line  FAIL: $got of $N nodes reported"
    echo "$line"; OUTL="$OUTL$t $P $arm $tot
"
  done; done; done; done
  $B set lanbench:go end >/dev/null
  echo "== median of $REPEAT rounds, total req/s across $N nodes"
  printf "%s" "$OUTL" | python3 -c "
import sys, statistics as st
d = {}
for l in sys.stdin:
    t, P, arm, v = l.split(); d.setdefault((t, P), {}).setdefault(arm, []).append(float(v))
print('%-6s %-6s %12s %12s %8s' % ('test', 'pipe', 'stock', 'region', 'ratio'))
for (t, P), a in d.items():
    s, g = st.median(a['stock']), st.median(a['region'])
    print('%-6s -P %-3s %12.0f %12.0f %8.2f' % (t.upper(), P, s, g, g / s if s else 0))"
  ;;
*) sed -n '4,20p' "$0"; exit 2 ;;
esac
