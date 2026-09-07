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
# frognet_tunnel_setup.sh — Register this FrogNet node with the broker
#
# Usage:
#   frognet_tunnel_setup.sh [--pond-password <pw>]
#
# Reads broker URL from /etc/frognet/broker.conf (written by setup_lillypad).
# Exits cleanly with an explanatory message if broker.conf is missing or
# the broker is unreachable.
#
# What this script does:
#   1. Generate WireGuard keypair (or reuse existing)
#   2. Detect local 10.x.x.0/24 subnet and node name
#   3. POST /api/v4/register to the broker (auto-joins entire_pond)
#   4. Enable and start frognet-tunnel-daemon
# =============================================================================

set -e
trap 'echo "[tunnel_setup] FATAL: Error on line $LINENO, exit $?" >&2; exit 1' ERR

TS() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[tunnel_setup] $(TS) $*"; }

POND_PASSWORD=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --pond-password) POND_PASSWORD="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

for cmd in wg curl jq ip python3; do
    command -v "$cmd" >/dev/null || { echo "[tunnel_setup] ERROR: '$cmd' not found" >&2; exit 1; }
done
[[ "$(id -u)" -eq 0 ]] || { echo "[tunnel_setup] ERROR: Must run as root" >&2; exit 1; }

# ---------------------------------------------------------------------------
# [BROKER_PIN_V1] / [BROKER_AUTH_V1] Broker trust for the shell register path.
#
# This script registers over plain `curl -sk`, which encrypts to whoever
# answers the broker's name without checking it is the broker, and puts the
# pond password in the POST body. Both halves are fixed here to match the
# daemon (broker_pin.py / broker_auth.py):
#
#   - Pin the broker certificate on first use, then require it to match.
#   - Prove the pond password with an HMAC over a broker nonce instead of
#     sending it as a field.
#
# No CA is involved - FrogNet has no authority to issue or validate certs.
# ---------------------------------------------------------------------------
PIN_DIR="${FROGNET_BROKER_PIN_DIR:-/etc/frognet/broker_pins}"

# Fingerprint the cert the broker is currently presenting (no validation - we
# are learning/comparing a fingerprint, not trusting a chain).
_broker_fingerprint() {
    local url="$1" hostport host port
    hostport="${url#*://}"; hostport="${hostport%%/*}"
    host="${hostport%%:*}"
    port="${hostport##*:}"; [[ "$port" == "$host" ]] && port=443
    echo | openssl s_client -connect "${host}:${port}" -servername "$host" 2>/dev/null \
        | openssl x509 -noout -fingerprint -sha256 2>/dev/null \
        | sed 's/^.*=//; s/://g' | tr 'A-Z' 'a-z'
}

# Enforce the pin. Learns on first use; fails on mismatch. Prints the CA-bundle
# arguments curl should use (empty here - we rely on the pin, checked before
# the request, plus curl's own connection to the same host).
_broker_pin_check() {
    local url="$1" host fp pinfile pinned
    host="${url#*://}"; host="${host%%[:/]*}"
    local port; port="${url#*://}"; port="${port%%/*}"
    case "$port" in *:*) port="${port##*:}";; *) port=443;; esac
    pinfile="${PIN_DIR}/${host}_${port}.sha256"
    fp="$(_broker_fingerprint "$url")"
    [[ -z "$fp" ]] && { log "ERROR: could not read broker certificate at $url"; return 1; }
    if [[ -f "$pinfile" ]]; then
        pinned="$(tr -d '[:space:]' < "$pinfile" | tr 'A-Z' 'a-z')"
        if [[ "$fp" != "$pinned" ]]; then
            log "FATAL: broker cert fingerprint $fp does not match pin $pinned"
            log "       ($pinfile). Refusing to register. If the broker cert was"
            log "       rotated on purpose, remove that file to re-pin."
            return 1
        fi
    else
        mkdir -p "$PIN_DIR"; chmod 700 "$PIN_DIR"
        printf '%s\n' "$fp" > "$pinfile"; chmod 600 "$pinfile"
        log "PINNED broker cert $fp for ${host}:${port} (first use)"
    fi
    return 0
}

# HMAC-SHA256(pond_password, "frognet-register-v1\nnonce\npond\npubkey\nguid").
# Mirrors broker_auth.compute_auth so the daemon and this script prove the
# password identically.
_register_hmac() {
    local pw="$1" nonce="$2" pond="$3" pubkey="$4" guid="$5"
    printf '%s\n%s\n%s\n%s\n%s' \
        "frognet-register-v1" "$nonce" "$pond" "$pubkey" "$guid" \
        | openssl dgst -sha256 -hmac "$pw" -hex 2>/dev/null \
        | sed 's/^.*= *//'
}

