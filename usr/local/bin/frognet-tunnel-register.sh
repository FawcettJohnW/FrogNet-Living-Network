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
# frognet-tunnel-register.sh — Edge node tunnel registration client
#
# Registers this FrogNet edge node with the cloud tunnel broker.
# Generates WireGuard keys, exchanges passcode for group token,
# then registers the tunnel and configures WireGuard + routes.
#
# Usage:
#   frognet-tunnel-register.sh <broker_url> <passcode> <subnet1> [subnet2] ...
#
# Example:
#   frognet-tunnel-register.sh https://streamingfrog.com:8444 \
#       "smoking-stovepipe" 10.101.10.0/24
#
# Flow:
#   1. POST /api/v1/connect  {passcode}        → group_token
#   2. POST /api/v1/register {group_token, ...} → tunnel config
#
# DESIGN: Tunnels are dumb pipes.  AllowedIPs = 10.0.0.0/8 always.
# Routes use "dev wgN src <local_gw>" — no "via" for tunnel devices.
# NEVER uses 2>/dev/null. All errors visible and trapped.
# =============================================================================

trap 'echo "FATAL: Error on line $LINENO, exit code $?" >&2; exit 1' ERR

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONF_DIR="/etc/wireguard"
STATE_DIR="/var/lib/frognet-tunnel"
LABEL="${FROGNET_TUNNEL_LABEL:-$(hostname)}"

# WireGuard interface name — find the next available wgN
find_next_wg_iface() {
    local n=0
    while ip link show "wg${n}" >/dev/null 2>&1; do
        n=$((n + 1))
        if [ "$n" -gt 16 ]; then
            echo "ERROR: Too many WireGuard interfaces (wg0-wg16 all exist)" >&2
            exit 1
        fi
    done
    echo "wg${n}"
}

# Derive local gateway (.1) from a subnet CIDR
# e.g. 10.101.10.0/24 → 10.101.10.1
get_local_gateway() {
    local network
    network=$(echo "$1" | cut -d/ -f1)
    echo "${network%.*}.1"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 <broker_url> <passcode> <subnet1> [subnet2] ..." >&2
    echo "" >&2
    echo "  broker_url : URL of the tunnel broker (e.g. https://streamingfrog.com:8444)" >&2
    echo "  passcode   : Group passcode from your FrogNet admin" >&2
    echo "  subnet1... : FrogNet subnets behind this edge node (CIDR)" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 https://streamingfrog.com:8444 'smoking-stovepipe' 10.101.10.0/24" >&2
    exit 1
fi

BROKER_URL="${1%/}"  # Strip trailing slash
PASSCODE="$2"
shift 2
SUBNETS=("$@")

LOCAL_GW=$(get_local_gateway "${SUBNETS[0]}")

echo "=== FrogNet Tunnel Registration ==="
echo "Broker:    $BROKER_URL"
echo "Label:     $LABEL"
echo "Subnets:   ${SUBNETS[*]}"
echo "Local GW:  $LOCAL_GW"
echo ""

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

echo "--- Checking prerequisites ---"

for cmd in wg curl jq ip; do
    if ! command -v "$cmd" >/dev/null; then
        echo "ERROR: '$cmd' not found. Install it first." >&2
        exit 1
    fi
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root (need to configure WireGuard and routes)" >&2
    exit 1
fi

mkdir -p "$CONF_DIR" "$STATE_DIR"

# ---------------------------------------------------------------------------
# Step 1: Exchange passcode for group token via /api/v1/connect
# ---------------------------------------------------------------------------

echo "--- Connecting with passcode ---"

CONNECT_RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"passcode\": \"${PASSCODE}\"}" \
    "${BROKER_URL}/api/v1/connect" 2>&1) || {
    echo "ERROR: curl failed connecting to broker at ${BROKER_URL}/api/v1/connect" >&2
    echo "Response: $CONNECT_RESPONSE" >&2
    exit 1
}

CONNECT_HTTP_CODE=$(echo "$CONNECT_RESPONSE" | tail -1)
CONNECT_BODY=$(echo "$CONNECT_RESPONSE" | sed '$d')

echo "HTTP Status: $CONNECT_HTTP_CODE"

if [ "$CONNECT_HTTP_CODE" != "200" ]; then
    echo "ERROR: Passcode exchange failed (HTTP $CONNECT_HTTP_CODE)" >&2
    echo "Response: $CONNECT_BODY" >&2
    exit 1
fi

echo "Connect response:"
echo "$CONNECT_BODY" | jq .

