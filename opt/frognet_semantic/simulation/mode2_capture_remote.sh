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
# mode2_capture_remote.sh - run on the REMOTE box (the daemon end).
#
# Stands up the mode-2 far end (origin echo + remote_test_daemon) on a true
# interface and snapshots cross-layer state into a tarball you upload back.
#
# Usage:
#   ./mode2_capture_remote.sh [LISTEN_PORT] [ORIGIN_URL]
#     LISTEN_PORT  default 19009  (keep OFF 9009 - live daemon)
#     ORIGIN_URL   default http://127.0.0.1:8080/echo.php  (php echo on this box)
#                  pass "" to run the daemon in self-echo mode (no php needed)
#
# Run THIS first, then run mode2_capture_near.sh on the other box, then press
# Enter here to finish and produce the tarball.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-19009}"
ORIGIN="${2-http://127.0.0.1:8080/echo.php}"
OUT="/tmp/frognet_mode2_remote"
rm -rf "$OUT"; mkdir -p "$OUT"
log(){ echo "[capture-remote] $*"; }
snap(){ echo "### $1" >> "$OUT/state.txt"; shift; "$@" >> "$OUT/state.txt" 2>&1 || echo "(unavailable)" >> "$OUT/state.txt"; echo >> "$OUT/state.txt"; }

log "snapshotting static state -> $OUT/state.txt"
snap "uname -a"            uname -a
snap "python3 --version"  python3 --version
snap "php --version"      php --version
snap "ip addr"            ip addr
snap "ip route"           ip route
snap "ss -tlnp (before)"  ss -tlnp
snap "tc qdisc show"      sh -c 'for i in $(ls /sys/class/net); do echo "-- $i"; tc qdisc show dev $i; done'
snap "conntrack sample"   sh -c 'conntrack -L 2>/dev/null | head -50'
snap "dmesg tail"         sh -c 'dmesg 2>/dev/null | tail -40'

export FROGNET_PROXY_ROOT="$HERE/.." FROGNET_BIN_ROOT="${FROGNET_BIN_ROOT:-/usr/local/bin}"

PHP_PID=""; DAEMON_PID=""
ORIGIN_ARG=()
if [ -n "$ORIGIN" ]; then
  # Start the bundled php echo if the origin points at localhost:8080.
  if command -v php >/dev/null 2>&1 && echo "$ORIGIN" | grep -q '127.0.0.1:8080'; then
    log "starting php echo origin on 0.0.0.0:8080"
    ( cd "$HERE" && php -S 0.0.0.0:8080 echo.php ) > "$OUT/php.log" 2>&1 &
    PHP_PID=$!; sleep 0.6
  else
    log "NOTE: assuming origin $ORIGIN is already reachable (php not auto-started)"
  fi
  ORIGIN_ARG=(--origin "$ORIGIN")
else
  log "self-echo mode (no origin process)"
fi

log "starting remote_test_daemon on 0.0.0.0:$PORT"
python3 "$HERE/remote_test_daemon.py" --listen "0.0.0.0:$PORT" "${ORIGIN_ARG[@]}" \
    > "$OUT/daemon.log" 2>&1 &
DAEMON_PID=$!; sleep 1.0
echo "### ss -tlnp (after daemon up)" >> "$OUT/state.txt"
ss -tlnp >> "$OUT/state.txt" 2>&1 || true

if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
  log "DAEMON FAILED TO START - see $OUT/daemon.log"; cat "$OUT/daemon.log"
fi
log "daemon up (pid $DAEMON_PID). Listening on 0.0.0.0:$PORT."
log "NOW: on the near box run  ./mode2_capture_near.sh <THIS_BOX_IP> $PORT"
log "When the near run finishes, press Enter here to capture + bundle."
read -r _ || true

echo "### ss -tnp (established, post-run)" >> "$OUT/state.txt"
ss -tnp >> "$OUT/state.txt" 2>&1 || true
[ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null
[ -n "$PHP_PID" ] && kill "$PHP_PID" 2>/dev/null

TARBALL="/tmp/frognet_mode2_remote_$(date +%Y%m%d_%H%M%S).tar.gz"
tar czf "$TARBALL" -C /tmp frognet_mode2_remote
log "DONE -> $TARBALL   (upload this back)"
log "daemon.log DIAG lines:"; grep -c '\[DIAG-DAEMON\]' "$OUT/daemon.log" 2>/dev/null || true
