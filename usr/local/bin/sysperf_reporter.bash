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
# sysperf_reporter.sh (metric_upsert version)
#
# Purpose:
#   Periodically collect system performance metrics and publish them
#   via metric_upsert:
#
#       metric_upsert.sh "System" "Perf" '<jsonData>'
#
# Env:
#   SAMPLE_SEC - sampling window per iteration (default: 1)
#   LOOP_SEC   - loop period seconds (default: 30)
#
# Deps:
#   - bash, jq, python3, ip
#   - /usr/local/bin/metric_upsert.sh

# set -x

SENSOR_TYPE="System"
METRIC_NAME="Perf"

: "${SAMPLE_SEC:=1}"
: "${LOOP_SEC:=30}"

HOSTNAME="$(hostname 2>/dev/null || echo UnknownFrog)"

METRIC_UPSERT="/usr/local/bin/metric_upsert.sh"

# ---------- Helpers ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 2; }; }
need curl || true   # metric_upsert may need it
need jq
need python3
need ip
[ -x "$METRIC_UPSERT" ] || { echo "[error] metric_upsert not executable: $METRIC_UPSERT" >&2; exit 1; }

echo "[info] sysperf_reporter: host=$HOSTNAME type=$SENSOR_TYPE metric=$METRIC_NAME" >&2
echo "[info] sysperf_reporter: sample=${SAMPLE_SEC}s loop=${LOOP_SEC}s" >&2

