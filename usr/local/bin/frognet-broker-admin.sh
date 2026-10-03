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
# frognet-broker-admin.sh — Admin helper for FrogNet Tunnel Broker
#
# Wraps common broker API calls for group and tunnel management.
#
# Usage:
#   frognet-broker-admin.sh <command> [args]
#
# Commands:
#   create-group <broker_url> <admin_token> <group_name>
#   group-status <broker_url> <group_token>
#   delete-tunnel <broker_url> <group_token> <tunnel_id>
#
# NEVER uses 2>/dev/null. All errors visible.
# =============================================================================

trap 'echo "FATAL: Error on line $LINENO, exit code $?" >&2; exit 1' ERR

usage() {
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "Commands:"
    echo "  create-group    <broker_url> <admin_token> <group_name> [tier]"
    echo "  create-passcode <broker_url> <admin_token> <passcode> <group_name> [tier] [max_nodes] [expires_days]"
    echo "  group-status    <broker_url> <group_token>"
    echo "  delete-tunnel   <broker_url> <group_token> <tunnel_id>"
    echo "  usage-status                               Show bandwidth usage for all groups"
    echo "  tiers                                      List available tiers"
    echo "  set-tier        <group_name> <tier>        Set group tier (basic/standard/mega)"
    echo "  set-budget      <group_name> <gb>          Manual override: custom budget in GB"
    echo "  reset-throttle  <group_name>               Remove throttle from a group"
    echo ""
    echo "Tiers:  basic (5 GB)  |  standard (50 GB)  |  mega (100 GB)"
    echo ""
    echo "Examples:"
    echo "  $0 create-passcode https://broker:8443 'admin_tok' smoking-stovepipe family_smith standard 2"
    echo "  $0 create-group https://broker:8443 'admin_tok' mygroup standard"
    echo "  $0 group-status https://broker:8443 'group_tok'"
    echo "  $0 set-tier mygroup mega"
    exit 1
}

if [ "$#" -lt 1 ]; then usage; fi

COMMAND="$1"
shift

