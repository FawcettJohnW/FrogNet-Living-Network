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
# frognet-create-passcode.sh — Create a passcode on the broker
#
# Usage: frognet-create-passcode.sh <broker_url> <admin_token> <passcode> <group_name> [max_tunnels]
#
# frognet-create-passcode.sh https://streamingfrog.com:8443/frognet-broker gcoCFq_eM-6yUoqFed-Leb34wGBsA8IV3tKfAs0yRhg flying-pizza frognet_test 8

if [ "$#" -lt 4 ]; then
    echo "Usage: $0 <broker_url> <admin_token> <passcode> <group_name> [max_tunnels]" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  $0 https://streamingfrog.com:8443/frognet-broker gcoCFq_... smoking-stovepipe family_smith 4" >&2
    exit 1
fi

BROKER_URL="${1%/}"
ADMIN_TOKEN="$2"
PASSCODE="$3"
GROUP_NAME="$4"
MAX_TUNNELS="${5:-4}"

RESPONSE=$(curl -sS -k -w "\n%{http_code}" \
    -X POST \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"passcode\":\"${PASSCODE}\",\"group_name\":\"${GROUP_NAME}\",\"tier\":\"standard\",\"max_tunnels\":${MAX_TUNNELS}}" \
    "${BROKER_URL}/api/v1/passcodes" 2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "201" ]; then
    echo "Passcode created:"
    echo "$BODY" | jq .
else
    echo "ERROR: HTTP $HTTP_CODE" >&2
    echo "$BODY" >&2
    exit 1
fi
