#!/bin/bash
# eexist_oracle.sh -- [JOIN_REFUSED_WAITS_V1] two members announce the same served /24: the second is REFUSED (EEXIST,
# the holder named), says so once and WAITS on the region (alive, no retry loop, no exit); when the first leaves, the
# second joins on that event. PASS only if all of that is seen. Its own RAM manager on loopback; every process it
# starts is stopped by its PID.  usage: tests/eexist_oracle.sh   (B=<dir with rina-host, fdf-client>: default build/static)
cd "$(dirname "$0")/.."; B=${B:-$PWD/build/static}; T=$PWD/../fdf/templates/fdf.prime; D=$(mktemp -d); PORT=${PORT:-51998}
API=/FawcettInnovations.RINA.ram_interface.php; U="http://127.0.0.1:$PORT$API?op=rina"
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
PH=""; PA=""; PB=""; cleanup() { kill $PB $PA $PH 2>/dev/null; }; trap cleanup EXIT
RINA_REGION_FILE=$D/area.perm RIBBIT_TEMPLATES=$T $B/rina-host --listen 127.0.0.1:$PORT --api $API > $D/host.log 2>&1 & PH=$!
for i in $(seq 100); do curl -s -m 1 -X POST -d '{"op":"version"}' "$U" > /dev/null && break; sleep 0.05; done
for m in a b; do printf '{"role":"node","name":"node-%s","guid":"g-%s","subnet":"10.44.4.0/24","pubkey":"","transit":[],"maps":[]}\n' $m $m > $D/f$m.json; done
FDF_FACTS=$D/fa.json RIBBIT_TEMPLATES=$T $B/fdf-client 127.0.0.1:$PORT > $D/a.log 2>&1 & PA=$!
for i in $(seq 100); do grep -q "joined the region" $D/a.log && break; sleep 0.05; done
FDF_FACTS=$D/fb.json RIBBIT_TEMPLATES=$T $B/fdf-client 127.0.0.1:$PORT > $D/b.log 2>&1 & PB=$!
for i in $(seq 100); do grep -q "JOIN REFUSED" $D/b.log && break; sleep 0.05; done
sleep 1
check $(grep -q "joined the region" $D/a.log && echo 0 || echo 1) "a joined: $(grep -m1 -o 'node-a joined.*' $D/a.log | cut -c1-80)"
check $(grep -q "JOIN REFUSED" $D/b.log && ! grep -q "joined the region" $D/b.log && echo 0 || echo 1) "b refused while a holds 10.44.4.0/24: $(grep -m1 -o 'JOIN REFUSED.*' $D/b.log | cut -c1-160)"
check $(kill -0 $PB 2>/dev/null && [ "$(grep -c 'JOIN REFUSED' $D/b.log)" = 1 ] && echo 0 || echo 1) "b waits on the region: alive, refused once (not a retry loop)"
kill $PA; wait $PA 2>/dev/null; PA=""
for i in $(seq 100); do grep -q "joined the region" $D/b.log && break; sleep 0.05; done
check $(grep -q "the region changed; trying the announcement again" $D/b.log && grep -q "joined the region" $D/b.log && echo 0 || echo 1) "a left: b tried again on that event and joined"
P=$(curl -s -X POST -d '{"op":"participants"}' "$U" | python3 -c 'import sys,json; print(sorted((p["name"],p["alive"]) for p in json.load(sys.stdin)["participants"]))' 2>&1)
check $([ "$P" = "[('node-b', True)]" ] && echo 0 || echo 1) "the region lists b alone, alive: $P"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS") ($D)"
[ $FAILS = 0 ]
