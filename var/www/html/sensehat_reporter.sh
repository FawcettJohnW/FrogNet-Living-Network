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
# sensehat_reporter.sh
# Usage:
#   sensehat_reporter.sh <SensorName> <SensorType> [Tags]
#
# Env:
#   BASE_URL   - API endpoint (default: http://databasehost.frognet/api.php)
#   FROGID     - optional override for FrogID
#
# Deps: bash, curl, jq, python3, iproute2 (ip), (ifconfig optional fallback)
#       Python module: sense_hat  (or sense_emu as fallback)
#
# Behavior:
#   - Resolves FrogID (env > /var/lib/frognet/frogid > ~/.frognet/frogid > new uuid)
#   - Derives SensorAddress from the default IPv4
#   - Derives SensorNetwork CIDR from the interface/prefix
#   - Creates Sensor if not present; ensures SensorData exists with "{}"
#   - Loops every 30 seconds: reads Sense HAT JSON and updates SensorData.jsonData
set -x
set -euo pipefail

# ---------- Config / Defaults ----------
: "${BASE_URL:=http://databasehost.frognet/api.php}"

# ---------- Helpers ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 2; }; }
need curl
need jq
need python3
need ip

# URL-encode
urlenc() {
  python3 - <<'PY' <<<"$1"
import sys, urllib.parse
print(urllib.parse.quote(sys.stdin.read(), safe=''))
PY
}

# UUID generator
gen_uuid() {
  if command -v uuidGen >/dev/null 2>&1; then uuidGen
  elif command -v uuidgen >/dev/null 2>&1; then uuidgen
  else cat /proc/sys/kernel/random/uuid
  fi
}

# Persist a stable FrogID (env > /var/lib/frognet > ~/.frognet)
resolve_frogid() {
  if [[ -n "${FROGID:-}" ]]; then echo "$FROGID"; return; fi
  for state_dir in /var/lib/frognet "$HOME/.frognet"; do
    mkdir -p "$state_dir" 2>/dev/null || true
    state_file="$state_dir/frogid"
    if [[ -r "$state_file" ]]; then tr -d '\n' < "$state_file"; echo; return; fi
    if [[ -w "$state_dir" ]]; then
      local id; id="$(gen_uuid)"
      printf "%s" "$id" > "$state_file"
      echo "$id"
      return
    fi
  done
  # last resort (non-persistent)
  gen_uuid
}

# Default route interface
default_iface() {
  ip route 2>/dev/null | awk '/^default/ {for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}'
}

# IPv4 address on iface
ip_for() {
  ip -o -4 addr show "$1" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1
}

# CIDR (network/prefix) for iface
cidr_for_iface() {
  local ifc="$1"
  # Use kernel route first
  local net
  net="$(ip route show dev "$ifc" 2>/dev/null | awk '/ proto kernel / {print $1; exit}')"
  if [[ -n "$net" ]]; then echo "$net"; return; fi
  # Derive from assigned prefix
  local hostcidr
  hostcidr="$(ip -o -4 addr show dev "$ifc" 2>/dev/null | awk '{print $4}' | head -n1)"
  if [[ -z "$hostcidr" ]]; then echo ""; return; fi
  python3 - "$hostcidr" <<'PY'
import sys, ipaddress
cidr = sys.argv[1]
ip = ipaddress.ip_interface(cidr)
print(f"{ip.network.network_address}/{ip.network.prefixlen}")
PY
}

derive_addr_and_net() {
  local ifc addr net
  ifc="$(default_iface || true)"
  if [[ -n "$ifc" ]]; then
    addr="$(ip_for "$ifc" || true)"
    net="$(cidr_for_iface "$ifc" || true)"
  fi
  # fallbacks
  if [[ -z "${addr:-}" ]]; then
    addr="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -z "${addr:-}" ]]; then
    if command -v ifconfig >/dev/null 2>&1; then
      addr="$(ifconfig | sed -En 's/127\.0\.0\.1//;s/.*inet (addr:)?(([0-9]*\.){3}[0-9]*).*/\2/p' | head -n1)"
    fi
  fi
  if [[ -z "${addr:-}" ]]; then
    echo "Could not determine IPv4 address" >&2
    return 1
  fi
  if [[ -z "${net:-}" ]]; then
    # coarse fallback: assume /24
    net="$(echo "$addr" | awk -F. '{printf "%s.%s.%s.0/24",$1,$2,$3}')"
  fi
  echo "$addr" "$net"
}

# ---------- Args ----------
SensorName="${1:?SensorName required}"
SensorType="${2:?SensorType required}"
Tags="${3:-}"

FrogID="$(resolve_frogid)"
read SensorAddress SensorNetwork < <(derive_addr_and_net)

HostName="$(hostname -s 2>/dev/null || hostname)"
if [[ -z "$Tags" ]]; then
  Tags="sensehat,host=${HostName}"
fi

echo "[info] FrogID=$FrogID  SensorName=$SensorName  Type=$SensorType" >&2
echo "[info] Address=$SensorAddress  Network=$SensorNetwork" >&2
echo "[info] Tags=$Tags" >&2

