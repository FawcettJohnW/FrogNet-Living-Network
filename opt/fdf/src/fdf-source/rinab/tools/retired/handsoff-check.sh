#!/bin/bash
# handsoff-check.sh -- [MR_FROM_THE_AREA_NAME_V1] completely hands off: a machine is given ONLY the area's name.
# Where the area's mapping system is comes from that name (DNS SRV _lisp-mr._tcp.<area>, published once when the area
# was created); where the RAM manager is comes from the mapping (the RAM manager registers itself). Three machines join,
# announce, and follow the RAM manager when it moves (10.60.0.1 -> 10.60.0.2) -- configured with nothing else.
# PASS only if: no machine has RINA_MAP_RESOLVER or RINA_RAM; all three join and are listed alive; all three re-attach
# after the move and are listed alive at the new address; no packet between two machines.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php; K=fdf-area-key; A=rina-area.test
PROBE=${RINA_CAPABILITY_PROBE:-/tmp/frognet-src/usr/local/bin/frognet_capability_probe.sh}
D=$(mktemp -d /tmp/handsoff-check.XXXX); echo "diag dir: $D"; export PATH=$B:$PATH
C() { ip netns exec core env -u RINA_MAP_RESOLVER -u RINA_RAM "$@"; }
cleanup() { for i in 1 2 3; do ip netns exec dm$i env RINA_AREA=$A rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.3; kill ${TD:-} 2>/dev/null
            for n in dm1 dm2 dm3 core; do for p in $(ip netns pids $n 2>/dev/null); do kill -9 $p; done; ip netns delete $n 2>/dev/null; rm -rf /etc/netns/$n; done
            ip link delete dmbr 2>/dev/null; }
cleanup; trap cleanup EXIT
ip link add dmbr type bridge; ip addr add 10.60.0.254/24 dev dmbr; ip link set dmbr up
for n in core dm1 dm2 dm3; do ip netns add $n; ip netns exec $n ip link set lo up; mkdir -p /etc/netns/$n; echo "nameserver 10.60.0.1" > /etc/netns/$n/resolv.conf
    ip link add v$n type veth peer name b$n; ip link set b$n master dmbr; ip link set b$n up; ip link set v$n netns $n; ip netns exec $n ip link set v$n up; done
ip netns exec core ip addr add 10.60.0.1/24 dev vcore; ip netns exec core ip addr add 10.60.0.2/24 dev vcore
for i in 1 2 3; do ip netns exec dm$i ip addr add 10.60.0.1$i/24 dev vdm$i; done
echo "PUBLISHED ONCE, when the area was created: DNS _lisp-mr._tcp.$A SRV -> mr.$A, mr.$A A 10.60.0.1"
C dnsmasq --no-resolv --no-hosts --listen-address=10.60.0.1 --bind-interfaces --srv-host=_lisp-mr._tcp.$A,mr.$A,4342,10,100 --host-record=mr.$A,10.60.0.1 --pid-file=$D/dns.pid
tcpdump -i dmbr -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
C env RINA_HOST_ROLE=mapping RINA_REGION_FILE=$D/map.perm rina-host --listen 10.60.0.1:4342 --api $API > $D/mapping.log 2>&1 &
for i in $(seq 100); do C bash -c "(exec 3<>/dev/tcp/10.60.0.1/4342)" 2>/dev/null && break; sleep 0.05; done
C rina-lisp map-site $A $K 10.60.0.1 > /dev/null || { echo "no site"; exit 1; }
echo "CONFIGURED: the RAM manager: RINA_MAP_AREA=$A RINA_MAP_KEY=<the site's key>. Each machine: RINA_AREA=$A. NOTHING ELSE (no RINA_MAP_RESOLVER, no RINA_RAM)"
up() { C env RINA_MAP_AREA=$A RINA_MAP_KEY=$K RINA_REGION_FILE=$D/area.perm RINA_RAM_NETNS=/proc/self/ns/net rina-host --listen $1:8840 --api $API > $D/area-$1.log 2>&1 &
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
echo "wire: $(tcpdump -nn -r $D/wire.pcap 2>/dev/null | wc -l) packets; $M2M between two machines; DNS queries: $(tcpdump -nn -r $D/wire.pcap 'udp port 53' 2>/dev/null | grep -c SRV)"
J=$(echo "$B4" | grep -c '"alive":true'); AL=$(echo "$AF" | grep -c '"alive":true'); RE=$(grep -h 're-attached to the area by name' $D/agent.*.log | wc -l)
[ "$J" = 3 ] && [ "$AL" = 3 ] && [ "$RE" -ge 3 ] && [ "$M2M" = 0 ] && { echo "RESULT PASS"; exit 0; }; echo "RESULT FAIL (joined=$J alive_after=$AL re-attached=$RE m2m=$M2M)"; exit 1
