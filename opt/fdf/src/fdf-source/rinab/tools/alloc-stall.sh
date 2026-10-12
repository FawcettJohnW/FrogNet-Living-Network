#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# alloc-stall.sh [N] [PORT] -- N one-shot flows (rinaperf -t ping -c 1) to one server; counts the ones that fail or take
# over 1 s, to reproduce the intermittent allocation stall.
N=${1:-200}; PORT=${2:-8847}; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/as-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv; sleep 0.5
bad=0; slow=0
for i in $(seq $N); do s=$(date +%s%N); timeout 10 rinaperf -z srv -t ping -c 1 -i 0 >/tmp/as-last.txt 2>&1; rc=$?; e=$(( ($(date +%s%N) - s) / 1000000 ))
  [ $rc -ne 0 ] && { bad=$((bad+1)); echo "run $i: rc=$rc after ${e} ms: $(tail -1 /tmp/as-last.txt)"; }
  [ $rc -eq 0 ] && [ $e -gt 1000 ] && { slow=$((slow+1)); echo "run $i: ok but ${e} ms"; }
done
echo "N=$N failed=$bad slow(>1s)=$slow"
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
