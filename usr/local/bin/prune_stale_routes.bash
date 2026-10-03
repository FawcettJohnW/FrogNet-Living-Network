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
# /usr/local/bin/prune_stale_routes.bash
#
# Prune ONLY invalid FrogNet routes.
# Preserve:
#   - eth0 anchor routes (via DHCP lease IP)
#   - eth0 child routes (via parent .1)
#   - wlan routes discovered by sync_interfaces
#
# Full paths only. No strict mode.

# set -x

IP=/usr/sbin/ip
AWK=/usr/bin/awk
GREP=/usr/bin/grep
SORT=/usr/bin/sort
ECHO=/usr/bin/echo
CUT=/usr/bin/cut
TEST=/usr/bin/test

SENT_DIR="/etc/sentinels"
EXPECTED="${SENT_DIR}/expected_routes"
LEASES="/var/lib/misc/dnsmasq.leases"

$TEST -f "$EXPECTED" || exit 0
$TEST -s "$EXPECTED" || exit 0

# -------------------------------------------------------------------
# Collect eth0 DHCP lease IPs (these are VALID anchor vias)
# -------------------------------------------------------------------
ETH0_LEASES="$(
  $AWK '{print $3}' "$LEASES" 2>/dev/null \
  | $GREP '^10\.' \
  | $SORT -u
)"

is_eth0_lease() {
  local ip="$1"
  $ECHO "$ETH0_LEASES" | $GREP -qx "$ip"
}

# -------------------------------------------------------------------
# Build WANT set from expected_routes
# key = cidr|via|dev
# -------------------------------------------------------------------
declare -A WANT

while read -r line; do
  [[ -z "$line" ]] && continue
  cidr="$($AWK '{print $1}' <<<"$line")"
  via="$($AWK '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$line")"
  dev="$($AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$line")"
  [[ -n "$cidr" && -n "$via" && -n "$dev" ]] || continue
  WANT["$cidr|$via|$dev"]=1
done < <($AWK NF "$EXPECTED")

# -------------------------------------------------------------------
# Walk current FrogNet routes and prune safely
# -------------------------------------------------------------------
$IP route show | $AWK '$1 ~ /^10\./ {print}' | while read -r line; do
  cidr="$($AWK '{print $1}' <<<"$line")"

  # Never touch kernel routes
  $GREP -q 'proto kernel' <<<"$line" && continue

  via="$($AWK '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$line")"
  dev="$($AWK '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$line")"

  [[ -n "$cidr" && -n "$dev" ]] || continue

  key="$cidr|$via|$dev"

  # ---------------------------------------------------------------
  # KEEP RULES (CRITICAL)
  # ---------------------------------------------------------------

  # 1. Explicitly wanted
  [[ -n "${WANT[$key]:-}" ]] && continue

  # 2. eth0 anchor route (via DHCP lease)
  if [[ "$dev" == "eth0" ]] && is_eth0_lease "$via"; then
    continue
  fi

  # 3. eth0 child route (via parent FrogNet .1)
  if [[ "$dev" == "eth0" ]] && [[ "$via" =~ ^10\. ]]; then
    continue
  fi

  # 4. wlan routes (sync_interfaces manages them)
  if [[ "$dev" == wlan* ]]; then
    continue
  fi

  # ---------------------------------------------------------------
  # DELETE ONLY IF NONE OF THE ABOVE
  # ---------------------------------------------------------------
  $ECHO "Pruning stale route: $line"
  if [[ -n "$via" ]]; then
    $IP route del "$cidr" via "$via" dev "$dev" 2>/dev/null || true
  else
    $IP route del "$cidr" dev "$dev" 2>/dev/null || true
  fi
done

exit 0
