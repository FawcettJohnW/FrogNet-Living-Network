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
# /usr/local/bin/discovery_pending.sh
#
# Persistent pending state for discovery (across runs).
#
# Usage:
#   discovery_pending.sh mark <ip>
#   discovery_pending.sh clear <ip>
#   discovery_pending.sh is_pending <ip>
#
# Storage:
#   /etc/sentinels/discovery_pending.tsv
#     <ip>\t<epoch>
#
# Contract:
#   - mark: records the IP and timestamp, forks runMerge so merge
#           may re-run later.
#   - clear: removes the IP entry.
#   - is_pending: returns 0 if present, 1 otherwise.

STATE_DIR="/etc/sentinels"
STATE_FILE="${STATE_DIR}/discovery_pending.tsv"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

cmd="${1:-}"
ip="${2:-}"

is_ip() { [[ "${1:-}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }

tmpfile() { mktemp "${STATE_DIR}/discovery_pending.tsv.XXXXXX"; }

case "$cmd" in
  mark)
    is_ip "$ip" || exit 2
    ts="$(date +%s)"

    tmp="$(tmpfile)"
    {
      awk -F'\t' -v ip="$ip" '$1!=ip && NF>=2 {print $0}' "$STATE_FILE" 2>/dev/null || true
      printf "%s\t%s\n" "$ip" "$ts"
    } | sort -u >"$tmp"
    mv -f "$tmp" "$STATE_FILE"

    # Request a merge re-run to pick up this pending IP.
    # runMerge's lock handles dedup — never touch runAgain directly.
    /usr/local/bin/runMerge.bash &
    exit 0
    ;;

  clear)
    is_ip "$ip" || exit 2
    tmp="$(tmpfile)"
    awk -F'\t' -v ip="$ip" '$1!=ip && NF>=2 {print $0}' "$STATE_FILE" 2>/dev/null >"$tmp" || true
    mv -f "$tmp" "$STATE_FILE"
    exit 0
    ;;

  is_pending)
    is_ip "$ip" || exit 2
    awk -F'\t' -v ip="$ip" '$1==ip {found=1} END{exit(found?0:1)}' "$STATE_FILE" 2>/dev/null
    ;;

  *)
    echo "Usage: $0 {mark|clear|is_pending} <ip>" >&2
    exit 2
    ;;
esac
