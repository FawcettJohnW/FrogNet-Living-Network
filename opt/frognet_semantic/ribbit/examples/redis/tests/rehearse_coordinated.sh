#!/bin/bash
# Rehearse tests/coordinated.py on one machine: one RAM host (password on), N "machines" -- each a front behind its own
# path of ONE_WAY_MS each way (tools/latency_relay.py) -- a participant on each, the coordinator on the first.
# Usage: rehearse_coordinated.sh [N=3] [SECONDS=10] [CLIENTS=10] [ONE_WAY_MS=12]
set -u
cd "$(dirname "$0")/.."
N=${1:-3}; SECS=${2:-10}; CL=${3:-10}; OW=${4:-12}; BASE=9940; API=/Fawcett.Redis.ram_interface.php
export FROGNET_BLOB_ROOT=/tmp/rehearse-blobs; mkdir -p $FROGNET_BLOB_ROOT
CONF=$(mktemp); echo "requirepass rehearse" > $CONF
PIDS=""; trap 'kill $PIDS 2>/dev/null; rm -f $CONF' EXIT
up() { for i in $(seq 1 200); do python3 -c "import socket;socket.create_connection(('127.0.0.1',$1),timeout=1)" 2>/dev/null && return 0; sleep 0.05; done; echo "port $1 never opened" >&2; exit 1; }
RIBBIT_REDIS_CONFIG=$CONF build/ribbit-redis-host --listen 127.0.0.1:$BASE --api $API --quiet >/dev/null 2>&1 & PIDS="$PIDS $!"; up $BASE
for i in $(seq 1 $N); do python3 tools/latency_relay.py --listen 127.0.0.1:$((BASE+10+i)) --to 127.0.0.1:$BASE --delay-ms $OW >/dev/null 2>&1 & PIDS="$PIDS $!"; done
for i in $(seq 1 $N); do up $((BASE+10+i)); done
for i in $(seq 1 $N); do RIBBIT_RAM=127.0.0.1:$((BASE+10+i)) build/ribbit-redis-front --port $((BASE+20+i)) --save "" >/dev/null 2>&1 & PIDS="$PIDS $!"; done
for i in $(seq 1 $N); do up $((BASE+20+i)); done
RUN=rehearse$$
PP=""; for i in $(seq 1 $N); do python3 tests/coordinated.py participate --run $RUN --name machine$i --port $((BASE+20+i)) -a rehearse --clients $CL > /tmp/$RUN-machine$i.log 2>&1 & PP="$PP $!"; done
python3 tests/coordinated.py coordinate --run $RUN --expect $N --port $((BASE+21)) -a rehearse --seconds $SECS --markers 50; rc=$?
wait $PP
for i in $(seq 1 $N); do sed "s/^/  /" /tmp/$RUN-machine$i.log | tail -2; done
exit $rc
