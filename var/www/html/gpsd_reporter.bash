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
# Usage:
#   gpsd_reporter.sh <SensorName> <SensorType> [Tags]
#
# Env:
#   BASE_URL  - API endpoint (default: http://databasehost.frognet/api.php)
#   FROGID    - optional override for FrogID
#   GPSD_HOST - gpsd host (default: 127.0.0.1)
#   GPSD_PORT - gpsd port (default: 2947)
#
# Deps: bash, curl, jq, python3, iproute2 (ip), (ifconfig optional),
#       gpsd running locally or reachable; optional: gpsd-clients (gpspipe), python 'gps' module
#
# Behavior:
#   - Resolves FrogID (env > /var/lib/frognet/frogid > ~/.frognet/frogid > new uuid)
#   - Derives SensorAddress from the default IPv4 + network CIDR
#   - Upserts Sensor, ensures SensorData exists ("{}")
#   - Loops every 30s: reads GPS data (TPV/SKY) and updates SensorData.jsonData (string)
set -x
set -euo pipefail

# ---------- Config ----------
: "${BASE_URL:=http://databasehost.frognet/api.php}"
: "${GPSD_HOST:=127.0.0.1}"
: "${GPSD_PORT:=2947}"

# ---------- Helpers ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 2; }; }
need curl
need jq
need python3
need ip

urlenc() {  # URL-encode a value
  python3 - <<'PY' <<<"$1"
import sys, urllib.parse
print(urllib.parse.quote(sys.stdin.read(), safe=''))
PY
}

gen_uuid() {  # UUID
  if command -v uuidGen >/dev/null 2>&1; then uuidGen
  elif command -v uuidgen >/dev/null 2>&1; then uuidgen
  else cat /proc/sys/kernel/random/uuid
  fi
}

resolve_frogid() {  # env > /var/lib/frognet > ~/.frognet > volatile
  if [[ -n "${FROGID:-}" ]]; then echo "$FROGID"; return; fi
  for state_dir in /var/lib/frognet "$HOME/.frognet"; do
    mkdir -p "$state_dir" 2>/dev/null || true
    state_file="$state_dir/frogid-$SensorName"
    if [[ -r "$state_file" ]]; then tr -d '\n' < "$state_file"; echo; return; fi
    if [[ -w "$state_dir" ]]; then local id; id="$(gen_uuid)"; printf "%s" "$id" > "$state_file"; echo "$id"; return; fi
  done
  gen_uuid
}

default_iface() { ip route 2>/dev/null | awk '/^default/ {for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}'; }
ip_for()       { ip -o -4 addr show "$1" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1; }
cidr_for_iface(){
  local ifc="$1" net
  net="$(ip route show dev "$ifc" 2>/dev/null | awk '/ proto kernel / {print $1; exit}')" || true
  if [[ -n "$net" ]]; then echo "$net"; return; fi
  local hostcidr; hostcidr="$(ip -o -4 addr show dev "$ifc" 2>/dev/null | awk '{print $4}' | head -n1)" || true
  [[ -z "$hostcidr" ]] && { echo ""; return; }
  python3 - "$hostcidr" <<'PY'
import sys, ipaddress
ip = ipaddress.ip_interface(sys.argv[1])
print(f"{ip.network.network_address}/{ip.network.prefixlen}")
PY
}
derive_addr_and_net() {
  local ifc addr net; ifc="$(default_iface || true)"
  if [[ -n "$ifc" ]]; then addr="$(ip_for "$ifc" || true)"; net="$(cidr_for_iface "$ifc" || true)"; fi
  if [[ -z "${addr:-}" ]]; then addr="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"; fi
  if [[ -z "${addr:-}" && $(command -v ifconfig || true) ]]; then
    addr="$(ifconfig | sed -En 's/127\.0\.0\.1//;s/.*inet (addr:)?(([0-9]*\.){3}[0-9]*).*/\2/p' | head -n1)"
  fi
  [[ -z "${addr:-}" ]] && { echo "Could not determine IPv4 address" >&2; return 1; }
  if [[ -z "${net:-}" ]]; then net="$(echo "$addr" | awk -F. '{printf "%s.%s.%s.0/24",$1,$2,$3}')"; fi
  echo "$addr" "$net"
}

