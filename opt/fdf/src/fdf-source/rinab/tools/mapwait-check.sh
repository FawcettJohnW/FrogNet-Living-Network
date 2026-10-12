#!/bin/bash
# mapwait-check.sh -- [MAP_WATCH_V1] a member waiting on the mapping learns the RAM manager moved, with no timer:
# the RAM manager starts at 127.0.0.2 (registering itself); a watcher waits (rina-lisp map-wait); the RAM manager is
# killed and restarted at 127.0.0.3, registering itself again. PASS only if the watcher returns, by itself, with .3.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php
D=$(mktemp -d /tmp/mapwait-check.XXXX); echo "diag dir: $D"; K=fdf-area-password; A=rina-area.test
cleanup() { kill ${MP:-} ${HP:-} ${WP:-} 2>/dev/null; }; trap cleanup EXIT
RINA_HOST_ROLE=mapping RINA_REGION_FILE=$D/map.perm $B/rina-host --listen 127.0.0.1:4342 --api $API > $D/mapping.log 2>&1 & MP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/4342) 2>/dev/null && break; sleep 0.05; done
$B/rina-lisp map-site $A $K 127.0.0.1 > /dev/null || { echo "no site"; exit 1; }
up() { RINA_MAP_AREA=$A RINA_MAP_RESOLVER=127.0.0.1 RINA_AREA_PASSWORD=$K RINA_REGION_FILE=$D/area.perm $B/rina-host --listen $1:8874 --api $API > $D/area-$1.log 2>&1 & HP=$!
       for i in $(seq 100); do grep -q "RAM_MANAGER_REGISTERS_ITSELF_V1" $D/area-$1.log && break; sleep 0.05; done; }
up 127.0.0.2; echo "the mapping now: $($B/rina-lisp map-resolve $A 127.0.0.1 2>&1)"
RINA_RAM_NETNS=/proc/self/ns/net $B/rina-lisp map-wait $A 127.0.0.1 > $D/watcher.out 2>&1 & WP=$!; sleep 0.5
kill -0 $WP 2>/dev/null && echo "the watcher is waiting (it has not returned before the move)" || echo "the watcher returned early: $(cat $D/watcher.out)"
T0=$(date +%s.%N); kill -9 $HP; wait $HP 2>/dev/null; up 127.0.0.3
for i in $(seq 100); do kill -0 $WP 2>/dev/null || break; sleep 0.02; done
T1=$(date +%s.%N); echo "the watcher: $(cat $D/watcher.out) ($(echo "($T1 - $T0) * 1000" | bc | cut -d. -f1) ms after the kill)"
grep -q -- "-> 127.0.0.3" $D/watcher.out && { echo "RESULT PASS"; exit 0; }; echo "RESULT FAIL"; exit 1
