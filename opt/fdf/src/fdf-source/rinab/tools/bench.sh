#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# bench.sh -- rinaperf through Mode B as handed off (generic per-SDU write/held read, old platform) and through the
# RINA region (Redis server model), same machine, same commands, one after the other.
set -u
U=${U:-}
run() {   # $1 label, $2 bin dir, $3 host command
  export PATH=$2:$PATH RINA_RAM=127.0.0.1:8842 RINA_RAM_NETNS=/proc/$$/ns/net
  $3 > /tmp/bench-host.log 2>&1 & H=$!
  for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/8842) 2>/dev/null && break; sleep 0.1; done
  rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
  rinaperf -lw -z srv; sleep 0.5
  echo "== $1"
  rinaperf -z srv -t ping -c 200 -i 0 2>&1 | grep -E "rtt|transmitted"
  rinaperf -z srv -t rr -c 5000 2>&1 | grep -E "Transactions|Sender"
  timeout 120 rinaperf -z srv -t perf -c 20000 2>&1 | grep -E "Packets|Sender|Receiver"
  pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
}
pkill -x rina-host; pkill -x rina-ram; pkill -x rlite-uipcps; sleep 0.5
OLD=${OLD:?OLD=<Mode B as handed off>/ribbit/examples/rina/rlite}
run "Mode B as handed off" $OLD/bin "$OLD/bin/rina-ram --listen 127.0.0.1:8842"
NEW=$(cd "$(dirname "$0")/../build" && pwd)
run "RINA region (Redis model)" $NEW/bin "$NEW/bin/rina-host --listen 127.0.0.1:8842 --api /FawcettInnovations.RINA.ram_interface.php"
