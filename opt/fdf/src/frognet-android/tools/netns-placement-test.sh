#!/bin/bash
# netns-placement-test.sh -- [ADDRESS_IS_THE_ONE_IT_HAS_V1] the member uses the address it already has.
#   hub ns:  rina-host + fdf-hub (wireguard-go) + the hub's fdf-client
#   bb ns:   a node, fdf-client, facts {role node, subnet 10.101.10.0/24, own 10.101.10.1}; LAN bridge br-lan
#   ph ns:   the Kotlin member, holding 10.101.10.170 on bb's LAN (a lease, as a phone on BlackBox's wifi)
# (A) on the LAN: placement OnMesh, no tunnel, no hub peer, facts carry lan{ip,subnet,served_by} and no addr/pubkey;
#     bb reaches it on its own /24.
# (B) BlackBox leaves the region, same session: the next p re-places the phone OffMesh, tunnel up, ping the hub.
# (C) BlackBox returns: OnMesh again, tunnel down, hub drops the peer. One session throughout.
# usage: bash netns-placement-test.sh <bin dir> <fdf-member-kt> <fdf/lib/python> <fdf-hub>
set -u
B=$1; K=$2; PY=$3; HUBP=$4; T=$(mktemp -d); API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; U="http://192.168.79.1:8840$API?op=rina"
ms() { date +%s%3N; }
pass=0; fail=0; check() { if eval "$2"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
cleanup() { for n in hub bb ph; do ip netns pids $n 2>/dev/null | xargs -r kill -9; ip netns del $n 2>/dev/null; done; ip link del br-u 2>/dev/null; ip link del br-lan 2>/dev/null; }
trap cleanup EXIT; cleanup
ip link add br-u type bridge && ip link set br-u up; ip link add br-lan type bridge && ip link set br-lan up
i=1; for n in hub bb ph; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add u-$n type veth peer name u-$n-b; ip link set u-$n netns $n; ip link set u-$n-b master br-u up
  ip netns exec $n ip addr add 192.168.79.$i/24 dev u-$n; ip netns exec $n ip link set u-$n up; i=$((i+1)); done
for n in bb ph; do ip link add l-$n type veth peer name l-$n-b; ip link set l-$n netns $n; ip link set l-$n-b master br-lan up; ip netns exec $n ip link set l-$n up; done
ip netns exec bb ip addr add 10.101.10.1/24 dev l-bb; ip netns exec ph ip addr add 10.101.10.170/24 dev l-ph
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=192.168.79.1:8840 RINA_AREA=$SITE "$@"; }
NS hub env RINA_REGION_FILE=$T/area.perm $B/rina-host --listen 192.168.79.1:8840 --api $API > $T/host.log 2>&1 &
sleep 1; NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"s3cret\"}" "$U" > /dev/null
NS hub env FDF_WG_USERSPACE=1 FDF_BIN=$B FDF_PY=$PY python3 $HUBP --area $SITE --iface wg-frognet --endpoint 192.168.79.1:50889 \
   --listen 50889 --key $T/hub.key --facts $T/hub-facts.json --name hubA > $T/hub.log 2>&1 &
