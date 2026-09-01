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
# frognet-tunnel-setup.sh — FrogNet tunnel node setup
#
# Handles the complete setup flow:
#   1. Bootstrap the broker (get admin token) — first node only
#   2. Create a passcode if needed
#   3. Redeem the passcode (create group)
#   4. Write /etc/frognet/tunnel.conf
#   5. Generate WireGuard keypair
#
# Usage:
#   First node (admin):
#     frognet-tunnel-setup.sh <broker_url> --bootstrap <passcode> <group_name> [max_tunnels]
#
#   Subsequent nodes (have passcode):
#     frognet-tunnel-setup.sh <broker_url> <passcode>
#
# Examples:
#   frognet-tunnel-setup.sh https://streamingfrog.com:8443/frognet-broker \
#       --bootstrap swimming-dog family_smith 8
#
#   frognet-tunnel-setup.sh https://streamingfrog.com:8443/frognet-broker \
#       swimming-dog
#
# After setup, use:
#   frognet-tunnel-host.sh <channel> <subnet>
#   frognet-tunnel-join.sh <channel> <subnet>
#
# Output is JSON on stdout. Logs on stderr.
# NEVER masks errors.
# =============================================================================

trap 'echo "{\"error\":\"Fatal error on line $LINENO\"}" ; exit 1' ERR

CONF_DIR="/etc/frognet"
CONF_FILE="${CONF_DIR}/tunnel.conf"
STATE_DIR="/var/lib/frognet-tunnel"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

if [ "$#" -lt 2 ]; then
    cat >&2 <<'USAGE'
Usage:
  Admin (first node):
    frognet-tunnel-setup.sh <broker_url> --bootstrap <passcode> <group_name> [max_tunnels]

  Regular node:
    frognet-tunnel-setup.sh <broker_url> <passcode>

  Examples:
    frognet-tunnel-setup.sh https://streamingfrog.com:8443/frognet-broker \
        --bootstrap swimming-dog family_smith 8
 
    frognet-tunnel-setup.sh https://streamingfrog.com:8443/frognet-broker \
        swimming-dog
USAGE
    exit 1
fi

BROKER_URL="${1%/}"
shift

BOOTSTRAP=false
PASSCODE=""
GROUP_NAME=""
MAX_TUNNELS=4
ADMIN_TOKEN=""

if [ "$1" = "--bootstrap" ]; then
    BOOTSTRAP=true
    shift
    if [ "$#" -lt 2 ]; then
        echo '{"error":"--bootstrap requires: <passcode> <group_name> [max_tunnels]"}'
        exit 1
    fi
    PASSCODE="$1"
    GROUP_NAME="$2"
    MAX_TUNNELS="${3:-4}"
else
    PASSCODE="$1"
fi

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

for cmd in curl jq wg; do
    if ! command -v "$cmd" >/dev/null; then
        echo "{\"error\":\"'$cmd' not found\"}"
        exit 1
    fi
done

if [ "$(id -u)" -ne 0 ]; then
    echo '{"error":"Must run as root"}'
    exit 1
fi

mkdir -p "$CONF_DIR" "$STATE_DIR"

# ---------------------------------------------------------------------------
# Generate WG keypair if not present
# ---------------------------------------------------------------------------

PRIVKEY_FILE="${STATE_DIR}/node_private.key"
PUBKEY_FILE="${STATE_DIR}/node_public.key"

if [ -f "$PRIVKEY_FILE" ] && [ -f "$PUBKEY_FILE" ]; then
    log "WireGuard keypair exists"
else
    log "Generating WireGuard keypair..."
    PRIVKEY=$(wg genkey)
    PUBKEY=$(echo "$PRIVKEY" | wg pubkey)
    umask 077
    echo "$PRIVKEY" > "$PRIVKEY_FILE"
    echo "$PUBKEY" > "$PUBKEY_FILE"
    chmod 600 "$PRIVKEY_FILE"
    chmod 644 "$PUBKEY_FILE"
fi

PUBKEY=$(cat "$PUBKEY_FILE")
log "Public key: ${PUBKEY}"

# ---------------------------------------------------------------------------
# Step 1: Bootstrap (admin only)
# ---------------------------------------------------------------------------

