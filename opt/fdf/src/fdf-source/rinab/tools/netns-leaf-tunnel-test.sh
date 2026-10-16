#!/bin/bash
# netns-leaf-tunnel-test.sh -- [FDF_MEMBER_KT_V1] TRANSITION-ANDROID §6's first experiment, in namespaces, with the real
# programs: rina-host + fdf-hub (wireguard-go, the userspace WireGuard that wireguard-android's GoBackend embeds) + the
# hub's own fdf-client on "hub"; the Kotlin member as a leaf on "kt", taking its WireGuard peer from the hub's facts in
# the region; fdf-client (C++) as a second leaf on "cpp". Every stage stamped in ms.
# usage: bash netns-leaf-tunnel-test.sh <dir with rina-host, fdf-client, fdf-events> <fdf-member-kt launcher> <fdf/lib/python> <fdf-hub>
set -u
B=$1; K=$2; PY=$3; HUBP=$4; T=$(mktemp -d); API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret
ms() { date +%s%3N; }
stamp() { while IFS= read -r l; do echo "$(ms) $l"; done; }
pass=0; fail=0; check() { if eval "$2"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
cleanup() { for n in hub kt cpp; do ip netns pids $n 2>/dev/null | xargs -r kill -9; ip netns del $n 2>/dev/null; done; ip link del br-l 2>/dev/null; }
trap cleanup EXIT; cleanup
ip link add br-l type bridge && ip link set br-l up
i=1; for n in hub kt cpp; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-l up
  ip netns exec $n ip addr add 192.168.77.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+1)); done
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=192.168.77.1:8840 RINA_AREA=$SITE "$@"; }
# ---- the hub
NS hub env RINA_REGION_FILE=$T/area.perm $B/rina-host --listen 192.168.77.1:8840 --api $API > $T/host.log 2>&1 &
sleep 1; NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "http://192.168.77.1:8840$API?op=rina" > /dev/null
NS hub env FDF_WG_IMPL=wireguard-go FDF_BIN=$B FDF_PY=$PY python3 $HUBP --area $SITE --iface wg-frognet --endpoint 192.168.77.1:50889 \
   --listen 50889 --key $T/hub.key --facts $T/hub-facts.json --name hubA 2>&1 | stamp > $T/hub.log &
for j in $(seq 100); do [ -s $T/hub-facts.json ] && break; sleep 0.1; done
NS hub env FDF_FACTS=$T/hub-facts.json $B/fdf-client > $T/hub-client.log 2>&1 &
sleep 1
HUBPUB=$(python3 -c "import json;print(json.load(open('$T/hub-facts.json'))['pubkey'])")
echo "hub: $(grep -m1 'hub hubA' $T/hub.log | cut -c15-)"
# ---- a leaf's WireGuard interface (wireguard-go), its key, its /32; the peer is set by the member (kt) or by hand (cpp)
leafwg() { local n=$1 wg0=wg-$1      # one name per namespace: wireguard-go's control socket /var/run/wireguard/<iface>.sock is shared
  (umask 077; wg genkey > $T/$n.key); wg pubkey < $T/$n.key > $T/$n.pub
  ip netns exec $n wireguard-go $wg0 >/dev/null 2>&1
  for j in $(seq 100); do ip netns exec $n wg show $wg0 >/dev/null 2>&1 && break; sleep 0.05; done   # wireguard-go makes it asynchronously
  ip netns exec $n wg set $wg0 private-key $T/$n.key
  ip netns exec $n ip link set $wg0 up; ip netns exec $n ip route add 10.0.0.0/8 dev $wg0; }   # no address: the region gives it
