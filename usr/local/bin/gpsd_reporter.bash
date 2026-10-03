#!/usr/bin/env bash
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
# gpsd_reporter.sh
#
# Purpose:
#   Periodically read GPS data and publish it as a metric using metric_upsert.
#
# Behavior:
#   - Every 30 seconds:
#       * Reads current GPS JSON from /usr/local/bin/get_gps_data.bash
#       * Calls metric_upsert.sh "GPS" "Position" '<jsonData>'
#
#   - metric_upsert.sh takes care of:
#       * Creating a local-bound Sensor (with sentinel in /etc/sentinels)
#       * Managing FrogID
#       * Upserting into sensor_data via upsert_by_name
#
# Requirements:
#   - /usr/local/bin/get_gps_data.bash  → prints a single JSON object to stdout
#   - /usr/local/bin/metric_upsert.sh  → the 3-arg helper you provided
#   - bash, curl, jq, python3 (as needed by metric_upsert/get_gps_data)
#
# Notes:
#   - SensorType is fixed to "GPS"
#   - MetricName is "Position" (SensorName will be: <hostname>.GPS.Position)

SENSOR_TYPE="GPS"
METRIC_NAME="Position"

# Where to get GPS JSON
GPS_CMD="/usr/local/bin/get_gps_data.bash"

# Where metric_upsert lives
METRIC_UPSERT="/usr/local/bin/metric_upsert.sh"

# Simple sanity checks
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 2; }; }

need bash
need jq
[ -x "$GPS_CMD" ] || { echo "[error] GPS command not executable: $GPS_CMD" >&2; exit 1; }
[ -x "$METRIC_UPSERT" ] || { echo "[error] metric_upsert not executable: $METRIC_UPSERT" >&2; exit 1; }

HOSTNAME="$(hostname 2>/dev/null || echo UnknownFrog)"
echo "[info] Starting GPS metric reporter on host=$HOSTNAME type=$SENSOR_TYPE metric=$METRIC_NAME" >&2
echo "[info] Using GPS source: $GPS_CMD" >&2
echo "[info] Using metric_upsert: $METRIC_UPSERT" >&2

# -------------------------------------------------------------------
# Main loop: every 30s, read GPS JSON and upsert metric
# -------------------------------------------------------------------
while true; do
  # Get current GPS JSON (must be a single valid JSON object)
  if ! data_json="$("$GPS_CMD" 2>/dev/null)"; then
    echo "[warn] GPS command failed; publishing error metric instead" >&2
    data_json='{"timestamp":'"$(date +%s)"',"error":"get_gps_data failed"}'
  fi

  # Validate it's at least parseable JSON; if not, wrap it
  if ! echo "$data_json" | jq . >/dev/null 2>&1; then
    echo "[warn] GPS output not valid JSON; wrapping as error" >&2
    esc=$(printf '%s' "$data_json" | jq -Rs .)
    data_json='{"timestamp":'"$(date +%s)"',"error":"invalid gps json","raw":'"$esc"'}'
  fi

  echo "[info] metric_upsert: type=$SENSOR_TYPE name=$METRIC_NAME json=$data_json" >&2

  # Call metric_upsert.sh <SensorType> <MetricName> '<jsonData>'
  # NOTE: pass the JSON as a single argument, keep quoting tight
  if ! "$METRIC_UPSERT" "$SENSOR_TYPE" "$METRIC_NAME" "$data_json"; then
    echo "[warn] metric_upsert failed for GPS metric" >&2
  fi

  sleep 30
done
