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
# sysperf_reporter.sh
# Usage:
#   sysperf_reporter.sh <SensorName> <SensorType> [Tags]
#
# Env:
#   BASE_URL        - API endpoint (default: http://databasehost.frognet/api.php)
#   FROGID          - optional override for FrogID
#   SAMPLE_SEC      - perf sampling window per iteration (default: 1)
#   LOOP_SEC        - loop period seconds (default: 30)
#
# Deps: bash, curl, jq, python3, iproute2 (ip), (ifconfig optional)
#
# Behavior:
#   - Resolves FrogID (env > /var/lib/frognet/frogid > ~/.frognet/frogid > new uuid)
#   - Derives SensorAddress (IPv4) and SensorNetwork (CIDR)
#   - Upserts Sensor; ensures SensorData exists with "{}"
#   - Every LOOP_SEC: collects metrics over SAMPLE_SEC and updates SensorData.jsonData (string)
set -x
set -euo pipefail

# ---------- Config ----------
: "${BASE_URL:=http://databasehost.frognet/api.php}"
: "${SAMPLE_SEC:=1}"
: "${LOOP_SEC:=30}"

# ---------- Helpers ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 2; }; }
need curl; need jq; need python3; need ip

urlenc() {
  python3 - <<'PY' <<<"$1"
import sys, urllib.parse
print(urllib.parse.quote(sys.stdin.read(), safe=''))
PY
}

gen_uuid() {
  if command -v uuidGen >/dev/null 2>&1; then uuidGen
  elif command -v uuidgen >/dev/null 2>&1; then uuidgen
  else cat /proc/sys/kernel/random/uuid
  fi
}

