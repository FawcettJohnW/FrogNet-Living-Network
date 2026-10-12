#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# perf-count.sh [N] [PORT] -- one rinaperf perf of N SDUs on the RINA host: every SDU must arrive (receiver count = N).
# N > RING (4096) makes the sender run out of credit and take it back by quarters.
N=${1:-20000}; PORT=${2:-8846}; B=$(cd "$(dirname "$0")/../build" && pwd)
# [PRIMED_TEMPLATES_V1] both ends learn the primed store at start (PRIME=0 to learn on the wire instead)
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")" && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")" && pwd)/templates/rina.prime
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/pc-host.log 2>&1 & H=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv; sleep 0.5
timeout 120 rinaperf -z srv -t perf -c $N > /tmp/pc-cli.txt 2>&1; echo "client rc=$? host alive: $(kill -0 $H 2>/dev/null && echo yes || echo NO)"; echo "taken: $(peek taken | grep -v rows | awk "{print \$2, \$3}" | tr "\n" " ")"; echo "client said: $(grep -iE "expected|WRN|Timeout" /tmp/pc-cli.txt | tr "\n" " ")"; grep -E "Packets|Sender|Receiver" /tmp/pc-cli.txt || { echo "--- client said:"; tail -5 /tmp/pc-cli.txt; for v in taken cr dropped sess; do echo "--- $v"; peek $v | tail -4; done; for p in $(pgrep -x rinaperf); do echo "--- rinaperf $p tasks (tid syscall-nr arg0 arg1 arg2):"; for t in /proc/$p/task/*; do echo "   $(basename $t) $(cut -d" " -f1-4 $t/syscall 2>/dev/null)"; done; echo "   maps: $(grep -E "rinaperf|heap" /proc/$p/maps | head -6 | awk "{print \$1, \$6}" | tr "\n" ";")"; done; sleep 5; for p in $(peek proc | awk "{print \$2}"); do echo "--- ev:$p after 5 s more: $(peek ev:$p | grep -c "") rows; control-flow SDUs: $(peek ev:$p | grep -v " s:.*-3-" | tail -3 | tr "\n" "|")"; done; }
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 1; kill $H; wait $H 2>/dev/null; pkill -x rlite-uipcps
