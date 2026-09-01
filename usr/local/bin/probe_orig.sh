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
# frognet_capability_probe.sh — emit THIS node's capability as a JSON blob on stdout.
#
# Used by the per-service capability writers (databasehost / mediahost timers). The
# EXPENSIVE measurements (cpu benchmark, disk write+fsync) run once per boot and are
# cached in /run/frognet_avcap.json; the cheap, time-varying signal (loadavg, temps,
# ts) is read FRESH on every call, so each emitted blob is self-contained — the
# election needs no second read to learn current load.
#
#   frognet_capability_probe.sh            -> {capability..., loadavg, temps_c, ts}
set -u
SAMPLE_SEC="${SAMPLE_SEC:-0}"
python3 - <<'PY'
import os, platform, glob, subprocess, re, json, time
import os,platform,glob,subprocess,re,json,time
def _run(cmd, timeout=4):
  import subprocess
  try:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
  except Exception:
    return ""

def loadavg():
  with open("/proc/loadavg") as f:
    a,b,c,*_ = f.read().split()
  return {"1": float(a), "5": float(b), "15": float(c)}

def temps():
  out=[]
  for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
    try:
      with open(p) as f: milli=int(f.read().strip())
      name=p.split("/")[-2]
      out.append({"name":name,"temp_c":round(milli/1000.0,1)})
    except: pass
  return out

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

cap = capability()
out = dict(cap)
out["loadavg"] = loadavg()
t = temps()
if t: out["temps_c"] = t
out["ts"] = int(time.time())
print(json.dumps(out, separators=(',',':')))
PY
