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
# /usr/local/bin/frognet_echo_cached.bash
#
# Usage:
#   frognet_echo_cached.bash <dev> <url> <key_ip>
#
# Behavior:
#   - Cache per key_ip in /etc/sentinels/frognet_echo_cache.tsv
#   - If cache hit and not stale, return cached echo
#   - Otherwise, perform a curl (best-effort) and cache the result if valid
#
# IMPORTANT:
#   - This script MUST actually attempt the HTTP call.
#   - It MUST NOT require port 9009 to be up.
#   - For topology-accurate discovery, we bind to the provided interface
#     using --interface <dev>, unless disabled.
#
# SLOW-LINK FIX (NEW):
#   - A failed probe does NOT immediately mean "dead".
#   - Failures are marked as PENDING for up to 4 seconds.
#   - While pending, we suppress repeated probes and upstream deletion.
#
# Sentinel helper:
#   /usr/local/bin/discovery_pending.sh
#


DEV="${1:-}"
URL="${2:-}"
KEY_IP="${3:-}"

CACHE_FILE="/etc/sentinels/frognet_echo_cache.tsv"
PENDING_HELPER="/usr/local/bin/discovery_pending.sh"

mkdir -p /etc/sentinels
touch "$CACHE_FILE"

[[ -z "$DEV" || -z "$URL" || -z "$KEY_IP" ]] && exit 2

TTL_SEC="${FROGNET_ECHO_TTL_SEC:-30}"
NO_BIND="${FROGNET_NO_INTERFACE_BIND:-0}"

now="$(date +%s)"

# ------------------------------------------------------------
# Echo validation
# ------------------------------------------------------------
is_valid_echo() {
  local s="${1:-}"
  [[ -z "$s" ]] && return 1
  s="$(echo "$s" | tr -d '\r' | tr -d '\n')"
  local commas
  commas="$(echo "$s" | awk -F',' '{print NF-1}')"
  [[ "$commas" -lt 3 ]] && return 1
  local f1 f2 f3 f4
  IFS=',' read -r f1 f2 f3 f4 <<< "$s"
  f1="$(echo "$f1" | xargs)"
  f2="$(echo "$f2" | xargs)"
  [[ -z "$f1" || -z "$f2" ]] && return 1
  [[ "$f2" == 10.* ]] || return 1
  return 0
}

# ------------------------------------------------------------
# Cache helpers
# ------------------------------------------------------------
get_cached() {
  awk -F'\t' -v k="$KEY_IP" -v now="$now" -v ttl="$TTL_SEC" '
    $1==k && (now-$2) <= ttl {print $3; exit}
  ' "$CACHE_FILE" 2>/dev/null || true
}

# ------------------------------------------------------------
# Fast path: valid cached echo
# ------------------------------------------------------------
cached="$(get_cached || true)"
if is_valid_echo "$cached"; then
  echo "$cached"
  exit 0
fi

# ------------------------------------------------------------
# NEW: pending suppression
# ------------------------------------------------------------
if [[ -x "$PENDING_HELPER" ]]; then
  if "$PENDING_HELPER" is_pending "$KEY_IP"; then
    # Still within slow-link grace window → do nothing
    exit 1
  fi
fi

# ------------------------------------------------------------
# Live probe (best effort)
# ------------------------------------------------------------
bind_args=()
if [[ "$NO_BIND" != "1" ]]; then
  bind_args=(--interface "$DEV")
fi

out="$(curl -fsS --connect-timeout 10 --max-time 45 "${bind_args[@]}" "$URL" 2>/dev/null || true)"
out="$(echo "$out" | tr -d '\r' | tr -d '\n')"

if is_valid_echo "$out"; then
  # Success → clear pending, update cache
  if [[ -x "$PENDING_HELPER" ]]; then
    "$PENDING_HELPER" clear "$KEY_IP" || true
  fi

  tmp="$(mktemp)"
  awk -F'\t' -v k="$KEY_IP" '$1!=k {print}' "$CACHE_FILE" > "$tmp" 2>/dev/null || true
  printf "%s\t%s\t%s\n" "$KEY_IP" "$now" "$out" >> "$tmp"
  mv "$tmp" "$CACHE_FILE"

  echo "$out"
  exit 0
fi

# ------------------------------------------------------------
# Failure: mark pending (do NOT declare dead yet)
# ------------------------------------------------------------
if [[ -x "$PENDING_HELPER" ]]; then
  "$PENDING_HELPER" mark "$KEY_IP" || true
fi

exit 1
