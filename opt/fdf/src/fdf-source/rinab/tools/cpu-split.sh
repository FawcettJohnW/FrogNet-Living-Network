#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# cpu-split.sh -- CPU per rr transaction, split by process (host, server-side rinaperf, client rinaperf), both systems.
set -u
cpu() { awk '{print $14+$15}' /proc/$1/stat; }   # utime+stime, clock ticks
run() {
  export PATH=$2:$PATH RINA_RAM=127.0.0.1:8843 RINA_RAM_NETNS=/proc/$$/ns/net
  $3 > /tmp/cs-host.log 2>&1 & H=$!
  for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/8843) 2>/dev/null && break; sleep 0.1; done
  rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
  rinaperf -lw -z srv; sleep 0.5; S=$(pgrep -nx rinaperf)
  h0=$(cpu $H); s0=$(cpu $S); t0=$(date +%s.%N)
  rinaperf -z srv -t rr -c 5000 > /tmp/cs-rr.txt 2>&1 & C=$!
  c=0; while kill -0 $C 2>/dev/null; do c=$(cpu $C 2>/dev/null || echo $c); sleep 0.05; done
  t1=$(date +%s.%N); h1=$(cpu $H); s1=$(cpu $S); tk=$(getconf CLK_TCK)
  awk -v l="$1" -v h=$((h1-h0)) -v s=$((s1-s0)) -v c=$c -v tk=$tk -v w=$(echo "$t1 - $t0" | bc) 'BEGIN{n=5000;
    printf "%-28s wall %.0f us/txn | CPU us/txn: host %.0f, server prog %.0f, client prog %.0f, total %.0f (%.0f%% of wall)\n", l, w*1e6/n, h*1e6/tk/n, s*1e6/tk/n, c*1e6/tk/n, (h+s+c)*1e6/tk/n, (h+s+c)/tk/w*100}'
  pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
}
OLD=${OLD:?OLD=<Mode B as handed off>/ribbit/examples/rina/rlite}; NEW=$(cd "$(dirname "$0")/../build" && pwd)
run "Mode B as handed off" $OLD/bin "$OLD/bin/rina-ram --listen 127.0.0.1:8843"
run "RINA region (Redis model)" $NEW/bin "$NEW/bin/rina-host --listen 127.0.0.1:8843 --api /FawcettInnovations.RINA.ram_interface.php"
