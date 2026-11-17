#!/bin/bash
# gate.sh -- soft Homa held to Homa's own tools, UNCHANGED (his util/server, util/homa_test and util/cp_node).
#   tools/gate.sh /path/to/HomaModule
# Builds what changed, starts the memory, runs his tools with libsofthoma.so ahead of libc, and says PASS or FAIL
# for each check. One machine: this shows the contract is kept, not how fast it is (a comparison is multi-machine).
HOMA="${1:?usage: tools/gate.sh /path/to/HomaModule}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE" || exit 1
make HOMA="$HOMA" -j8 >/dev/null || { echo "  [FAIL] build"; exit 1; }
( cd "$HOMA/util" && make server homa_test cp_node >/dev/null 2>&1 ) || { echo "  [FAIL] his tools did not build"; exit 1; }
export SOFTHOMA_NODE1=127.0.0.1                # cp_node names its servers node<N>; /etc/hosts is not written
RAMPORT=${SOFTHOMA_GATE_PORT:-9111}; OUT=$(mktemp -d); fails=0
export SOFTHOMA_RAM=127.0.0.1:$RAMPORT
soft() { env LD_PRELOAD="$HERE/build/libsofthoma.so" "$@"; }   # in the background, exec so that $! is the program
check() { if [ "$1" = 0 ]; then echo "  [PASS] $2"; else echo "  [FAIL] $2"; fails=$((fails+1)); fi; }
# [FIELD_ORDER_WITHOUT_LEAF_TYPES_V1] the platform change soft Homa's speed rests on: same field order as before it
build/field-order-check 300000 >"$OUT/field-order.log" 2>&1; check $? "a cell write's field order without classifying its strings is the order with: $(tail -1 "$OUT/field-order.log")"
build/softhoma-ram 127.0.0.1:$RAMPORT 2>"$OUT/ram.log" & RAM=$!
sleep 1
env LD_PRELOAD="$HERE/build/libsofthoma.so" "$HOMA/util/server" --port 4100 >"$OUT/server.log" 2>&1 & SRV=$!
sleep 1
T="$HOMA/util/homa_test 127.0.0.1:4100"
soft timeout 30 $T invoke >"$OUT/invoke.log" 2>&1
grep -q "Received message from 127.0.0.1:4100 with 100 bytes, seed 12345" "$OUT/invoke.log"; check $? "homa_test invoke: his request is answered by his server and the contents check (seed 12345)"
soft timeout 30 $T poll >"$OUT/poll.log" 2>&1
grep -q "Poll succeeded with mask 0x1" "$OUT/poll.log" && grep -q "rcvmsg returned 100 bytes" "$OUT/poll.log"; check $? "homa_test poll: the socket is readable to poll() when a response is there"
soft timeout 30 $T shutdown >"$OUT/shutdown.log" 2>&1
grep -q "Error in recvmsg: Cannot send after transport endpoint shutdown" "$OUT/shutdown.log"; check $? "homa_test shutdown: a blocked recvmsg ends with ESHUTDOWN"
soft timeout 60 $T rtt >"$OUT/rtt.log" 2>&1
grep -q "^Avg:" "$OUT/rtt.log"; check $? "homa_test rtt: $(grep -E '^(P50|Avg|P90):' "$OUT/rtt.log" | tr -s ' ' | tr '\n' ' ')"
soft timeout 30 $T ioctl >"$OUT/ioctl.log" 2>&1
! grep -q "Unexpected return" "$OUT/ioctl.log" && grep -q "P50" "$OUT/ioctl.log"; check $? "homa_test ioctl: a request the module does not know is answered EINVAL"
soft timeout 30 $T read >"$OUT/read.log" 2>&1
! grep -q "Unexpected return" "$OUT/read.log" && grep -q "P50" "$OUT/read.log"; check $? "homa_test read: read() on a Homa socket is answered EINVAL"
soft timeout 30 $T private >"$OUT/private.log" 2>&1
[ "$(grep -c "Received message from 127.0.0.1:4100 with 100 bytes, seed 12345" "$OUT/private.log")" = 3 ]; check $? "homa_test private: three requests, each response asked for by its own id, all three arrive and check"
soft timeout 30 $T set_buf >"$OUT/set_buf.log" 2>&1
! grep -q "Error\|Couldn" "$OUT/set_buf.log"; check $? "homa_test set_buf: a receive region is registered"
soft timeout 60 $T stream >"$OUT/stream.log" 2>&1
grep -q "Homa throughput using 100 concurrent 100 byte messages" "$OUT/stream.log"; check $? "homa_test stream: 100 requests in flight at once, every response matched: $(grep -o 'messages: .*' "$OUT/stream.log")"
soft timeout 60 $T wmem >"$OUT/wmem.log" 2>&1; rc=$?
[ $rc = 0 ] && grep -q "Sender shutting down socket" "$OUT/wmem.log"; check $? "homa_test wmem: his sends complete beside a slow receiver, and the shutdown ends the receiver (exit $rc)"
kill $SRV 2>/dev/null
env LD_PRELOAD="$HERE/build/libsofthoma.so" "$HOMA/util/cp_node" server --protocol homa --first-port 4200 --port-threads 2 >"$OUT/cp-server.log" 2>&1 & CPS=$!
sleep 1.5
for spec in 100:8 w2:8 w4:8 500000:1 1000000:1; do
    w=${spec%:*}; n=${spec#*:}
    soft timeout 16 "$HOMA/util/cp_node" client --protocol homa --workload $w --client-max $n --ports 1 --first-port 4200 --first-server 1 --server-nodes 1 >"$OUT/cp-$w.log" 2>&1
    line=$(grep "clients:" "$OUT/cp-$w.log" | tail -1 | sed 's/^[0-9.]* //')
    stalled=$(grep "clients:" "$OUT/cp-$w.log" | tail -n +3 | grep -c -- "-nan bytes")     # a second in which nothing completed
    [ -n "$line" ] && [ "$stalled" = 0 ] && ! grep -q "terminate\|FATAL\|MEMORY IS GONE" "$OUT/cp-$w.log"; check $? "cp_node workload $w, $n outstanding, no second without a completion: $line"
done
# two clients at once against one server: each gets its own answers (names and ids are each socket's own)
for c in a b; do soft timeout 12 "$HOMA/util/cp_node" client --protocol homa --workload 100 --client-max 8 --ports 1 --first-port 4200 --first-server 1 --server-nodes 1 >"$OUT/two-$c.log" 2>&1 & done
wait %% 2>/dev/null; sleep 1
la=$(grep "clients:" "$OUT/two-a.log" | tail -2 | head -1 | grep -o "[0-9.]* Kops/sec"); lb=$(grep "clients:" "$OUT/two-b.log" | tail -2 | head -1 | grep -o "[0-9.]* Kops/sec")
[ -n "$la" ] && [ -n "$lb" ] && [ "$(cat "$OUT"/two-?.log | grep "clients:" | tail -n +5 | grep -c -- "-nan bytes")" = 0 ] && ! grep -q "terminate\|FATAL\|MEMORY IS GONE" "$OUT"/two-?.log; check $? "two cp_node clients at once on one server, neither stalls: $la and $lb"
# a server that dies with requests outstanding: its capability cell goes with it and every one ends, with no timer
env LD_PRELOAD="$HERE/build/libsofthoma.so" timeout 20 "$HOMA/util/cp_node" client --protocol homa --workload 100 --client-max 8 --ports 1 --first-port 4200 --first-server 1 --server-nodes 1 >"$OUT/cp-gone.log" 2>&1 & CL=$!
sleep 3; t0=$(date +%s%N); kill -9 $CPS; 
for i in $(seq 1 100); do grep -q "is GONE" "$OUT/cp-gone.log" && break; sleep 0.05; done
ms=$(( ($(date +%s%N) - t0) / 1000000 ))
grep -q "is GONE (its capability cell left the memory)" "$OUT/cp-gone.log"; check $? "his server killed with requests outstanding: the client is told it is gone within about $ms ms (checked every 50 ms), by its capability cell leaving the memory: $(grep -o '[0-9]* request(s) from' "$OUT/cp-gone.log" | head -1 | cut -d' ' -f1) request(s) ended"
kill $CL $RAM 2>/dev/null; wait 2>/dev/null
[ "$fails" = 0 ] && { echo; echo "ALL SOFT-HOMA GATE CHECKPOINTS PASS"; rm -rf "$OUT"; exit 0; }
echo; echo "SOFT-HOMA GATE FAILED ($fails); logs kept in $OUT"; exit 1
