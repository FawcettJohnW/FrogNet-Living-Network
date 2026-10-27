#!/bin/bash
# hospital-demo.sh [RUNS] -- [DAF_MODEL_V1] the plan's demonstration: the scripted incident (examples/hcm/hcm_incident),
# with the board (examples/hcm/hcm_board) serving it on http://127.0.0.1:$BOARD_PORT/ while it runs. Fresh RAM hosts for
# every run. Exit 0 only if every run passes (the plan: three in a row). WATCH=1 pauses 4 s before each step, for a
# person watching the board. Needs tools/build.sh first.
set -u
cd "$(dirname "$0")/.."; RUNS=${1:-3}; PORT=${PORT:-51993}; BOARD_PORT=${BOARD_PORT:-8077}; API=/FawcettInnovations.RINA.ram_interface.php
D=$(mktemp -d /tmp/hospital-demo.XXXXXX)
F="-std=c++20 -O2 -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc"; L="build/obj/frogram.o -llz4 -lcrypto -lresolv"
g++ $F examples/hcm/hcm_incident.cpp $L -o $D/hcm_incident && g++ $F examples/hcm/hcm_board.cpp $L -o $D/hcm_board || { echo "the demonstration does not build"; exit 1; }
bad=0
for r in $(seq $RUNS); do
    RINA_REGION_FILE=$D/a$r.perm build/rina-host --listen 127.0.0.1:$PORT --api $API > $D/hostA$r.log 2>&1 & HA=$!
    RINA_REGION_FILE=$D/b$r.perm build/rina-host --listen 127.0.0.1:$((PORT + 1)) --api $API > $D/hostB$r.log 2>&1 & HB=$!
    for p in $PORT $((PORT + 1)); do for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && break; sleep 0.05; done; done
    TAG=$$r
    echo "== run $r"
    # the board joins the incident as soon as the incident exists (step 1 says when)
    [ "${WATCH:-0}" = 1 ] && export HCM_PAUSE_MS=4000
    $D/hcm_incident 127.0.0.1:$PORT 127.0.0.1:$((PORT + 1)) $TAG $BOARD_PORT | while IFS= read -r line; do
        echo "$line"
        case "$line" in *"(board) start hcm_board"*) $D/hcm_board 127.0.0.1:$PORT "hcm-$TAG#incident" $BOARD_PORT > $D/board$r.log 2>&1 & echo $! > $D/board.pid ;; esac
    done
    rc=${PIPESTATUS[0]}; [ $rc = 0 ] || bad=$((bad + 1))
    [ -f $D/board.pid ] && kill $(cat $D/board.pid) 2>/dev/null
    kill $HA $HB 2>/dev/null; wait $HA $HB 2>/dev/null
done
echo "logs: $D"
[ $bad = 0 ] && echo "RESULT PASS ($RUNS of $RUNS runs)" || { echo "RESULT FAIL ($bad of $RUNS runs failed)"; exit 1; }
