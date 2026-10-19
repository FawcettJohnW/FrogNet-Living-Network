#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# bench.sh -- unmodified against modified, on this machine, in one command. Builds stock Mosquitto 2.1.2 (stock/: the
# v2.1.2 release tree, untouched) and MQTT on Ribbit (tools/build.sh), then drives the same load, by the same driver,
# against stock and against every shape: the region resident with N fronts (rN) and the region in mqtt-ram with N fronts
# (ownN). Prints the side-by-side tables. With SUITE=1 also runs Mosquitto's own suite against stock and every shape.
#
#   tools/bench.sh                                     from examples/mqtt
#   FRONTS="1 2 4" SECS=10 REPEAT=3 SUITE=1 SCENARIOS=many-1024,crowd-4096 tools/bench.sh
#
# ownN: the region in mqtt-ram. Every operation is a request on the semantic wire (SAME/DIFF, coalescing on both sides),
#       a front's sessions one per core with a client's operations always on one, a pass's publishes one request per
#       session, any number in flight, deliveries by a held read parked in the host ([NOBODY_WAITS_V1]
#       [SESSIONS_PER_CLIENT_V1] [BATCH_ON_THE_WIRE_V1] [HELD_WAIT_V1]); the host runs each session's requests in order
#       on its pool, sessions at once ([SESSION_ORDER_V1] [HOST_FANOUT_V1]).
#
# The tables include "lost": messages sent in the window that never reached a subscriber, even after a drain -- at
# QoS 1/2, messages acknowledged to the publisher and then dropped ([DRAIN_COUNT_V1]).
#
# A run that passes its deadline (warmup + seconds + 120 s), a broker that will not stop, or a region host that outlives
# its broker is not left to hang the bench: every thread's state and kernel stack, every TCP socket's queues, the
# broker's $SYS counters and the driver's per-connection state are printed as [HANG_DUMP_V1] lines and kept in the
# row, the run is stopped, and the bench goes on; the tables end with a HUNG line for it.
#
# Everything goes to the terminal. The raw rows are kept in results/bench-<host>-<time>-*.jsonl.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE"
JOBS=${JOBS:-$(nproc)}
FRONTS=${FRONTS:-"1 2 4"}
RESIDENT=${RESIDENT:-0}   # 1: also the resident shapes (rN: the region inside one process -- one machine, no wire)
SECS=${SECS:-10}
REPEAT=${REPEAT:-3}
SUITE=${SUITE:-0}
SCENARIOS=${SCENARIOS:-all}
VERSION=$(cat "$HERE/VERSION")
STAMP=$(hostname)-$(date +%Y%m%d-%H%M%S)

if [ "$SUITE" = 1 ] && [ "$(id -u)" = 0 ]; then
  echo "bench FAIL: SUITE=1 needs a non-root user (Mosquitto drops privileges when started as root; the suite breaks)" >&2
  exit 1
fi

echo "== MQTT on Ribbit $VERSION"
echo "== machine"
echo "host:    $(hostname)"
echo "kernel:  $(uname -srm)"
echo "cores:   $(nproc)"
echo "cpu:     $(lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)"
echo "memory:  $(awk '/MemTotal/ {printf "%.1f GB", $2/1048576}' /proc/meminfo)"
echo "fronts:  $FRONTS   seconds: $SECS   repeats: $REPEAT   suite: $SUITE"

echo "== build MQTT on Ribbit"
JOBS=$JOBS tools/build.sh

echo "== build stock Mosquitto 2.1.2"
make -C stock -j"$JOBS" WITH_DOCS=no binary
if [ "$SUITE" = 1 ]; then make -C stock/test/broker/c -j"$JOBS"; fi
echo "stock:   $(stock/src/mosquitto -h | head -1)"
echo "ribbit:  $(front/src/mosquitto -h | head -1)   front $(sha256sum front/src/mosquitto | cut -c1-12)  mqtt-ram $(sha256sum front/src/mqtt-ram | cut -c1-12)"

B=(--broker stock="$HERE/stock/src/mosquitto")
NAMES=stock
[ "$RESIDENT" = 1 ] && for n in $FRONTS; do
  B+=(--broker "r$n=$HERE/front/src/mosquitto" --broker-env "r$n:MQTT_THREADS=$n")
  NAMES+=",r$n"
done
for n in $FRONTS; do
  B+=(--broker "own$n=$HERE/front/src/mosquitto" --broker-env "own$n:MQTT_THREADS=$n" --broker-env "own$n:MQTT_REGION=own")
  NAMES+=",own$n"
done

LOAD=results/bench-$STAMP-load.jsonl
echo "== load: same driver, every broker, $REPEAT x $SECS s per scenario"
python3 harness/load_compare.py "${B[@]}" --seconds "$SECS" --repeat "$REPEAT" --scenarios "$SCENARIOS" --out "$LOAD"

if [ "$SUITE" = 1 ]; then
  echo "== Mosquitto's suite: stock"
  python3 harness/run_suite.py --build-root stock --label stock --out "results/bench-$STAMP-suite-stock.jsonl"
  for n in $FRONTS; do
    [ "$RESIDENT" = 1 ] && { echo "== Mosquitto's suite: r$n"
      MQTT_THREADS=$n python3 harness/run_suite.py --build-root front --label "r$n" --out "results/bench-$STAMP-suite-r$n.jsonl"; }
    echo "== Mosquitto's suite: own$n"
    MQTT_THREADS=$n MQTT_REGION=own python3 harness/run_suite.py --build-root front --label "own$n" --out "results/bench-$STAMP-suite-own$n.jsonl"
  done
fi

echo "== MQTT on Ribbit $VERSION   machine: $(hostname), $(nproc) cores, $(lscpu 2>/dev/null | sed -n 's/^Model name:[[:space:]]*//p' | head -1)"
python3 harness/load_table.py "$LOAD" --brokers "$NAMES"
if [ "$SUITE" = 1 ]; then
  for n in $FRONTS; do
    for s in $([ "$RESIDENT" = 1 ] && echo r$n) own$n; do
      echo "== suite: stock vs $s"
      python3 harness/compare.py "results/bench-$STAMP-suite-stock.jsonl" "results/bench-$STAMP-suite-$s.jsonl" | sed -n "3,9p"
    done
  done
fi
echo "== two boxes on one memory [MEMORY_IS_MEMORY_V1]: checks and load across boxes"
SECS=$SECS tools/two_boxes.sh
echo "bench DONE: $LOAD"
