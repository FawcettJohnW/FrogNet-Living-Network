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
# /usr/local/bin/frognet_tunnel_check.sh
#
# Checks every wg* tunnel on the machine. Three failure tests:
#   1. Interface doesn't exist (config present, no interface)
#   2. Interface exists but handshake is stale (>150s)
#   3. Interface exists, handshake fresh, but can't echo remote host
#
# On any failure: wg-quick down the tunnel.
# The tunnel daemon's next poll cycle will bring it back up.
#
# Usage: frognet_tunnel_check.sh [--dry-run] [--verbose]
#

set -eu

DRY_RUN=false
VERBOSE=false
HANDSHAKE_MAX=150  # seconds — WG keepalive is 25s, so 150 = 6 missed
ECHO_TIMEOUT=5     # seconds to wait for frognet_echo

for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=true ;;
    --verbose)  VERBOSE=true ;;
    *)          echo "Usage: $0 [--dry-run] [--verbose]"; exit 1 ;;
  esac
done

log()  { echo "[tunnel_check][$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
vlog() { $VERBOSE && log "$*" || true; }

NOW=$(date +%s)
DOWN_COUNT=0
OK_COUNT=0
TOTAL=0

# Find all WG interfaces the tunnel daemon could own.
# Source 1: running interfaces from the kernel
# Source 2: config files in /etc/wireguard (may exist without interface)
declare -A IFACES
for iface in $(wg show interfaces 2>/dev/null); do
  IFACES[$iface]=1
done
for conf in /etc/wireguard/wg*.conf; do
  [[ -f "$conf" ]] || continue
  iface="$(basename "$conf" .conf)"
  IFACES[$iface]=1
done

if [[ ${#IFACES[@]} -eq 0 ]]; then
  log "NO_TUNNELS — nothing to check"
  exit 0
fi

for iface in $(printf '%s\n' "${!IFACES[@]}" | sort -V); do
  TOTAL=$((TOTAL + 1))
  reason=""

  # ── Test 1: Does the interface exist? ──────────────────────
  if ! ip link show "$iface" &>/dev/null; then
    reason="NO_INTERFACE"
  fi

  # ── Test 2: Is the handshake fresh? ────────────────────────
  if [[ -z "$reason" ]]; then
    handshake_ts=$(wg show "$iface" latest-handshakes 2>/dev/null \
                   | awk '{print $2}' | head -1)
    if [[ -z "$handshake_ts" || "$handshake_ts" == "0" ]]; then
      reason="NO_HANDSHAKE"
    else
      age=$(( NOW - handshake_ts ))
      if (( age > HANDSHAKE_MAX )); then
        reason="STALE_HANDSHAKE age=${age}s"
      else
        vlog "OK_HANDSHAKE iface=$iface age=${age}s"
      fi
    fi
  fi

  # ── Test 3: Can we echo the remote FrogNet host? ───────────
  if [[ -z "$reason" ]]; then
    # Derive remote host from routes through this interface.
    # Look for a 10.x.x.0/24 route (the FrogNet subnet), target is .1
    remote_host=""
    while read -r net _via gw _dev _d rest; do
      # Match 10.x.x.0/24 routes, skip transit 10.253.x.x
      if [[ "$net" =~ ^10\. && "$net" =~ /24$ && ! "$net" =~ ^10\.253\. ]]; then
        remote_host="${net%%.0/24}.1"
        break
      fi
    done < <(ip route show dev "$iface" 2>/dev/null)

    # Fallback: parse AllowedIPs from wg show
    if [[ -z "$remote_host" ]]; then
      while read -r subnet; do
        if [[ "$subnet" =~ ^10\. && "$subnet" =~ /24$ && ! "$subnet" =~ ^10\.253\. ]]; then
          remote_host="${subnet%%.0/24}.1"
          break
        fi
      done < <(wg show "$iface" allowed-ips 2>/dev/null | awk '{for(i=2;i<=NF;i++) print $i}')
    fi

    if [[ -z "$remote_host" ]]; then
      reason="NO_REMOTE_HOST (can't derive target to echo)"
    else
      echo_result=$(curl -sS --max-time "$ECHO_TIMEOUT" \
                    "http://${remote_host}/frognet_echo.php" 2>/dev/null) || echo_result=""
      if [[ -z "$echo_result" ]]; then
        reason="ECHO_FAILED host=$remote_host"
      else
        vlog "OK_ECHO iface=$iface host=$remote_host"
      fi
    fi
  fi

  # ── Verdict ────────────────────────────────────────────────
  if [[ -n "$reason" ]]; then
    DOWN_COUNT=$((DOWN_COUNT + 1))
    if $DRY_RUN; then
      log "WOULD_DOWN iface=$iface reason=$reason"
    else
      log "DOWN iface=$iface reason=$reason"
      # Remove FrogNet routes through this interface (installed by poll.py / runMerge,
      # not by wg-quick, so wg-quick down won't clean them up)
      while read -r route_net _rest; do
        [[ "$route_net" =~ ^10\. ]] || continue
        ip route del "$route_net" dev "$iface" 2>/dev/null && \
          log "  ROUTE_DEL $route_net dev $iface" || true
      done < <(ip route show dev "$iface" 2>/dev/null)
      # Tear down the interface
      wg-quick down "$iface" 2>/dev/null || true
      # Remove the config so the daemon treats this as a fresh bring-up
      if [[ -f "/etc/wireguard/${iface}.conf" ]]; then
        rm -f "/etc/wireguard/${iface}.conf"
        log "  CONF_DEL /etc/wireguard/${iface}.conf"
      fi
    fi
  else
    OK_COUNT=$((OK_COUNT + 1))
    vlog "OK iface=$iface"
  fi
done

log "SUMMARY total=$TOTAL ok=$OK_COUNT down=$DOWN_COUNT dry_run=$DRY_RUN"
exit 0
