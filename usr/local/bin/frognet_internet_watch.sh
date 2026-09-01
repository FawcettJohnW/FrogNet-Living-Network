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
# frognet_internet_watch.bash
#
# Called at the top of every runMerge.  Detects whether this node has
# *direct* internet (a non-10.x global address AND 8.8.8.8 reachable from
# that interface) and takes the appropriate action when state changes:
#
#   direct  -> register with broker + start frognet-tunnel-daemon-v3
#   lan_only -> deregister, stop+disable daemon, tear down wg[0-9]+
#
# Idempotent: no-op if state hasn't changed.
#
# Sentinel: /etc/sentinels/internet_state  ("direct" or "lan_only")
# Lock:     /etc/sentinels/internet_state.lock
#
# Exit codes:
#   0  no-op or successful state transition
#   1  state-transition action failed
# =============================================================================

set -u

SENTINEL_DIR="/etc/sentinels"
STATE_FILE="${SENTINEL_DIR}/internet_state"
LOCK_FILE="${SENTINEL_DIR}/internet_state.lock"
STATE_DIR="/var/lib/frognet-tunnel"
PUBKEY_FILE="${STATE_DIR}/node_public.key"
# [ONE_CONF_V1] broker.conf folded into tunnel.conf.
BROKER_CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"

PING_TARGET="8.8.8.8"
PING_COUNT=2
PING_TIMEOUT=3

TS() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[internet_watch][$(TS)] $*"; }

mkdir -p "$SENTINEL_DIR"

# ---------------------------------------------------------------------------
# Acquire lock.  Non-blocking — if another merge is already evaluating,
# just exit.  It'll re-evaluate on the next merge anyway.
# ---------------------------------------------------------------------------
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "another instance holds the lock, skipping"
    exit 0
fi

# ---------------------------------------------------------------------------
# has_direct_internet: echo "direct" or "lan_only"
#
# A node has direct internet iff:
#   (a) some interface has a non-10.x, non-127.x, non-169.254.x,
#       non-100.64.x CGNAT global address, AND
#   (b) ping 8.8.8.8 succeeds from that specific interface.
# ---------------------------------------------------------------------------
has_direct_internet() {
    local line iface addr rc
    while read -r line; do
        # "1: eth0    inet 10.102.30.1/24 ..."
        iface=$(echo "$line" | awk '{print $2}')
        addr=$(echo "$line" | awk '{print $4}' | cut -d/ -f1)

        [[ -z "$addr" || -z "$iface" ]] && continue
        [[ "$addr" =~ ^10\. ]]           && continue
        [[ "$addr" =~ ^127\. ]]          && continue
        [[ "$addr" =~ ^169\.254\. ]]     && continue
        [[ "$addr" =~ ^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\. ]] && continue  # CGNAT
        # Skip tunnel/virtual interfaces even if somehow non-10 (paranoia)
        [[ "$iface" =~ ^(wg[0-9]+|frognet[0-9]+|lo|docker|veth)$ ]] && continue

        log "testing: iface=$iface addr=$addr"
        if ping -c "$PING_COUNT" -W "$PING_TIMEOUT" -I "$iface" "$PING_TARGET" >/dev/null 2>&1; then
            log "PASS: $iface ($addr) reached $PING_TARGET"
            echo "direct"
            return
        else
            log "FAIL: $iface ($addr) could not reach $PING_TARGET"
        fi
    done < <(ip -4 -o addr show scope global)

    echo "lan_only"
}

# ---------------------------------------------------------------------------
# go_direct: idempotently ensure registered + daemon running
# ---------------------------------------------------------------------------
go_direct() {
    log "ACTION: transitioning to DIRECT-internet state"

    if [[ ! -f "$BROKER_CONF" ]]; then
        log "  no $BROKER_CONF — cannot register (setup_lillypad needs --broker)"
        return 1
    fi

    if [[ ! -x /usr/local/bin/frognet-tunnel-setup-v3.sh ]]; then
        log "  /usr/local/bin/frognet-tunnel-setup-v3.sh missing or not executable"
        return 1
    fi

    log "  running frognet-tunnel-setup-v3.sh (idempotent register)"
    if /usr/local/bin/frognet-tunnel-setup-v3.sh 2>&1 | sed 's/^/    /'; then
        log "  setup_v3 completed"
    else
        log "  setup_v3 failed"
        return 1
    fi

    log "  enabling + starting frognet-tunnel-daemon-v3"
    systemctl enable  frognet-tunnel-daemon-v3 2>/dev/null || true
    systemctl start   frognet-tunnel-daemon-v3 2>/dev/null || {
        log "  failed to start daemon"
        return 1
    }

    return 0
}

