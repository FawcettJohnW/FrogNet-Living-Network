#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# run_bench.sh -- run the FrogNet RAM tests against a real server from a real
# machine, and keep everything needed to make the numbers mean something.
#
#   ./run_bench.sh HOST PORT [--out DIR] [--quick] [--seconds N] [--server-pid PID] [--label TEXT]
#
#   --seconds N   length of each test (default 5). --quick = 2 s and a thinner matrix.
#
# Run this on the CLIENT machine. On the SERVER, at the same time, run
#   ./server_monitor.sh PORT --out DIR        (stop it with Ctrl-C when this finishes)
# Both stamp everything in UTC epoch seconds, so the two directories line up.
#
# What is recorded:
#   env.json            this machine (CPU, cores, memory, kernel), the target, when
#   path.json           ping RTT (if allowed) and 30 timed TCP connects to the port
#   runs/NNN_*.json     one machine-readable result per test   (runs/NNN_*.log = its output)
#   client_metrics.csv  1 Hz while the tests run: CPU busy, load, memory, TCP segments
#                       out and RETRANSMITTED -- so a bad number can be told apart from
#                       a busy client or a lossy path
#   summary.csv         one line per run
#
# THE CONTENTION TEST (mesh --every --ack) MUST NOT DROP A SEQUENCE NUMBER. If it
# does, this script says so loudly and exits 1 after finishing the rest.
set -uo pipefail
HOST="${1:-}"; PORT="${2:-}"; shift 2 2>/dev/null || true
[ -n "$HOST" ] && [ -n "$PORT" ] || { echo "usage: $0 HOST PORT [--out DIR] [--quick] [--server-pid PID] [--label TEXT]" >&2; exit 2; }
OUT=""; QUICK=0; SPID=""; LABEL=""; SECS_ARG=""
while [ $# -gt 0 ]; do case "$1" in --out) OUT="$2"; shift 2;; --quick) QUICK=1; shift;; --seconds) SECS_ARG="$2"; shift 2;; --server-pid) SPID="$2"; shift 2;; --label) LABEL="$2"; shift 2;; *) echo "unknown arg $1" >&2; exit 2;; esac; done
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -n "$OUT" ] || OUT="bench_$(hostname)_to_${HOST}_$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/runs" || exit 1
make -s -C "$HERE" all || { echo "FATAL: could not build the tools" >&2; exit 1; }
SECS=5; [ "$QUICK" = 1 ] && SECS=2; [ -n "$SECS_ARG" ] && SECS="$SECS_ARG"
PIDARG=(); [ -n "$SPID" ] && PIDARG=(--server-pid "$SPID")

python3 - "$OUT" "$HOST" "$PORT" "$LABEL" <<'PY'
import json, os, platform, socket, sys, time, subprocess, statistics
out, host, port, label = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
def rd(p):
    try: return open(p).read()
    except OSError: return ""
cpu = [l.split(":",1)[1].strip() for l in rd("/proc/cpuinfo").splitlines() if l.startswith(("model name","Model"))]
mem = [l for l in rd("/proc/meminfo").splitlines() if l.startswith("MemTotal")]
json.dump({"label": label, "started_epoch": time.time(), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "client_host": socket.gethostname(), "target": {"host": host, "port": port},
           "cpu_model": cpu[0] if cpu else platform.processor(), "cores": os.cpu_count(), "mem_total": mem[0].split(":")[1].strip() if mem else "",
           "kernel": platform.release(), "machine": platform.machine()}, open(os.path.join(out, "env.json"), "w"), indent=1)
path = {"tcp_connect_ms": [], "ping": None}
for _ in range(30):
    t = time.perf_counter()
    try:
        s = socket.create_connection((host, port), 5); path["tcp_connect_ms"].append((time.perf_counter() - t) * 1000); s.close()
    except OSError as e:
        path["tcp_connect_error"] = str(e); break
    time.sleep(0.05)
c = path["tcp_connect_ms"]
if c: path["tcp_connect"] = {"min": min(c), "median": statistics.median(c), "max": max(c), "stdev": statistics.pstdev(c)}
try:
    r = subprocess.run(["ping", "-c", "20", "-i", "0.2", "-q", host], capture_output=True, text=True, timeout=30)
    for l in r.stdout.splitlines():
        if "min/avg/max" in l:
            v = l.split("=")[1].split()[0].split("/"); path["ping"] = {"min": float(v[0]), "avg": float(v[1]), "max": float(v[2]), "mdev": float(v[3])}
        if "packet loss" in l:
            path["ping_loss_pct"] = float(l.split("%")[0].split()[-1])
except Exception as e:
    path["ping_error"] = str(e)
json.dump(path, open(os.path.join(out, "path.json"), "w"), indent=1)
print("path: tcp connect median %.2f ms%s" % (path.get("tcp_connect", {}).get("median", -1), ("; ping avg %.2f ms" % path["ping"]["avg"]) if path.get("ping") else "; ping not available"))
PY
[ -s "$OUT/path.json" ] || { echo "FATAL: could not characterise the path" >&2; exit 1; }
grep -q tcp_connect_error "$OUT/path.json" && { echo "FATAL: cannot connect to $HOST:$PORT -- $(grep tcp_connect_error "$OUT/path.json")" >&2; exit 1; }

