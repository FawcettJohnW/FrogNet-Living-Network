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
# frognet-unmake-gateway.sh - Return this node to LAN-only (CLI)
#
# [UNMAKE_GATEWAY_CLI_V1] The inverse of frognet-make-gateway.sh. Deregisters the
# node from the broker, clears the broker config, and tears down the WireGuard
# tunnels + tunnel daemon, leaving a working LAN-only FrogNet node.
#
# It performs the same teardown as frognet_internet_watch.sh's go_lan_only
# (which runs automatically when a gateway loses its uplink), but on demand from
# the command line. Order matters: deregister at the broker FIRST (while the
# tunnels still carry the route to it), then tear the tunnels down.
#
# Idempotent: safe to run on a node that is already LAN-only.
#
# Usage:
#   frognet-unmake-gateway.sh              # deregister + clear + tear down
#   frognet-unmake-gateway.sh --keep-config  # tear down tunnels, KEEP BROKER_URL
#                                            #   (node re-enrols on next merge)
#   frognet-unmake-gateway.sh --local-only   # skip broker deregister (offline)
#   -h, --help
# =============================================================================
set -euo pipefail

CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"
CONF_LIB="${FROGNET_CONF_LIB:-/usr/local/lib/frognet/conf.sh}"
HELPER="/usr/local/bin/frognet_setup_v4_helper.bash"
STATE_DIR="/var/lib/frognet-tunnel"
PUBKEY_FILE="${STATE_DIR}/node_public.key"

KEEP_CONFIG=0
LOCAL_ONLY=0

log() { printf '%s [unmake-gateway] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '%s [unmake-gateway] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-config) KEEP_CONFIG=1; shift ;;
        --local-only)  LOCAL_ONLY=1; shift ;;
        -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "must run as root (stops the daemon and tears down WireGuard)"

# --- 1. deregister from the broker, while the route still exists --------------
# Best-effort: the broker may be unreachable (that is often WHY a node is being
# demoted). A failure here does not block local teardown.
if [[ "$LOCAL_ONLY" -eq 0 ]]; then
    broker_url=""; pubkey=""
    [[ -f "$CONF" ]] && broker_url=$(grep '^BROKER_URL=' "$CONF" | cut -d= -f2- | tr -d '[:space:]' || true)
    [[ -f "$PUBKEY_FILE" ]] && pubkey=$(tr -d '[:space:]' < "$PUBKEY_FILE" || true)
    if [[ -n "$broker_url" && -n "$pubkey" ]]; then
        log "deregistering from broker: $broker_url"
        # /api/v4/deregister destroys this node's tunnels broker-side and frees
        # its IP allocation. Idempotent: 200 even if not registered.
        resp=$(curl -sk -m 15 -X POST "${broker_url%/}/api/v4/deregister" \
            -H "Content-Type: application/json" \
            -d "{\"pubkey\": \"${pubkey}\"}" 2>&1 || true)
        status=$(echo "$resp" | jq -r '.status // empty' 2>/dev/null || true)
        case "$status" in
            deregistered)   log "  broker confirmed deregistration" ;;
            not_registered) log "  broker: not currently registered" ;;
            "")             log "  broker unreachable (continuing with local teardown): $resp" ;;
            *)              log "  broker response: $resp" ;;
        esac
    else
        log "no broker URL or pubkey on file - skipping broker deregister"
    fi
else
    log "--local-only: skipping broker deregister"
fi

# --- 2. stop + disable the tunnel daemon -------------------------------------
for unit in frognet-tunnel-daemon-v3 frognet-tunnel-daemon; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}\."; then
        log "stopping and disabling $unit"
        systemctl stop    "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
    fi
done

# --- 3. tear down the WireGuard data plane (frognet_nuke_tunnels) ------------
# [UNMAKE_GATEWAY_NUKE_V1] Use the thorough teardown, not a wg[0-9]+ loop. nuke
# also clears stale /32 routes, the 10.253.253.x transit addresses, and the
# /var/lib/frognet-tunnel state dir -- the leftovers that otherwise linger and
# mislead the next discovery pass. It removes /etc/wireguard/wg*.conf but does
# NOT touch /etc/frognet/tunnel.conf, so the node config is preserved.
NUKE="/usr/local/bin/frognet_nuke_tunnels"
if [[ -x "$NUKE" ]]; then
    log "destroying the WireGuard data plane (frognet_nuke_tunnels)"
    "$NUKE" || log "  nuke reported an issue; inspect the log above"
else
    log "WARNING: $NUKE not found - falling back to wg[0-9]+ teardown"
    for iface in $(wg show interfaces 2>/dev/null || true); do
        [[ "$iface" =~ ^wg[0-9]+$ ]] || continue
        wg-quick down "$iface" 2>/dev/null || true
        ip link show "$iface" >/dev/null 2>&1 && ip link delete "$iface" 2>/dev/null || true
    done
    for conf in /etc/wireguard/wg[0-9]*.conf; do
        [[ -f "$conf" ]] || continue; rm -f "$conf"
    done
fi

# --- 4. clear the broker config (unless --keep-config) -----------------------
# Clearing BROKER_URL is what makes the daemon settle into LAN-only mode on its
# next merge and stops the deferred-enrolment retry from re-registering. The
# membership card (GUID, tokens) is preserved -- clear_broker only unsets the
# broker/pond keys.
if [[ "$KEEP_CONFIG" -eq 1 ]]; then
    log "--keep-config: leaving BROKER_URL in place (node will re-enrol on next merge)"
else
    if [[ -x "$HELPER" ]]; then
        log "clearing broker config"
        "$HELPER" clear_broker >/dev/null 2>&1 || true
    elif [[ -f "$CONF_LIB" ]]; then
        # shellcheck source=/dev/null
        . "$CONF_LIB"
        fn_conf_backup >/dev/null 2>&1 || true
        for k in BROKER_URL POND_NAME GROUP_NAME POND_PASSWORD CHORUSES; do
            fn_conf_unset "$k"
        done
        log "cleared broker config via conf.sh"
    else
        log "WARNING: no helper or conf lib - BROKER_URL not cleared; edit $CONF by hand"
    fi
    # Do not let the deferred-enrolment path immediately re-register.
    rm -f /etc/frognet/pond_bootstrap_done 2>/dev/null || true
fi

# --- 5. fire a network event so discovery re-evaluates NOW -------------------
# [UNMAKE_GATEWAY_MERGE_EVENT_V1] Tearing down the tunnels changed this node's
# routing topology. Run a merge now so discovery notices the tunnels are gone and
# backup routing takes over, and tell directly-attached neighbors so they
# re-evaluate paths that ran through this node. runMerge self-queues, so this is
# the reliable signal; the teardown used to be silent and nothing re-ran discovery.
if [[ -x /usr/local/bin/runMerge.bash ]]; then
    log "firing a merge so discovery re-evaluates the torn-down topology"
    /usr/local/bin/runMerge.bash >/dev/null 2>&1 || true
    [[ -x /usr/local/bin/propogateNotification ]] && /usr/local/bin/propogateNotification 2>/dev/null || true
fi

log "DONE - node is now LAN-only."
if [[ "$KEEP_CONFIG" -eq 1 ]]; then
    log "  Broker config kept; it will attempt to re-enrol on the next network change."
else
    log "  Broker config cleared; it stays LAN-only until you run frognet-make-gateway again."
fi
