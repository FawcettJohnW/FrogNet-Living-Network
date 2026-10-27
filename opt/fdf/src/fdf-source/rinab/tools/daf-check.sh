#!/bin/bash
# daf-check.sh [TEST ...] -- [DAF_MODEL_V1] the DAF model's tests against a fresh build/rina-host: each test program runs
# phase 1, the host is killed with SIGKILL and restarted on the same region and DAF files, then phase 2.
# Default: every tests/daf_*.cpp. Needs tools/build.sh first. Exit 0 only if every phase of every test passes.
set -u
cd "$(dirname "$0")/.."; D=$(mktemp -d /tmp/daf-check.XXXXXX); PORT=${PORT:-51995}; API=/FawcettInnovations.RINA.ram_interface.php
F="-std=c++20 -O2 -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc"; L="build/obj/frogram.o -llz4 -lcrypto -lresolv"
TESTS=("$@"); [ ${#TESTS[@]} -eq 0 ] && TESTS=($(ls tests/daf_*.cpp | sed 's|tests/||; s|\.cpp$||'))
# host A is restarted between the phases; host B is a second island for the partition tests
start() { RINA_REGION_FILE=$D/region.perm build/rina-host --listen 127.0.0.1:$PORT --api $API >> $D/host.log 2>&1 & H=$!
          for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && return 0; sleep 0.05; done; echo "host did not start"; exit 1; }
RINA_REGION_FILE=$D/regionB.perm build/rina-host --listen 127.0.0.1:$((PORT + 1)) --api $API >> $D/hostB.log 2>&1 & HB=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$((PORT + 1))) 2>/dev/null && break; sleep 0.05; done
trap 'kill $H $HB 2>/dev/null' EXIT
bad=0; TAG=$$
for t in "${TESTS[@]}"; do
    g++ $F tests/$t.cpp $L -o $D/$t || { echo "$t: does not build"; bad=$((bad + 1)); continue; }
    start
    echo "== $t phase 1"; $D/$t 127.0.0.1:$PORT 1 $TAG 127.0.0.1:$((PORT + 1)) || bad=$((bad + 1))
    kill -9 $H; wait $H 2>/dev/null; start
    echo "== $t phase 2 (after kill -9 and restart)"; $D/$t 127.0.0.1:$PORT 2 $TAG 127.0.0.1:$((PORT + 1)) || bad=$((bad + 1))
    kill $H; wait $H 2>/dev/null
done
grep -h "DAF_FILE_V1" $D/host.log | tail -1
echo "host log: $D/host.log"
[ $bad = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL ($bad)"; exit 1; }
