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
# frognet-channel-join.sh — Join a FrogNet tunnel channel
#
# Calls the broker to join an advertised channel, receives WG config,
# brings up the tunnel. The tunnel is named <joiner>_to_<host>.
#
# Usage:
#   frognet-channel-join.sh <broker_url> <group_token> <channel_name> <subnet1> [subnet2...]
#
# Example:
#   frognet-channel-join.sh \
#       https://streamingfrog.com:8443/frognet-broker \
#       "nEWkmqs..." \
#       "ironbox" \
#       10.101.10.0/24
#
# DESIGN: Tunnels are dumb pipes. AllowedIPs = 10.0.0.0/8.
# Routes use "dev wgN src <local_gw>" — no "via".
# NEVER uses 2>/dev/null. All errors visible and trapped.
# =============================================================================

trap 'echo "FATAL: Error on line $LINENO, exit code $?" >&2; exit 1' ERR

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONF_DIR="/etc/wireguard"
STATE_DIR="/var/lib/frognet-tunnel"
LABEL="${FROGNET_TUNNEL_LABEL:-$(hostname)}"

get_local_gateway() {
    local network
    network=$(echo "$1" | cut -d/ -f1)
    echo "${network%.*}.1"
}

find_next_wg_iface() {
    local n=0
    while ip link show "wg${n}" >/dev/null 2>&1; do
        n=$((n + 1))
        if [ "$n" -gt 16 ]; then
            echo "ERROR: Too many WireGuard interfaces" >&2
            exit 1
        fi
    done
    echo "wg${n}"
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

if [ "$#" -lt 4 ]; then
    echo "Usage: $0 <broker_url> <group_token> <channel_name> <subnet1> [subnet2...]" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 https://streamingfrog.com:8443/frognet-broker 'token...' 'ironbox' 10.101.10.0/24" >&2
    exit 1
fi

BROKER_URL="${1%/}"
GROUP_TOKEN="$2"
CHANNEL_NAME="$3"
shift 3
SUBNETS=("$@")

LOCAL_GW=$(get_local_gateway "${SUBNETS[0]}")

echo "=== FrogNet Channel Join ==="
echo "Broker:   $BROKER_URL"
echo "Channel:  $CHANNEL_NAME"
echo "Label:    $LABEL"
echo "Subnets:  ${SUBNETS[*]}"
echo "Local GW: $LOCAL_GW"
echo ""

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

for cmd in wg curl jq ip; do
    if ! command -v "$cmd" >/dev/null; then
        echo "ERROR: '$cmd' not found." >&2
        exit 1
    fi
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root" >&2
    exit 1
fi

mkdir -p "$CONF_DIR" "$STATE_DIR"

# ---------------------------------------------------------------------------
# Get or generate WG keypair
# ---------------------------------------------------------------------------

PRIVKEY_FILE="${STATE_DIR}/node_private.key"
PUBKEY_FILE="${STATE_DIR}/node_public.key"

if [ -f "$PRIVKEY_FILE" ] && [ -f "$PUBKEY_FILE" ]; then
    PRIVKEY=$(cat "$PRIVKEY_FILE")
    PUBKEY=$(cat "$PUBKEY_FILE")
else
    echo "Generating WireGuard keypair..."
    PRIVKEY=$(wg genkey)
    PUBKEY=$(echo "$PRIVKEY" | wg pubkey)
    umask 077
    echo "$PRIVKEY" > "$PRIVKEY_FILE"
    echo "$PUBKEY" > "$PUBKEY_FILE"
    chmod 600 "$PRIVKEY_FILE"
    chmod 644 "$PUBKEY_FILE"
fi

echo "Public key: ${PUBKEY}"

# ---------------------------------------------------------------------------
# Build subnet JSON
# ---------------------------------------------------------------------------

SUBNETS_JSON="["
for i in "${!SUBNETS[@]}"; do
    if [ "$i" -gt 0 ]; then SUBNETS_JSON+=","; fi
    SUBNETS_JSON+="\"${SUBNETS[$i]}\""
done
SUBNETS_JSON+="]"

# ---------------------------------------------------------------------------
# Call /api/v1/join
# ---------------------------------------------------------------------------

echo ""
echo "--- Joining channel '$CHANNEL_NAME' ---"

REQ_BODY=$(cat <<EOF
{
    "group_token": "${GROUP_TOKEN}",
    "channel_name": "${CHANNEL_NAME}",
    "pubkey": "${PUBKEY}",
    "frognet_subnets": ${SUBNETS_JSON},
    "label": "${LABEL}"
}
EOF
)

echo "Request:"
echo "$REQ_BODY" | jq .

RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "$REQ_BODY" \
    "${BROKER_URL}/api/v1/join" 2>&1) || {
    echo "ERROR: curl failed calling ${BROKER_URL}/api/v1/join" >&2
    echo "Response: $RESPONSE" >&2
    exit 1
}

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