if [ "$BOOTSTRAP" = true ]; then
    log "Bootstrapping broker..."

    RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
        -X POST \
        "${BROKER_URL}/api/v1/bootstrap" 2>&1) || {
        echo "{\"error\":\"Failed to reach broker at ${BROKER_URL}\"}"
        exit 1
    }

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
        ADMIN_TOKEN=$(echo "$BODY" | jq -r '.admin_token')
        STATUS=$(echo "$BODY" | jq -r '.status')
        log "Bootstrap ${STATUS}: admin token received"
    else
        ERR_MSG=$(echo "$BODY" | jq -r '.detail // .error // empty' 2>/dev/null || echo "$BODY")
        echo "{\"error\":\"Bootstrap failed (HTTP ${HTTP_CODE}): ${ERR_MSG}\"}"
        exit 1
    fi

    # Step 2: Create passcode
    log "Creating passcode '${PASSCODE}' for group '${GROUP_NAME}'..."

    RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"passcode\":\"${PASSCODE}\",\"group_name\":\"${GROUP_NAME}\",\"tier\":\"standard\",\"max_tunnels\":${MAX_TUNNELS}}" \
        "${BROKER_URL}/api/v1/passcodes" 2>&1) || {
        echo "{\"error\":\"Failed to create passcode\"}"
        exit 1
    }

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" = "201" ]; then
        log "Passcode '${PASSCODE}' created"
    else
        ERR_MSG=$(echo "$BODY" | jq -r '.detail // .error // empty' 2>/dev/null || echo "$BODY")
        # 400 "already exists" is OK — passcode was created before
        if echo "$ERR_MSG" | grep -qi "already exists"; then
            log "Passcode already exists — continuing"
        else
            echo "{\"error\":\"Passcode creation failed (HTTP ${HTTP_CODE}): ${ERR_MSG}\"}"
            exit 1
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Redeem passcode
# ---------------------------------------------------------------------------

log "Redeeming passcode '${PASSCODE}'..."

RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"passcode\":\"${PASSCODE}\"}" \
    "${BROKER_URL}/api/v1/redeem" 2>&1) || {
    echo "{\"error\":\"Failed to reach broker\"}"
    exit 1
}

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "201" ]; then
    ERR_MSG=$(echo "$BODY" | jq -r '.detail // .error // empty' 2>/dev/null || echo "$BODY")
    echo "{\"error\":\"Redeem failed (HTTP ${HTTP_CODE}): ${ERR_MSG}\"}"
    exit 1
fi

GROUP_TOKEN=$(echo "$BODY" | jq -r '.group_token')
GROUP_NAME_RESP=$(echo "$BODY" | jq -r '.group_name')
MAX_TUNNELS_RESP=$(echo "$BODY" | jq -r '.max_tunnels')

if [ -z "$GROUP_TOKEN" ] || [ "$GROUP_TOKEN" = "null" ]; then
    echo "{\"error\":\"No group_token in redeem response\"}"
    exit 1
fi

log "Group '${GROUP_NAME_RESP}' ready (token: ${GROUP_TOKEN:0:12}...)"

# ---------------------------------------------------------------------------
# Step 4: Write config
# ---------------------------------------------------------------------------

cat > "$CONF_FILE" <<EOF
# FrogNet tunnel configuration
# Generated by frognet-tunnel-setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
BROKER_URL=${BROKER_URL}
PASSCODE=${PASSCODE}
GROUP_TOKEN=${GROUP_TOKEN}
GROUP_NAME=${GROUP_NAME_RESP}
MAX_TUNNELS=${MAX_TUNNELS_RESP}
EOF

# Store admin token if we have it
if [ -n "$ADMIN_TOKEN" ]; then
    echo "ADMIN_TOKEN=${ADMIN_TOKEN}" >> "$CONF_FILE"
fi

chmod 600 "$CONF_FILE"

log "Config written to ${CONF_FILE}"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

echo "{\"status\":\"ready\",\"group_name\":\"${GROUP_NAME_RESP}\",\"max_tunnels\":${MAX_TUNNELS_RESP},\"config\":\"${CONF_FILE}\"}"
