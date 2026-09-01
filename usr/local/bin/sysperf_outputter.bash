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

def _run(cmd, timeout=4):
  import subprocess
  try:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
  except Exception:
    return ""

def capability():
  """STATIC capability + tool configs. Cheap, but it never changes between boots,
  so compute once and cache to /run; subsequent loops read the cache (truly free)."""
  import os, json as _j, re, glob as _g
  cache="/run/frognet_avcap.json"
  try:
    with open(cache) as f: return _j.load(f)
  except Exception: pass

  cap={}
  # cores / clock / arch
  try: cap["cores"]=os.cpu_count() or 1
  except Exception: cap["cores"]=1
  mhz=0
  try:
    with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") as f:
      mhz=int(f.read().strip())//1000
  except Exception:
    try:
      with open("/proc/cpuinfo") as f:
        for line in f:
          if "cpu MHz" in line: mhz=int(float(line.split(":")[1])); break
    except Exception: pass
  cap["cpu_mhz"]=mhz
  cap["arch"]=platform.machine()
  model=""
  try:
    with open("/proc/cpuinfo") as f:
      for line in f:
        if line.startswith(("model name","Model")): model=line.split(":",1)[1].strip(); break
  except Exception: pass
  cap["cpu_model"]=model or cap["arch"]

  # ffmpeg + codecs (the A/V hard gate + what it can do)
  ff=_run(["bash","-lc","command -v ffmpeg"]).strip()
  cap["ffmpeg"]=bool(ff)
  cap["ffmpeg_path"]=ff
  if ff:
    enc=_run([ff,"-hide_banner","-encoders"])
    cap["libvpx"]="libvpx" in enc
    cap["encoders"]=sorted({e for e in ("libvpx","libvpx-vp9","libx264","libx265",
                            "h264_v4l2m2m","h264_vaapi","h264_nvenc","libopus","aac")
                            if e in enc})
    ver=_run([ff,"-hide_banner","-version"]).splitlines()
    cap["ffmpeg_version"]=ver[0].split()[2] if ver and len(ver[0].split())>2 else ""
  else:
    cap["libvpx"]=False; cap["encoders"]=[]; cap["ffmpeg_version"]=""

  # hardware encoder presence
  hw="none"
  if _g.glob("/dev/video11"): hw="v4l2m2m"
  if os.path.exists("/dev/dri/renderD128"): hw="vaapi"
  if _run(["bash","-lc","command -v nvidia-smi"]).strip(): hw="nvenc"
  cap["hw_encoder"]=hw
  # GPU/VAAPI render node actually present (not just an ffmpeg build flag)
  cap["gpu_render_node"]=bool(_g.glob("/dev/dri/renderD*"))
  # cross-architecture CPU benchmark: a measured per-core integer score so a
  # 1.5 GHz ARM and a 1.5 GHz x86 are compared by WORK DONE, not nominal clock.
  try:
    import time as _t
    t0=_t.monotonic(); x=0
    for i in range(400000): x=(x*1103515245+12345)&0x7fffffff
    dt=max(1e-6,_t.monotonic()-t0)
    cap["cpu_bench_per_core"]=round(400000.0/dt/1000.0,1)   # k-iters/sec, 1 core
    cap["cpu_bench_total"]=round(cap["cpu_bench_per_core"]*int(cap.get("cores",1)),1)
  except Exception:
    cap["cpu_bench_per_core"]=0.0; cap["cpu_bench_total"]=0.0

  # memory: total AND available (free headroom matters as much as total for innodb)
  try:
    mi={}
    for ln in open("/proc/meminfo"):
      k,_,v=ln.partition(":")
      if k in ("MemTotal","MemAvailable"): mi[k]=int(v.strip().split()[0])
    cap["mem_total_kb"]=mi.get("MemTotal",0)
    cap["mem_available_kb"]=mi.get("MemAvailable",0)
  except Exception:
    cap.setdefault("mem_total_kb",0); cap["mem_available_kb"]=0

  # mysql/mariadb presence + config (DB-host capability)
  myc=_run(["bash","-lc","command -v mysqld || command -v mariadbd"]).strip()
  mcli=_run(["bash","-lc","command -v mysql || command -v mariadb"]).strip()
  cap["mysql"]=bool(myc or mcli)
  # mysql INSTALLED is not mysql RUNNING — a DB host must actually be serving.
  cap["mysql_running"]=False
  try:
    import subprocess as _sp
    for chk in (["pgrep","-x","mysqld"],["pgrep","-x","mariadbd"]):
      if _sp.run(chk,capture_output=True).returncode==0:
        cap["mysql_running"]=True; break
    if not cap["mysql_running"]:
      ss=_run(["bash","-lc","ss -ltn 2>/dev/null | grep -E ':3306 '"])
      cap["mysql_running"]=bool(ss.strip())
  except Exception: pass
  if cap["mysql"]:
    cap["mysql_server"]=myc; cap["mysql_client"]=mcli
    cfg=""
    for c in ("/etc/mysql/my.cnf","/etc/my.cnf","/etc/mysql/mariadb.cnf"):
      if os.path.exists(c): cfg=c; break
    cap["mysql_config"]=cfg
    ib=""
    if cfg:
      try:
        txt=open(cfg, errors="replace").read()
        m=re.search(r"innodb_buffer_pool_size\s*=\s*(\S+)", txt)
        if m: ib=m.group(1)
      except Exception: pass
    cap["mysql_innodb_buffer_pool_size"]=ib
    if ib:
      _mult={"k":1024,"m":1024**2,"g":1024**3,"t":1024**4}
      _mm=re.match(r"(\d+)\s*([kKmMgGtT]?)", ib.strip())
      if _mm:
        cap["mysql_innodb_pool_bytes"]=int(_mm.group(1))*_mult.get(_mm.group(2).lower(),1)

  # Disk facts a DATABASE cares about: capacity, free space, and storage CLASS
  # (NVMe > SSD > spinning > SD card). A DB is I/O-bound; an SD-card Pi must not win
  # a DB role over a box with an NVMe just because it has more RAM. We report the
  # filesystem holding the mysql data dir (or / if mysql absent) and classify its
  # backing device via /sys/block rotational + name.
  def _backing_dev(path):
    import os as _os
    try:
        st=_os.stat(path); maj,mino=_os.major(st.st_dev),_os.minor(st.st_dev)
        # walk /sys/block to find the parent block device for this dev_t
        for d in _os.listdir("/sys/block"):
            dpath=f"/sys/block/{d}/dev"
            try:
                if open(dpath).read().strip()==f"{maj}:{mino}": return d
            except Exception: pass
            # partitions live under the parent
            for p in glob.glob(f"/sys/block/{d}/{d}*/dev"):
                try:
                    if open(p).read().strip()==f"{maj}:{mino}": return d
                except Exception: pass
    except Exception: pass
    return ""
  def _disk_class(dev):
    if not dev: return "unknown"
    if dev.startswith("nvme"): return "nvme"
    if dev.startswith("mmcblk"): return "sdcard"        # Pi SD — slowest, penalize hard
    try:
        rot=open(f"/sys/block/{dev}/queue/rotational").read().strip()
        return "hdd" if rot=="1" else "ssd"
    except Exception: return "unknown"
  data_dir = cap.get("mysql_data_dir","") or "/var/lib/mysql"
  import os as _os
  dpath = data_dir if _os.path.isdir(data_dir) else "/"
  try:
    sv=_os.statvfs(dpath)
    cap["disk_total_gb"]=round(sv.f_blocks*sv.f_frsize/(1024**3),1)
    cap["disk_free_gb"]=round(sv.f_bavail*sv.f_frsize/(1024**3),1)
  except Exception:
    cap["disk_total_gb"]=0.0; cap["disk_free_gb"]=0.0
  _dev=_backing_dev(dpath)
  cap["disk_dev"]=_dev
  cap["disk_class"]=_disk_class(_dev)
  # MEASURED disk write throughput + fsync latency on the data dir (real DB signal;
  # class is only a hint). Bounded, runs once/boot with the static block, cleaned up.
  try:
    import time as _t, tempfile as _tf
    _wdir = dpath if (os.path.isdir(dpath) and os.access(dpath, os.W_OK)) else "/tmp"
    tf=_tf.NamedTemporaryFile(dir=_wdir, delete=False); tp=tf.name
    buf=b"\0"*(1024*1024)
    t0=_t.monotonic()
    for _ in range(16): tf.write(buf)            # 16 MiB
    tf.flush(); os.fsync(tf.fileno()); tf.close()
    dt=max(1e-6,_t.monotonic()-t0)
    cap["disk_write_mbps"]=round(16.0/dt,1)
    t1=_t.monotonic()
    with open(tp,"ab") as fh: fh.write(b"\0"*4096); fh.flush(); os.fsync(fh.fileno())
    cap["disk_fsync_ms"]=round((_t.monotonic()-t1)*1000.0,2)
    os.unlink(tp)
  except Exception:
    cap["disk_write_mbps"]=0.0; cap["disk_fsync_ms"]=0.0

  # LAN address this node bears — its OWN identity, from getFrogNet.bash (the
  # canonical source metric_upsert uses), so a node is ALWAYS a valid candidate for
  # itself. Critical in standalone mode, where this node is the ONLY target: it must
  # see its own address or the election finds nobody. Falls through identity -> any
  # 10/8 -> loopback so lan_ip is NEVER empty.
  lan=""
  gf=_run(["bash","-lc","/usr/local/bin/getFrogNet.bash 2>/dev/null"]).strip()
  if gf:
    parts=gf.split(",")                     # fqdn,eth0IP,wlan0IP,wlan1IP
    for f in parts[1:]:                      # prefer a borne interface IP
      f=f.strip()
      if f.startswith("10."):
        lan=f; break
    if not lan and len(parts) > 1:
      # eth0IP may be a host-path like 10.x.x.1; take it if present
      for f in parts[1:]:
        f=f.strip()
        if f: lan=f; break
  if not lan:
    out=_run(["bash","-lc","ip -4 -o addr show | awk '$2 !~ /^wg/ {print $4}'"])
    cands=[x.split("/")[0] for x in out.split() if x.startswith("10.")]
    ones=[x for x in cands if x.endswith(".1")]
    lan=(sorted(ones, key=lambda a:[int(o) for o in a.split(".")])[-1]
         if ones else (cands[0] if cands else ""))
  if not lan:
    lan="127.0.0.1"                          # standalone floor: still a valid self-target
  cap["lan_ip"]=lan
  cap["av_port"]=9000

  try:
    with open(cache,"w") as f: _j.dump(cap,f)
  except Exception: pass
  return cap

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
  data_json="$(read_perf_json)"

  # Sanity: ensure it's valid JSON; if not, wrap as error
  if ! echo "$data_json" | jq . >/dev/null 2>&1; then
    echo "[warn] sysperf_reporter: invalid JSON from read_perf_json; wrapping" >&2
    esc=$(printf '%s' "$data_json" | jq -Rs .)
    data_json='{"timestamp":'"$(date +%s)"',"error":"invalid sysperf json","raw":'"$esc"'}'
  fi

  echo "[info] metric_upsert: type=$SENSOR_TYPE name=$METRIC_NAME" >&2
  echo $data_json