resolve_frogid() {
  if [[ -n "${FROGID:-}" ]]; then echo "$FROGID"; return; fi
  for state_dir in /var/lib/frognet "$HOME/.frognet"; do
    mkdir -p "$state_dir" 2>/dev/null || true
    state_file="$state_dir/frogid"
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
[[ -z "$Tags" ]] && Tags="perf,host=${HostName}"

echo "[info] FrogID=$FrogID  SensorName=$SensorName  Type=$SensorType" >&2
echo "[info] Address=$SensorAddress  Network=$SensorNetwork" >&2
echo "[info] Tags=$Tags  sample=${SAMPLE_SEC}s  loop=${LOOP_SEC}s" >&2

# ---------- API helpers ----------
api_list(){ # $entity key=val ...
  local entity="$1"; shift || true
  local qs=""
  for kv in "$@"; do
    local k="${kv%%=*}" v="${kv#*=}"
    qs+="${qs:+&}${v}"
  done
  curl -sS --max-time 10 "$BASE_URL?entity=${entity}&action=list${qs:+&$qs}"
}
api_post_json(){ curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' --data "$3" "$BASE_URL?entity=$1&action=$2"; }
api_put_json(){  curl -sS --max-time 10 -X PUT  -H 'Content-Type: application/json' --data "$3" "$BASE_URL?entity=$1&action=$2"; }

# ---------- Upsert Sensor ----------
existing="$(api_list "sensors" "SensorAddress=${SensorAddress}" "SensorName=${SensorName}" "SensorType=${SensorType}")"
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

# ---------- Perf reader (Python, no external deps) ----------
read_perf_json() {
  SAMPLE_SEC="$SAMPLE_SEC" python3 - <<'PY'
import os, time, json, platform, re, glob

sample = float(os.environ.get("SAMPLE_SEC","1") or 1)

def read_proc_stat():
  with open("/proc/stat","r") as f:
    line = f.readline()
  parts = line.strip().split()
  if parts[0] != "cpu": return {}
  vals = list(map(int, parts[1:]))  # user nice system idle iowait irq softirq steal guest guest_nice (guest* ignore)
  keys = ["user","nice","system","idle","iowait","irq","softirq","steal","guest","guest_nice"]
  return dict(zip(keys, vals))

def cpu_pct():
  a = read_proc_stat(); time.sleep(sample); b = read_proc_stat()
  if not a or not b: return {}
  def d(k): return max(0, b.get(k,0)-a.get(k,0))
  total = sum(d(k) for k in ["user","nice","system","idle","iowait","irq","softirq","steal"])
  if total <= 0: total = 1
  pct = { k: round(d(k)*100.0/total,2) for k in ["user","nice","system","idle","iowait","irq","softirq","steal"] }
  pct["total_busy"] = round(100.0 - pct.get("idle",0.0), 2)
  return pct

def loadavg():
  with open("/proc/loadavg") as f:
    a,b,c,*_ = f.read().split()
  return {"1": float(a), "5": float(b), "15": float(c)}

def meminfo():
  kv = {}
  with open("/proc/meminfo") as f:
    for line in f:
      parts = line.split(':',1)
      if len(parts)!=2: continue
      k = parts[0].strip()
      v = parts[1].strip().split()[0]
      try: kv[k] = int(v)  # kB
      except: pass
  tot = kv.get("MemTotal",0)
  free = kv.get("MemFree",0)
  avail = kv.get("MemAvailable",free)
  buf = kv.get("Buffers",0)
  cached = kv.get("Cached",0) + kv.get("SReclaimable",0)
  used = max(0, tot - free - buf - cached)
  pct_used = round((used / tot * 100.0),2) if tot>0 else 0.0
  stot = kv.get("SwapTotal",0); sfree = kv.get("SwapFree",0); sused = max(0, stot - sfree)
  spct = round((sused / stot * 100.0),2) if stot>0 else 0.0
  return {
    "mem_kb": {"total":tot,"used":used,"free":free,"available":avail,"buffers":buf,"cached":cached,"pct_used":pct_used},
    "swap_kb":{"total":stot,"used":sused,"free":sfree,"pct_used":spct}
  }

def sector_size(dev):
  try:
    with open(f"/sys/block/{dev}/queue/hw_sector_size") as f:
      return int(f.read().strip() or "512")
  except: return 512

DEV_RE = re.compile(r'^(sd|vd|nvme|mmcblk)')

def read_diskstats():
  st = {}
  with open("/proc/diskstats") as f:
    for line in f:
      parts = line.split()
      if len(parts) < 14: continue
      dev = parts[2]
      if not DEV_RE.match(dev): continue
      reads_completed = int(parts[3]); reads_merged = int(parts[4]); sectors_read = int(parts[5]); ms_reading = int(parts[6])
      writes_completed = int(parts[7]); writes_merged = int(parts[8]); sectors_written = int(parts[9]); ms_writing = int(parts[10])
      st[dev] = (reads_completed, writes_completed, sectors_read, sectors_written)
  return st

def disk_rates():
  a = read_diskstats(); t0 = time.time(); time.sleep(sample); b = read_diskstats(); dt = max(1e-6, time.time()-t0)
  total = {"read_ios_per_s":0.0,"write_ios_per_s":0.0,"read_bytes_per_s":0.0,"write_bytes_per_s":0.0}
  devices = {}
  for dev, A in a.items():
    B = b.get(dev); if not B: continue
    r_ios = max(0,B[0]-A[0]) / dt
    w_ios = max(0,B[1]-A[1]) / dt
    r_sec = max(0,B[2]-A[2]); w_sec = max(0,B[3]-A[3])
    ssize = sector_size(dev)
    r_bps = r_sec * ssize / dt
    w_bps = w_sec * ssize / dt
    devices[dev] = {
      "read_ios_per_s": round(r_ios,2),
      "write_ios_per_s": round(w_ios,2),
      "read_bytes_per_s": round(r_bps,2),
      "write_bytes_per_s": round(w_bps,2)
    }
    total["read_ios_per_s"] += r_ios; total["write_ios_per_s"] += w_ios
    total["read_bytes_per_s"] += r_bps; total["write_bytes_per_s"] += w_bps
  for k in list(total): total[k] = round(total[k],2)
  return {"total": total, "devices": devices}

def read_netdev():
  data = {}
  with open("/proc/net/dev") as f:
    for line in f:
      if ':' not in line: continue
      iface, stats = line.split(':',1)
      iface = iface.strip()
      parts = stats.split()
      if len(parts) < 16: continue
      rx_bytes, rx_packets, rx_errs, rx_drop = map(int, parts[0:4])
      tx_bytes, tx_packets, tx_errs, tx_drop = map(int, parts[8:12])
      data[iface] = (rx_bytes, rx_packets, tx_bytes, tx_packets, rx_errs, rx_drop, tx_errs, tx_drop)
  return data

def net_rates():
  a = read_netdev(); t0 = time.time(); time.sleep(sample); b = read_netdev(); dt = max(1e-6, time.time()-t0)
  total = {"rx_bytes_per_s":0.0,"tx_bytes_per_s":0.0,"rx_packets_per_s":0.0,"tx_packets_per_s":0.0}
  interfaces = {}
  for iface, A in a.items():
    B = b.get(iface); if not B: continue
    rx_bps = max(0,B[0]-A[0]) / dt
    rx_pps = max(0,B[1]-A[1]) / dt
    tx_bps = max(0,B[2]-A[2]) / dt
    tx_pps = max(0,B[3]-A[3]) / dt
    interfaces[iface] = {
      "rx_bytes_per_s": round(rx_bps,2), "tx_bytes_per_s": round(tx_bps,2),
      "rx_packets_per_s": round(rx_pps,2), "tx_packets_per_s": round(tx_pps,2),
      "rx_errs": B[4]-A[4], "rx_drop": B[5]-A[5], "tx_errs": B[6]-A[6], "tx_drop": B[7]-A[7]
    }
    total["rx_bytes_per_s"] += rx_bps; total["tx_bytes_per_s"] += tx_bps
    total["rx_packets_per_s"] += rx_pps; total["tx_packets_per_s"] += tx_pps
  for k in list(total): total[k] = round(total[k],2)
  return {"total": total, "interfaces": interfaces}

def temps():
  out=[]
  for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
    try:
      with open(path) as f: milli=int(f.read().strip())
      name=path.split("/")[-2]
      out.append({"name": name, "temp_c": round(milli/1000.0,1)})
    except: pass
  return out

payload = {
  "timestamp": int(time.time()),
  "host": platform.node(),
  "sample_sec": sample,
  "cpu_pct": cpu_pct(),
  "loadavg": loadavg(),
  **meminfo(),
  "disk_io": disk_rates(),
  "net_io": net_rates(),
}
t = temps()
if t: payload["temps_c"] = t

print(json.dumps(payload, separators=(',',':')))
PY
}

# ---------- Update loop ----------
echo "[info] Starting ${LOOP_SEC}s update loop for SensorID=$SensorID (Ctrl-C to stop)" >&2
while true; do
  data_json="$(read_perf_json)"
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
  sleep "$LOOP_SEC"
done