# ---------------------------------------------------------------------------
# Direct-upstream gate
#
# Only a node with a DIRECT Internet upstream may register with the broker.
# "Direct upstream" is defined exactly as fixDefaultRoute defines it: a
# default route whose nexthop (via) is NON-10.x.  A LAN-only node's only
# path out is via a 10.x mesh peer, so it has no non-10.x default.
#
# A LAN-only node simply does not register: we return before the POST. We do
# NOT remove broker config to enforce that. Absence of a direct upstream is a
# CURRENT-MOMENT observation - it is false during a carrier bounce, a DHCP
# renew, an interface down event, and every other transient - while deleting
# the config is permanent. Config is also not the thing that causes an
# unserviceable registration; reaching the POST is, and the exit below
# prevents that. Everything else that reads tunnel.conf (the tunnel daemon,
# config.py, pond-bootstrap, runMerge's autoswitch) has its own broker gate
# and needs the file present to work at all.
# ---------------------------------------------------------------------------

# [ONE_CONF_V1] broker.conf folded into tunnel.conf - one config file.
BROKER_CONF="${FROGNET_CONF:-/etc/frognet/tunnel.conf}"
TUNNEL_CONF="/etc/frognet/tunnel.conf"

# True iff there is at least one default route with a non-10.x via.
has_direct_upstream() {
    local line via
    while IFS= read -r line; do
        via="$(awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$line")"
        [[ -n "$via" ]] || continue
        # non-10.x nexthop = a real Internet upstream (per fixDefaultRoute)
        if [[ "$via" != 10.* ]]; then
            return 0
        fi
    done < <(ip route show default | awk 'NF')
    return 1
}

if has_direct_upstream; then
    log "Direct upstream detected (non-10.x default route present) - proceeding with broker registration."
else
    log "No direct upstream (no non-10.x default route) - this node is LAN-only."
    log "Not registering. Broker config left intact at $BROKER_CONF."
    log "The mesh reaches this node via its upstream peer. If this node later"
    log "gains a direct upstream, the next run registers with no re-enrolment."
    exit 0
fi

# ---------------------------------------------------------------------------
# Read broker URL
# ---------------------------------------------------------------------------

if [[ ! -f "$BROKER_CONF" ]]; then
    log "No broker config at $BROKER_CONF."
    log "Run setup_lillypad.bash with --broker <url> to configure online access."
    exit 0
fi

BROKER_URL=$(grep '^BROKER_URL=' "$BROKER_CONF" | cut -d= -f2- | tr -d '[:space:]')
if [[ -z "$BROKER_URL" ]]; then
    log "BROKER_URL not set in $BROKER_CONF — online features disabled."
    exit 0
fi

log "Broker: $BROKER_URL"

# ---------------------------------------------------------------------------
# WireGuard keypair
# ---------------------------------------------------------------------------

STATE_DIR="/var/lib/frognet-tunnel"
PRIVKEY_FILE="${STATE_DIR}/node_private.key"
PUBKEY_FILE="${STATE_DIR}/node_public.key"

mkdir -p "${STATE_DIR}/active"
chmod 700 "$STATE_DIR"

if [[ -f "$PRIVKEY_FILE" && -f "$PUBKEY_FILE" ]]; then
    log "WireGuard keypair exists"
else
    log "Generating WireGuard keypair"
    umask 077
    wg genkey | tee "$PRIVKEY_FILE" | wg pubkey > "$PUBKEY_FILE"
    chmod 600 "$PRIVKEY_FILE"
    chmod 644 "$PUBKEY_FILE"
fi

PUBKEY=$(cat "$PUBKEY_FILE")
log "Public key: $PUBKEY"

# ---------------------------------------------------------------------------
# Detect local identity
# ---------------------------------------------------------------------------

