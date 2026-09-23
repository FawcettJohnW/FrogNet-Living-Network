#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# server_monitor.sh -- run ON THE SERVER while run_bench.sh runs on the client.
#
#   ./server_monitor.sh PORT [--out DIR] [--seconds N]        (Ctrl-C to stop)
#
# 1 Hz, UTC epoch stamped, into DIR/server_metrics.csv:
#   whole machine : CPU busy %, iowait %, load, memory available, TCP out / retransmitted
#   the port      : established connections, and bytes waiting in their receive and send
#                   queues -- a growing receive queue IS the un-acknowledged backlog
#   per process   : CPU % of one core, RSS, threads, for each of
#                   ram_server | ram_listener.py | apache2 (all workers, and how many) | mariadbd/mysqld
#   database      : questions/s and threads running, when `mysql` can be reached as this user
# plus DIR/server_env.json (CPU, cores, memory, kernel, what is listening on the port).
set -u
PORT="${1:-}"; shift || true
[ -n "$PORT" ] || { echo "usage: $0 PORT [--out DIR] [--seconds N]" >&2; exit 2; }
OUT="server_$(hostname)_$(date -u +%Y%m%d-%H%M%S)"; LIMIT=0
while [ $# -gt 0 ]; do case "$1" in --out) OUT="$2"; shift 2;; --seconds) LIMIT="$2"; shift 2;; *) echo "unknown arg $1" >&2; exit 2;; esac; done
mkdir -p "$OUT" || exit 1
TICK=$(getconf CLK_TCK)
python3 - "$OUT" "$PORT" <<'PY'
import json, os, platform, socket, subprocess, sys, time
out, port = sys.argv[1], sys.argv[2]
def rd(p):
    try: return open(p).read()
    except OSError: return ""
cpu = [l.split(":",1)[1].strip() for l in rd("/proc/cpuinfo").splitlines() if l.startswith(("model name","Model"))]
mem = [l for l in rd("/proc/meminfo").splitlines() if l.startswith("MemTotal")]
try: listening = subprocess.run(["ss", "-ltnp", "sport = :" + port], capture_output=True, text=True).stdout.strip().splitlines()[1:]
except Exception: listening = []
json.dump({"started_epoch": time.time(), "server_host": socket.gethostname(), "port": int(port), "cpu_model": cpu[0] if cpu else platform.processor(),
           "cores": os.cpu_count(), "mem_total": mem[0].split(":")[1].strip() if mem else "", "kernel": platform.release(), "listening": listening},
          open(os.path.join(out, "server_env.json"), "w"), indent=1)
PY
procs() {  # name pattern -> "count cpu_ticks rss_kb threads" summed over matching processes
  local n=0 c=0 r=0 t=0 p
  for p in $(pgrep -f "$1" 2>/dev/null); do [ "$p" = "$$" ] && continue; [ -r /proc/$p/stat ] || continue
    local st; st=$(sed 's/.*) //' /proc/$p/stat 2>/dev/null) || continue; set -- $st
    c=$((c + ${12:-0} + ${13:-0})); r=$((r + $(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null || echo 0))); t=$((t + $(awk '/Threads/{print $2}' /proc/$p/status 2>/dev/null || echo 0))); n=$((n+1)); done
  echo "$n $c $r $t"; }
NAMES=(ram_server ram_listener.py apache2 "mariadbd|mysqld")
HDR="epoch,cpu_busy_pct,iowait_pct,load1,mem_avail_kb,tcp_out_segs,tcp_retrans_segs,port_established,port_recvq_bytes,port_sendq_bytes"
for nm in ram_server ram_listener apache2 db; do HDR="$HDR,${nm}_procs,${nm}_cpu_pct,${nm}_rss_kb,${nm}_threads"; done
echo "$HDR,db_questions_per_s,db_threads_running" > "$OUT/server_metrics.csv"
read -r _ u n s i w q sq _ < /proc/stat; pb=$((u+n+s+q+sq)); pt=$((u+n+s+i+w+q+sq)); pw=$w
declare -A PC; for k in 0 1 2 3; do set -- $(procs "${NAMES[$k]}"); PC[$k]=$2; done
PQ=$(mysql -N -B -e "SHOW GLOBAL STATUS LIKE 'Questions'" 2>/dev/null | awk '{print $2}'); START=$(date +%s)
echo "sampling into $OUT/server_metrics.csv -- Ctrl-C to stop"
trap 'echo; echo "stopped. $(($(wc -l < "$OUT/server_metrics.csv") - 1)) samples in $OUT/"; exit 0' INT TERM
while :; do sleep 1
  read -r _ u n s i w q sq _ < /proc/stat; b=$((u+n+s+q+sq)); t=$((u+n+s+i+w+q+sq)); d=$((t-pt)); [ $d -gt 0 ] || d=1
  LINE="$(date -u +%s),$((100*(b-pb)/d)),$((100*(w-pw)/d)),$(cut -d' ' -f1 /proc/loadavg),$(awk '/MemAvailable/{print $2}' /proc/meminfo),$(awk '/^Tcp:/{if(h){print $12","$13}else h=1}' /proc/net/snmp)"
  LINE="$LINE,$(ss -tn state established "( sport = :$PORT )" 2>/dev/null | awk 'NR>1{n++; r+=$1; s+=$2} END{print n+0","r+0","s+0}')"
  for k in 0 1 2 3; do set -- $(procs "${NAMES[$k]}"); LINE="$LINE,$1,$(( 100*($2-${PC[$k]})/TICK )),$3,$4"; PC[$k]=$2; done
  Q=$(mysql -N -B -e "SHOW GLOBAL STATUS WHERE Variable_name IN ('Questions','Threads_running')" 2>/dev/null | awk '{v[$1]=$2} END{print v["Questions"]","v["Threads_running"]}')
  if [ -n "$Q" ] && [ -n "$PQ" ]; then LINE="$LINE,$(( ${Q%%,*} - PQ )),${Q##*,}"; PQ=${Q%%,*}; else LINE="$LINE,,"; fi
  echo "$LINE" >> "$OUT/server_metrics.csv"; pb=$b; pt=$t; pw=$w
  [ "$LIMIT" -gt 0 ] && [ $(( $(date +%s) - START )) -ge "$LIMIT" ] && break
done
echo "$(($(wc -l < "$OUT/server_metrics.csv") - 1)) samples in $OUT/"