case "$COMMAND" in
    create-group)
        if [ "$#" -lt 3 ]; then
            echo "Usage: $0 create-group <broker_url> <admin_token> <group_name> [tier]" >&2
            echo "  Tiers: basic (5 GB), standard (50 GB), mega (100 GB)" >&2
            exit 1
        fi
        BROKER_URL="${1%/}"
        ADMIN_TOKEN="$2"
        GROUP_NAME="$3"
        TIER="${4:-basic}"

        echo "Creating group '${GROUP_NAME}' (tier: ${TIER})..."
        RESPONSE=$(curl -sS \
            -X POST \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"name\": \"${GROUP_NAME}\", \"tier\": \"${TIER}\"}" \
            "${BROKER_URL}/api/v1/groups")

        echo "$RESPONSE" | jq .

        TOKEN=$(echo "$RESPONSE" | jq -r '.group_token // empty')
        if [ -n "$TOKEN" ]; then
            echo ""
            echo "======================================"
            echo "  GROUP TOKEN (give to edge nodes):"
            echo "  ${TOKEN}"
            echo "======================================"
        fi
        ;;

    create-passcode)
        if [ "$#" -lt 4 ]; then
            echo "Usage: $0 create-passcode <broker_url> <admin_token> <passcode> <group_name> [tier] [max_nodes] [expires_days]" >&2
            echo "" >&2
            echo "  Creates a passcode for consumer FrogNet registration." >&2
            echo "  If the group doesn't exist, it is created automatically." >&2
            echo "" >&2
            echo "  tier          : basic (default), standard, mega" >&2
            echo "  max_nodes     : max FrogNets in group (default: 2)" >&2
            echo "  expires_days  : days until passcode expires (default: never)" >&2
            exit 1
        fi
        BROKER_URL="${1%/}"
        ADMIN_TOKEN="$2"
        PASSCODE="$3"
        GROUP_NAME="$4"
        TIER="${5:-basic}"
        MAX_NODES="${6:-2}"
        EXPIRES="${7:-null}"

        echo "Creating passcode '${PASSCODE}' for group '${GROUP_NAME}'..."

        # Build JSON payload
        PAYLOAD="{\"passcode\": \"${PASSCODE}\", \"group_name\": \"${GROUP_NAME}\", \"tier\": \"${TIER}\", \"max_nodes\": ${MAX_NODES}"
        if [ "$EXPIRES" != "null" ]; then
            PAYLOAD="${PAYLOAD}, \"expires_days\": ${EXPIRES}"
        fi
        PAYLOAD="${PAYLOAD}}"

        RESPONSE=$(curl -sS \
            -X POST \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD" \
            "${BROKER_URL}/api/v1/passcodes")

        echo "$RESPONSE" | jq .

        PC=$(echo "$RESPONSE" | jq -r '.passcode // empty')
        if [ -n "$PC" ]; then
            echo ""
            echo "======================================"
            echo "  PASSCODE (give to customer):"
            echo "  ${PC}"
            echo ""
            echo "  Customer runs:"
            echo "    frognet-tunnel-register.sh ${PC}"
            echo "======================================"
        fi
        ;;

    group-status)
        if [ "$#" -lt 2 ]; then
            echo "Usage: $0 group-status <broker_url> <group_token>" >&2
            exit 1
        fi
        BROKER_URL="${1%/}"
        GROUP_TOKEN="$2"

        curl -sS "${BROKER_URL}/api/v1/groups/${GROUP_TOKEN}/status" | jq .
        ;;

    delete-tunnel)
        if [ "$#" -lt 3 ]; then
            echo "Usage: $0 delete-tunnel <broker_url> <group_token> <tunnel_id>" >&2
            exit 1
        fi
        BROKER_URL="${1%/}"
        GROUP_TOKEN="$2"
        TUNNEL_ID="$3"

        echo "Deleting tunnel ${TUNNEL_ID}..."
        curl -sS \
            -X DELETE \
            -H "Content-Type: application/json" \
            -d "{\"group_token\": \"${GROUP_TOKEN}\"}" \
            "${BROKER_URL}/api/v1/tunnels/${TUNNEL_ID}" | jq .
        ;;

    usage-status)
        # Runs locally on the droplet
        BROKER_DIR="${FROGNET_BROKER_DIR:-/opt/frognet-broker}"
        "${BROKER_DIR}/venv/bin/python3" "${BROKER_DIR}/broker_metering.py" status
        ;;

    tiers)
        BROKER_DIR="${FROGNET_BROKER_DIR:-/opt/frognet-broker}"
        "${BROKER_DIR}/venv/bin/python3" "${BROKER_DIR}/broker_metering.py" tiers
        ;;

    set-tier)
        if [ "$#" -lt 2 ]; then
            echo "Usage: $0 set-tier <group_name> <tier>" >&2
            echo "  Tiers: basic (5 GB), standard (50 GB), mega (100 GB)" >&2
            exit 1
        fi
        BROKER_DIR="${FROGNET_BROKER_DIR:-/opt/frognet-broker}"
        "${BROKER_DIR}/venv/bin/python3" "${BROKER_DIR}/broker_metering.py" set-tier "$1" "$2"
        ;;

    set-budget)
        if [ "$#" -lt 2 ]; then
            echo "Usage: $0 set-budget <group_name> <budget_gb>" >&2
            exit 1
        fi
        BROKER_DIR="${FROGNET_BROKER_DIR:-/opt/frognet-broker}"
        "${BROKER_DIR}/venv/bin/python3" "${BROKER_DIR}/broker_metering.py" set-budget "$1" "$2"
        ;;

    reset-throttle)
        if [ "$#" -lt 1 ]; then
            echo "Usage: $0 reset-throttle <group_name>" >&2
            exit 1
        fi
        BROKER_DIR="${FROGNET_BROKER_DIR:-/opt/frognet-broker}"
        "${BROKER_DIR}/venv/bin/python3" "${BROKER_DIR}/broker_metering.py" reset "$1"
        ;;

    *)
        echo "Unknown command: ${COMMAND}" >&2
        usage
        ;;
esac
