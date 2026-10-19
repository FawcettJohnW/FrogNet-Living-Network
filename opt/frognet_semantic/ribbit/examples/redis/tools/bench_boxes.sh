#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# bench_boxes.sh -- side by side, same machine, same driver (redis-benchmark), interleaved, REPEAT runs each (median
# [min..max]): stock redis-server; one box on its own memory; two boxes on one memory with the clients split between
# them (each box applies every command, so the two together are the region).
#   STOCK=path/to/redis-server  REPEAT=3  N=200000  tools/bench_boxes.sh
# Shapes: -c 50 -P 16 (pipelined), -c 50 -P 1 (many clients, one command at a time), -c 1 -P 1 (one client: latency).
set -u
cd "$(dirname "$0")/.."
STOCK=${STOCK:-$(command -v redis-server)}; BENCH=${BENCH:-$(command -v redis-benchmark)}
REPEAT=${REPEAT:-3}; N=${N:-200000}
OUT=results/bench-boxes-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"
port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
wait_up() { for i in $(seq 1 200); do redis-cli -p $1 ping >/dev/null 2>&1 && return 0; sleep 0.05; done; return 1; }
PIDS=(); cleanup() { for p in "${PIDS[@]}"; do kill $p 2>/dev/null; done; wait 2>/dev/null; PIDS=(); }
trap cleanup EXIT
# one run: $1 arm, $2 clients, $3 pipeline, $4 test -> requests per second (summed over the endpoints)
run() {
  local arm=$1 c=$2 p=$3 t=$4 ports=()
  case $arm in
    stock) local a=$(port); $STOCK --port $a --save "" --appendonly no > "$OUT/stock.log" 2>&1 & PIDS+=($!); ports=($a) ;;
    box1)  local a=$(port); build/ribbit-redis-front --port $a --save "" > "$OUT/box1.log" 2>&1 & PIDS+=($!); ports=($a) ;;
    box2)  local m=$(port) a=$(port) b=$(port)
           build/ribbit-redis-host --listen 127.0.0.1:$m > "$OUT/mem.log" 2>&1 & PIDS+=($!); sleep 0.3
           RIBBIT_RAM=127.0.0.1:$m RIBBIT_REDIS_CONFIG_OWNER=1 build/ribbit-redis-front --port $a --save "" > "$OUT/box2a.log" 2>&1 & PIDS+=($!)
           RIBBIT_RAM=127.0.0.1:$m build/ribbit-redis-front --port $b --save "" > "$OUT/box2b.log" 2>&1 & PIDS+=($!); ports=($a $b) ;;
  esac
  for x in "${ports[@]}"; do wait_up $x || { echo "0"; cleanup; return; }; done
  local each=$(( c / ${#ports[@]} )); [ $each -lt 1 ] && each=1
  local n=$(( N / ${#ports[@]} )); [ $c = 1 ] && n=$(( N / 20 ))
  local tot=0 i=0 f=() bp=()
  for x in "${ports[@]}"; do f+=("$OUT/r-$arm-$c-$p-$t-$i.csv"); $BENCH -p $x -c $each -P $p -n $n -r 100000 -t $t --csv > "${f[$i]}" 2>/dev/null & bp+=($!); i=$((i+1)); done
  wait "${bp[@]}"
  for x in "${f[@]}"; do v=$(grep -i "^\"$t\"" "$x" | head -1 | cut -d, -f2 | tr -d '"'); tot=$(python3 -c "print(int($tot + float('${v:-0}')))"); done
  echo $tot
  cleanup
}
echo "shape test arm runs..." > "$OUT/raw.txt"
for shape in "50 16" "50 1" "1 1"; do
  set -- $shape
  for t in set get incr lpush; do
    for r in $(seq 1 $REPEAT); do
      for arm in stock box1 box2; do echo "$1 $2 $t $arm $(run $arm $1 $2 $t)" >> "$OUT/raw.txt"; done
    done
  done
done
python3 - "$OUT/raw.txt" <<'PY' | tee "$OUT/SUMMARY.txt"
import sys, statistics, collections
d = collections.defaultdict(list)
for l in open(sys.argv[1]).read().split("\n")[1:]:
    f = l.split()
    if len(f) == 5: d[(int(f[0]), int(f[1]), f[2], f[3])].append(int(f[4]))
print("%-14s %-6s %24s %24s %24s %7s %7s" % ("shape", "cmd", "stock", "one box", "two boxes", "1/st", "2/st"))
for (c, p, t) in sorted({k[:3] for k in d}, key=lambda k: (-k[0], -k[1], k[2])):
    g = lambda a: d.get((c, p, t, a), [0])
    f = lambda v: "%8.0f [%6.0f..%6.0f]" % (statistics.median(v), min(v), max(v))
    s = statistics.median(g("stock")) or 1
    print("%-14s %-6s %24s %24s %24s %7.2f %7.2f" % ("-c %d -P %d" % (c, p), t, f(g("stock")), f(g("box1")), f(g("box2")), statistics.median(g("box1")) / s, statistics.median(g("box2")) / s))
PY
echo "raw runs and logs: $OUT"
