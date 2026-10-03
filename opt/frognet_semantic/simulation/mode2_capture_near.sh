#!/usr/bin/env bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# mode2_capture_near.sh - run on the NEAR box (the proxy/driver end).
#
# Drives the proxy over a TRUE interface to the remote daemon, with an
# independent latency baseline (ping) and an on-the-wire pcap, and bundles
# everything for post-hoc validation.
#
# Usage:
#   ./mode2_capture_near.sh REMOTE_IP [PORT] [ECHO_BYTES] [ECHO_COUNT] [SHAPE_IFACE]
#     PORT        default 19009
#     ECHO_BYTES  default 4096   (use e.g. 60000 to probe MTU/fragmentation)
#     ECHO_COUNT  default 10
#     SHAPE_IFACE optional: if set, applies tc-netem to it (root) and records
#                 the qdisc readback - DANGER on a production bearer.
#
# Run mode2_capture_remote.sh on the other box FIRST.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REMOTE_IP="${1:?need REMOTE_IP}"
PORT="${2:-19009}"
EBYTES="${3:-4096}"
ECOUNT="${4:-10}"
SHAPE_IFACE="${5:-}"
OUT="/tmp/frognet_mode2_near"
rm -rf "$OUT"; mkdir -p "$OUT"
log(){ echo "[capture-near] $*"; }
snap(){ echo "### $1" >> "$OUT/state.txt"; shift; "$@" >> "$OUT/state.txt" 2>&1 || echo "(unavailable)" >> "$OUT/state.txt"; echo >> "$OUT/state.txt"; }

log "snapshotting static state -> $OUT/state.txt"
snap "uname -a"               uname -a
snap "python3 --version"      python3 --version
snap "ip addr"                ip addr
snap "ip route get $REMOTE_IP" ip route get "$REMOTE_IP"
snap "tc qdisc show (before)" sh -c 'for i in $(ls /sys/class/net); do echo "-- $i"; tc qdisc show dev $i; done'

log "independent latency baseline: ping -c 20 $REMOTE_IP"
ping -c 20 "$REMOTE_IP" > "$OUT/ping.txt" 2>&1 || log "(ping unavailable/blocked)"

# Optional pcap on the wire (post-hoc evidence of the two-socket flow + bytes).
TCPDUMP_PID=""
if command -v tcpdump >/dev/null 2>&1; then
  log "starting tcpdump ring buffer on port $PORT -> $OUT/wire.pcap"
  tcpdump -n -s 0 -w "$OUT/wire.pcap" "host $REMOTE_IP and tcp port $PORT" \
      > "$OUT/tcpdump.log" 2>&1 &
  TCPDUMP_PID=$!; sleep 0.5
else
  log "(tcpdump not present - skipping pcap)"
fi

# Optional shaping on the near egress (records the qdisc readback).
SHAPE_ARGS=()
if [ -n "$SHAPE_IFACE" ]; then
  log "SHAPING $SHAPE_IFACE via tc-netem (FROGNET_SIM_EXECUTE=1). DANGER if live bearer."
  export FROGNET_SIM_EXECUTE=1
  SHAPE_ARGS=(--shape-iface "$SHAPE_IFACE" --latency-ms 50 --jitter-ms 10)
fi

export FROGNET_PROXY_ROOT="$HERE/.." FROGNET_BIN_ROOT="${FROGNET_BIN_ROOT:-/usr/local/bin}"
log "driving proxy: --mode 2 --remote-addr $REMOTE_IP --remote-port $PORT "\
"--echo-bytes $EBYTES --echo-count $ECOUNT"
python3 "$HERE/sotf_video_stream_test.py" --mode 2 \
    --remote-addr "$REMOTE_IP" --remote-port "$PORT" \
    --echo-bytes "$EBYTES" --echo-count "$ECOUNT" \
    "${SHAPE_ARGS[@]}" 2>&1 | tee "$OUT/driver.log"

if [ -n "$SHAPE_IFACE" ]; then
  echo "### tc qdisc show dev $SHAPE_IFACE (after apply)" >> "$OUT/state.txt"
  tc qdisc show dev "$SHAPE_IFACE" >> "$OUT/state.txt" 2>&1 || true
fi
[ -n "$TCPDUMP_PID" ] && { sleep 0.5; kill "$TCPDUMP_PID" 2>/dev/null; }
echo "### ss -tnp (post-run)" >> "$OUT/state.txt"
ss -tnp >> "$OUT/state.txt" 2>&1 || true

TARBALL="/tmp/frognet_mode2_near_$(date +%Y%m%d_%H%M%S).tar.gz"
tar czf "$TARBALL" -C /tmp frognet_mode2_near
log "DONE -> $TARBALL   (upload this back, with the remote tarball)"
log "DIAG line for quick paste:"
grep '\[DIAG-MODE2\]' "$OUT/driver.log" || log "(no DIAG-MODE2 line - check driver.log)"
