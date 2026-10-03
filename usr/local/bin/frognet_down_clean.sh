#!/bin/bash
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
# /usr/local/bin/frognet_down_clean.sh
#
# FrogNet clean shutdown:
#   - Stop proxy/daemon services
#   - Stop ICMP helper processes (pidfile-driven, then pkill fallback)
#   - Remove ham0
#   - Leave NetworkManager running (do not disrupt admin connectivity)
#

RUN_DIR="/run/frognet"
LOG="/var/log/frognet_down_clean.log"
mkdir -p "$RUN_DIR"
touch "$LOG"
chmod 0644 "$LOG" || true

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

svc_stop() {
  local name="$1"
  if have systemctl && systemctl list-unit-files 2>/dev/null | awk '{print $1}' | grep -qx "${name}.service"; then
    systemctl stop "${name}.service" >/dev/null 2>&1 || true
    return 0
  fi
  if have service; then
    service "$name" stop >/dev/null 2>&1 || true
  fi
}

stop_pidfile() {
  local name="$1"
  local pidfile="${RUN_DIR}/${name}.pid"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      log "Stopping $name (pid $pid)"
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pidfile" >/dev/null 2>&1 || true
  fi
}

must_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Must run as root" >&2
    exit 1
  fi
}

main() {
  must_root
  log "=== FrogNet DOWN (clean) begin ==="

  log "Stopping frognet services"
  svc_stop frognet-proxy
  svc_stop frognet-daemon

  log "Stopping ICMP helper processes"
  stop_pidfile icmp_handler
  stop_pidfile ham_icmp_receiver
  pkill -f icmp_handler.py >/dev/null 2>&1 || true
  pkill -f ham_icmp_receiver.py >/dev/null 2>&1 || true

  log "Deleting ham0 (if present)"
  ip link del ham0 >/dev/null 2>&1 || true

  log "=== FrogNet DOWN (clean) complete ==="
}

main "$@"
