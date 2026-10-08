#!/bin/bash
# fdf-comms.sh -- [KART_CALLS_V1] the real Communicator's shared memory (comms-ram, port 8800) and media server
# (comms-media, port 8994), on the same machine as the race. The kart demo's Call buttons open comms-app against these.
# COMMS_BIN: the directory with comms-ram and comms-media (the Communicator's build/; default below).
# Foreground; both print to this terminal; Ctrl-C stops both. Open TCP 8800 and 8994 to the players.
set -eu
B=${COMMS_BIN:-/opt/fdf/comms}
for p in comms-ram comms-media; do [ -x "$B/$p" ] || { echo "no $B/$p: set COMMS_BIN to the Communicator's build directory"; exit 1; }; done
"$B/comms-ram" --listen 0.0.0.0:8800 & R=$!
trap 'kill $R 2>/dev/null' EXIT
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8800) 2>/dev/null && break; sleep 0.05; done
"$B/comms-media" --listen 0.0.0.0:8994 --ram 127.0.0.1:8800