# ---------- Args ----------
SensorName="${1:?SensorName required}"
SensorType="${2:?SensorType required}"
Tags="${3:-}"

FrogID="$(resolve_frogid)"
read SensorAddress SensorNetwork < <(derive_addr_and_net)

HostName="$(hostname -s 2>/dev/null || hostname)"
[[ -z "$Tags" ]] && Tags="gps,host=${HostName}"

echo "[info] FrogID=$FrogID  SensorName=$SensorName  Type=$SensorType" >&2
echo "[info] Address=$SensorAddress  Network=$SensorNetwork" >&2
echo "[info] Tags=$Tags  GPSD=${GPSD_HOST}:${GPSD_PORT}" >&2

# ---------- API helpers ----------
api_list(){ # $entity key=val ...
  local entity="$1"; shift || true
  local qs=""
  for kv in "$@"; do
    local k="${kv%%=*}" v="${kv#*=}"
    qs+="${qs:+&}${k}=$(urlenc "$v")"
  done
  curl -sS --max-time 10 "$BASE_URL?entity=${entity}&action=list${qs:+&$qs}"
}
api_post_json(){ curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' --data "$3" "$BASE_URL?entity=$1&action=$2"; }
api_put_json(){  curl -sS --max-time 10 -X PUT  -H 'Content-Type: application/json' --data "$3" "$BASE_URL?entity=$1&action=$2"; }

# ---------- Upsert Sensor ----------
existing="$(api_list "sensors" "FrogID=${FrogID}" "SensorAddress=${SensorAddress}" "SensorName=${SensorName}")"
SensorID="$(jq -r '.rows[0].SensorID // empty' <<<"$existing" 2>/dev/null || true)"

if [[ -z "$SensorID" ]]; then
  echo "[info] Creating Sensor…" >&2
  payload="$(jq -nc --arg f "$FrogID" --arg a "$SensorAddress" --arg n "$SensorNetwork" \
                    --arg sn "$SensorName" --arg st "$SensorType" --arg t "$Tags" \
                    '{FrogID:$f, SensorAddress:$a, SensorNetwork:$n, SensorName:$sn, SensorType:$st, Tags:$t}')"
  resp="$(api_post_json "sensors" "create" "$payload")"
  SensorID="$(jq -r '.row.SensorID // .insert_id // empty' <<<"$resp" 2>/dev/null || true)"
  [[ -z "$SensorID" ]] && { echo "[error] Failed to create Sensor: $resp" >&2; exit 1; }
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
  ok="$(jq -r '.ok // false' <<<"$sd_resp")"
  [[ "$ok" == "true" ]] || { echo "[error] SensorData create failed: $sd_resp" >&2; exit 1; }
fi

# ---------- GPS readers ----------
have_python_gps(){
  python3 - <<'PY' >/dev/null 2>&1
try:
    import gps  # gpsd-py3 / python-gps
except Exception:
    raise SystemExit(1)
PY
}

read_gps_python(){ # prints one compact JSON blob; returns 0 on success
  GPSD_HOST="$GPSD_HOST" GPSD_PORT="$GPSD_PORT" python3 - <<'PY'
import os, json, time
try:
  from gps import gps, WATCH_ENABLE, WATCH_JSON
except Exception:
  raise SystemExit(1)

host = os.environ.get("GPSD_HOST","127.0.0.1")
port = int(os.environ.get("GPSD_PORT","2947"))

session = gps(host=host, port=port, mode=WATCH_ENABLE|WATCH_JSON)
deadline = time.time() + 8.0
tpv, sky = None, None

while time.time() < deadline:
  try:
    r = session.next()
  except StopIteration:
    time.sleep(0.2); continue
  if not isinstance(r, dict): 
    continue
  c = r.get("class")
  if c == "TPV": tpv = r
  elif c == "SKY": sky = r
  if tpv and "lat" in tpv and "lon" in tpv:
    break

t = int(time.time())
def f(x): 
  try: return round(float(x), 6)
  except Exception: return None

def num(x):
  try: 
    v=float(x)
    return round(v,3)
  except Exception: 
    try: return int(x)
    except Exception: return None

mode = (tpv or {}).get("mode")
fix = {1:"NO_FIX", 2:"FIX_2D", 3:"FIX_3D"}.get(mode, "UNKNOWN")
lat = f((tpv or {}).get("lat"))
lon = f((tpv or {}).get("lon"))
alt = num((tpv or {}).get("alt"))
spd = num((tpv or {}).get("speed"))   # m/s
trk = num((tpv or {}).get("track"))   # degrees
clb = num((tpv or {}).get("climb"))   # m/s
ts  = (tpv or {}).get("time")

# DOP/EPH/EPV + satellites from SKY
sat = (sky or {}).get("satellites") or []
used = sum(1 for s in sat if s and s.get("used"))
visible = len(sat)
vdop = num((sky or {}).get("vdop"))
hdop = num((sky or {}).get("hdop"))
pdop = num((sky or {}).get("pdop"))
eph  = num((tpv or {}).get("eph"))
epv  = num((tpv or {}).get("epv"))

out = {
  "timestamp": t,
  "gps_time": ts,
  "fix": fix, "mode": mode,
  "lat": lat, "lon": lon, "alt_m": alt,
  "speed_mps": spd, "track_deg": trk, "climb_mps": clb,
  "hdop": hdop, "vdop": vdop, "pdop": pdop,
  "eph": eph, "epv": epv,
  "sats_used": used, "sats_visible": visible
}
# print(json.dumps(out, separators=(',',':')))
print(json.dumps(out)
PY
}

read_gps_gpspipe(){ # gpspipe fallback; prints JSON
  command -v gpspipe >/dev/null 2>&1 || return 1
  gpspipe -w -n 12 -h "$GPSD_HOST" -p "$GPSD_PORT" 2>/dev/null | \
  jq -s 'def num: if type=="number" then . else try (tonumber) catch null end;
    def lastclass(c): (map(select(.class==c)) | last // {});

    . as $all
    | {tpv:(lastclass("TPV")), sky:(lastclass("SKY"))}
    | .timestamp = (now|floor) | .gps_time  = (.tpv.time // null) | .mode      = (.tpv.mode // null) | .fix       = (if .mode==3 then "FIX_3D" elif .mode==2 then "FIX_2D" elif .mode==1 then "NO_FIX" else "UNKNOWN" end)
    | .lat       = (.tpv.lat|num)
    | .lon       = (.tpv.lon|num)
    | .alt_m     = (.tpv.alt|num)
    | .speed_mps = (.tpv.speed|num)
    | .track_deg = (.tpv.track|num)
    | .climb_mps = (.tpv.climb|num)
    | .hdop      = (.sky.hdop|num)
    | .vdop      = (.sky.vdop|num)
    | .pdop      = (.sky.pdop|num)
    | .eph       = (.tpv.eph|num)
    | .epv       = (.tpv.epv|num)
    | .sats_visible = (.sky.satellites|length)
    | .sats_used    = ( (.sky.satellites // []) | map(select(.used==true)) | length )
    | del(.tpv,.sky)' 2>/dev/null
}

read_gps_json(){
  if have_python_gps; then
    read_gps_python && return 0
  fi
  if read_gps_gpspipe; then
    return 0
  fi
  # Last resort: an error blob (still useful heartbeat)
  python3 - <<'PY'
import json, time
print(json.dumps({"timestamp": int(time.time()), "error": "gpsd not reachable; no gpspipe/python-gps available"}))
PY
}

# ---------- Update loop ----------
echo "[info] Starting 30s update loop for SensorID=$SensorID (Ctrl-C to stop)" >&2
while true; do
  data_json="$(read_gps_json)"
  upd_payload="$(jq -nc --arg sid "$SensorID" --arg jd "$data_json" \
    '{SensorID: ($sid|tonumber), jsonData: $jd}')"
  resp="$(api_put_json "sensor_data" "update" "$upd_payload" || true)"
  ok="$(jq -r '.ok // false' <<<"$resp" 2>/dev/null || echo false)"
  if [[ "$ok" != "true" ]]; then
    echo "[warn] Update failed: $resp" >&2
  else
    ts="$(jq -r 'try (.row.jsonData | fromjson.gps_time) // try (.row.jsonData | fromjson.timestamp) // empty' <<<"$resp" 2>/dev/null || true)"
    echo "[ok] Updated SensorData (gps_time=${ts:-n/a})" >&2
  fi
  sleep 30
done
