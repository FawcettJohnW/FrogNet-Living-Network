#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# wire-mix.sh -- what the region's traffic is ON THE WIRE: rinaperf ping / rr / perf against the region, each
# program printing its session's FNWP counters at exit (RINA_WIRE_STATS=1): request kinds (RAW/FULL/DIFF/REPEAT),
# reply kinds (RAW/DIFF/SAME), bytes each way.
PORT=${PORT:-8851}; B=$(cd "$(dirname "$0")/../build" && pwd)
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net RINA_WIRE_STATS=1
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/wm-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv 2>/tmp/wm-server.err; sleep 0.3
for t in "ping -c 1000 -i 0" "rr -c 2000" "perf -c 5000 -s 1400"; do
  echo "== rinaperf -t $t"; rinaperf -z srv -t $t > /tmp/wm-cli.txt 2>&1; grep -E "wire|rtt min|Sender|Receiver" /tmp/wm-cli.txt
done
pkill -x rinaperf; sleep 0.5; echo "== server program (whole run)"; grep wire /tmp/wm-server.err
rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
