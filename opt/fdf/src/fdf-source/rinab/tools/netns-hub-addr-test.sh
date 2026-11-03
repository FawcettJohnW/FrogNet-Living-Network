#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-hub-addr-test.sh -- [HUB_ADDRESS_FROM_ITS_HOST_V1] (John 2026-11-02: "Why isn't the hub using the IP address of
# its host?" "Use RAM."; he chose 10.253.A.B) every hub was 10.253.253.254, so a device holding two regions' tunnels
# had two routes to one address. Two regions:
#   A 198.18.0.1   a FrogNet node serving 10.170.170.0/24 (its LAN, lan0, a veth): its hub is 10.253.170.170
#   B 198.18.0.3   a machine serving no FrogNet LAN (as StreamingFrog): its hub keeps 10.253.253.254
#   q 198.18.0.5   the device: logged in at both with one line
# Checks, each with what it saw:
#   1  fdf_regions.own_hub_addr on A gives 10.253.170.170 (from lan0), on B 10.253.253.254 (no LAN); the pool of
#      edges on A never gives 10.253.170.170
#   2  A's hub interface carries exactly 10.253.170.170/32; its fdf-org listens there; its hub row says so (addr)
#   3  q holds both tunnels at once: one route to each hub address, each through its own tunnel
#   4  both sessions answer at the same time: A's RAM through one tunnel, B's through the other
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-hub-addr-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-hub-addr}; rm -rf $T; mkdir -p $T/bin $T/q
API=/FawcettInnovations.RINA.ram_interface.php; HAA=10.253.170.170; HAB=10.253.253.254
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="A B q"
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in $NODES; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in $NODES; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for n in $NODES; do ip link del v-$n-b 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/rina-host $T/bin/ 2>/dev/null; [ -x $T/bin/rina-host ] || cp $H/build/bin/rina-host-static $T/bin/rina-host
cp $PK/bin/fdf-hub $PK/bin/fdf-org $PK/bin/fdf-login $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH FDF_PY=$FDFPY
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
mkdir -p /run/wireguard
hostup() { local n=$1 ram=$2; NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/rina-host --listen $ram --api $API >> $T/$n/host.log 2>&1 &
           for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$ram$API?op=rina" > /dev/null && break; sleep 0.05; done; }
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
# ---- A serves a FrogNet LAN (10.170.170.1 on lan0, a dummy); B serves none
mkdir -p $T/A $T/B
NS A ip link add lan0 type veth peer name lan0p; NS A ip addr add 10.170.170.1/24 dev lan0; NS A ip link set lan0 up; NS A ip link set lan0p up
OWN() { NS $1 env FDF_HUB_ROOT=$T/$1/hubroot python3 -c "import sys; sys.path.insert(0, '$FDFPY'); import fdf_regions as r; a, w = r.own_hub_addr(); print(a); print(w, file=sys.stderr)"; }
GA=$(OWN A 2>$T/A/own.why); GB=$(OWN B 2>$T/B/own.why)
# ---- two RAM hosts, each its region; each hub at the address own_hub_addr gave (as fdf-region-create writes it)
for n in A B; do mkdir -p $T/$n/admitted $T/$n/run-wg; PUB=$(NS $n ip -4 -o addr show dev v-$n | awk '{print $4}' | cut -d/ -f1)
  HADDR=$([ $n = A ] && echo $GA || echo $GB); RAM=$HADDR:8840
  KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
  printf 'RINA_RAM=%s\nFDF_HUB_ADDR=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $HADDR $n $PUB > $T/$n/hub.env
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ADDR=$HADDR FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n $RAM
  NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_HUB_ADDR=$HADDR FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  ORG $n init > $T/$n/org.out 2>&1; ORG $n level Responders --reach ram >> $T/$n/org.out 2>&1
  svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-org serve"
done
for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 2 ] && break; sleep 0.1; done; done
# ---- 1 the derivation, and the pool
mkdir -p $T/A/hubroot/D; cp $T/A/hub.env $T/A/hubroot/D/hub.env
POOL=$(NS A env FDF_HUB_ROOT=$T/A/hubroot python3 -c "
import sys; sys.path.insert(0, '$FDFPY'); import fdf_regions as r
taken = {'10.253.%d.%d' % (x, y) for x in range(1, 171) for y in range(1, 255)} - {'10.253.170.170'}
print(r.free_edge(taken))")
check $([ "$GA" = 10.253.170.170 ] && [ "$GB" = 10.253.253.254 ] && [ "$POOL" != 10.253.170.170 ] && echo 0 || echo 1) \
      "1 A's hub address $GA ($(cat $T/A/own.why)); B's $GB ($(cat $T/B/own.why)); with every edge below it taken, A's pool gives $POOL, not the hub's"
# ---- 2 A's hub interface, its fdf-org, its row
AIF=$(NS A ip -o -4 addr show dev wg-D | awk '{print $4}' | tr '\n' ' ')
ROW=$(NS A python3 -c "
import sys; sys.path.insert(0, '$FDFPY'); import fdfram
print([c['bag'].get('addr') for c in fdfram.Ram('$GA:8840').read('fdf-org', 'hub')][-1])")
check $([ "$AIF" = "10.253.170.170/32 " ] && grep -q "listening on 10.253.170.170:" $T/A/org.log && [ "$ROW" = 10.253.170.170 ] && echo 0 || echo 1) \
      "2 A's wg-D carries $AIF; fdf-org: $(grep -o 'listening on [0-9.:]*' $T/A/org.log | head -1); its hub row says addr $ROW"
# ---- the line: A then B; q logs in at both
LB=$(ORG B line Responders 2>$T/B/line.err); LINE=$(ORG A line Responders --with $LB 2>$T/A/line.err)
NS q ip route add default via 198.18.0.254 dev v-q 2>/dev/null
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
mkdir -p $T/q/run-wg
DEV q python3 -u $T/bin/fdf-login "$LINE" < /dev/null > $T/q/login.log 2>&1; RC=$?
# ---- 3 both tunnels at once, one route to each hub
RA=$(NS q ip route show $GA/32 | tr '\n' ' '); RB=$(NS q ip route show $GB/32 | tr '\n' ' ')
check $([ $RC = 0 ] && grep -q "logged in at 2 of 2" $T/q/login.log && echo "$RA" | grep -q "dev wg-own0" && echo "$RB" | grep -q "dev wg-own1" && echo 0 || echo 1) \
      "3 q logged in at both (exit $RC): $GA via [$RA], $GB via [$RB]"
# ---- 4 both sessions at the same time
SA=$(python3 -c "import json;m=json.load(open('$T/q/login.json'))['managers'];print([v['ram'] for v in m.values() if v['order']==0][0])" 2>/dev/null)
SB=$(python3 -c "import json;m=json.load(open('$T/q/login.json'))['managers'];print([v['ram'] for v in m.values() if v['order']==1][0])" 2>/dev/null)
ver() { NS q curl -s -m 4 -X POST -d '{"op":"version"}' "http://$1$API?op=rina" | grep -q '"ok":true' && echo ok || echo no; }
( ver $SA > $T/q/va ) & P1=$!; ( ver $SB > $T/q/vb ) & P2=$!; wait $P1 $P2
check $([ "$(cat $T/q/va)" = ok ] && [ "$(cat $T/q/vb)" = ok ] && [ "${SA%:*}" = $GA ] && [ "${SB%:*}" = $GB ] && echo 0 || echo 1) \
      "4 at the same time: A's session $SA $(cat $T/q/va), B's $SB $(cat $T/q/vb)"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