leafwg cpp; leafwg kt
echo "{\"role\":\"leaf\",\"name\":\"cpp-leaf\",\"guid\":\"cpp-leaf-1\",\"pubkey\":\"$(cat $T/cpp.pub)\"}" > $T/cpp.json
echo "{\"role\":\"leaf\",\"name\":\"kt-leaf\",\"guid\":\"kt-leaf-1\",\"pubkey\":\"$(cat $T/kt.pub)\"}" > $T/kt.json
ip netns exec cpp wg set wg-cpp peer $HUBPUB endpoint 192.168.77.1:50889 allowed-ips 10.0.0.0/8 persistent-keepalive 25
NS cpp env FDF_FACTS=$T/cpp.json $B/fdf-client > $T/cpp-client.log 2>&1 &
for j in $(seq 100); do grep -q "the region gave this leaf" $T/cpp-client.log && break; sleep 0.05; done
CPPA=$(grep -m1 -o "gave this leaf [0-9./]*" $T/cpp-client.log | awk '{print $4}'); CPPIP=${CPPA%/32}
echo "cpp leaf: $(grep -m1 'gave this leaf' $T/cpp-client.log)"
ip netns exec cpp ip addr add $CPPA dev wg-cpp      # what the laptop user does with the address the program printed
sleep 1
# ---- the Kotlin leaf: joins, reads the hub from the region, sets its own peer
T0=$(ms)
NS kt env FDF_FACTS=$T/kt.json FDF_LEAF_WG=wg-kt $K 2>&1 | grep --line-buffered -v JAVA_TOOL | stamp > $T/kt.log &
for j in $(seq 200); do grep -q "tunnel: wg-kt peer" $T/kt.log && break; sleep 0.05; done
T_JOIN=$(grep -m1 "joined the region" $T/kt.log | awk '{print $1}'); T_TUN=$(grep -m1 "tunnel: wg-kt peer" $T/kt.log | awk '{print $1}')
KTPUB=$(cat $T/kt.pub); KTA=$(grep -m1 -o "gave this leaf [0-9./]*" $T/kt.log | awk '{print $4}'); KTIP=${KTA%/32}
echo "kt leaf: $(grep -m1 'gave this leaf' $T/kt.log | cut -c15-)"
for j in $(seq 200); do ip netns exec hub wg show wg-frognet allowed-ips 2>/dev/null | grep -q "^$KTPUB" && break; sleep 0.05; done; T_PEER=$(ms)
for j in $(seq 400); do hs=$(ip netns exec kt wg show wg-kt latest-handshakes | awk '{print $2}'); [ "${hs:-0}" != 0 ] && break; ip netns exec kt ping -c1 -W0.2 10.253.253.254 >/dev/null 2>&1; done; T_HS=$(ms)
echo "--- the Kotlin leaf, stamped from its start (JVM start included)"
echo "    joined the region                 +$((T_JOIN-T0)) ms"
echo "    tunnel peer set from hub facts    +$((T_TUN-T0)) ms   ($(grep -m1 'tunnel: wg-kt' $T/kt.log | cut -c15- | cut -c1-140))"
echo "    hub has its peer (wg show)        +$((T_PEER-T0)) ms"
echo "    first WireGuard handshake         +$((T_HS-T0)) ms"
echo "--- checks"
check "the two leaves were given different /32s ($CPPA, $KTA)" "[ -n '$CPPA' ] && [ -n '$KTA' ] && [ '$CPPA' != '$KTA' ]"
check "hub peer for kt leaf: allowed-ips $KTA" "ip netns exec hub wg show wg-frognet allowed-ips | grep -q '^$KTPUB.$KTA$'"
check "hub route $KTIP dev wg-frognet (on-link)" "ip netns exec hub ip route show dev wg-frognet | grep -q '^$KTIP '"
check "kt -> hub 10.253.253.254" "ip netns exec kt ping -c3 -W1 -q 10.253.253.254 >/dev/null"
check "hub -> kt $KTIP" "ip netns exec hub ping -c3 -W1 -q $KTIP >/dev/null"
check "kt -> cpp leaf $CPPIP (leaf to leaf through the hub)" "ip netns exec kt ping -c3 -W1 -q $CPPIP >/dev/null"
check "cpp leaf -> kt $KTIP" "ip netns exec cpp ping -c3 -W1 -q $KTIP >/dev/null"
echo "    ping kt -> cpp: $(ip netns exec kt ping -c20 -i0.05 -q $CPPIP | tail -1)"
# ---- the Kotlin leaf leaves (process killed): the hub deletes its peer (no memory)
KP=$(ip netns pids kt | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = java ] && echo $p; done | head -1)
T_K=$(ms); kill -9 $KP
for j in $(seq 200); do ip netns exec hub wg show wg-frognet allowed-ips | grep -q "^$KTPUB" || break; sleep 0.02; done; T_DEL=$(ms)
echo "    kt killed -> hub peer deleted     $((T_DEL-T_K)) ms   ($(grep 'deleted peer' $T/hub.log | tail -1 | cut -c15-))"
check "hub has no peer for kt after it left" "! ip netns exec hub wg show wg-frognet allowed-ips | grep -q '^$KTPUB'"
check "kt cannot reach cpp now (gone is gone)" "! ip netns exec kt ping -c2 -W1 -q $CPPIP >/dev/null"
BODY=$(python3 -c 'import json; print(json.dumps({"op":"announce","ns":"w:typed","name":"typed","ips":[],"cap":"{}","facts":json.dumps({"role":"leaf","addr":"10.252.0.50/32","pubkey":"X"})}))')
R=$(NS hub curl -s -X POST -d "$BODY" "http://192.168.77.1:8840$API?op=rina")
echo "    a leaf that names its own addr: $R"
echo "$R" > $T/typed.out; check "a leaf naming its own addr is refused (EINVAL)" "grep -q EINVAL $T/typed.out"
echo "--- hub log"; sed 's/^[0-9]* /    /' $T/hub.log | grep -v "^    $" | tail -12
echo "--- kt log"; sed 's/^[0-9]* /    /' $T/kt.log
echo "RESULT $([ $fail = 0 ] && echo PASS || echo FAIL) ($pass passed, $fail failed) $T"