# ---------- Perf reader (unchanged core logic) ----------
read_perf_json() {
  SAMPLE_SEC="$SAMPLE_SEC" python3 - <<'PY'
import os, time, json, platform, glob

SAMPLE = float(os.environ.get("SAMPLE_SEC","1") or 1)

def read_proc_stat():
  with open("/proc/stat","r") as f:
    first = f.readline().strip().split()
  if not first or first[0] != "cpu": return None
  keys = ["user","nice","system","idle","iowait","irq","softirq","steal","guest","guest_nice"]
  vals = list(map(int, first[1:])) + [0]*(len(keys)-len(first[1:]))
  return dict(zip(keys, vals[:len(keys)]))

def cpu_pct(a,b):
  if not a or not b: return {}
  def d(k): return max(0, b.get(k,0)-a.get(k,0))
  busy_keys = ["user","nice","system","irq","softirq","steal","iowait"]
  total = sum(d(k) for k in busy_keys+["idle"])
  if total <= 0: total = 1
  pct = { k: round(d(k)*100.0/total, 2) for k in busy_keys+["idle"] }
  pct["total_busy"] = round(100.0 - pct.get("idle",0.0), 2)
  return pct

def loadavg():
  with open("/proc/loadavg") as f:
    a,b,c,*_ = f.read().split()
  return {"1": float(a), "5": float(b), "15": float(c)}

def meminfo():
  kv={}
  with open("/proc/meminfo") as f:
    for line in f:
      if ":" not in line: continue
      k,v = line.split(":",1)
      try: kv[k.strip()] = int(v.strip().split()[0])  # kB
      except: pass
  tot = kv.get("MemTotal",0); free = kv.get("MemFree",0)
  avail = kv.get("MemAvailable",free)
  buf = kv.get("Buffers",0)
  cached = kv.get("Cached",0) + kv.get("SReclaimable",0)
  used = max(0, tot - free - buf - cached)
  pct = round((used / tot * 100.0),2) if tot>0 else 0.0
  stot = kv.get("SwapTotal",0); sfree = kv.get("SwapFree",0); sused = max(0, stot - sfree)
  spct = round((sused / stot * 100.0),2) if stot>0 else 0.0
  return {
    "mem_kb": {"total":tot,"used":used,"free":free,"available":avail,"buffers":buf,"cached":cached,"pct_used":pct},
    "swap_kb":{"total":stot,"used":sused,"free":sfree,"pct_used":spct}
  }

def sector_size(dev):
  try:
    p=f"/sys/block/{dev}/queue/hw_sector_size"
    if os.path.exists(p):
      with open(p) as f: return int(f.read().strip() or "512")
    matches = glob.glob(f"/sys/block/*/{dev}/queue/hw_sector_size")
    if matches:
      with open(matches[0]) as f: return int(f.read().strip() or "512")
  except: pass
  return 512

def read_diskstats():
  st={}
  with open("/proc/diskstats") as f:
    for line in f:
      parts = line.split()
      if len(parts)<14: continue
      dev = parts[2]
      # Filter out loop/ram/dm/md/sr/fd/zd/nbd (keep nvme, sdX, vdX, mmcblk, etc.)
      if dev.startswith(("loop","ram","dm-","md","sr","fd","zd","nbd")):
        continue
      try:
        rc=int(parts[3]); wc=int(parts[7]); sr=int(parts[5]); sw=int(parts[9])
      except: 
        continue
      st[dev]=(rc,wc,sr,sw)
  return st

def read_netdev():
  data={}
  with open("/proc/net/dev") as f:
    for line in f:
      if ":" not in line: continue
      iface, rest = line.split(":",1)
      iface = iface.strip()
      parts = rest.split()
      if len(parts) < 16: continue
      rx_bytes=int(parts[0]); rx_pkts=int(parts[1])
      tx_bytes=int(parts[8]); tx_pkts=int(parts[9])
      rx_err=int(parts[2]);  rx_drop=int(parts[3])
      tx_err=int(parts[10]); tx_drop=int(parts[11])
      data[iface]=(rx_bytes,rx_pkts,tx_bytes,tx_pkts,rx_err,rx_drop,tx_err,tx_drop)
  return data

def temps():
  out=[]
  for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
    try:
      with open(p) as f: milli=int(f.read().strip())
      name=p.split("/")[-2]
      out.append({"name":name,"temp_c":round(milli/1000.0,1)})
    except: pass
  return out

# Capture snapshots
a_cpu = read_proc_stat()
a_disk = read_diskstats()
a_net  = read_netdev()
t0 = time.monotonic()
time.sleep(max(0.0, SAMPLE))
b_cpu = read_proc_stat()
b_disk = read_diskstats()
b_net  = read_netdev()
dt = max(1e-6, time.monotonic()-t0)

# CPU %
cpu = cpu_pct(a_cpu, b_cpu)

# Disk rates
total_disk = {"read_ios_per_s":0.0,"write_ios_per_s":0.0,"read_bytes_per_s":0.0,"write_bytes_per_s":0.0}
perdev={}
for dev, A in a_disk.items():
  B = b_disk.get(dev)
  if not B: continue
  reads = max(0, B[0]-A[0]); writes = max(0, B[1]-A[1])
  sr = max(0, B[2]-A[2]); sw = max(0, B[3]-A[3])
  ssize = sector_size(dev)
  r_iops = reads/dt; w_iops=writes/dt
  r_bps = (sr*ssize)/dt; w_bps=(sw*ssize)/dt
  perdev[dev]={
    "read_ios_per_s":round(r_iops,2),"write_ios_per_s":round(w_iops,2),
    "read_bytes_per_s":round(r_bps,2),"write_bytes_per_s":round(w_bps,2),
    "sector_size":ssize
  }
  total_disk["read_ios_per_s"] += r_iops
  total_disk["write_ios_per_s"] += w_iops
  total_disk["read_bytes_per_s"] += r_bps
  total_disk["write_bytes_per_s"] += w_bps
for k in list(total_disk): total_disk[k]=round(total_disk[k],2)

# Net rates
total_net={"rx_bytes_per_s":0.0,"tx_bytes_per_s":0.0,"rx_packets_per_s":0.0,"tx_packets_per_s":0.0}
ifaces={}
for iface, A in a_net.items():
  B = b_net.get(iface)
  if not B: continue
  rx_bps = max(0,B[0]-A[0])/dt; rx_pps=max(0,B[1]-A[1])/dt
  tx_bps = max(0,B[2]-A[2])/dt; tx_pps=max(0,B[3]-A[3])/dt
  ifaces[iface]={
    "rx_bytes_per_s":round(rx_bps,2),"tx_bytes_per_s":round(tx_bps,2),
    "rx_packets_per_s":round(rx_pps,2),"tx_packets_per_s":round(tx_pps,2),
    "rx_errs":B[4]-A[4],"rx_drop":B[5]-A[5],"tx_errs":B[6]-A[6],"tx_drop":B[7]-A[7]
  }
  total_net["rx_bytes_per_s"] += rx_bps; total_net["tx_bytes_per_s"] += tx_bps
  total_net["rx_packets_per_s"] += rx_pps; total_net["tx_packets_per_s"] += tx_pps
for k in list(total_net): total_net[k]=round(total_net[k],2)

payload = {
  "timestamp": int(time.time()),
  "host": platform.node(),
  "sample_sec": round(dt,3),
  "cpu_pct": cpu,
  "loadavg": loadavg(),
  **meminfo(),
  "disk_io": {"total": total_disk, "devices": perdev},
  "net_io": {"total": total_net, "interfaces": ifaces},
}
t = temps()
if t: payload["temps_c"] = t

print(json.dumps(payload, separators=(',',':')))
PY
}

# ---------- Update loop (metric_upsert) ----------
echo "[info] Starting ${LOOP_SEC}s update loop for System Perf metrics (Ctrl-C to stop)" >&2
while true; do
  data_json="$(read_perf_json)"

  # Sanity: ensure it's valid JSON; if not, wrap as error
  if ! echo "$data_json" | jq . >/dev/null 2>&1; then
    echo "[warn] sysperf_reporter: invalid JSON from read_perf_json; wrapping" >&2
    esc=$(printf '%s' "$data_json" | jq -Rs .)
    data_json='{"timestamp":'"$(date +%s)"',"error":"invalid sysperf json","raw":'"$esc"'}'
  fi

  echo "[info] metric_upsert: type=$SENSOR_TYPE name=$METRIC_NAME" >&2

  # metric_upsert.sh <SensorType> <MetricName> '<jsonData>'
  if ! "$METRIC_UPSERT" "$SENSOR_TYPE" "$METRIC_NAME" "$data_json"; then
    echo "[warn] metric_upsert failed for System Perf metric" >&2
  fi

  sleep "$LOOP_SEC"
done