for j in $(seq 100); do [ -s $T/hub-facts.json ] && break; sleep 0.1; done
NS hub env FDF_FACTS=$T/hub-facts.json $B/fdf-client > $T/hub-client.log 2>&1 &
echo '{"role":"node","name":"BlackBox","guid":"bb-1","subnet":"10.101.10.0/24","addrs":[{"iface":"l-bb","ip":"10.101.10.1","value":"FrogNetHost.BlackBox","kind":"own","mac":""}]}' > $T/bb.json
NS bb env FDF_FACTS=$T/bb.json $B/fdf-client > $T/bb.log 2>&1 &
sleep 1
(umask 077; wg genkey > $T/ph.key); wg pubkey < $T/ph.key > $T/ph.pub; PHPUB=$(cat $T/ph.pub)
ip netns exec ph wireguard-go wg-ph >/dev/null 2>&1; for j in $(seq 100); do ip netns exec ph wg show wg-ph >/dev/null 2>&1 && break; sleep 0.05; done
ip netns exec ph wg set wg-ph private-key $T/ph.key; ip netns exec ph ip link set wg-ph up; ip netns exec ph ip route add 10.0.0.0/8 dev wg-ph
echo "{\"role\":\"leaf\",\"name\":\"phone\",\"guid\":\"phone-1\",\"pubkey\":\"$PHPUB\"}" > $T/ph.json
participant() { curl -s -X POST -d '{"op":"participants"}' "$U" --interface u-hub 2>/dev/null; }
echo "=== (A) on BlackBox's LAN, holding 10.101.10.170"
NS ph env FDF_FACTS=$T/ph.json FDF_LEAF_WG=wg-ph $K > $T/ph-a.log 2>&1 &
for j in $(seq 100); do grep -q "joined the region" $T/ph-a.log && break; sleep 0.1; done; sleep 1
grep -h "placement\|joined\|tunnel" $T/ph-a.log | grep -v JAVA_TOOL | cut -c25- | sed 's/^/    /'
F=$(NS hub curl -s -X POST -d '{"op":"participants"}' "$U" | python3 -c "import json,sys; print([json.dumps(p['facts']) for p in json.load(sys.stdin)['participants'] if p['ns']=='w:phone-1' and p['alive']][0])")
echo "    phone's facts in the region: $F"
check "placement on the mesh, served by BlackBox" "grep -q 'placement: on the mesh: 10.101.10.170 is in 10.101.10.0/24, served by BlackBox' $T/ph-a.log"
check "no tunnel step taken" "! grep -q 'tunnel: wg-ph peer' $T/ph-a.log"
check "facts: lan{} present, no addr, no pubkey" "echo '$F' | python3 -c \"import json,sys; f=json.load(sys.stdin); sys.exit(0 if f.get('lan',{}).get('ip')=='10.101.10.170' and 'addr' not in f and 'pubkey' not in f else 1)\""
check "hub has no peer for the phone" "! ip netns exec hub wg show wg-frognet peers | grep -q '$PHPUB'"
check "BlackBox reaches the phone on its own /24" "ip netns exec bb ping -c2 -W1 -q 10.101.10.170 >/dev/null"
echo "=== (B) BlackBox leaves the region; the phone's session goes on: the next p re-places it"
BBP=$(ip netns pids bb | while read p; do [ "$(cat /proc/$p/comm)" = fdf-client ] && echo $p; done | head -1)
T0=$(ms); kill -9 $BBP
for j in $(seq 200); do ip netns exec hub wg show wg-frognet peers | grep -q "$PHPUB" && break; sleep 0.02; done; T1=$(ms)
for j in $(seq 100); do grep -q "tunnel: wg-ph peer hubA" $T/ph-a.log && break; sleep 0.05; done
grep -h "placement\|tunnel\|re-announced" $T/ph-a.log | grep -v JAVA_TOOL | cut -c25- | cut -c1-150 | sed 's/^/    /'
echo "    BlackBox killed -> hub has the phone's peer: $((T1-T0)) ms"
check "placement off the mesh, same session" "grep -q 'placement: off the mesh' $T/ph-a.log"
check "facts re-announced" "grep -q 'facts changed, re-announced' $T/ph-a.log"
check "tunnel peer set from the hub's facts" "grep -q 'tunnel: wg-ph peer hubA' $T/ph-a.log"
PHA=$(grep -o "gave this leaf [0-9./]*" $T/ph-a.log | tail -1 | awk '{print $4}')
check "the region gave the phone a /32 ($PHA)" "[ -n '$PHA' ]"
check "hub peer for the phone: $PHA" "ip netns exec hub wg show wg-frognet allowed-ips | grep -q '^$PHPUB.$PHA$'"
for j in $(seq 300); do hs=$(ip netns exec ph wg show wg-ph latest-handshakes | awk '{print $2}'); [ "${hs:-0}" != 0 ] && break; sleep 0.05; done; T2=$(ms)
echo "    BlackBox killed -> first handshake: $((T2-T0)) ms (tunnel raised on the event that shows the hub holds its key, [HUB_SAYS_ITS_PEERS_V1])"
check "phone -> hub 10.253.253.254 through the tunnel" "ip netns exec ph ping -c3 -W1 -q 10.253.253.254 >/dev/null"
echo "    route: $(ip netns exec ph ip route get 10.253.253.254 | head -1); ph handshake: $(ip netns exec ph wg show wg-ph latest-handshakes); hub routes: $(ip netns exec hub ip route show dev wg-frognet | tr '\n' ';')"
echo "=== (C) BlackBox comes back: the phone is on its LAN again"
T0=$(ms); NS bb env FDF_FACTS=$T/bb.json $B/fdf-client > $T/bb2.log 2>&1 &
for j in $(seq 300); do ip netns exec hub wg show wg-frognet peers | grep -q "$PHPUB" || break; sleep 0.02; done; T1=$(ms)
grep -h "placement\|tunnel" $T/ph-a.log | grep -v JAVA_TOOL | tail -2 | cut -c25- | cut -c1-150 | sed 's/^/    /'
echo "    BlackBox started -> hub has dropped the phone's peer: $((T1-T0)) ms (BlackBox's fdf-client start included)"
check "placement on the mesh again" "[ \$(grep -c 'placement: on the mesh' $T/ph-a.log) -ge 2 ]"
check "tunnel taken down" "grep -q 'removed (on the mesh now)' $T/ph-a.log"
check "hub has no peer for the phone" "! ip netns exec hub wg show wg-frognet peers | grep -q '$PHPUB'"
check "BlackBox reaches the phone on its /24" "ip netns exec bb ping -c2 -W1 -q 10.101.10.170 >/dev/null"
check "the region freed it: the phone's facts carry no addr" "NS hub curl -s -X POST -d '{\"op\":\"participants\"}' $U | python3 -c \"import json,sys; f=[p['facts'] for p in json.load(sys.stdin)['participants'] if p['ns']=='w:phone-1' and p['alive']][0]; sys.exit(0 if 'addr' not in f and 'lan' in f else 1)\""
check "the region said so to the phone" "grep -q 'the region holds no leaf address for this session' $T/ph-a.log"
check "one session throughout" "[ \$(grep -c 'joined the region' $T/ph-a.log) = 1 ]"
echo "RESULT $([ $fail = 0 ] && echo PASS || echo FAIL) ($pass passed, $fail failed) $T"
