#!/bin/bash
# rule-check.sh -- [MR_ADDRESS_FROM_NAME_V1] completely hands off, by RULE (John, 2026-10-07: option B; experiments/
# DESIGN-MR-RULE.md): a machine is given ONLY the area's name. Where the area's mapping system is, is COMPUTED from that
# name (198.18.0.0/15 + the leading bits of SHA-256(name) here; IPv6 in deployment); the mapping host binds that address.
# Where the RAM manager is comes from the mapping (it registers itself). Three machines join, announce, and follow the
# RAM manager when it moves (10.60.0.1 -> 10.60.0.2). No DNS anywhere: any DNS packet fails the test.
# PASS only if: no machine has RINA_MAP_RESOLVER or RINA_RAM; all three join and are listed alive; all three re-attach
# after the move and are listed alive at the new address; no packet between two machines; no DNS packet.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php; K=fdf-area-password; A=rina-area.test
PROBE=${RINA_CAPABILITY_PROBE:-/tmp/frognet-src/usr/local/bin/frognet_capability_probe.sh}
D=$(mktemp -d /tmp/rule-check.XXXX); echo "diag dir: $D"; export PATH=$B:$PATH
C() { ip netns exec core env -u RINA_MAP_RESOLVER -u RINA_RAM "$@"; }
cleanup() { for i in 1 2 3; do ip netns exec dm$i env RINA_AREA=$A rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.3; kill ${TD:-} 2>/dev/null
            for n in dm1 dm2 dm3 core; do for p in $(ip netns pids $n 2>/dev/null); do kill -9 $p; done; ip netns delete $n 2>/dev/null; done
            ip link delete dmbr 2>/dev/null; }
cleanup; trap cleanup EXIT
ip link add dmbr type bridge; ip addr add 10.60.0.254/24 dev dmbr; ip link set dmbr up
for n in core dm1 dm2 dm3; do ip netns add $n; ip netns exec $n ip link set lo up
    ip link add v$n type veth peer name b$n; ip link set b$n master dmbr; ip link set b$n up; ip link set v$n netns $n; ip netns exec $n ip link set v$n up; done
ip netns exec core ip addr add 10.60.0.1/24 dev vcore; ip netns exec core ip addr add 10.60.0.2/24 dev vcore
for i in 1 2 3; do ip netns exec dm$i ip addr add 10.60.0.1$i/24 dev vdm$i; done
MR=$(rina-lisp mr-address $A | head -1)
echo "THE RULE: '$A' -> its mapping system at $MR (computed by every machine from the name; nothing published, no DNS)"
ip netns exec core ip addr add $MR/32 dev vcore                 # the machine serving the area's mapping binds the derived address
for i in 1 2 3; do ip netns exec dm$i ip route add 198.18.0.0/15 via 10.60.0.1; done
echo "THE NETWORK: a route to the reserved prefix 198.18.0.0/15 (anycast routing; knows no area)"
tcpdump -i dmbr -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
C env RINA_HOST_ROLE=mapping RINA_REGION_FILE=$D/map.perm rina-host --listen $MR:4342 --api $API > $D/mapping.log 2>&1 &
for i in $(seq 100); do C bash -c "(exec 3<>/dev/tcp/$MR/4342)" 2>/dev/null && break; sleep 0.05; done
C rina-lisp map-site $A $K $MR > /dev/null || { echo "no site"; exit 1; }
echo "CONFIGURED: the RAM manager: RINA_MAP_AREA=$A RINA_AREA_PASSWORD=<the area's password>. Each machine: RINA_AREA=$A. NOTHING ELSE (no RINA_MAP_RESOLVER, no RINA_RAM)"
up() { C env RINA_MAP_AREA=$A RINA_AREA_PASSWORD=$K RINA_REGION_FILE=$D/area.perm RINA_RAM_NETNS=/proc/self/ns/net rina-host --listen $1:8840 --api $API > $D/area-$1.log 2>&1 &
       for i in $(seq 100); do grep -q "RAM_MANAGER_REGISTERS_ITSELF_V1" $D/area-$1.log && break; sleep 0.05; done; grep -h "REGISTERS_ITSELF" $D/area-$1.log; }
up 10.60.0.1
for i in 1 2 3; do ip netns exec dm$i env -u RINA_MAP_RESOLVER -u RINA_RAM RINA_AREA=$A RINA_RAM_NETNS=/proc/self/ns/net RINA_AGENT_LOG=$D RINA_CAPABILITY_PROBE=$PROBE \
    unshare --uts bash -c "hostname machine-$i; exec rlite-uipcps -d" || echo "machine-$i: agent failed"; done
for i in $(seq 100); do [ "$(grep -h 'FDF_DISCOVERY_V1\] announced' $D/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.05; done
echo "== joined (asked at 10.60.0.1)"; B4=$(RINA_RAM=10.60.0.1:8840 fdf-participants); echo "$B4" | grep -o '"hostname":"[^"]*","ips":\[[^]]*\],"alive":[a-z]*'
T0=$(date +%s.%N); for p in $(ip netns pids core); do grep -qa -- "10.60.0.1:8840" /proc/$p/cmdline 2>/dev/null && kill -9 $p; done; sleep 0.2; up 10.60.0.2
for i in $(seq 200); do [ "$(grep -h 're-attached to the area by name' $D/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.02; done
T1=$(date +%s.%N); echo "re-attached: $(grep -h 're-attached to the area by name' $D/agent.*.log | wc -l) of 3, $(echo "($T1 - $T0) * 1000" | bc | cut -d. -f1) ms after the kill"
sleep 0.3; echo "== after the move (asked at 10.60.0.2)"; AF=$(RINA_RAM=10.60.0.2:8840 fdf-participants); echo "$AF" | grep -o '"hostname":"[^"]*","ips":\[[^]]*\],"alive":[a-z]*'
kill $TD 2>/dev/null; wait $TD 2>/dev/null
M2M=$(tcpdump -nn -r $D/wire.pcap 'src net 10.60.0.0/24 and dst net 10.60.0.0/24 and not host 10.60.0.1 and not host 10.60.0.2 and not host 10.60.0.254' 2>/dev/null | wc -l)
echo "wire: $(tcpdump -nn -r $D/wire.pcap 2>/dev/null | wc -l) packets; $M2M between two machines; DNS packets: $(tcpdump -nn -r $D/wire.pcap 'port 53' 2>/dev/null | wc -l)"
DNS=$(tcpdump -nn -r $D/wire.pcap 'port 53' 2>/dev/null | wc -l)
J=$(echo "$B4" | grep -c '"alive":true'); AL=$(echo "$AF" | grep -c '"alive":true'); RE=$(grep -h 're-attached to the area by name' $D/agent.*.log | wc -l)
[ "$J" = 3 ] && [ "$AL" = 3 ] && [ "$RE" -ge 3 ] && [ "$M2M" = 0 ] && [ "$DNS" = 0 ] && { echo "RESULT PASS"; exit 0; }; echo "RESULT FAIL (joined=$J alive_after=$AL re-attached=$RE m2m=$M2M dns=$DNS)"; exit 1
