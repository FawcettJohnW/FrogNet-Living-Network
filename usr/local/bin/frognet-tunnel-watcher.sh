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
# frognet-tunnel-watcher.sh — Wait for remote peers, then merge
#
# Launched by the registration script when this is the first node in a group
# (no remote_subnets yet). Polls the broker every POLL_SEC seconds. When a
# remote FrogNet appears, installs routes, updates the state file, runs
# discovery merge, and exits.
#
# Usage:
#   frognet-tunnel-watcher.sh <wg_iface>
#
# Runs as a background process. PID saved to /var/run/frognet-watcher-<iface>.pid
# so it can be killed on teardown.
# =============================================================================

IFACE="${1:?Usage: frognet-tunnel-watcher.sh <wg_iface>}"
STATE_DIR="/var/lib/frognet-tunnel"
STATE_FILE="${STATE_DIR}/${IFACE}.json"
PIDFILE="/var/run/frognet-watcher-${IFACE}.pid"
POLL_SEC=30
MAX_WAIT=3600   # give up after 1 hour (user can re-run ConnectFamily)

log() {
    echo "[tunnel-watcher][$(date '+%Y-%m-%d %H:%M:%S')] $*" | logger -t frognet-tunnel-watcher
    echo "[tunnel-watcher][$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

cleanup() {
    rm -f "$PIDFILE"
    log "Watcher exiting for ${IFACE}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

if [[ ! -f "$STATE_FILE" ]]; then
    log "FATAL: No state file at ${STATE_FILE}"
    exit 1
fi

BROKER_URL=$(jq -r '.broker_url // empty' "$STATE_FILE")
GROUP_TOKEN=$(jq -r '.group_token // empty' "$STATE_FILE")

if [[ -z "$BROKER_URL" || -z "$GROUP_TOKEN" ]]; then
    log "FATAL: Missing broker_url or group_token in ${STATE_FILE}"
    exit 1
fi

# Read our local subnets so we can filter them out
LOCAL_SUBNETS=$(jq -r '.local_subnets[]?' "$STATE_FILE")

# Save PID
echo $$ > "$PIDFILE"
log "Watching for remote peers on ${IFACE} (poll every ${POLL_SEC}s, max ${MAX_WAIT}s)"

# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

elapsed=0
while (( elapsed < MAX_WAIT )); do
    sleep "$POLL_SEC"
    elapsed=$(( elapsed + POLL_SEC ))

    # Check interface is still up
    if ! ip link show "$IFACE" >/dev/null 2>&1; then
        log "Interface ${IFACE} is down — stopping watcher"
        exit 1
    fi

    # Query broker for group status
    response=$(curl -sS -w "%{http_code}" -o /dev/stdout \
        -H "Authorization: Bearer ${GROUP_TOKEN}" \
        "${BROKER_URL}/api/v1/groups/${GROUP_TOKEN}/status" 2>/dev/null) || continue

    http_code="${response: -3}"
    body="${response:0:${#response}-3}"

    if [[ "$http_code" != "200" ]]; then
        log "Broker returned HTTP ${http_code} — will retry"
        continue
    fi

    # Extract remote /24 subnets, excluding our own
    remote_subnets=$(echo "$body" | jq -r '
        [.tunnels[]?.subnets[]? // empty]
        | map(select(endswith("/24")))
        | unique
        | .[]
    ' 2>/dev/null || true)

    filtered=""
    for rsub in $remote_subnets; do
        is_ours=0
        for lsub in $LOCAL_SUBNETS; do
            [[ "$rsub" == "$lsub" ]] && { is_ours=1; break; }
        done
        [[ "$is_ours" == "0" ]] && filtered="${filtered}${rsub} "
    done
    filtered="${filtered% }"

    # Nothing yet — keep polling
    [[ -z "$filtered" ]] && continue

    # =========================================================================
    # PEER FOUND
    # =========================================================================

    log "Remote FrogNet(s) detected: ${filtered}"

    # Install routes
    for rsub in $filtered; do
        log "Installing route: ${rsub} dev ${IFACE}"
        ip route replace "${rsub}" dev "${IFACE}" || log "WARNING: route install failed for ${rsub}"
    done

    # Build JSON array and update state file
    json_arr=$(printf '%s\n' $filtered | jq -R . | jq -s .)
    tmp=$(mktemp)
    jq --argjson rs "$json_arr" \
       --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
       '.remote_subnets = $rs | .updated = $ts' \
       "$STATE_FILE" > "$tmp"
    mv "$tmp" "$STATE_FILE"

    # Flush discovery cache for remote gateways so Seed 7 isn't blocked
    for rsub in $filtered; do
        gw="${rsub%.*}.1"
        /usr/local/bin/frognet_discovery_cache.sh reset "$gw" 2>/dev/null || true
    done

    # Run merge
    log "Triggering discovery merge..."
    /usr/local/bin/runMerge.bash 2>&1 | logger -t frognet-tunnel-merge &

    log "Merge triggered — watcher complete"
    exit 0
done

log "Timed out after ${MAX_WAIT}s — no remote peers found. Re-run ConnectFamily when ready."
exit 1
