#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# cg-rr.sh [N] [OUT] [PORT] -- the RINA host under callgrind, counting instructions only inside api() (every region
# call), while one rinaperf rr of N runs. Prints the total; OUT keeps the profile (callgrind_annotate OUT).
N=${1:-500}; OUT=${2:-callgrind.out.rr}; PORT=${3:-8844}; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
valgrind --tool=callgrind --callgrind-out-file="$OUT" --collect-atstart=no --toggle-collect='*api*' \
  $B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/cg-host.log 2>&1 & H=$!
for i in $(seq 300); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv; sleep 1
rinaperf -z srv -t rr -c $N 2>&1 | grep -A1 Transactions
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
echo "instructions inside region calls: $(callgrind_annotate "$OUT" 2>/dev/null | grep -m1 TOTALS | awk '{print $1}') for $N transactions"
grep "session down" /tmp/cg-host.log | sed 's/.*after \([0-9]*\) requests.*/\1/' | sort -n | tail -2 | awk '{printf "program session: %d requests\n", $1}'
