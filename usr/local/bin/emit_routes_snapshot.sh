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
#######################################################################
# emit_routes_snapshot.sh
#
# PURPOSE:
#   Emit the current route table snapshot as telemetry, without changing
#   any routes. This restores the older "discovery emits routes" behavior
#   in a safe, non-thrashing way.
#
# OUTPUT:
#   - Writes /etc/sentinels/routes_snapshot.json (local debug artifact)
#   - Upserts metric via metric_upsert.sh:
#       SensorType = Routes
#       MetricName = Table
#
# NOTES:
#   - Uses ip -j to avoid parsing ambiguity.
#   - Full route table stays LOCAL (/etc/sentinels/routes_snapshot.json).
#   - DB payload carries classified summary + expected_routes only.
#   - Never calls "ip route add/replace/del" (read-only).
#######################################################################
# set -x

. /usr/local/bin/mapInterfaces

IP="/usr/sbin/ip"
JQ="/usr/bin/jq"
AWK="/usr/bin/awk"
DATE="/bin/date"
HOSTNAME_BIN="/bin/hostname"
MKDIR="/usr/bin/mkdir"
CAT="/bin/cat"

SENT_DIR="/etc/sentinels"
EXPECTED="${SENT_DIR}/expected_routes"
OUT_JSON="${SENT_DIR}/routes_snapshot.json"

TTL_SEC=45

$MKDIR -p "$SENT_DIR"

NOW_TS="$($DATE +%s)"
RUN_ID="routes-${NOW_TS}-$$"
HOST_SHORT="$($HOSTNAME_BIN -s 2>/dev/null || echo FrogNetHost)"
DOMAIN="$(/usr/local/bin/getOurDomain 2>/dev/null || echo "")"

# ----------------------------
# Collect kernel routes (main)
# ----------------------------
# Full table (v4). Used to derive summary counts; raw table stays local only.
KERNEL_MAIN_JSON="$($IP -4 -j route show table main 2>/dev/null || echo '[]')"

# Stable summary for dashboards — classified by interface type
KERNEL_SUMMARY_JSON="$(
  echo "$KERNEL_MAIN_JSON" | $JQ -c '
    {
      routes_total: length,
      defaults: ([ .[] | select(.dst == "default") ] | length),
      frognet_routes: ([ .[] | select(.dst? | type=="string" and (startswith("10."))) ] | length),
      wg_tunnel_routes: ([ .[] | select(.dev? | type=="string" and startswith("wg")) ] | length),
      local_routes: ([ .[] | select(.dev? | type=="string" and (. == "eth0" or . == "wlan0")) ] | length),
      transit_routes: ([ .[] | select(.dev? | type=="string" and startswith("ham")) ] | length),
      by_dev: (reduce .[] as $r ({}; .[$r.dev // "none"] += 1))
    }' 2>/dev/null || echo '{"routes_total":0,"defaults":0,"frognet_routes":0,"wg_tunnel_routes":0,"local_routes":0,"transit_routes":0,"by_dev":{}}'
)"

# ----------------------------
# Collect expected_routes lines
# ----------------------------
EXPECTED_LINES_JSON="[]"
if [[ -f "$EXPECTED" ]]; then
  EXPECTED_LINES_JSON="$(
    $CAT "$EXPECTED" \
      | $AWK 'NF{print}' \
      | $JQ -Rs 'split("\n") | map(select(length>0))' \
      || echo '[]'
  )"
fi

# ----------------------------
# Build payloads
# ----------------------------
# Full payload: local debug artifact (includes raw route table)
LOCAL_JSON="$(
  $JQ -n \
    --arg host "$HOST_SHORT" \
    --arg domain "$DOMAIN" \
    --arg run_id "$RUN_ID" \
    --argjson now_ts "$NOW_TS" \
    --argjson ttl_sec "$TTL_SEC" \
    --argjson kernel_main "$KERNEL_MAIN_JSON" \
    --argjson kernel_summary "$KERNEL_SUMMARY_JSON" \
    --argjson expected_lines "$EXPECTED_LINES_JSON" \
    '{
      now_ts: $now_ts,
      ttl_sec: $ttl_sec,
      run_id: $run_id,
      host: $host,
      domain: $domain,
      routes: {
        kernel_main: $kernel_main,
        kernel_summary: $kernel_summary,
        expected_lines: $expected_lines
      }
    }'
)"

echo "$LOCAL_JSON" > "$OUT_JSON"

# Lean payload: DB telemetry (summary + expected only; full table stays local)
DB_JSON="$(
  $JQ -n \
    --arg host "$HOST_SHORT" \
    --arg domain "$DOMAIN" \
    --arg run_id "$RUN_ID" \
    --argjson now_ts "$NOW_TS" \
    --argjson ttl_sec "$TTL_SEC" \
    --argjson kernel_summary "$KERNEL_SUMMARY_JSON" \
    --argjson expected_lines "$EXPECTED_LINES_JSON" \
    '{
      now_ts: $now_ts,
      ttl_sec: $ttl_sec,
      run_id: $run_id,
      host: $host,
      domain: $domain,
      routes: {
        kernel_summary: $kernel_summary,
        expected_lines: $expected_lines
      }
    }'
)"

# ----------------------------
# Upsert into DB via existing metric_upsert.sh
# ----------------------------
if [[ -x /usr/local/bin/metric_upsert.sh ]]; then
  # SensorName will become: <FQDN>.Routes.Table (per metric_upsert.sh rules)
  /usr/local/bin/metric_upsert.sh "Routes" "Table" "$DB_JSON" >/dev/null || true
fi

exit 0
