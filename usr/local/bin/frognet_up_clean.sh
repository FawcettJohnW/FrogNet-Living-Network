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
# /usr/local/bin/frognet_up_clean.sh
#
# FrogNet clean re-establishment (boot-equivalent) — CORE ONLY.
#
# MODEL COMPLIANCE:
#   - DOES NOT bring up radio/ham0.
#   - DOES NOT create overlays.
#   - DOES NOT install radio routes.
#   - Radio is established ONLY by an explicit user-invoked action (frognet_bootstrap.sh with enable flag/config).
#
# What it DOES:
#   - Stops frognet-proxy and frognet-daemon (clean slate).
#   - Applies /etc/setup_iptables.
#   - Cleans bogus non-kernel routes that shadow directly-connected carrier/L2 prefixes
#     (fixes the "10.102.40.0/24 via peer dev eth1 metric 22 onlink" class generically).
#   - Starts frognet-daemon and frognet-proxy and verifies they are ACTIVE.
#
# Optional behavior:
#   - If FROGNET_RESTART_NETWORK=1, restarts NetworkManager and dnsmasq (best-effort).
#
# Logs:
#   /var/log/frognet_up_clean.log


RUN_DIR="/run/frognet"
LOG="/var/log/frognet_up_clean.log"

mkdir -p "$RUN_DIR"
touch "$LOG"
chmod 0644 "$LOG" || true

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

must_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Must run as root" >&2
    exit 1
  fi
}

unit_exists() {
  local unit="$1"
  if have systemctl; then
    systemctl list-unit-files 2>/dev/null | awk '{print $1}' | grep -qx "$unit"
    return $?
  fi
  return 1
}

svc_stop() {
  local name="$1"
  if have systemctl && unit_exists "${name}.service"; then
    systemctl stop "${name}.service" || true
    return 0
  fi
  if have service; then
    service "$name" stop || true
  fi
}

svc_start_strict() {
  local name="$1"
  if have systemctl && unit_exists "${name}.service"; then
    log "Starting ${name}.service"
    systemctl restart "${name}.service"
    if ! systemctl is-active --quiet "${name}.service"; then
      log "ERROR: ${name}.service failed to become active"
      systemctl status "${name}.service" --no-pager | tee -a "$LOG" >&2 || true
      journalctl -u "${name}.service" -n 120 --no-pager | tee -a "$LOG" >&2 || true
      return 1
    fi
    return 0
  fi

  if have service; then
    log "Restarting service $name (sysv)"
    service "$name" restart
    return 0
  fi

  log "ERROR: cannot start service $name (no systemctl/service)"
  return 1
}

kill_foreground_helpers() {
  pkill -f proxy/proxy_main.py >/dev/null 2>&1 || true
  pkill -f daemon/daemon_main.py >/dev/null 2>&1 || true
  pkill -f icmp_handler.py >/dev/null 2>&1 || true
  pkill -f ham_icmp_receiver.py >/dev/null 2>&1 || true
}

# ------------------------------------------------------------
# Route hygiene: remove non-kernel routes that shadow connected prefixes
# (This fixes the "underlay via route" class generically.)
# ------------------------------------------------------------
_connected_prefixes_on_dev() {
  local dev="$1"
  ip -4 route show dev "$dev" proto kernel 2>/dev/null | awk '{print $1}' | awk 'NF' | sort -u
}

_delete_shadow_routes_for_dev() {
  local dev="$1"
  local pfx

  mapfile -t pfxs < <(_connected_prefixes_on_dev "$dev" || true)
  ((${#pfxs[@]})) || return 0

  for pfx in "${pfxs[@]}"; do
    # Delete any non-kernel route to that same prefix (any via/metric), because it is invalid.
    # Keep the kernel-connected route only.
    while read -r line; do
      [[ -n "$line" ]] || continue
      echo "$line" | grep -q 'proto kernel' && continue
      # Only delete lines that explicitly mention the dev (safety).
      echo "$line" | grep -q " dev ${dev}\b" || continue

      log "Pruning shadow route on connected prefix ($dev): $line"
      # Use 'ip route del <full line>' to match exactly.
      ip route del $line >/dev/null 2>&1 || true
    done < <(ip -4 route show "$pfx" 2>/dev/null || true)
  done
}

prune_shadow_connected_routes() {
  # Default set: eth1 + wlan0 + wlan1 if present. We do not assume they exist.
  for dev in eth1 wlan0 wlan1 ham0; do
    ip link show "$dev" >/dev/null 2>&1 || continue
    _delete_shadow_routes_for_dev "$dev" || true
  done
}

main() {
  must_root
  log "=== FrogNet UP (clean core) begin ==="

  log "Stopping FrogNet services"
  svc_stop frognet-proxy
  svc_stop frognet-daemon
  kill_foreground_helpers

  # Optional: restart base network services
  if [[ "${FROGNET_RESTART_NETWORK:-0}" == "1" ]]; then
    log "Restarting NetworkManager + dnsmasq (best-effort)"
    if have systemctl; then
      systemctl restart NetworkManager || true
      sleep 2
      systemctl restart dnsmasq || true
      sleep 1
    else
      have service && service NetworkManager restart || true
      sleep 2
      have service && service dnsmasq restart || true
      sleep 1
    fi
  fi

  log "Applying /etc/setup_iptables"
  if [[ ! -x /etc/setup_iptables ]]; then
    log "ERROR: /etc/setup_iptables missing or not executable"
    exit 1
  fi
  /etc/setup_iptables || { log "ERROR: /etc/setup_iptables failed"; exit 1; }

  log "Pruning shadow routes (connected prefix safety)"
  prune_shadow_connected_routes

  log "Starting frognet-daemon then frognet-proxy"
  svc_start_strict frognet-daemon || exit 1

  # FAST allowed only on fast links; ham0 must NOT be included (consistent with your model)
  export FROGNET_SEMANTIC_IFACES="${FROGNET_SEMANTIC_IFACES:-wlan0,eth1}"

  svc_start_strict frognet-proxy || exit 1

  log "=== FrogNet UP (clean core) complete ==="
}

main "$@"
