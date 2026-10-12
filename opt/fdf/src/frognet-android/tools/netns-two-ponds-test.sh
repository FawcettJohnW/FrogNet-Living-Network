#!/bin/bash
# netns-two-ponds-test.sh -- [PONDS_V1] PLAN-PONDS §4 for the member side (the phone's logic, fdf-member-kt `ponds`):
#   pond A: rina-host + fdf-hub (hubA) + its fdf-client on "ha"; a leaf member mA (fdf-client) with its own tunnel
#   pond B: the same on "hb"; a leaf member mB, which also posts the notice (fdf-attention)
#   ph:     ONE process, one session per pond; Leaf on the forward pond only; one WireGuard interface that follows it
# Checks: forward A (A's /32 and hubA peer, participant row in B, no hubB peer); a notice in B reaches ph; forward B
# (stamped: hubA peer gone, hubB peer, handshake, ping mB; A's /32 freed); ten switches without leftovers.
# usage: bash netns-two-ponds-test.sh <bin dir: rina-host fdf-client fdf-events fdf-attention> <fdf-member-kt> <fdf/lib/python> <fdf-hub>
set -u
B=$1; K=$2; PY=$3; HUBP=$4; T=$(mktemp -d); API=/FawcettInnovations.RINA.ram_interface.php
ms() { date +%s%3N; }
pass=0; fail=0; check() { if eval "$2"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
NSS="ha hb mA mB ph"
cleanup() { for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill -9; ip netns del $n 2>/dev/null; done; ip link del br-2p 2>/dev/null; }
trap cleanup EXIT; cleanup
ip link add br-2p type bridge && ip link set br-2p up
i=1; for n in $NSS; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add u-$n type veth peer name u-$n-b; ip link set u-$n netns $n; ip link set u-$n-b master br-2p up
  ip netns exec $n ip addr add 192.168.80.$i/24 dev u-$n; ip netns exec $n ip link set u-$n up; i=$((i+1)); done
# ---- two ponds: ha = 192.168.80.1 (pondA), hb = 192.168.80.2 (pondB)
pond() { local n=$1 ip=$2 area=$3 port=$4
  ip netns exec $n env RINA_REGION_FILE=$T/$n.perm $B/rina-host --listen $ip:8840 --api $API > $T/$n-host.log 2>&1 &
  sleep 1
  ip netns exec $n env RINA_RAM=$ip:8840 FDF_WG_USERSPACE=1 FDF_BIN=$B FDF_PY=$PY python3 $HUBP --area $area --iface wg-$n --endpoint $ip:$port \
     --listen $port --key $T/$n.key --facts $T/$n-facts.json --name hub-$area > $T/$n-hub.log 2>&1 &
  for j in $(seq 100); do [ -s $T/$n-facts.json ] && break; sleep 0.1; done
  ip netns exec $n env RINA_RAM=$ip:8840 FDF_FACTS=$T/$n-facts.json $B/fdf-client > $T/$n-client.log 2>&1 &
  ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; }
pond ha 192.168.80.1 pondA 50001; pond hb 192.168.80.2 pondB 50002; sleep 1
hubkey() { python3 -c "import json;print(json.load(open('$T/$1-facts.json'))['pubkey'])"; }
wgif() { local n=$1 ifc=$2
  (umask 077; wg genkey > $T/$n.wgkey); wg pubkey < $T/$n.wgkey > $T/$n.wgpub
  ip netns exec $n wireguard-go $ifc >/dev/null 2>&1; for j in $(seq 100); do ip netns exec $n wg show $ifc >/dev/null 2>&1 && break; sleep 0.05; done
  ip netns exec $n wg set $ifc private-key $T/$n.wgkey; ip netns exec $n ip link set $ifc up; ip netns exec $n ip route add 10.0.0.0/8 dev $ifc; }
# ---- a leaf member in each pond (the laptop procedure: facts without addr; Address = what fdf-client printed)
leaf() { local n=$1 hub=$2 hip=$3 port=$4
  wgif $n wg-$n
  echo "{\"role\":\"leaf\",\"name\":\"$n\",\"guid\":\"g-$n\",\"pubkey\":\"$(cat $T/$n.wgpub)\"}" > $T/$n.json
  ip netns exec $n env RINA_RAM=$hip:8840 FDF_FACTS=$T/$n.json $B/fdf-client > $T/$n-client.log 2>&1 &
  for j in $(seq 100); do grep -q "gave this leaf" $T/$n-client.log && break; sleep 0.05; done
  local a=$(grep -m1 -o "gave this leaf [0-9./]*" $T/$n-client.log | awk '{print $4}')
  ip netns exec $n ip addr add $a dev wg-$n
  for j in $(seq 200); do grep -q "$(cat $T/$n.wgpub)" $T/$hub-facts.json && break; sleep 0.05; done   # [HUB_SAYS_ITS_PEERS_V1] then the peer
  ip netns exec $n wg set wg-$n peer $(hubkey $hub) endpoint $hip:$port allowed-ips 10.0.0.0/8 persistent-keepalive 25
  echo "$a"; }
MA=$(leaf mA ha 192.168.80.1 50001); MB=$(leaf mB hb 192.168.80.2 50002); MAIP=${MA%/32}; MBIP=${MB%/32}
echo "mA in pondA: $MA    mB in pondB: $MB"
# ---- the phone: one process, both ponds, forward A
wgif ph wg-ph
echo "{\"role\":\"leaf\",\"name\":\"phone\",\"guid\":\"phone-1\",\"pubkey\":\"$(cat $T/ph.wgpub)\"}" > $T/ph.json; PHPUB=$(cat $T/ph.wgpub)
echo pondA > $T/forward
ip netns exec ph env FDF_PONDS=pondA=192.168.80.1:8840,pondB=192.168.80.2:8840 FDF_FORWARD_FILE=$T/forward FDF_FACTS=$T/ph.json FDF_LEAF_WG=wg-ph \
   $K ponds 2>&1 | grep --line-buffered -v JAVA_TOOL | while IFS= read -r l; do echo "$(ms) $l"; done > $T/ph.log &
hubpeer() { ip netns exec $1 wg show wg-$1 peers 2>/dev/null | grep -q "$PHPUB"; }
for j in $(seq 300); do hubpeer ha && break; sleep 0.05; done
participants() { curl -s -X POST -d '{"op":"participants"}' "http://$1:8840$API?op=rina" --interface u-ph 2>/dev/null; }
role_in() { ip netns exec ph curl -s -X POST -d '{"op":"participants"}' "http://$1:8840$API?op=rina" | python3 -c "import json,sys; r=[p['facts'] for p in json.load(sys.stdin)['participants'] if p['ns']=='w:phone-1' and p['alive']]; print(r[0].get('role','?')+' '+r[0].get('addr','-') if r else 'absent')"; }
echo "=== (1) forward A"
for j in $(seq 300); do ip netns exec ph ping -c1 -W0.2 -q $MAIP >/dev/null 2>&1 && break; sleep 0.01; done
check "pondA: phone is a leaf with a /32 from A ($(role_in 192.168.80.1))" "role_in 192.168.80.1 | grep -q '^leaf 10.252'"
check "pondB: phone is a participant, no addr ($(role_in 192.168.80.2))" "[ \"\$(role_in 192.168.80.2)\" = 'participant -' ]"
check "hubA holds the phone's peer" "hubpeer ha"
check "hubB does not" "! hubpeer hb"
check "phone -> mA $MAIP through hubA" "ip netns exec ph ping -c2 -W1 -q $MAIP >/dev/null"
echo "=== (2) a notice in pond B"
t0=$(ms); ip netns exec mB env RINA_RAM=192.168.80.2:8840 FDF_FACTS=$T/mB.json $B/fdf-attention "match starting" > /dev/null
for j in $(seq 200); do grep -q "ATTENTION pondB: match starting" $T/ph.log && break; sleep 0.01; done; t1=$(grep -m1 "ATTENTION pondB" $T/ph.log | awk '{print $1}')
check "the phone heard it ($(( ${t1:-0} - t0 )) ms from fdf-attention's start)" "[ -n '$t1' ]"
echo "=== (3) bring pond B forward"
T0=$(ms); echo pondB > $T/forward.tmp && mv $T/forward.tmp $T/forward
for j in $(seq 300); do hubpeer ha || break; sleep 0.01; done; TA=$(ms)
for j in $(seq 300); do hubpeer hb && break; sleep 0.01; done; TB=$(ms)
for j in $(seq 500); do hs=$(ip netns exec ph wg show wg-ph latest-handshakes | awk '{print $2}'); [ "${hs:-0}" != 0 ] && ip netns exec ph wg show wg-ph endpoints | grep -q 192.168.80.2 && break; sleep 0.01; done; TH=$(ms)
for j in $(seq 300); do ip netns exec ph ping -c1 -W0.2 -q $MBIP >/dev/null 2>&1 && break; sleep 0.01; done; TP=$(ms)
echo "    forward written -> hubA dropped the phone: $((TA-T0)) ms; hubB holds it: $((TB-T0)) ms; handshake with hubB: $((TH-T0)) ms; first ping to mB answered: $((TP-T0)) ms"
grep "forward:" $T/ph.log | tail -1 | cut -c15- | sed 's/^/    /'
check "pondB: phone is a leaf with a /32 from B ($(role_in 192.168.80.2))" "role_in 192.168.80.2 | grep -q '^leaf 10.252'"
check "pondA: phone is a participant, its /32 freed ($(role_in 192.168.80.1))" "[ \"\$(role_in 192.168.80.1)\" = 'participant -' ]"
check "hubA no longer holds the phone" "! hubpeer ha"
check "phone -> mB $MBIP through hubB" "ip netns exec ph ping -c2 -W1 -q $MBIP >/dev/null"
# (mA and mB may hold the same /32: each pond gives its own; only one pond is forward, so the phone sees one of them)
check "the phone's tunnel peer is hubB only (gone is gone for A)" "[ \"\$(ip netns exec ph wg show wg-ph peers)\" = \"\$(hubkey hb)\" ]"
echo "=== (4) ten switches"
: > $T/sw; : > $T/swfail
for k in $(seq 10); do
  to=$([ $((k % 2)) = 1 ] && echo pondA || echo pondB); hub=$([ $to = pondA ] && echo ha || echo hb); other=$([ $to = pondA ] && echo hb || echo ha)
  dst=$([ $to = pondA ] && echo $MAIP || echo $MBIP); fip=$([ $to = pondA ] && echo 192.168.80.1 || echo 192.168.80.2)
  T0=$(ms); echo $to > $T/forward.tmp && mv $T/forward.tmp $T/forward
  ok=0; for j in $(seq 500); do ip netns exec ph wg show wg-ph endpoints | grep -q $fip && ip netns exec ph ping -c1 -W0.2 -q $dst >/dev/null 2>&1 && { ok=1; break; }; sleep 0.01; done; TP=$(ms)
  [ $ok = 1 ] && echo "$((TP-T0))" >> $T/sw || echo "  switch $k to $to: no answer from $dst within the loop" | tee -a $T/swfail
  hubpeer $other && echo "  leftover: $other still holds the phone after switch $k"
done
echo "    forward written -> first ping answered through the new hub, 10 switches: $(sort -n $T/sw | tr '\n' ' ')ms"
check "every one of the ten switches answered" "[ ! -s $T/swfail ]"
check "no leftover peers after ten switches (only the forward hub holds the phone)" "hubpeer hb && ! hubpeer ha"
check "one session per pond throughout (2 joins)" "[ \$(grep -c 'joined the region' $T/ph.log) = 2 ]"
echo "RESULT $([ $fail = 0 ] && echo PASS || echo FAIL) ($pass passed, $fail failed) $T"
