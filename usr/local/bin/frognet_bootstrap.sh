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
# /usr/local/bin/frognet_bootstrap.sh
#
# FrogNet one-command "on-air" bootstrap.
#
# MODEL COMPLIANCE:
#   - Always brings up core FrogNet cleanly.
#   - Brings up radio/overlay ONLY if explicitly enabled (flag, env, or sentinel file).
#   - Transport establishment remains out-of-band from FrogNet semantics logic.
#
# Radio enable triggers:
#   - --radio (explicit CLI)
#   - FROGNET_ENABLE_RADIO=1 (env)
#   - /etc/frognet/radio_enable (sentinel file exists)
#
# Optional: specify carrier iface for ham_autoconnect:
#   frognet_bootstrap.sh --radio --iface eth1
#
# Logs:
#   /var/log/frognet_bootstrap.log

RUN_DIR="/run/frognet"
LOG="/var/log/frognet_bootstrap.log"

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

RADIO_SENTINEL="/etc/frognet/radio_enable"
RADIO_IFACE=""

usage() {
  cat >&2 <<EOF
Usage:
  frognet_bootstrap.sh [--radio] [--iface <carrier_iface>]

Radio enable:
  --radio
  OR FROGNET_ENABLE_RADIO=1
  OR touch /etc/frognet/radio_enable

Notes:
  - Core bring-up always runs.
  - Radio bring-up is optional and explicit.
EOF
}

want_radio() {
  if [[ "${FROGNET_ENABLE_RADIO:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -f "$RADIO_SENTINEL" ]]; then
    return 0
  fi
  return 1
}

parse_args() {
  local arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    case "$arg" in
      --radio)
        export FROGNET_ENABLE_RADIO=1
        shift
        ;;
      --iface)
        RADIO_IFACE="${2:-}"
        [[ -n "$RADIO_IFACE" ]] || { echo "--iface requires a value" >&2; exit 2; }
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown argument: $arg" >&2
        usage
        exit 2
        ;;
    esac
  done
}

# Gating helpers (radio phase only)
ham0_has_ipv4() {
  ip -4 addr show dev ham0 2>/dev/null | grep -q 'inet '
}

get_ham_local_ip() {
  ip -4 addr show dev ham0 2>/dev/null | awk '/inet /{print $2}' | head -n1 | cut -d/ -f1
}

calc_peer_ip_same_30() {
  local ip="$1"
  local base="${ip%.*}"
  local last="${ip##*.}"
  [[ "$last" == "1" ]] && echo "${base}.2" && return
  [[ "$last" == "2" ]] && echo "${base}.1" && return
  echo ""
}

gate_peer_daemon() {
  local local_h peer_h
  local_h="$(get_ham_local_ip || true)"
  [[ -n "$local_h" ]] || { log "ERROR: ham0 has no IPv4"; return 1; }
  peer_h="$(calc_peer_ip_same_30 "$local_h" || true)"
  [[ -n "$peer_h" ]] || { log "ERROR: cannot infer peer ham0 IP from $local_h"; return 1; }

  log "Gate: TCP connect to peer daemon ($peer_h:9009) with retries (45s timeout)"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if timeout 45 bash -c "cat </dev/null >/dev/tcp/${peer_h}/9009" >/dev/null 2>&1; then
      log "OK: peer daemon reachable"
      return 0
    fi
    sleep 1
  done
  log "ERROR: peer daemon not reachable on $peer_h:9009"
  return 1
}

main() {
  must_root
  parse_args "$@"

  log "=== FrogNet bootstrap begin ==="

  # 1) Core bring-up (NO radio)
  if [[ ! -x /usr/local/bin/frognet_up_clean.sh ]]; then
    log "ERROR: /usr/local/bin/frognet_up_clean.sh missing"
    exit 1
  fi

  /usr/local/bin/frognet_up_clean.sh

  # 2) Optional radio bring-up
  if want_radio; then
    log "Radio enable requested: bringing up overlay via ham_autoconnect.sh"

    if [[ ! -x /usr/local/bin/ham_autoconnect.sh ]]; then
      log "ERROR: ham_autoconnect.sh not found/executable"
      exit 1
    fi

    # IMPORTANT: radio setup is external to FrogNet semantics.
    # ham_autoconnect is responsible for creating ham0 and installing only the remote FrogNet routes.
    if [[ -n "$RADIO_IFACE" ]]; then
      /usr/local/bin/ham_autoconnect.sh "$RADIO_IFACE"
    else
      /usr/local/bin/ham_autoconnect.sh
    fi

    if ! ip link show ham0 >/dev/null 2>&1; then
      log "ERROR: ham0 not created by ham_autoconnect"
      exit 1
    fi
    if ! ham0_has_ipv4; then
      log "ERROR: ham0 has no IPv4 after ham_autoconnect"
      ip -4 addr show dev ham0 | tee -a "$LOG" >&2 || true
      exit 1
    fi

    # Re-apply iptables to ensure any radio-related policies are present (safe/idempotent)
    if [[ -x /etc/setup_iptables ]]; then
      /etc/setup_iptables || { log "ERROR: /etc/setup_iptables failed after radio bring-up"; exit 1; }
    fi

    # Gate daemon reachability over the overlay peer (radio path correctness)
    gate_peer_daemon || exit 1

    log "Radio bring-up complete"
  else
    log "Radio not enabled (core FrogNet only)."
  fi

  log "=== FrogNet bootstrap complete ==="
}

main "$@"
