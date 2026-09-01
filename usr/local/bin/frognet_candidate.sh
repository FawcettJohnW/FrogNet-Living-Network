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
# frognet_candidate.sh — register THIS machine as a role candidate. NO FrogNet
# required: it probes the box and POSTs a capability blob via metric_upsert. The
# FrogNet nodes read it, score it, and float the role to it if it wins. The
# candidate never knows or cares.
#
#   frognet_candidate.sh <Role> [address] [port]
#     Role    : DatabaseCandidate | MediaCandidate | AIHost | SensorPlatform
#     address : this machine's LAN 10/8 address (auto-detected if omitted)
#     port    : service port the role would use (default 9000)
#
# Blob shape matches the election reader: {"capability":{...,"lan_ip":...}}.
# Run from cron/systemd-timer as often as you like — the probe is cheap and the
# write is a SAME/DIFF convergence, so re-registering costs nothing when unchanged.
set -u

ROLE="${1:-}"
[ -n "$ROLE" ] || { echo "usage: $0 <Role> [address] [port]" >&2; exit 2; }
ADDR="${2:-}"
PORT="${3:-9000}"

MUP="/usr/local/bin/metric_upsert.sh"
[ -x "$MUP" ] || { echo "[candidate] metric_upsert.sh not found at $MUP" >&2; exit 1; }

# auto-detect LAN 10/8 address if not given (prefer non-wg, prefer a .1, else any)
if [ -z "$ADDR" ]; then
  _ips="$(ip -4 -o addr show 2>/dev/null | awk '$2 !~ /^wg/ {print $4}' | cut -d/ -f1 | grep -E '^10\.' || true)"
  ADDR="$(printf '%s\n' "$_ips" | grep -E '\.1$' | head -1)"
  [ -n "$ADDR" ] || ADDR="$(printf '%s\n' "$_ips" | head -1)"
fi
[ -n "$ADDR" ] || { echo "[candidate] no LAN 10/8 address found; pass one explicitly" >&2; exit 1; }

BLOB="$(ADDR="$ADDR" PORT="$PORT" python3 - <<'PY'
import os, json, platform, glob, subprocess, re

def run(cmd, t=4):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""

cap = {"lan_ip": os.environ["ADDR"], "av_port": int(os.environ.get("PORT", "9000"))}

# cores / clock / arch / model
try: cap["cores"] = os.cpu_count() or 1
except Exception: cap["cores"] = 1
mhz = 0
try:
    with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") as f:
        mhz = int(f.read().strip()) // 1000
except Exception:
    try:
        with open("/proc/cpuinfo") as f:
            for ln in f:
                if "cpu MHz" in ln: mhz = int(float(ln.split(":")[1])); break
    except Exception: pass
cap["cpu_mhz"] = mhz
cap["arch"] = platform.machine()
model = ""
try:
    with open("/proc/cpuinfo") as f:
        for ln in f:
            if ln.startswith(("model name", "Model")):
                model = ln.split(":", 1)[1].strip(); break
except Exception: pass
cap["cpu_model"] = model or cap["arch"]

# memory
try:
    with open("/proc/meminfo") as f:
        for ln in f:
            if ln.startswith("MemTotal:"):
                cap["mem_total_kb"] = int(ln.split()[1]); break
except Exception:
    cap["mem_total_kb"] = 0

# ffmpeg + codecs
ff = run(["bash", "-lc", "command -v ffmpeg"]).strip()
cap["ffmpeg"] = bool(ff); cap["ffmpeg_path"] = ff
if ff:
    enc = run([ff, "-hide_banner", "-encoders"])
    cap["libvpx"] = "libvpx" in enc
    cap["encoders"] = sorted({e for e in ("libvpx", "libvpx-vp9", "libx264", "libx265",
                              "h264_v4l2m2m", "h264_vaapi", "h264_nvenc", "libopus", "aac")
                              if e in enc})
else:
    cap["libvpx"] = False; cap["encoders"] = []

# hardware encoder
hw = "none"
if glob.glob("/dev/video11"): hw = "v4l2m2m"
if os.path.exists("/dev/dri/renderD128"): hw = "vaapi"
if run(["bash", "-lc", "command -v nvidia-smi"]).strip(): hw = "nvenc"
cap["hw_encoder"] = hw

# mysql / mariadb presence + config (for DatabaseCandidate scoring)
myd = run(["bash", "-lc", "command -v mysqld || command -v mariadbd"]).strip()
mcli = run(["bash", "-lc", "command -v mysql || command -v mariadb"]).strip()
cap["mysql"] = bool(myd or mcli)
if cap["mysql"]:
    for c in ("/etc/mysql/my.cnf", "/etc/my.cnf", "/etc/mysql/mariadb.cnf"):
        if os.path.exists(c):
            cap["mysql_config"] = c
            try:
                m = re.search(r"innodb_buffer_pool_size\s*=\s*(\S+)",
                              open(c, errors="replace").read())
                if m: cap["mysql_innodb_buffer_pool_size"] = m.group(1)
            except Exception: pass
            break

print(json.dumps({"capability": cap}, separators=(",", ":")))
PY
)"

# guard: only POST if we produced valid JSON
if [ -z "$BLOB" ] || ! printf '%s' "$BLOB" | python3 -c 'import sys,json; json.load(sys.stdin)' 2>/dev/null; then
  echo "[candidate] probe produced invalid JSON; not registering" >&2
  exit 1
fi

echo "[candidate] $ROLE @ $ADDR:$PORT -> $BLOB" >&2
exec "$MUP" "$ROLE" "Candidate" "$BLOB"
