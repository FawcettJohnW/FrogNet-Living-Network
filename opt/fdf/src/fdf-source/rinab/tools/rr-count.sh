#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# rr-count.sh [N] [PORT] -- one rinaperf rr of N on the RINA host; prints rinaperf's result and each program's
# requests per transaction (from the host's own session log).
N=${1:-5000}; PORT=${2:-8845}; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/rc-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv; sleep 0.5
rinaperf -z srv -t rr -c $N 2>&1 | grep -A1 Transactions
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
grep "session down" /tmp/rc-host.log | sed 's/.*after \([0-9]*\) requests.*/\1/' | sort -n | tail -2 | awk -v n=$N '{printf "program session: %d requests = %.2f per transaction\n", $1, $1/n}'
