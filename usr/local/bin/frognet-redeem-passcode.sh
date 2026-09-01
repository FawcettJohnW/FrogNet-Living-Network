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
# frognet-redeem-passcode.sh — Redeem a passcode, create the group, get the group token
#
# Usage: frognet-redeem-passcode.sh <broker_url> <passcode>

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <broker_url> <passcode>" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 https://streamingfrog.com:8443/frognet-broker smoking-stovepipe" >&2
    exit 1
fi

BROKER_URL="${1%/}"
PASSCODE="$2"

RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{\"passcode\":\"${PASSCODE}\"}" \
    "${BROKER_URL}/api/v1/redeem" 2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
    GROUP_TOKEN=$(echo "$BODY" | jq -r '.group_token')
    GROUP_NAME=$(echo "$BODY" | jq -r '.group_name')
    STATUS=$(echo "$BODY" | jq -r '.status')
    MAX_TUNNELS=$(echo "$BODY" | jq -r '.max_tunnels')

    echo "Group:      ${GROUP_NAME}"
    echo "Status:     ${STATUS}"
    echo "Max tunnels: ${MAX_TUNNELS}"
    echo "Group token: ${GROUP_TOKEN}"
    echo ""
    echo "Save this token. Your nodes need it to advertise and join channels."
else
    echo "ERROR: HTTP $HTTP_CODE" >&2
    echo "$BODY" >&2
    exit 1
fi