echo ""
echo "HTTP Status: $HTTP_CODE"

if [ "$HTTP_CODE" != "201" ]; then
    echo "ERROR: Join failed (HTTP $HTTP_CODE)" >&2
    echo "Response: $BODY" >&2
    exit 1
fi

echo "Response:"
echo "$BODY" | jq .

# ---------------------------------------------------------------------------
# Parse response
# ---------------------------------------------------------------------------

TUNNEL_NAME=$(echo "$BODY" | jq -r '.tunnel_name')
EDGE_IP=$(echo "$BODY" | jq -r '.wg_config.edge_ip')
EDGE_MASK=$(echo "$BODY" | jq -r '.wg_config.edge_mask')
DROPLET_IP=$(echo "$BODY" | jq -r '.wg_config.droplet_ip')
DROPLET_PUBKEY=$(echo "$BODY" | jq -r '.wg_config.droplet_pubkey')
DROPLET_ENDPOINT=$(echo "$BODY" | jq -r '.wg_config.droplet_endpoint')
ALLOWED_IPS=$(echo "$BODY" | jq -r '.wg_config.allowed_ips')
REMOTE_SUBNETS=$(echo "$BODY" | jq -r '.wg_config.remote_subnets[]' 2>/dev/null || true)
HOST_SUBNETS=$(echo "$BODY" | jq -r '.host_subnets[]' 2>/dev/null || true)

echo ""
echo "Tunnel:    $TUNNEL_NAME"
echo "Edge IP:   ${EDGE_IP}/${EDGE_MASK}"
echo "Droplet:   ${DROPLET_ENDPOINT}"
echo "AllowedIPs: ${ALLOWED_IPS}"

# ---------------------------------------------------------------------------
# Configure WireGuard
# ---------------------------------------------------------------------------

WG_IFACE=$(find_next_wg_iface)
WG_CONF="${CONF_DIR}/${WG_IFACE}.conf"

echo ""
echo "--- Configuring ${WG_IFACE} ---"

cat > "$WG_CONF" <<WGEOF
# FrogNet Tunnel — ${TUNNEL_NAME}
# Auto-configured by frognet-channel-join.sh

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

# ---------------------------------------------------------------------------
# Bring up
# ---------------------------------------------------------------------------

echo "--- Bringing up ${WG_IFACE} ---"
wg-quick up "${WG_IFACE}"

# ---------------------------------------------------------------------------
# Install routes
# ---------------------------------------------------------------------------

echo ""
echo "--- Installing routes ---"

# Routes from host_subnets (the channel we joined)
ALL_REMOTE="${HOST_SUBNETS}"
if [ -n "$REMOTE_SUBNETS" ]; then
    ALL_REMOTE="${ALL_REMOTE}
${REMOTE_SUBNETS}"
fi

if [ -n "$ALL_REMOTE" ]; then
    echo "$ALL_REMOTE" | sort -u | while IFS= read -r subnet; do
        if [ -n "$subnet" ]; then
            echo "  route: ${subnet} dev ${WG_IFACE} src ${LOCAL_GW}"
            ip route replace "${subnet}" dev "${WG_IFACE}" src "${LOCAL_GW}"
        fi
    done
fi

# Catchall for future discovery
if ! ip route show 10.0.0.0/8 dev "${WG_IFACE}" 2>/dev/null | grep -q .; then
    echo "  route: 10.0.0.0/8 dev ${WG_IFACE} src ${LOCAL_GW} metric 100"
    ip route replace 10.0.0.0/8 dev "${WG_IFACE}" src "${LOCAL_GW}" metric 100
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

echo ""
echo "--- Verifying ---"
wg show "${WG_IFACE}"

echo ""
echo "Ping test to droplet (${DROPLET_IP}):"
if ping -c 2 -W 3 "$DROPLET_IP"; then
    echo "SUCCESS: Tunnel is UP"
else
    echo "WARNING: Ping failed. Check firewall/NAT."
fi

# ---------------------------------------------------------------------------
# Save state
# ---------------------------------------------------------------------------

STATE_FILE="${STATE_DIR}/${WG_IFACE}.json"
cat > "$STATE_FILE" <<STEOF
{
    "interface": "${WG_IFACE}",
    "tunnel_name": "${TUNNEL_NAME}",
    "channel_name": "${CHANNEL_NAME}",
    "broker_url": "${BROKER_URL}",
    "edge_ip": "${EDGE_IP}",
    "edge_mask": ${EDGE_MASK},
    "droplet_ip": "${DROPLET_IP}",
    "droplet_endpoint": "${DROPLET_ENDPOINT}",
    "local_subnets": ${SUBNETS_JSON},
    "local_gateway": "${LOCAL_GW}",
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
STEOF

echo ""
echo "=== Join Complete ==="
echo "Tunnel:  ${TUNNEL_NAME} on ${WG_IFACE}"
echo "To tear down: wg-quick down ${WG_IFACE}"
echo "State: ${STATE_FILE}"
