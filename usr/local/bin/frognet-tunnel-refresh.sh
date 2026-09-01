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
# =============================================================================
# frognet-tunnel-refresh.sh — Refresh remote subnet list from the broker
#
# Queries the broker for the current group membership and updates the local
# tunnel state file with the latest remote subnets. Run this periodically
# (cron) so discovery finds newly-joined FrogNets without re-registration.
#
# Usage:
#   frognet-tunnel-refresh.sh [wg_iface]
#
# If wg_iface is omitted, refreshes all tunnels in /var/lib/frognet-tunnel/.
# =============================================================================

trap 'echo "FATAL: Error on line $LINENO, exit code $?" >&2; exit 1' ERR

STATE_DIR="/var/lib/frognet-tunnel"

refresh_one() {
    local state_file="$1"
    local iface broker_url group_token

    iface=$(jq -r '.interface // empty' "$state_file")
    broker_url=$(jq -r '.broker_url // empty' "$state_file")
    group_token=$(jq -r '.group_token // empty' "$state_file")

    if [[ -z "$iface" || -z "$broker_url" || -z "$group_token" ]]; then
        echo "SKIP: Incomplete state file: $state_file" >&2
        return 1
    fi

    # Check interface is up
    if ! ip link show "$iface" >/dev/null 2>&1; then
        echo "SKIP: Interface $iface is not up" >&2
        return 1
    fi

    # Query broker for group status
    local response http_code body
    response=$(curl -sS -w "%{http_code}" -o /dev/stdout \
        -H "Authorization: Bearer ${group_token}" \
        "${broker_url}/api/v1/groups/${group_token}/status" 2>&1) || {
        echo "ERROR: Failed to reach broker at ${broker_url}" >&2
        return 1
    }

    http_code="${response: -3}"
    body="${response:0:${#response}-3}"

    if [[ "$http_code" != "200" ]]; then
        echo "ERROR: Broker returned HTTP $http_code for $iface" >&2
        return 1
    fi

    # Extract our local subnets for filtering
    local local_subnets
    local_subnets=$(jq -r '.local_subnets[]?' "$state_file")

    # Extract all remote /24 subnets, excluding our own
    local remote_subnets
    remote_subnets=$(echo "$body" | jq -r '
        [.tunnels[]?.subnets[]? // empty]
        | map(select(endswith("/24")))
        | unique
        | .[]
    ' 2>/dev/null || true)

    # Filter out our own subnets
    local filtered=""
    for rsub in $remote_subnets; do
        local is_ours=0
        for lsub in $local_subnets; do
            [[ "$rsub" == "$lsub" ]] && { is_ours=1; break; }
        done
        [[ "$is_ours" == "0" ]] && filtered="${filtered}${rsub}\n"
    done

    # Build JSON array
    local json_arr
    json_arr=$(printf '%b' "$filtered" | sed '/^$/d' | jq -R . | jq -s .)

    # Update state file — preserve everything, update remote_subnets + updated timestamp
    local tmp
    tmp=$(mktemp)
    jq --argjson rs "$json_arr" \
       --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
       '.remote_subnets = $rs | .updated = $ts' \
       "$state_file" > "$tmp"
    mv "$tmp" "$state_file"

    # Determine our local FrogNet IP for src parameter
    local local_frognet_ip
    local_frognet_ip=$(jq -r '.local_subnets[0]? // empty' "$state_file" | sed 's|\.0/24$|.1|')
    if [[ -z "$local_frognet_ip" ]]; then
        echo "WARNING: No local_subnets in $state_file — routes will lack src" >&2
    fi

    # Install/update routes for remote subnets
    for rsub in $(echo "$json_arr" | jq -r '.[]'); do
        if [[ -n "$local_frognet_ip" ]]; then
            ip route replace "${rsub}" dev "$iface" src "$local_frognet_ip"
        else
            ip route replace "${rsub}" dev "$iface"
        fi
    done

    local count
    count=$(echo "$json_arr" | jq 'length')
    echo "OK: $iface — $count remote subnets"
    if [[ "$count" -gt 0 ]]; then
        echo "$json_arr" | jq -r '.[]' | sed 's/^/  /'
    fi
}

# ---------------------------------------------------------------------------

if [[ $# -gt 0 ]]; then
    # Refresh a specific interface
    state="${STATE_DIR}/${1}.json"
    if [[ ! -f "$state" ]]; then
        echo "ERROR: No state file for interface $1 at $state" >&2
        exit 1
    fi
    refresh_one "$state"
else
    # Refresh all tunnels
    found=0
    for state in "${STATE_DIR}"/wg*.json; do
        [[ -f "$state" ]] || continue
        found=1
        refresh_one "$state" || true
    done
    if [[ "$found" == "0" ]]; then
        echo "No tunnel state files found in $STATE_DIR"
    fi
fi
