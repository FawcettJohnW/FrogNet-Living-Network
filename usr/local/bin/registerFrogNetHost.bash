#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC              #
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
# set -x
export BASE_URL="http://databasehost.frognet/api.php"
source "$(dirname ${BASH_SOURCE[0]})/frognet_common.sh"

. /usr/local/bin/mapInterfaces
NetworkName=`hostname`

IPAddress=`/usr/local/bin/getEth0Address`
Tags="${1-}"; shift || true

# Sanity-check IPAddress.  known_frognets.IPAddress is NOT NULL with no
# default in the schema, so an empty value here previously produced a 500
# from /api.php?entity=known_frognets&action=create on every cron tick.
if [[ -z "$IPAddress" ]]; then
    echo "registerFrogNetHost: getEth0Address returned empty — refusing to send empty IPAddress to known_frognets/create" >&2
    exit 1
fi

existingFrogNet=`curl -sS "http://databasehost.frognet/api.php?entity=known_frognets&action=list&NetworkName=$NetworkName"`
if [[ $(echo "$existingFrogNet" | jq '.rows | length') -eq 0 ]]; then
    echo "The JSON array is empty."
    frogID=`uuidgen`
    MYSQL_TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
    # CRITICAL: every value passed to json_payload must be a SEPARATE,
    # QUOTED arg.  Unquoted "${Tags}" with empty $Tags drops the arg
    # entirely, which shifts every subsequent (key,value) pair, which
    # corrupts the JSON.  In our specific log we saw IPAddress get
    # dropped (missing from the body, NOT NULL constraint fires, 500).
    # Quote everything; pass empty Tags as "".
    payload="$(json_payload \
        NetworkName  "${NetworkName}" \
        FrogID       "${frogID}" \
        IPAddress    "${IPAddress}" \
        Tags         "${Tags}" \
        LastHeartbeat "${MYSQL_TIMESTAMP}")"
    api_post "known_frognets" "create" "$payload"
else
    echo "The JSON array is not empty."
    frogID=$(echo "$existingFrogNet" | jq -r '.rows[0].FrogID')
    MYSQL_TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
    payload="$(json_payload \
        NetworkName  "${NetworkName}" \
        LastHeartbeat "${MYSQL_TIMESTAMP}")"
    api_post "known_frognets" "update" "$payload"
fi