# ---------- API helpers ----------
api_list() { # $entity key=val ...
  local entity="$1"; shift || true
  local qs=""
  for kv in "$@"; do
    local k="${kv%%=*}" v="${kv#*=}"
    qs+="${qs:+&}${v}"
  done
  curl -sS --max-time 10 "$BASE_URL?entity=${entity}&action=list${qs:+&$qs}"
}

api_post_json() { # $entity $action $json
  curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
    --data "$3" "$BASE_URL?entity=$1&action=$2"
}

api_put_json() { # $entity $action $json
  curl -sS --max-time 10 -X PUT -H 'Content-Type: application/json' \
    --data "$3" "$BASE_URL?entity=$1&action=$2"
}

# ---------- Upsert Sensor ----------
existing="$(api_list "sensors" "FrogID=${FrogID}" "SensorAddress=${SensorAddress}" "SensorName=${SensorName}")"
SensorID="$(jq -r '.rows[0].SensorID // empty' <<<"$existing" 2>/dev/null || true)"

if [[ -z "$SensorID" ]]; then
  echo "[info] Creating Sensor..." >&2
  payload="$(jq -nc \
    --arg f "$FrogID" \
    --arg a "$SensorAddress" \
    --arg n "$SensorNetwork" \
    --arg sn "$SensorName" \
    --arg st "$SensorType" \
    --arg t "$Tags" \
    '{FrogID:$f, SensorAddress:$a, SensorNetwork:$n, SensorName:$sn, SensorType:$st, Tags:$t}')"
  resp="$(api_post_json "sensors" "create" "$payload")"
  SensorID="$(jq -r '.row.SensorID // .insert_id // empty' <<<"$resp" 2>/dev/null || true)"
  if [[ -z "$SensorID" ]]; then
    echo "[error] Failed to create Sensor: $resp" >&2
    exit 1
  fi
  echo "[info] Created SensorID=$SensorID" >&2
else
  echo "[info] Found existing SensorID=$SensorID" >&2
fi

# ---------- Ensure SensorData row exists ----------
sd_check="$(api_list "sensor_data" "SensorID=${SensorID}")"
sd_exists="$(jq -r '.count // 0' <<<"$sd_check")"
if [[ "$sd_exists" == "0" ]]; then
  echo "[info] Creating initial SensorData with empty JSON" >&2
  sd_payload="$(jq -nc --arg sid "$SensorID" --arg f "$FrogID" --arg jd "{}" \
    '{SensorID: ($sid|tonumber), FrogID:$f, jsonData:$jd}')"
  sd_resp="$(api_post_json "sensor_data" "create" "$sd_payload")"
  # ok="$(jq -r '.ok // false' <<<"$sd_resp")"
  # [[ "$ok" == "true" ]] || { echo "[error] SensorData create failed: $sd_resp" >&2; exit 1; }
fi

# ---------- Sense HAT reader (Python) ----------
read_sensehat_json() {
  python3 - <<'PY'
import json, time
try:
    try:
        from sense_hat import SenseHat
    except Exception:
        from sense_emu import SenseHat  # fallback to emulator if available
    s = SenseHat()
    # Some sensors can be noisy/warm; take small average where sensible
    t = sum(s.get_temperature() for _ in range(3)) / 3.0
    h = sum(s.get_humidity() for _ in range(3)) / 3.0
    p = sum(s.get_pressure() for _ in range(3)) / 3.0
    o = s.get_orientation()  # dict: pitch, roll, yaw
    a = s.get_accelerometer_raw()  # x,y,z
    g = s.get_gyroscope_raw()      # x,y,z
    m = s.get_compass_raw()        # x,y,z
    out = {
        "timestamp": int(time.time()),
        "temperature_c": round(t, 2),
        "humidity_pct": round(h, 2),
        "pressure_mbar": round(p, 2),
        "orientation": {k: round(float(v), 3) for k, v in o.items()},
        "accel": {k: round(float(v), 4) for k, v in a.items()},
        "gyro":  {k: round(float(v), 4) for k, v in g.items()},
        "mag":   {k: round(float(v), 4) for k, v in m.items()}
    }
    print(json.dumps(out, separators=(',',':')))
except Exception as e:
    # Print an empty JSON with error field so the DB still stores something
    print(json.dumps({"timestamp": int(time.time()), "error": str(e)}))
PY
}

# ---------- Update loop ----------
echo "[info] Starting 30s update loop for SensorID=$SensorID (Ctrl-C to stop)" >&2
while true; do
  data_json="$(read_sensehat_json)"
  # Build update payload (jsonData is a *string* column)
  upd_payload="$(jq -nc --arg sid "$SensorID" --arg jd "$data_json" \
    '{SensorID: ($sid|tonumber), jsonData: $jd}')"
  resp="$(api_put_json "sensor_data" "update" "$upd_payload" || true)"
  ok="$(jq -r '.ok // false' <<<"$resp" 2>/dev/null || echo false)"
  if [[ "$ok" != "true" ]]; then
    echo "[warn] Update failed: $resp" >&2
  else
    ts="$(jq -r 'try (.row.jsonData | fromjson.timestamp) // now' <<<"$resp" 2>/dev/null || date +%s)"
    echo "[ok] Updated SensorData (ts=${ts})" >&2
  fi
  sleep 30
done
