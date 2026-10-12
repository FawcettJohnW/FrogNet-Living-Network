#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# quick-bench.sh [LABEL] -- the measurement taken after every step: rinaperf ping (1,000), rr (5,000), perf
# (20,000 x 1,400 B) against the region on this machine, plus each program's wire counters. One line of numbers per
# test, appended to evidence/quick-bench.tsv with the label, so each step is measured the same way as the one before.
LABEL=${1:-unlabelled}; PORT=${PORT:-8861}; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build
# [PRIMED_TEMPLATES_V1] both ends learn the primed store at start (PRIME=0 to learn on the wire instead)
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")" && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")" && pwd)/templates/rina.prime
[ "${PRIME:-1}" = 1 ] && [ -f "$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime" ] && export RIBBIT_TEMPLATES=$(cd "$(dirname "$0")/.." && pwd)/templates/rina.prime
export PATH=$B/bin:$PATH RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
$B/bin/rina-host --listen 127.0.0.1:$PORT --api /FawcettInnovations.RINA.ram_interface.php > /tmp/qb-host.log 2>&1 & HP=$!
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
rlite-uipcps -d; rlite-ctl ipcp-create x normal dd >/dev/null; rlite-ctl ipcp-enroller-enable x
rinaperf -lw -z srv; sleep 0.5
ping=$(rinaperf -z srv -t ping -c 1000 -i 0 2>&1 | grep "rtt min" | awk -F/ '{print $5}')
rr=$(rinaperf -z srv -t rr -c 5000 2>&1 | awk '/^Sender/{print $3*1000}')
perf=$(timeout 60 rinaperf -z srv -t perf -c 20000 -s 1400 2>&1 | awk '/^Receiver/{print $2, $4}')
pkill -x rinaperf; rlite-ctl reset >/dev/null 2>&1; rlite-ctl terminate >/dev/null 2>&1; sleep 0.5; kill $HP; wait $HP 2>/dev/null; pkill -x rlite-uipcps
[ -s "$H/evidence/quick-bench.tsv" ] || printf "label\tping_ms\trr_tps\tperf_received\tperf_mbps\n" > "$H/evidence/quick-bench.tsv"
printf "%s\t%s\t%s\t%s\n" "$LABEL" "$ping" "$rr" "$(echo $perf | tr ' ' '\t')" | tee -a "$H/evidence/quick-bench.tsv"
