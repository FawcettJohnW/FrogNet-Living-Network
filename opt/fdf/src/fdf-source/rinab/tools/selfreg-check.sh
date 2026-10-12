#!/bin/bash
# selfreg-check.sh -- [RAM_MANAGER_REGISTERS_ITSELF_V1] the RAM manager registers its own LISP mapping when it listens,
# and again after it moves. A mapping region holds the site; the RAM manager starts at 127.0.0.2, then is killed and
# restarted at 127.0.0.3. PASS only if the mapping answers .2 after the first start and .3 after the move, both
# registered by the RAM manager itself (nothing else registers).
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php
D=$(mktemp -d /tmp/selfreg-check.XXXX); echo "diag dir: $D"; K=fdf-area-password; A=rina-area.test
cleanup() { kill ${MP:-} ${HP:-} 2>/dev/null; }; trap cleanup EXIT
RINA_HOST_ROLE=mapping RINA_REGION_FILE=$D/map.perm $B/rina-host --listen 127.0.0.1:4342 --api $API > $D/mapping.log 2>&1 & MP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/4342) 2>/dev/null && break; sleep 0.05; done
$B/rina-lisp map-site $A $K 127.0.0.1 > /dev/null || { echo "no site"; exit 1; }
up() { RINA_MAP_AREA=$A RINA_MAP_RESOLVER=127.0.0.1 RINA_AREA_PASSWORD=$K RINA_REGION_FILE=$D/area.perm $B/rina-host --listen $1:8874 --api $API > $D/area-$1.log 2>&1 & HP=$!
       for i in $(seq 100); do grep -q "RAM_MANAGER_REGISTERS_ITSELF_V1" $D/area-$1.log && break; sleep 0.05; done; grep -h "RAM_MANAGER_REGISTERS_ITSELF_V1" $D/area-$1.log; }
up 127.0.0.2; R1=$($B/rina-lisp map-resolve $A 127.0.0.1 2>&1); echo "after the start: $R1"
kill -9 $HP; wait $HP 2>/dev/null; up 127.0.0.3; R2=$($B/rina-lisp map-resolve $A 127.0.0.1 2>&1); echo "after the move:  $R2"
echo "$R1" | grep -q -- "-> 127.0.0.2" && echo "$R2" | grep -q -- "-> 127.0.0.3" && { echo "RESULT PASS"; exit 0; }; echo "RESULT FAIL"; exit 1