GROUP_TOKEN=$(echo "$CONNECT_BODY" | jq -r '.group_token')
GROUP_NAME=$(echo "$CONNECT_BODY" | jq -r '.group_name')
MAX_NODES=$(echo "$CONNECT_BODY" | jq -r '.max_nodes')
ACTIVE_NODES=$(echo "$CONNECT_BODY" | jq -r '.active_nodes')

if [ -z "$GROUP_TOKEN" ] || [ "$GROUP_TOKEN" = "null" ]; then
    echo "ERROR: No group_token in connect response" >&2
    exit 1
fi

echo ""
echo "Group:        $GROUP_NAME"
echo "Capacity:     $ACTIVE_NODES / $MAX_NODES nodes"
echo "Group token:  ${GROUP_TOKEN:0:12}..."

# ---------------------------------------------------------------------------
# Generate WireGuard keypair (or reuse existing)
# ---------------------------------------------------------------------------

echo ""
echo "--- WireGuard keys ---"

WG_IFACE=$(find_next_wg_iface)
PRIVKEY_FILE="${STATE_DIR}/${WG_IFACE}_private.key"
PUBKEY_FILE="${STATE_DIR}/${WG_IFACE}_public.key"

if [ -f "$PRIVKEY_FILE" ] && [ -f "$PUBKEY_FILE" ]; then
    echo "Reusing existing keypair for ${WG_IFACE}"
    PRIVKEY=$(cat "$PRIVKEY_FILE")
    PUBKEY=$(cat "$PUBKEY_FILE")
else
    echo "Generating new keypair for ${WG_IFACE}"
    PRIVKEY=$(wg genkey)
    PUBKEY=$(echo "$PRIVKEY" | wg pubkey)

    umask 077
    echo "$PRIVKEY" > "$PRIVKEY_FILE"
    echo "$PUBKEY" > "$PUBKEY_FILE"
    chmod 600 "$PRIVKEY_FILE"
    chmod 644 "$PUBKEY_FILE"
fi

echo "Public key: ${PUBKEY}"
echo "Interface:  ${WG_IFACE}"

# ---------------------------------------------------------------------------
# Step 2: Register tunnel via /api/v1/register
# ---------------------------------------------------------------------------

echo ""
echo "--- Registering tunnel with broker ---"

# Build JSON array of subnets
SUBNETS_JSON="["
for i in "${!SUBNETS[@]}"; do
    if [ "$i" -gt 0 ]; then SUBNETS_JSON+=","; fi
    SUBNETS_JSON+="\"${SUBNETS[$i]}\""
done
SUBNETS_JSON+="]"

REQ_BODY=$(cat <<EOF
{
    "group_token": "${GROUP_TOKEN}",
    "wg_public_key": "${PUBKEY}",
    "frognet_subnets": ${SUBNETS_JSON},
    "label": "${LABEL}"
}
EOF
)

echo "Request body:"
echo "$REQ_BODY" | jq .

REGISTER_RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$REQ_BODY" \
    "${BROKER_URL}/api/v1/register" 2>&1) || {
    echo "ERROR: curl failed calling ${BROKER_URL}/api/v1/register" >&2
    echo "Response: $REGISTER_RESPONSE" >&2
    exit 1
}

REG_HTTP_CODE=$(echo "$REGISTER_RESPONSE" | tail -1)
REG_BODY=$(echo "$REGISTER_RESPONSE" | sed '$d')

echo ""
echo "HTTP Status: $REG_HTTP_CODE"

if [ "$REG_HTTP_CODE" != "201" ]; then
    echo "ERROR: Registration failed (HTTP $REG_HTTP_CODE)" >&2
    echo "Response: $REG_BODY" >&2
    exit 1
fi

echo "Registration response:"
echo "$REG_BODY" | jq .

# ---------------------------------------------------------------------------
# Parse registration response
# ---------------------------------------------------------------------------

echo ""
echo "--- Parsing registration response ---"

EDGE_IP=$(echo "$REG_BODY" | jq -r '.edge_ip')
EDGE_MASK=$(echo "$REG_BODY" | jq -r '.edge_mask')
DROPLET_PUBKEY=$(echo "$REG_BODY" | jq -r '.droplet_pubkey')
DROPLET_ENDPOINT=$(echo "$REG_BODY" | jq -r '.droplet_endpoint')
REMOTE_SUBNETS=$(echo "$REG_BODY" | jq -r '.remote_subnets[]' 2>/dev/null || true)
DROPLET_IP=$(echo "$REG_BODY" | jq -r '.droplet_ip')

# Tunnel is a dumb pipe — always 10.0.0.0/8
ALLOWED_IPS="10.0.0.0/8"