# ---- 1 Hz client-side sampler -------------------------------------------------
( echo "epoch,cpu_busy_pct,load1,mem_avail_kb,tcp_out_segs,tcp_retrans_segs"
  read -r _ u n s i w q sq _ < /proc/stat; pb=$((u+n+s+q+sq)); pt=$((u+n+s+i+w+q+sq))
  while :; do sleep 1
    read -r _ u n s i w q sq _ < /proc/stat; b=$((u+n+s+q+sq)); t=$((u+n+s+i+w+q+sq)); d=$((t-pt)); [ $d -gt 0 ] || d=1
    tcp=$(awk '/^Tcp:/{if(h){print $12","$13}else h=1}' /proc/net/snmp)
    echo "$(date -u +%s),$((100*(b-pb)/d)),$(cut -d' ' -f1 /proc/loadavg),$(awk '/MemAvailable/{print $2}' /proc/meminfo),$tcp"; pb=$b; pt=$t
  done ) > "$OUT/client_metrics.csv" &
SAMPLER=$!; trap 'kill $SAMPLER 2>/dev/null' EXIT

N=0; FAILED=0; CONTENTION_FAIL=0
run() {  # run NAME tool args...
  local name="$1"; shift; N=$((N+1)); local id; id=$(printf "%03d_%s" "$N" "$name")
  echo; echo "=== $id"
  "$HERE/$1" "$HOST" "$PORT" "${@:2}" --json "$OUT/runs/$id.json" --label "$name" 2>&1 | tee "$OUT/runs/$id.log"
  local rc=${PIPESTATUS[0]}; [ $rc -eq 0 ] || { FAILED=$((FAILED+1)); case "$name" in contention_*) CONTENTION_FAIL=1;; esac; }
  sleep 1
}
# the path and the server's service time, lightly loaded
run rtt_small             flood --seconds $SECS --ack --rate 20
run rtt_64k               flood --seconds $SECS --ack --rate 4 --payload 65536
# one writer, one reader
run one_pair_ack_flat     flood --seconds $SECS --ack
run one_pair_noack_flat   flood --seconds $SECS
RATES="5 10 20 40 100 500 1000 5000"; [ "$QUICK" = 1 ] && RATES="10 40 1000"
for r in $RATES; do run "one_pair_paced_$r" flood --seconds $SECS --rate "$r"; done
# THE CONTENTION TEST: every message its own cell, every sequence number checked
run contention_ack        mesh --every --ack --clients 10 --seconds $SECS "${PIDARG[@]}"
run contention_noack      mesh --every       --clients 10 --seconds $SECS "${PIDARG[@]}"
# distinct state, PACED: the ladder the envelope's "every value matters" limit is read from
ER="5 10 20 50 100 200 400"; [ "$QUICK" = 1 ] && ER="20 200"
for r in $ER; do run "contention_paced_$r" mesh --every --clients 10 --seconds $SECS --rate "$r" "${PIDARG[@]}"; done
CL="2 5 20"; [ "$QUICK" = 1 ] && CL="5"
for c in $CL; do run "contention_ack_${c}clients" mesh --every --ack --clients "$c" --seconds $SECS "${PIDARG[@]}"; done
# latest-value semantics under cross-posting: where does skipping start?
PR="10 50 100 200 400"; [ "$QUICK" = 1 ] && PR="50"
for r in $PR; do run "latest_paced_$r" mesh --latest --clients 10 --seconds $SECS --rate "$r" "${PIDARG[@]}"; done
run latest_ack_flat       mesh --latest --ack --clients 10 --seconds $SECS "${PIDARG[@]}"

kill $SAMPLER 2>/dev/null; trap - EXIT
python3 - "$OUT" <<'PY'
import glob, json, os, sys
out = sys.argv[1]; rows = []
for p in sorted(glob.glob(os.path.join(out, "runs", "*.json"))):
    try: d = json.load(open(p))
    except ValueError: continue
    rows.append([os.path.basename(p)[:-5], d["tool"], d.get("mode"), d.get("ack"), d.get("clients", 2), d.get("writes_per_s"), d.get("reads_per_s"),
                 d.get("dropped", d.get("skipped")), d.get("out_of_order"), d.get("delivered_twice"), d["delivery_ms"]["p50"], d["delivery_ms"]["p99"], d["ack_ms"]["p50"], d.get("pass")])
with open(os.path.join(out, "summary.csv"), "w") as f:
    f.write("run,tool,mode,ack,clients,writes_per_s,reads_per_s,dropped_or_skipped,out_of_order,delivered_twice,delivery_p50_ms,delivery_p99_ms,ack_p50_ms,pass\n")
    for r in rows: f.write(",".join(str(x) for x in r) + "\n")
print("\n%d runs recorded in %s/summary.csv" % (len(rows), out))
PY
tar czf "$OUT.tgz" "$OUT" && echo "everything is in $OUT/ and $OUT.tgz"
[ "$CONTENTION_FAIL" = 0 ] || { echo; echo "!!!!!!!!  THE CONTENTION TEST DROPPED SEQUENCE NUMBERS OR FAILED. See $OUT/runs/*contention*.log  !!!!!!!!"; exit 1; }
[ "$FAILED" = 0 ] || { echo "$FAILED run(s) failed; see $OUT/runs/"; exit 1; }
echo "all runs passed; the contention test dropped nothing."
