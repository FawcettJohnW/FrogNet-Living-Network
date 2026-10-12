#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# stall-repro.sh [N] -- rlite 0011's multi-worker line (rinaperf -z rpinstance3 -p 3 -c 3 -i 0 -g 0) N times against
# one server; on the first failure, dumps what the region holds for every program (peek) and stops.
N=${1:-100}; PORT=8849; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/sr-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create n.1 normal dd >/dev/null; rlite-ctl ipcp-enroller-enable n.1
rinaperf -lw -z rpinstance3; sleep 0.3
for i in $(seq $N); do
  if ! rinaperf -z rpinstance3 -p 3 -c 3 -i 0 -g 0 > /tmp/sr-last.txt 2>&1; then
    echo "run $i FAILED:"; grep -i "timed out\|error" /tmp/sr-last.txt | head -3
    echo "--- sessions"; $B/peek sess | tail -12
    echo "--- flow records"; $B/peek fl | tail -12
    for v in $($B/peek sess | awk '{print $3}' | grep -o 'proc:[^"]*' | sed 's/proc://' | sort -u); do echo "--- ev:$v"; $B/peek "ev:$v" | tail -8; done
    break
  fi
done
echo "done after $i runs"
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
