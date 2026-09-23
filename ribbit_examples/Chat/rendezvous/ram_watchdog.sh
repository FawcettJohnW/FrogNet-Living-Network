#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ram_watchdog.sh RAM_SERVER PORT IDLE_S BIND_HOST
#
# Launch one RAM-only server and stop it once its port has had no established
# connection for IDLE_S. ram_server holds its memory in RAM and clears it on
# exit, so this is the whole lifecycle of one demo tag: it lives while people
# are using it and frees its port when they stop. Launched detached by the
# rendezvous endpoint; it is not a service.
set -u
SRV="$1"; PORT="$2"; IDLE="$3"; BIND="${4:-0.0.0.0}"
"$SRV" --listen "$BIND:$PORT" &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT TERM INT
# grace: give the first client time to arrive before the idle clock counts
last=$(( $(date +%s) + 30 ))
while kill -0 $PID 2>/dev/null; do
  sleep 5
  # count established connections to this port (any local address)
  n=$(ss -tn state established "( sport = :$PORT )" 2>/dev/null | grep -c ':')
  now=$(date +%s)
  if [ "${n:-0}" -gt 0 ]; then last=$now; fi
  if [ $(( now - last )) -ge "$IDLE" ]; then
    kill $PID 2>/dev/null
    break
  fi
done
wait $PID 2>/dev/null
