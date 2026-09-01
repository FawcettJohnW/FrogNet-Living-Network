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
# metric_upsert.sh (stateless; server does lookup/create/upsert)
#
# Usage:
#   metric_upsert.sh <SensorType> <MetricName> <jsonData>
#
# Notes:
#   - NO retries, NO caching, NO background loops.
#   - Posts to databasehost.frognet via /api.php?entity=sensor_data&action=upsert_by_name
#
# ARCHITECTURE-CORRECT TRANSPORT (v3):
#   - All FrogNet HTTP must enter the local proxy first.
#   - Connect to 127.0.0.1:80 and use Host: databasehost.frognet
#   - This allows hop-by-hop FAST/SEMANTIC conversion.
#
# FIXES:
#   1) SensorName is domain-qualified using getFrogNet.bash, not hostname -s.
#   2) SensorNetwork is stable /24 (A.B.C.0/24), never the host IP.
#      We DO NOT trust convertToShortIP for this because it has returned host IPs.
#######################################################################
# set -x

SENSOR_TYPE="${1:?SensorType required}"
METRIC_NAME="${2:?MetricName required}"
METRIC_JSON="${3:?jsonData required}"

API_HOST="${FROGNET_DB_HOST:-databasehost.frognet}"
API_PATH="/api.php"
API_QS="entity=sensor_data&action=upsert_by_name"

# IMPORTANT: connect to local proxy, preserve destination via Host header
PROXY_CONNECT_HOST="${FROGNET_PROXY_HOST:-127.0.0.1}"
PROXY_CONNECT_PORT="${FROGNET_PROXY_PORT:-80}"
PROXY_URL="http://${PROXY_CONNECT_HOST}:${PROXY_CONNECT_PORT}${API_PATH}?${API_QS}"

DIAG="${FROGNET_DIAG:-0}"
CORRID="$(date +%s%N)-$$"

# ------------------------------------------------------------
# Canonical identity: use getFrogNet.bash if available
# getFrogNet.bash outputs: fqdn,hostPath,wlan0IP,wlan1IP
# ------------------------------------------------------------
FN_ECHO="$(/usr/local/bin/getFrogNet.bash  || true)"
FN_ECHO="$(echo "$FN_ECHO" | tr -d '\r\n')"

FQDN="$(echo "$FN_ECHO" | awk -F',' '{print $1}' | xargs)"
HOSTPATH="$(echo "$FN_ECHO" | awk -F',' '{print $2}' | xargs)"

# Fallbacks (best-effort; should rarely trigger if getFrogNet works)
if [[ -z "${FQDN:-}" ]]; then
  SHORT="$(hostname -s  || hostname  || echo FrogNetHost)"
  DOMAIN="$(/usr/local/bin/getOurDomain  || true)"
  if [[ -n "${DOMAIN:-}" ]]; then
    FQDN="${SHORT}.${DOMAIN}"
  else
    FQDN="${SHORT}"
  fi
fi

# SensorAddress: authoritative FrogNet host path if available; else eth0 address
SENSOR_ADDR=""
if [[ -n "${HOSTPATH:-}" && "${HOSTPATH}" =~ ^([0-9]+\.){3}[0-9]+$ ]]; then
  SENSOR_ADDR="$HOSTPATH"
else
  SENSOR_ADDR="$(/usr/local/bin/getEth0Address || echo "")"
fi

# SensorNetwork: stable /24 derived from SensorAddress (never host IP)
SENSOR_NET=""
if [[ -n "$SENSOR_ADDR" && "$SENSOR_ADDR" =~ ^([0-9]+\.){3}[0-9]+$ ]]; then
  SENSOR_NET="${SENSOR_ADDR%.*}.0/24"
fi

SENSOR_LOC="${SENSOR_ADDR}"
SENSOR_NAME="${FQDN}.${SENSOR_TYPE}.${METRIC_NAME}"

# Reject obviously bad metrics payloads
case "$METRIC_JSON" in
  "null"|"[]"|"{}")
    echo "metric_upsert: refusing empty jsonData payload" >&2
    exit 3
    ;;
esac

PY_ERR="$(mktemp)"
PAYLOAD="$(
python3 - "$SENSOR_NAME" "$SENSOR_TYPE" "$SENSOR_ADDR" "$SENSOR_NET" "$METRIC_JSON" "$SENSOR_LOC" 2>"$PY_ERR" <<'PY'
import json, sys
sensor_name = sys.argv[1]
sensor_type = sys.argv[2]
sensor_addr = sys.argv[3]
sensor_net  = sys.argv[4]
raw_json    = sys.argv[5]
sensor_loc  = sys.argv[6] if len(sys.argv) > 6 else ""

try:
    json_data = json.loads(raw_json)
except Exception:
    json_data = {"value": raw_json}

payload = {
  "SensorName": sensor_name,
  "SensorType": sensor_type,
  "SensorLocation": sensor_loc,
  "SensorAddress": sensor_addr,
  "SensorNetwork": sensor_net,
  "jsonData": json_data
}
print(json.dumps(payload, separators=(",",":")))
PY
)"
PY_RC=$?

if [[ $PY_RC -ne 0 ]]; then
  echo "metric_upsert: python3 failed rc=$PY_RC stderr=$(cat "$PY_ERR")" >&2
  rm -f "$PY_ERR"
  exit 4
fi

if [[ -z "$PAYLOAD" ]]; then
  echo "metric_upsert: python3 produced empty PAYLOAD stderr=$(cat "$PY_ERR")" >&2
  rm -f "$PY_ERR"
  exit 5
fi
rm -f "$PY_ERR"

RESP="$(
curl -sS --max-time 5 \
  -X POST "$PROXY_URL" \
  -H "Host: ${API_HOST}" \
  -H "Content-Type: application/json" \
  -H "Accept-Encoding: identity" \
  -H "X-FrogNet-CorrID: $CORRID" \
  -H "X-FrogNet-Diag: ${DIAG}" \
  --data "$PAYLOAD"
)"
CURL_RC=$?

if [[ $CURL_RC -ne 0 ]]; then
  echo "metric_upsert: curl failed rc=$CURL_RC sensor=$SENSOR_NAME" >&2
fi

if [[ "$DIAG" == "1" ]]; then
  echo "metric_upsert diag corrid=$CORRID url=$PROXY_URL host=$API_HOST sensor=$SENSOR_NAME json_len=${#METRIC_JSON} resp=$RESP" >&2
fi

echo "$RESP"
exit $CURL_RC
