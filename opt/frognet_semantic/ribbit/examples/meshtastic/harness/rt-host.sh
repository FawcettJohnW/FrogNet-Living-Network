#!/bin/bash
# rt-host.sh up N | down
# Host-mode equivalent of RepeaterTastic tests/interop/run_meshtasticd.sh (no docker):
# same config.yaml (Module: sim, EnableUDP, debug log), same -h/-p/-d arguments,
# absolute binary path so the firmware's execv(argv[0]) "reboot" works.
set -u
BIN=${MESHTASTICD_BIN:?set MESHTASTICD_BIN}
STATE=${STATE_DIR:-/home/claude/mesh/p0/rt-state}
BASE=${API_BASE_PORT:-4410}
case $1 in
up)
  n=${2:-2}
  for ((i = 1; i <= n; i++)); do
    hex=$(printf '%02X' $i); d=$STATE/node$i; port=$((BASE + i - 1))
    mkdir -p $d/vfs
    cat > $d/config.yaml <<EOF
Lora:
  Module: sim
Logging:
  LogLevel: debug
Config:
  EnableUDP: true
General:
  MaxNodes: 200
EOF
    ( while [ ! -f $d/stop ]; do "$BIN" -c $d/config.yaml -d $d/vfs -h 02001EE7A0$hex -p $port >> $d/meshtasticd.log 2>&1; echo "=== exit $? $(date +%s.%N)" >> $d/meshtasticd.log; sleep 1; done ) &
    echo "node$i hwid=02001EE7A0$hex api=127.0.0.1:$port log=$d/meshtasticd.log"
  done
  for ((i = 1; i <= n; i++)); do
    port=$((BASE + i - 1))
    for _ in $(seq 120); do (exec 3<>/dev/tcp/127.0.0.1/$port) 2>/dev/null && break; sleep 0.5; done
  done
  echo up ;;
down)
  for d in $STATE/node*; do touch $d/stop; done
  pkill -f -- "-d $STATE/node" ; sleep 2
  [ "${2:-}" == --wipe ] && rm -rf $STATE
  echo down ;;
esac