NODE_NAME=""
for conf in /etc/dnsmasq.d/*.conf; do
    [[ -f "$conf" ]] || continue
    NODE_NAME=$(grep -m1 '^domain=' "$conf" 2>/dev/null | cut -d= -f2) || true
    [[ -n "$NODE_NAME" ]] && break
done
[[ -n "$NODE_NAME" ]] || { log "ERROR: No domain= found in /etc/dnsmasq.d/*.conf"; exit 1; }

# Detect local subnet — any 10.x.x.x excluding transit (10.253) and chorus (10.254)
LOCAL_SUBNET=""
while IFS= read -r line; do
    if echo "$line" | grep -q "inet 10\." \
       && ! echo "$line" | grep -q "inet 10\.253\." \
       && ! echo "$line" | grep -q "inet 10\.254\."; then
        ip_addr=$(echo "$line" | awk '{print $2}' | cut -d/ -f1)
        prefix=$(echo "$ip_addr" | rev | cut -d. -f2- | rev)
        LOCAL_SUBNET="${prefix}.0/24"
        break
    fi
done < <(ip -4 addr show)

[[ -n "$LOCAL_SUBNET" ]] || { log "ERROR: No 10.x.x.x address found"; exit 1; }
log "Node: $NODE_NAME  Subnet: $LOCAL_SUBNET"

# Determine pond name from gateways.conf
POND_NAME=$(grep -m1 -E '^(POND_NAME|GROUP_NAME)=' "$BROKER_CONF" | cut -d= -f2-)
if [[ -z "$POND_NAME" ]]; then
    POND_NAME=$(grep '^NETWORK_NAME=' /etc/frognet/gateways.conf 2>/dev/null | cut -d= -f2)
fi
[[ -n "$POND_NAME" ]] || { log "ERROR: No POND_NAME in broker.conf or NETWORK_NAME in gateways.conf"; exit 1; }
log "Pond: $POND_NAME"

# ---------------------------------------------------------------------------
# Register with broker
# ---------------------------------------------------------------------------

log "Registering with broker..."

# [BROKER_PIN_V1] Enforce the cert pin before sending anything.
_broker_pin_check "${BROKER_URL}" || exit 1

# Canonical machine identity = install-time GUID, sent in the "mac" field
# (decision A 2026-06-02: reuse the field as an opaque identity; the broker
# reconciles by (pond_id, mac) with no broker-side change). The helper
# generates+locks (0444) the GUID on first call if not already present.
NODE_GUID="$(/usr/local/bin/frognet-node-guid.sh --read)" || {
    log "FATAL: no identity at /etc/fnid - refusing to register."
    log "       Registering with a minted GUID creates a duplicate node row."
    exit 1
}
[[ -n "$NODE_GUID" ]] || { log "ERROR: could not obtain node GUID"; exit 1; }

# [BROKER_AUTH_V1] Fetch a one-time nonce, then prove the pond password with an
# HMAC over it instead of putting the password in the body. --cacert is not
# used (no CA); the pin checked above is the endpoint authentication.
NONCE=$(curl -sk --connect-timeout 5 --max-time 10 \
    "${BROKER_URL%/}/api/v4/register-challenge?pond=${POND_NAME}" 2>/dev/null \
    | jq -r '.nonce // empty' 2>/dev/null || true)

AUTH=""
if [[ -n "$NONCE" ]]; then
    AUTH=$(_register_hmac "$POND_PASSWORD" "$NONCE" "$POND_NAME" "$PUBKEY" "$NODE_GUID")
else
    # [BROKER_AUTH_V1] Broker has no challenge route yet (not upgraded). Fall
    # back to the legacy plaintext field so a new client still works against an
    # old broker; this fallback goes away once brokers issue nonces. The pin
    # above still protects this body from an interceptor.
    log "NOTE: broker issued no nonce - using legacy password field (pin still enforced)"
fi

# Build the body with jq so a password containing quotes or backslashes cannot
# break the JSON (the old hand-built heredoc could).
REG_BODY=$(jq -nc \
    --arg pond "$POND_NAME" --arg pubkey "$PUBKEY" --arg subnet "$LOCAL_SUBNET" \
    --arg node_name "$NODE_NAME" --arg label "$NODE_NAME" --arg guid "$NODE_GUID" \
    --arg auth "$AUTH" --arg nonce "$NONCE" --arg pw "$POND_PASSWORD" \
    '{pond:$pond, pubkey:$pubkey, subnet:$subnet, node_name:$node_name,
      label:$label, guid:$guid}
     + (if $auth  != "" then {auth:$auth, auth_nonce:$nonce} else {} end)
     + (if $nonce == "" then {pond_password:$pw} else {} end)')

RESPONSE=$(curl -sk -X POST "${BROKER_URL%/}/api/v4/register" \
    -H "Content-Type: application/json" \
    -d "$REG_BODY" 2>&1) || true

STATUS=$(echo "$RESPONSE" | jq -r '.status // empty' 2>/dev/null || true)

if [[ -z "$STATUS" ]]; then
    log "ERROR: Registration failed"
    log "Response: $RESPONSE"
    exit 1
fi

log "Registration: $STATUS"
CHORUSES=$(echo "$RESPONSE" | jq -r '.choruses[]' 2>/dev/null | tr '\n' ',' | sed 's/,$//')
EP_IP=$(echo "$RESPONSE" | jq -r '.entire_pond_ip // empty' 2>/dev/null || true)
EP_SUBNET=$(echo "$RESPONSE" | jq -r '.entire_pond_subnet // empty' 2>/dev/null || true)
log "Choruses: $CHORUSES"
[[ -n "$EP_IP" ]] && log "Entire Pond IP: $EP_IP in $EP_SUBNET"

# ---------------------------------------------------------------------------
# Mirror BROKER_URL into tunnel.conf
#
# Registration just used $BROKER_URL (from broker.conf).  The live tunnel
# daemon reads /etc/frognet/tunnel.conf, NOT broker.conf — if tunnel.conf
# BROKER_URL is empty or stale, the daemon falls back to LAN-only mode
# silently, never polls, and the tunnel slot the broker just created sits
# unclaimed.  Keep them in sync.
# ---------------------------------------------------------------------------
TUNNEL_CONF="/etc/frognet/tunnel.conf"
# [ONE_CONF_V1] tunnel.conf and broker.conf are the same file now, so this
# reconcile is vacuous; guarded to run only on a not-yet-consolidated node.
if [[ -f "$TUNNEL_CONF" && "$TUNNEL_CONF" != "$BROKER_CONF" ]]; then
    EXISTING_TUN_URL=$(grep '^BROKER_URL=' "$TUNNEL_CONF" | cut -d= -f2- | tr -d '[:space:]')
    if [[ -z "$EXISTING_TUN_URL" ]]; then
        sed -i "s|^BROKER_URL=.*|BROKER_URL=${BROKER_URL}|" "$TUNNEL_CONF"
        log "tunnel.conf: BROKER_URL was empty — set to $BROKER_URL"
    elif [[ "$EXISTING_TUN_URL" != "$BROKER_URL" ]]; then
        log "WARNING: tunnel.conf BROKER_URL=$EXISTING_TUN_URL differs from broker.conf BROKER_URL=$BROKER_URL"
        log "         Updating tunnel.conf to match broker.conf (the URL we just registered against)"
        cp -p "$TUNNEL_CONF" "${TUNNEL_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
        sed -i "s|^BROKER_URL=.*|BROKER_URL=${BROKER_URL}|" "$TUNNEL_CONF"
    else
        log "tunnel.conf: BROKER_URL already matches broker.conf"
    fi
    # GROUP_NAME and GROUP_TOKEN should also reflect the pond we just
    # joined — daemon-side code uses them for the propagation/chorus
    # paths.
    if ! grep -q '^GROUP_NAME=' "$TUNNEL_CONF"; then
        echo "GROUP_NAME=${POND_NAME}" >> "$TUNNEL_CONF"
    else
        sed -i "s|^GROUP_NAME=.*|GROUP_NAME=${POND_NAME}|" "$TUNNEL_CONF"
    fi
else
    # tunnel.conf doesn't exist — create a minimal one so the daemon can
    # start.  Pre-fills the URL plus the pond we just joined.
    umask 077
    cat > "$TUNNEL_CONF" <<TCONF
BROKER_URL=${BROKER_URL}
PASSCODE=
GROUP_TOKEN=
GROUP_NAME=${POND_NAME}
MAX_TUNNELS=5000
TCONF
    chmod 600 "$TUNNEL_CONF"
    log "tunnel.conf: created — BROKER_URL=$BROKER_URL, GROUP_NAME=$POND_NAME"
fi

# ---------------------------------------------------------------------------
# Enable and start daemon
# ---------------------------------------------------------------------------

# Per primer: internet_tunnels_v3 is the live code tree, served by
# frognet-tunnel-daemon-v3.service.  The legacy frognet-tunnel-daemon
# service exists too; prefer v3, fall back to v2.  Either way, force a
# restart — we just changed tunnel.conf and the daemon won't notice
# without one (it reads the file once at startup).
DAEMON_UNIT=""
for unit in frognet-tunnel-daemon-v3 frognet-tunnel-daemon; do
    if systemctl list-unit-files "${unit}.service" 2>/dev/null | grep -q "${unit}"; then
        DAEMON_UNIT="$unit"
        break
    fi
done

if [[ -z "$DAEMON_UNIT" ]]; then
    log "NOTE: No frognet-tunnel-daemon service found — start the daemon manually"
else
    log "Daemon unit: $DAEMON_UNIT"
    systemctl enable "$DAEMON_UNIT" 2>/dev/null || true
    if systemctl is-active --quiet "$DAEMON_UNIT" 2>/dev/null; then
        log "Restarting $DAEMON_UNIT to pick up updated tunnel.conf"
        systemctl restart "$DAEMON_UNIT"
    else
        log "Starting $DAEMON_UNIT"
        systemctl start "$DAEMON_UNIT" || log "NOTE: Failed to start $DAEMON_UNIT — check 'journalctl -u $DAEMON_UNIT'"
    fi
fi

log "Done — registered in pond '$POND_NAME', joined: $CHORUSES"
