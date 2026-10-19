#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# two_boxes.sh -- two brokers (two boxes) on one memory, the way two FrogNet nodes share it [MEMORY_IS_MEMORY_V1]:
# each writes its own truths there and reads the other's by triggers. Checks, with stock clients:
#   1. a publish on box A reaches a subscriber on box B                       (msg.<box>)
#   2. a retained message published on A is given to a new subscriber on B    (retain)
#   3. a wildcard subscriber on B gets a publish on A                         (msg.<box>, the box's own match)
#   4. a client that connects to B while A holds it is taken over: A drops it, its subscription goes with it  (at, session)
#   5. load: publishers on A, subscribers on B, side by side with one box     (mqtt-load --pub-port)
# Then prints PASS or FAIL per check. Run from examples/mqtt after tools/build.sh, as a non-root user or root.
#   MEMORY=host:port tools/two_boxes.sh      the memory elsewhere (another node); default: one started here
set -u
cd "$(dirname "$0")/.."
HERE=$PWD
export LD_LIBRARY_PATH=$HERE/front/lib:$HERE/front/libcommon
SUB=$HERE/front/client/mosquitto_sub; PUB=$HERE/front/client/mosquitto_pub
port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
LOGS=results/two-boxes-$(date +%Y%m%d-%H%M%S); mkdir -p "$LOGS"
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup EXIT
if [ -z "${MEMORY:-}" ]; then
  M=$(port); front/src/mqtt-ram --listen 127.0.0.1:$M > "$LOGS/memory.log" 2>&1 & PIDS+=($!); MEMORY=127.0.0.1:$M; sleep 0.3
fi
PA=$(port); PB=$(port)
MQTT_REGION=$MEMORY MQTT_THREADS=${FRONTS:-1} front/src/mosquitto -p $PA > "$LOGS/box-a.log" 2>&1 & PIDS+=($!)
MQTT_REGION=$MEMORY MQTT_THREADS=${FRONTS:-1} front/src/mosquitto -p $PB > "$LOGS/box-b.log" 2>&1 & PIDS+=($!)
sleep 1
ok=0; bad=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; ok=$((ok+1)); else echo "FAIL $1: wanted [$3], got [$2]"; bad=$((bad+1)); fi; }

# 1. across boxes
got=$(timeout 5 $SUB -p $PB -t t/1 -q 1 -C 1 & sleep 0.5; $PUB -p $PA -t t/1 -q 1 -m across; wait)
check "publish on A reaches a subscriber on B" "$got" "across"
# 2. retained across boxes
$PUB -p $PA -t r/1 -q 1 -r -m kept; sleep 0.3
got=$(timeout 5 $SUB -p $PB -t r/1 -q 1 -C 1)
check "retained on A is given to a new subscriber on B" "$got" "kept"
$PUB -p $PA -t r/1 -q 1 -r -n; sleep 0.3
got=$(timeout 2 $SUB -p $PB -t r/1 -q 1 -C 1 -W 1 2>/dev/null)
check "a retained clear on A is seen on B" "$got" ""
# 3. wildcard on B
got=$(timeout 5 $SUB -p $PB -t 'w/+/x' -q 1 -C 1 & sleep 0.5; $PUB -p $PA -t w/a/x -q 1 -m wild; wait)
check "a wildcard subscriber on B gets a publish on A" "$got" "wild"
# 4. a session moves across boxes. C1 subscribes on A with a persistent session and goes away; a message is queued for
#    it on A; C1 connects to B: its session (the subscription, the queued message) comes from A.
timeout 5 $SUB -p $PA -i C1 -c -t s/1 -q 1 -W 1 > /dev/null 2>&1; sleep 0.3
$PUB -p $PA -t s/1 -q 1 -m queued; sleep 0.3
got=$(timeout 5 $SUB -p $PB -i C1 -c -t nothing/here -q 1 -C 1 2>/dev/null)
check "C1's session (its queued message) moves from A to B" "$got" "queued"
got=$(timeout 5 $SUB -p $PB -i C1 -c -t nothing/here -q 1 -C 1 & sleep 0.5; $PUB -p $PA -t s/1 -q 1 -m after; wait)
check "C1's subscription moved with it" "$got" "after"
# 4b. a connected client is taken over: C2 holds a connection on A (a raw MQTT 3.1.1 CONNECT, no reconnecting), then
#     connects to B; A must close the first connection
python3 - $PA > "$LOGS/c2.out" 2>&1 <<'PY' &
import socket, sys, time
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
cid = b"C2"; vh = b"\x00\x04MQTT\x04\x00\x00\x3c"; pl = len(cid).to_bytes(2, "big") + cid
s.sendall(bytes([0x10, len(vh) + len(pl)]) + vh + pl)
print("connack", s.recv(4).hex(), flush=True)
s.settimeout(5)
try: d = s.recv(1); print("closed" if not d else "data", flush=True)
except socket.timeout: print("still-connected", flush=True)
PY
c2=$!; sleep 0.5
timeout 3 $SUB -p $PB -i C2 -c -t nothing/here -q 1 -W 2 > /dev/null 2>&1
wait $c2
check "A drops C2 when it connects to B" "$(tail -1 "$LOGS/c2.out")" "closed"
# 5. load: publishers on A, subscribers on B -- and one box for comparison
echo "== load, qos1, 4 publishers on A, 4 subscribers on B"
harness/mqtt-load --port $PB --pub-port $PA --pubs 4 --subs 4 --topics 4 --qos 1 --seconds ${SECS:-5} --warmup 1 --threads 2 | tee "$LOGS/load-across.json" | tr ',' '\n' | grep -E '"received_per_s"|"lost"|"lat_us_p50"'
echo "== load, the same, everything on A"
harness/mqtt-load --port $PA --pubs 4 --subs 4 --topics 4 --qos 1 --seconds ${SECS:-5} --warmup 1 --threads 2 | tee "$LOGS/load-one-box.json" | tr ',' '\n' | grep -E '"received_per_s"|"lost"|"lat_us_p50"'
grep -h 'overwritten\|trigger on .* ends\|gave no session\|memory is gone' "$LOGS"/box-*.log | sort | uniq -c
echo "two_boxes: $ok passed, $bad failed; logs in $LOGS"
[ $bad = 0 ]
