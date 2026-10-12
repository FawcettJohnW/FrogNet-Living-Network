#!/bin/bash
# move-check.sh -- [MAP_WATCH_V1] the RAM manager moves; every machine finds it again through the mapping, by itself.
# Three machines (own namespace, hostname, address) are given the area's NAME and the mapping region's address (the
# Map-Resolver: candidate 1 of item 3's open question, printed as configured). The RAM manager registers itself at
# 10.60.0.1; the machines join by name and announce. It is killed and restarted at 10.60.0.2 on the same region file.
# PASS only if all three re-attach by themselves (on its re-registration), re-announce, and are listed alive at the new
# address; and no packet passed between two machines.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php; K=fdf-area-password; A=rina-area.test
PROBE=${RINA_CAPABILITY_PROBE:-/tmp/frognet-src/usr/local/bin/frognet_capability_probe.sh}
D=$(mktemp -d /tmp/move-check.XXXX); echo "diag dir: $D"
export RINA_AREA=$A RINA_MAP_RESOLVER=10.60.0.1 RINA_RAM_NETNS=/proc/self/ns/net RINA_AGENT_LOG=$D RINA_CAPABILITY_PROBE=$PROBE PATH=$B:$PATH
echo "CONFIGURED: each machine: RINA_AREA=$A RINA_MAP_RESOLVER=10.60.0.1 (nothing else about the area)"
cleanup() { for i in 1 2 3; do ip netns exec dm$i rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.3; kill ${HP:-} ${MP:-} ${TD:-} 2>/dev/null
            for i in 1 2 3; do for p in $(ip netns pids dm$i 2>/dev/null); do kill -9 $p; done; ip netns delete dm$i 2>/dev/null; done; ip link delete dmbr 2>/dev/null; }
cleanup; trap cleanup EXIT
ip link add dmbr type bridge; ip addr add 10.60.0.1/24 dev dmbr; ip addr add 10.60.0.2/24 dev dmbr; ip link set dmbr up
for i in 1 2 3; do ip netns add dm$i; ip netns exec dm$i ip link set lo up
    ip link add v$i type veth peer name b$i; ip link set b$i master dmbr; ip link set b$i up; ip link set v$i netns dm$i
    ip netns exec dm$i ip addr add 10.60.0.1$i/24 dev v$i; ip netns exec dm$i ip link set v$i up; done
tcpdump -i dmbr -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
RINA_HOST_ROLE=mapping RINA_REGION_FILE=$D/map.perm rina-host --listen 10.60.0.1:4342 --api $API > $D/mapping.log 2>&1 & MP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/10.60.0.1/4342) 2>/dev/null && break; sleep 0.05; done
rina-lisp map-site $A $K 10.60.0.1 > /dev/null || { echo "no site"; exit 1; }
up() { RINA_MAP_AREA=$A RINA_AREA_PASSWORD=$K RINA_REGION_FILE=$D/area.perm rina-host --listen $1:8840 --api $API > $D/area-$1.log 2>&1 & HP=$!
       for i in $(seq 100); do grep -q "RAM_MANAGER_REGISTERS_ITSELF_V1" $D/area-$1.log && break; sleep 0.05; done; grep -h "REGISTERS_ITSELF" $D/area-$1.log; }
up 10.60.0.1
for i in 1 2 3; do ip netns exec dm$i unshare --uts bash -c "hostname machine-$i; exec rlite-uipcps -d" || echo "machine-$i: agent failed"; done
for i in $(seq 100); do [ "$(grep -h 'FDF_DISCOVERY_V1\] announced' $D/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.05; done
echo "== before the move"; RINA_RAM=10.60.0.1:8840 fdf-participants | grep -o '"hostname":"[^"]*","ips":\[[^]]*\],"alive":[a-z]*'
T0=$(date +%s.%N); kill -9 $HP; wait $HP 2>/dev/null; up 10.60.0.2
for i in $(seq 200); do [ "$(grep -h 're-attached to the area by name' $D/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.02; done
T1=$(date +%s.%N); echo "re-attached: $(grep -h 're-attached to the area by name' $D/agent.*.log | wc -l) of 3, $(echo "($T1 - $T0) * 1000" | bc | cut -d. -f1) ms after the kill"
sleep 0.3; echo "== after the move (asked at the new address)"; AFTER=$(RINA_RAM=10.60.0.2:8840 fdf-participants); echo "$AFTER" | grep -o '"hostname":"[^"]*","ips":\[[^]]*\],"alive":[a-z]*'
kill $TD 2>/dev/null; wait $TD 2>/dev/null
M2M=$(tcpdump -nn -r $D/wire.pcap 'src net 10.60.0.0/24 and dst net 10.60.0.0/24 and not host 10.60.0.1 and not host 10.60.0.2' 2>/dev/null | wc -l)
echo "wire: $(tcpdump -nn -r $D/wire.pcap 2>/dev/null | wc -l) packets; $M2M between two machines"
ALIVE=$(echo "$AFTER" | grep -c '"alive":true'); RE=$(grep -h 're-attached to the area by name' $D/agent.*.log | wc -l)
[ "$ALIVE" = 3 ] && [ "$RE" -ge 3 ] && [ "$M2M" = 0 ] && { echo "RESULT PASS"; exit 0; }; echo "RESULT FAIL (alive=$ALIVE re-attached=$RE m2m=$M2M)"; exit 1