# ---------------------------------------------------------------------------
# go_lan_only: idempotently deregister + stop daemon + tear down tunnels
# ---------------------------------------------------------------------------
go_lan_only() {
    log "ACTION: transitioning to LAN-ONLY state"

    local broker_url pubkey resp status
    broker_url=""
    pubkey=""
    # [ONE_CONF_V1] BROKER_CONF is tunnel.conf now (see top-of-file assignment).
    [[ -f "$BROKER_CONF" ]] && broker_url=$(grep '^BROKER_URL=' "$BROKER_CONF" | cut -d= -f2- | tr -d '[:space:]' || true)
    [[ -f "$PUBKEY_FILE" ]] && pubkey=$(tr -d '[:space:]' < "$PUBKEY_FILE" || true)

    # 1. Deregister from broker (best effort — broker may be unreachable
    # precisely because we've lost internet)
    if [[ -n "$broker_url" && -n "$pubkey" ]]; then
        log "  deregistering pubkey from broker: $broker_url"
        # [DEREGISTER_V4_V1] was /api/v1/deregister - no broker serves v1; the live
        # broker serves /api/v4/deregister, which destroys this node's tunnels
        # broker-side and frees its IP allocation. Needs pubkey (400 without).
        resp=$(curl -sk -m 15 -X POST "${broker_url%/}/api/v4/deregister" \
            -H "Content-Type: application/json" \
            -d "{\"pubkey\": \"${pubkey}\"}" 2>&1 || true)
        # v4 /api/v4/deregister returns the SAME {"status":...} shape v1 did:
        # "deregistered" | "not_registered". Only the URL version changed.
        status=$(echo "$resp" | jq -r '.status // empty' 2>/dev/null || true)
        case "$status" in
            deregistered)   log "    broker confirmed deregistration" ;;
            not_registered) log "    broker: not currently registered" ;;
            "")             log "    broker unreachable (expected if internet just died): $resp" ;;
            *)              log "    broker response: $resp" ;;
        esac
    fi

    # 2. Stop + disable the tunnel daemon
    for unit in frognet-tunnel-daemon-v3 frognet-tunnel-daemon; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}\."; then
            log "  stopping and disabling $unit"
            systemctl stop    "$unit" 2>/dev/null || true
            systemctl disable "$unit" 2>/dev/null || true
        fi
    done

    # 3. Tear down every wg[0-9]+ interface and remove its persistent config
    local iface
    log "  tearing down WireGuard tunnels"
    for iface in $(wg show interfaces 2>/dev/null); do
        [[ "$iface" =~ ^wg[0-9]+$ ]] || continue
        wg-quick down "$iface" 2>/dev/null || true
        ip link show "$iface" >/dev/null 2>&1 && ip link delete "$iface" 2>/dev/null || true
        log "    tore down $iface"
    done

    local conf
    for conf in /etc/wireguard/wg[0-9]*.conf; do
        [[ -f "$conf" ]] || continue
        rm -f "$conf"
        log "    removed $conf"
    done

    # 4. Clear daemon active-tunnel state
    if [[ -d "${STATE_DIR}/active" ]]; then
        find "${STATE_DIR}/active" -maxdepth 1 -name '*.json' -type f -delete 2>/dev/null || true
        log "  cleared ${STATE_DIR}/active"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Main
#
# [SOURCEABLE_TEARDOWN_V1] Guard the watch logic so this file can be SOURCED to
# reuse go_lan_only / go_direct without running the auto-detect. frognet-make-
# lanonly.sh sources it to force a teardown on operator command (uplink present
# or not); run directly, it behaves exactly as before.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 0 2>/dev/null || true
fi

current=$(has_direct_internet)
last=""
[[ -f "$STATE_FILE" ]] && last=$(tr -d '[:space:]' < "$STATE_FILE" || true)

log "current=$current  last=${last:-<unset>}"

if [[ "$current" == "$last" ]]; then
    log "no change, exiting"
    exit 0
fi

log "state transition: ${last:-<unset>} -> $current"

rc=0
case "$current" in
    direct)   go_direct   || rc=$? ;;
    lan_only) go_lan_only || rc=$? ;;
    *)        log "unexpected state '$current' — refusing to act"; exit 1 ;;
esac

if (( rc == 0 )); then
    echo "$current" > "$STATE_FILE"
    log "state persisted: $current"
    # [WATCH_TRIGGER_MERGE_V1] Route topology changed -> run a merge. runMerge
    # self-queues (bails + touches runAgain if one is already running), so this
    # works whether called inside a merge or standalone. Backgrounded; never blocks.
    /usr/local/bin/runMerge.bash >/dev/null 2>&1 &
    exit 0
else
    log "action failed (rc=$rc) — NOT persisting state, will retry next merge"
    exit 1
fi
