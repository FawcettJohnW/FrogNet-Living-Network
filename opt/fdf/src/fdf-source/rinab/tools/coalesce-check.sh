#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# coalesce-check.sh [N] [ROUNDS] -- [HOST_COALESCE_V1]: N identical requests at once (rlite-ctl ipcps-show from N separate
# sessions), ROUNDS times; every one must answer the same table; then the host's own count of executions led vs requests
# answered by another's execution (printed when it exits).
N=${1:-40}; ROUNDS=${2:-10}; PORT=${PORT:-8857}; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/cc-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; for i in $(seq 8); do rlite-ctl ipcp-create n$i normal dd$i >/dev/null; done
want=$(rlite-ctl ipcps-show | md5sum | cut -c1-12); bad=0
for r in $(seq $ROUNDS); do
  for i in $(seq $N); do ( got=$(rlite-ctl ipcps-show | md5sum | cut -c1-12); [ "$got" = "$want" ] || echo BAD ) > /tmp/cc-$i.out & done; wait $(jobs -p | grep -v "^$H$") 2>/dev/null
  bad=$((bad + $(cat /tmp/cc-*.out | grep -c BAD))); rm -f /tmp/cc-*.out
done
echo "answers that differed from the table: $bad of $((N * ROUNDS))"
rlite-ctl reset >/dev/null; rlite-ctl terminate >/dev/null; sleep 0.5; kill $H; wait $H 2>/dev/null
grep HOST_COALESCE /tmp/cc-host.log
