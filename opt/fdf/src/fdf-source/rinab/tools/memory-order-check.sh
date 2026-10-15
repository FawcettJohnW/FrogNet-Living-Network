#!/bin/bash
# memory-order-check.sh [WRITERS] [WRITES] -- three properties of the RAM host, against a fresh build/rina-host:
#   [PUBLISH_IN_ID_ORDER_V1]          a reader following `after` never skips a cell, on one variable and on a whole
#                                     service (tests/order_race.cpp; WRITERS sessions x WRITES new cells each)
#   [REGION_COALESCE_PER_SESSION_V1]  a region request is answered for the session that asked it: two processes from
#                                     127.0.0.2 and 127.0.0.3, 16 sessions x 2,000 `seen` each (tests/seen_race.cpp)
#   [HOST_COALESCE_V1]                identical writes from 16 sessions in flight together: all answered, one cell, the
#                                     value written (tests/identical_writes.cpp)
# and the write rate with no reader (tests/write_rate.cpp), for comparison between builds. Needs tools/build.sh first.
set -e -o pipefail
W=${1:-8}; N=${2:-2000}; PORT=${PORT:-51990}; API=/FawcettInnovations.RINA.ram_interface.php
cd "$(dirname "$0")/.."; R=$(pwd); D=$(mktemp -d /tmp/memory-order-check.XXXXXX)
F="-std=c++20 -O2 -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc"; L="build/obj/frogram.o -llz4 -lcrypto -lresolv"
for t in order_race seen_race write_rate identical_writes; do g++ $F tests/$t.cpp $L -o $D/$t; done
gcc -shared -fPIC -O2 tests/srcbind.c -o $D/srcbind.so -ldl
RINA_DIAG=1 RINA_REGION_FILE=$D/region.perm build/rina-host --listen 0.0.0.0:$PORT --api $API > $D/host.log 2>&1 & H=$!
trap 'kill $H 2>/dev/null' EXIT
for i in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
bad=0
for mode in variable service; do $D/order_race 127.0.0.1:$PORT $mode $W $N || bad=$((bad + 1)); done
SRC_IP=127.0.0.2 LD_PRELOAD=$D/srcbind.so $D/seen_race 127.0.0.1:$PORT 16 2000 & A=$!
SRC_IP=127.0.0.3 LD_PRELOAD=$D/srcbind.so $D/seen_race 127.0.0.1:$PORT 16 2000 || bad=$((bad + 1))
wait $A || bad=$((bad + 1))
$D/identical_writes 127.0.0.1:$PORT 16 500 || bad=$((bad + 1))
$D/write_rate 127.0.0.1:$PORT 16 4000
echo "host log: $D/host.log"
[ $bad = 0 ] && echo "RESULT PASS" || { echo "RESULT FAIL ($bad)"; exit 1; }
