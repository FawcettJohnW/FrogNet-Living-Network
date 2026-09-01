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
##############################################################
#  routeMetrics.sh (API-correct, uses Sensor.FrogID)         #
#
#  USAGE:
#    routeMetrics.sh <SensorType> '<jsonData>'
#
#  Example:
#    routeMetrics.sh SemanticWrapper \
#      '{"counters":{"defaults_deleted_10x":0,"offlan0":1},"latency_ms":36}'
##############################################################

set -x

SENSOR_TYPE="$1"
JSON_DATA="$2"

HOSTNAME="$(hostname 2>/dev/null || echo UnknownFrog)"

# 1. Resolve databasehost.frognet
DB_HOST_IP="$(getent hosts databasehost.frognet 2>/dev/null | awk '{print $1}' | head -n1 || echo "")"
if [[ -z "$DB_HOST_IP" ]]; then
    /usr/local/bin/debugTag "routeMetrics: no databasehost.frognet resolved; skipping"
    exit 0
fi

API_BASE="http://databasehost.frognet/api.php"

# 2. Look up the FrogID for this host’s SemanticWrapper sensor
FROG_ID="$(
    curl -sS --max-time 15 \
      "$API_BASE?entity=sensors&action=values&SensorName=$HOSTNAME&SensorType=SemanticWrapper" \
      2>/dev/null \
    | jq -r '.rows[0].FrogID // empty'
)"

if [[ -z "$FROG_ID" ]]; then
    /usr/local/bin/debugTag "routeMetrics: no FrogID found for $HOSTNAME SemanticWrapper; skipping metrics"
    exit 0
fi

SENSOR_NAME="${HOSTNAME}"

# 3. Build full payload
read -r -d '' PAYLOAD <<EOF || true
{
  "FrogID": "$FROG_ID",
  "SensorName": "$SENSOR_NAME",
  "SensorType": "$SENSOR_TYPE",
  "jsonData": $JSON_DATA
}
EOF

# 4. POST to sensor_data / upsert
/usr/local/bin/debugTag "routeMetrics: posting metrics for $SENSOR_NAME with FrogID=$FROG_ID"
curl -sS --max-time 15 \
  -X POST \
  "$API_BASE?entity=sensor_data&action=upsert" \
  -H "Content-Type: application/json" \
  --data "$PAYLOAD" >/dev/null 2>&1 || /usr/local/bin/debugTag "routeMetrics: curl upsert failed"

exit 0
