#!/bin/bash
# map-removal-check.sh -- [MAP_REMOVAL_IS_PERMANENT_V1] [OWNED_MAPPING_IS_NOT_RESTORED_V1] a removed mapping stays removed
# across a RAM host restart, and a session-bound mapping held at an UNCLEAN stop is not restored
# (tests/map_removal_persists.cpp). Starts build/rina-host on 127.0.0.1:4342 with a fresh region file, runs phase 1,
# holds a session-bound mapping, kills the host with SIGKILL, starts it again on the same file, runs phase 2.
set -e -o pipefail
cd "$(dirname "$0")/.."; D=$(mktemp -d /tmp/map-removal-check.XXXXXX); API=/FawcettInnovations.RINA.ram_interface.php
g++ -std=c++20 -O2 -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc tests/map_removal_persists.cpp build/obj/frogram.o \
    -llz4 -lcrypto -lresolv -o $D/map_removal_persists
KEY=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'); AREA=mrc$$
start() { RINA_REGION_FILE=$D/region.perm build/rina-host --listen 127.0.0.1:4342 --api $API >> $D/host.log 2>&1 & H=$!
          for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/4342) 2>/dev/null && return; sleep 0.1; done; echo "host did not start"; exit 1; }
start; trap 'kill $H 2>/dev/null' EXIT
r1=0; $D/map_removal_persists 127.0.0.1:4342 1 $AREA $KEY || r1=$?
$D/map_removal_persists 127.0.0.1:4342 hold $AREA $KEY > $D/hold.out 2>&1 & HP=$!
for i in $(seq 50); do grep -q '^held' $D/hold.out 2>/dev/null && break; sleep 0.1; done; cat $D/hold.out
kill -9 $H; wait $H 2>/dev/null || true; kill $HP 2>/dev/null; wait $HP 2>/dev/null || true
start
r2=0; $D/map_removal_persists 127.0.0.1:4342 2 $AREA $KEY || r2=$?
echo "host log: $D/host.log"
[ $r1 = 0 ] && [ $r2 = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL"; exit 1; }
