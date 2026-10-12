#!/bin/bash
# [REGION_FILE_V1] the host needs a file for the region's Permanent records; a fresh one per run unless given
export RINA_REGION_FILE=${RINA_REGION_FILE:-/tmp/rinab-region-$$.perm}
# run-suite.sh RLITE_ROOT OUT [FIRST LAST] -- rlite's integration tests, unchanged, against the RINA region.
# rlite's runner minus the kernel modules. ISOLATED=1: the host in its own pid and mount namespaces with the test
# namespaces and devices hidden (it can read nothing a participant has not told it).
set -u
ROOT=${1:?usage: run-suite.sh RLITE_ROOT OUT [FIRST LAST]}; OUT=${2:?usage: run-suite.sh RLITE_ROOT OUT [FIRST LAST]}; FIRST=${3:-1}; LAST=${4:-30}
# [NO_FALLBACKS] the rlite tree must be there, and built: stop at once, never run the suite from somewhere else
[ -d "$ROOT/tests/integration" ] || { echo "run-suite: $ROOT has no tests/integration (is the rlite source tree there?)" >&2; exit 2; }
B=$(cd "$(dirname "$0")/../build" && pwd); export PATH=$B/bin:$PATH
export RINA_RAM=${RINA_RAM:-127.0.0.1:8840} RINA_RAM_NETNS=/proc/$$/ns/net
API=/FawcettInnovations.RINA.ram_interface.php
[ -x "$B/bin/rlite-uipcps" ] || { echo "run-suite: $B/bin has no rlite-uipcps: run tools/build_rlite.sh RLITE_ROOT first" >&2; exit 2; }
# (pgrep matches at most 15 characters of a name: one pattern per program)
if pgrep -x rina-host >/dev/null || pgrep -x rlite-uipcps >/dev/null; then echo "run-suite: a rina-host or rlite-uipcps is already running" >&2; exit 2; fi
mkdir -p /var/run/netns
if [ "${ISOLATED:-0}" = 1 ]; then
  unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec $B/bin/rina-host --listen $RINA_RAM --api $API" > /tmp/rinab-host.log 2>&1 &
else
  $B/bin/rina-host --listen $RINA_RAM --api $API > /tmp/rinab-host.log 2>&1 &
fi
UP=$!; trap "pkill -x rina-host; wait $UP 2>/dev/null" EXIT
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/${RINA_RAM##*:}) 2>/dev/null && break; sleep 0.1; done
cd "$ROOT" || exit 2
# a run from test 1 starts its own table; a later range (FIRST > 1) adds to it
if [ "$FIRST" -le 1 ] || [ ! -s "$OUT" ]; then printf "n\ttest\tresult\tseconds\n" > "$OUT"; fi
n=0
for t in tests/integration/*.sh; do
    n=$((n + 1)); [ $n -lt $FIRST ] && continue; [ $n -gt $LAST ] && break
    name=$(basename $t .sh)
    rlite-uipcps -d || { printf "%d\t%s\tPREPARE_FAILED\t0\n" $n $name >> $OUT; continue; }
    s=$(date +%s.%N)
    timeout 120 $t > /tmp/rinab-suite-$name.log 2>&1; rc=$?
    e=$(date +%s.%N)
    rm -f /tmp/ceft-dft-* /tmp/ceft-aa-*
    rlite-ctl reset > /dev/null 2>&1; rlite-ctl terminate > /dev/null 2>&1; rlite-ctl reset > /dev/null 2>&1
    for p in rinaperf rina-echo-async iporinad; do pkill -x $p 2>/dev/null; done
    for i in $(seq 50); do pgrep -x rlite-uipcps >/dev/null || break; sleep 0.1; done
    if [ $rc -eq 0 ]; then r=PASS; else r="FAIL(rc=$rc)"; fi
    printf "%d\t%s\t%s\t%.2f\n" $n $name "$r" $(echo "$e - $s" | bc) >> $OUT
done
awk -F'\t' 'NR>1{print $3}' $OUT | sort | uniq -c
awk -F'\t' 'NR>1 && $3!="PASS"{print "  " $2 " " $3}' $OUT