echo "Edge IP:          ${EDGE_IP}/${EDGE_MASK}"
echo "Droplet endpoint: ${DROPLET_ENDPOINT}"
echo "Droplet pubkey:   ${DROPLET_PUBKEY}"
echo "Droplet transit:  ${DROPLET_IP}"
echo "AllowedIPs:       ${ALLOWED_IPS}"
echo "Local gateway:    ${LOCAL_GW}"

# ---------------------------------------------------------------------------
# Generate WireGuard config
# ---------------------------------------------------------------------------

echo ""
echo "--- Configuring WireGuard interface ${WG_IFACE} ---"

WG_CONF="${CONF_DIR}/${WG_IFACE}.conf"

cat > "$WG_CONF" <<WGEOF
# FrogNet Tunnel — auto-generated by frognet-tunnel-register.sh
# Group: ${GROUP_NAME}
# Label: ${LABEL}
# Created: $(date -u +%Y-%m-%dT%H:%M:%SZ)

[Interface]
Address = ${EDGE_IP}/${EDGE_MASK}
PrivateKey = ${PRIVKEY}

[Peer]
PublicKey = ${DROPLET_PUBKEY}
Endpoint = ${DROPLET_ENDPOINT}
AllowedIPs = ${ALLOWED_IPS}
PersistentKeepalive = 25
WGEOF

chmod 600 "$WG_CONF"
echo "Config written to ${WG_CONF}"

# ---------------------------------------------------------------------------
# Bring up the interface
# ---------------------------------------------------------------------------

echo ""
echo "--- Bringing up ${WG_IFACE} ---"

wg-quick up "${WG_IFACE}"

# ---------------------------------------------------------------------------
# Install routes for remote subnets.
#
# Routes use "dev wgN src <local_gw>" — NOT "via <remote_ip>".
# WireGuard handles encapsulation; kernel just needs to know the dev.
# ---------------------------------------------------------------------------

echo ""
echo "--- Installing routes for remote subnets ---"

if [ -n "$REMOTE_SUBNETS" ]; then
    while IFS= read -r subnet; do
        if [ -n "$subnet" ]; then
            echo "  route: ${subnet} dev ${WG_IFACE} src ${LOCAL_GW}"
            ip route replace "${subnet}" dev "${WG_IFACE}" src "${LOCAL_GW}"
        fi
    done <<< "$REMOTE_SUBNETS"
else
    echo "  (no remote subnets yet — discovery will find them)"
fi

# Catchall for future subnets discovered after registration
if ! ip route show 10.0.0.0/8 dev "${WG_IFACE}" 2>/dev/null | grep -q .; then
    echo "  route: 10.0.0.0/8 dev ${WG_IFACE} src ${LOCAL_GW} metric 100"
    ip route replace 10.0.0.0/8 dev "${WG_IFACE}" src "${LOCAL_GW}" metric 100
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying ---"

echo "Interface status:"
wg show "${WG_IFACE}"

echo ""
echo "Routing table (FrogNet-related):"
ip route show | grep -E "10\.(101|253|0\.0\.0/8)" || echo "(no FrogNet routes yet)"

echo ""
echo "Ping test to droplet transit IP (${DROPLET_IP}):"
if ping -c 2 -W 3 "$DROPLET_IP"; then
    echo "SUCCESS: Tunnel is UP"
else
    echo "WARNING: Ping to droplet failed. Check firewall/NAT. WireGuard may still work."
fi

# ---------------------------------------------------------------------------
# Save state
# ---------------------------------------------------------------------------

STATE_FILE="${STATE_DIR}/${WG_IFACE}.json"
cat > "$STATE_FILE" <<STEOF
{
    "interface": "${WG_IFACE}",
    "broker_url": "${BROKER_URL}",
    "group_name": "${GROUP_NAME}",
    "passcode": "${PASSCODE}",
    "edge_ip": "${EDGE_IP}",
    "edge_mask": ${EDGE_MASK},
    "droplet_ip": "${DROPLET_IP}",
    "droplet_endpoint": "${DROPLET_ENDPOINT}",
    "droplet_pubkey": "${DROPLET_PUBKEY}",
    "local_subnets": ${SUBNETS_JSON},
    "local_gateway": "${LOCAL_GW}",
    "allowed_ips": "${ALLOWED_IPS}",
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
STEOF

echo ""
echo "=== Registration Complete ==="
echo ""
echo "Tunnel ${WG_IFACE} is UP."
echo "Group:      ${GROUP_NAME}"
echo "AllowedIPs: ${ALLOWED_IPS}"
echo "Routes:     dev ${WG_IFACE} src ${LOCAL_GW}"
echo ""
echo "To tear down:  wg-quick down ${WG_IFACE}"
echo "To restart:    wg-quick up ${WG_IFACE}"
echo "State saved:   ${STATE_FILE}"
