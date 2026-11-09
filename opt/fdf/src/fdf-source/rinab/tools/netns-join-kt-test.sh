#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-join-kt-test.sh -- [JOIN_LINE_V1] the Windows and Android member's login with an organization's join line, on the
# JVM: the Kotlin member (fdf-member-kt join) on the bridge (fdf-tun, one TUN, each RAM manager an island aliased from
# 100.64.0.0/10, the generic credential's island for the door), the same code the Windows bridge and the Android app run.
# Two RAM hosts A and B, two spaces (Responders, Admin), level HQ with a password joining both.
#   q 198.18.0.5   the device: fdf-member-kt join <HQ's line> Responders Admin
# Checks:
#   1  a wrong password is refused, and said
#   2  q logs in at A and B through the bridge: its edge at each, RAM managers reached through their aliases
#   3  q's application in Responders and Admin, on A; its truth in each space on A
#   4  Admin's RAM manager on A killed: q's Admin on B within milliseconds, its Responders still on A; fdf-org starts it
#      again on A: q's Admin back on A, the better host by capability [SPACE_BY_CAPABILITY_V1]
# Needs root, ip, wg, wireguard-go, python3, curl, nft, java, the built fdf-member-kt and fdf-tun.
#   usage: tools/netns-join-kt-test.sh [RINAB_DIR]   KT_MEMBER=<fdf-member-kt launcher> FDF_TUN=<fdf-tun-x86_64>
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-spaces-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-joinkt}; rm -rf $T; mkdir -p $T/bin
AND=$H/../../frognet-android; KT=${KT_MEMBER:-$AND/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt}; FT=${FDF_TUN:-$AND/tun/out/fdf-tun-$(uname -m)}
[ -x "$KT" ] && [ -x "$FT" ] || { echo "FAIL  setup: $KT or $FT missing (build member-cli: gradle -Pfdf.jvmOnly=true :member-cli:installDist)"; exit 1; }
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
NODES="A B q"
            for n in $NODES; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for n in $NODES; do ip link del v-$n-b 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/fdf-ram $T/bin/ 2>/dev/null; [ -x $T/bin/fdf-ram ] || cp $H/build/bin/fdf-ram-static $T/bin/fdf-ram
cp $PK/bin/fdf-hub $PK/bin/fdf-org $PK/bin/fdf-login $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH FDF_PY=$FDFPY
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
mkdir -p /run/wireguard
hostup() { local n=$1; NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/fdf-ram --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
           for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done; }
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_BIN=$T/bin python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
for n in A B; do mkdir -p $T/$n/admitted $T/$n/run-wg; PUB=$(NS $n ip -4 -o addr show dev v-$n | awk '{print $4}' | cut -d/ -f1)
  KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
  printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env; echo "FDF_FACTS=$T/$n/facts.json" >> $T/$n/hub.env   # [HUB_SAYS_ITS_PEERS_V1] the door answers once the hub holds the peer
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n
  NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  ORG $n init > $T/$n/org.out 2>&1; ORG $n space Responders >> $T/$n/org.out 2>&1; ORG $n space Admin >> $T/$n/org.out 2>&1

  ORG $n level HQ --password --reach ram --space Responders --space Admin >> $T/$n/org.out 2>&1; echo chiefpw | ORG $n user HQ chief >> $T/$n/org.out 2>&1
  printf '{"name":"host%s","guid":"guid-%s","capability":{"mem_total_kb":%d,"cores":4}}\n' $n $n $([ $n = A ] && echo 8000000 || echo 4000000) > $T/$n/node-facts.json   # [SPACE_BY_CAPABILITY_V1] A is the better
  svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_FACTS=$T/$n/node-facts.json FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-org serve"
done
for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 3 ] && grep -q "SPACE Admin: its RAM manager on" $T/$n/org.log && grep -q "SPACE Responders: its RAM manager on" $T/$n/org.log && break; sleep 0.1; done; done
sport() { ORG $1 list 2>/dev/null | awk -v s="$2" '$1=="space" && $2==s {n=split($NF,a,":"); print a[n]}'; }
PA_ADMIN=$(sport A Admin); PA_RESP=$(sport A Responders); PB_ADMIN=$(sport B Admin)
check $([ -n "$PA_ADMIN" ] && [ -n "$PA_RESP" ] && [ -n "$PB_ADMIN" ] && echo 0 || echo 1) "0 each RAM host runs each space's RAM manager: A Responders :$PA_RESP Admin :$PA_ADMIN; B Responders :$(sport B Responders) Admin :$PB_ADMIN"
LINE() { local lb; lb=$(ORG B line $1 2>>$T/B/line.err); ORG A line $1 --with $lb 2>>$T/A/line.err; }
L_q=$(LINE HQ)
mkdir -p $T/q; NS q ip route add default via 198.18.0.254 dev v-q 2>/dev/null
KTQ() { ip netns exec q env FDF_TUN_BIN=$FT FDF_TUN_NAME=fdft0 FDF_KEY_FILE=$T/q/wg.key FDF_FACTS=$T/q/facts.json FDF_DEVICE_ID=dev-q "$KT" "$@"; }
printf '{"name":"q","guid":"g-q"}\n' > $T/q/facts.json
# 1 a wrong password
echo "chief wrong" | KTQ join "$L_q" Responders Admin > $T/q/bad.log 2>&1; BAD=$?
check $([ $BAD = 3 ] && grep -q "wrong user or password" $T/q/bad.log && echo 0 || echo 1) "1 a wrong password refused (exit $BAD): $(grep -o 'LOGIN FAILED: .*' $T/q/bad.log | cut -c1-120)"
# 2 the login, then the applications
( echo "chief chiefpw" | KTQ join "$L_q" Responders Admin >> $T/q/app.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined: " $T/q/app.log && break; grep -q "LOGIN FAILED" $T/q/app.log && break; sleep 0.25; done
check $(grep -q "joined: hubA edge .* RAM 100\.[0-9.]*:[0-9]*; hubB edge .* RAM 100\.[0-9.]*:[0-9]*" $T/q/app.log && echo 0 || echo 1) "2 q logged in at both through the bridge: $(grep -o 'joined: .*' $T/q/app.log | cut -c1-200)"
check $(grep -q "door at 198.18.0.1:51820: I am d-" $T/q/app.log && grep -q "door at 198.18.0.3:51820: I am d-" $T/q/app.log && echo 0 || echo 1) "2 each door on the generic credential's island: $(grep -o 'door at [^ ]* I am [^,]*, edge [0-9.]*' $T/q/app.log | tr '\n' ';')"
on() { grep "CHANGE $1 " $T/q/app.log 2>/dev/null | tail -1 | awk '{print $NF}'; }
waiton() { local want=$1 sp=$2 t0=$(date +%s.%N); for i in $(seq 160); do [ "$(on $sp)" = "$want" ] && break; sleep 0.25; done
  echo "$([ "$(on $sp)" = "$want" ] && echo 1 || echo 0) $(python3 -c "print('%.1f' % ($(date +%s.%N) - $t0))")"; }
# a read's answer has a cell whose bag has KEY = VALUE (parsed: a bag's text is kept as its writer wrote it)
baghas() { python3 -c 'import json,sys; c=json.load(sys.stdin).get("cells") or []; sys.exit(0 if any((x.get("bag") or {}).get(sys.argv[1])==sys.argv[2] for x in c) else 1)' "$1" "$2" 2>/dev/null; }
cellin() { NS $1 curl -s -m 3 "http://$HA:$2$API?op=read&service=app&variable=pos&instance=q" | baghas space "$3" && echo yes || echo no; }
read O1 S1 <<< "$(waiton hubA Responders)"; read O2 S2 <<< "$(waiton hubA Admin)"
check $([ $O1 = 1 ] && [ $O2 = 1 ] && [ "$(cellin A $PA_RESP Responders)" = yes ] && [ "$(cellin A $PA_ADMIN Admin)" = yes ] && echo 0 || echo 1) "3 q's application in Responders and Admin on A, its truth in each ($S1 s, $S2 s)"
# 4 Admin's RAM manager on A killed; fdf-org starts it again at once, on the listening socket it keeps
# ([LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]): q is told the memory it read is gone (ESTALE), moves to B, writes its truth
# there, and is back on A as soon as A's capability is written again -- the move to B is looked for, not the state now
T0=$(date +%s%3N); for p in $(ip netns pids A); do tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q "fdf-ram --listen $HA:$PA_ADMIN" && kill $p; done
for i in $(seq 160); do grep -q "CHANGE Admin hubA -> hubB" $T/q/app.log && break; sleep 0.25; done
OK=$(grep -q "CHANGE Admin hubA -> hubB" $T/q/app.log && echo 1 || echo 0)
MS=$(grep "CHANGE Admin hubA -> hubB" $T/q/app.log | tail -1 | awk '{print $1" "$2}'); MS=$(python3 -c "import sys,datetime as d; t=d.datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M:%S.%f').timestamp()*1000; print(int(t-int(sys.argv[2])))" "$MS" $T0 2>/dev/null)
check $([ $OK = 1 ] && [ "$(on Responders)" = hubA ] && [ "$(cellin B $PB_ADMIN Admin)" = yes ] && echo 0 || echo 1) "4 Admin's RAM manager on A killed: q's Admin on B ${MS} ms after the kill, its truth written there; Responders still on $(on Responders)"
for i in $(seq 80); do [ "$(grep -c "SPACE Admin: its RAM manager on" $T/A/org.log)" -ge 2 ] && break; sleep 0.25; done
read OK S <<< "$(waiton hubA Admin)"
check $([ $OK = 1 ] && [ "$(cellin A $PA_ADMIN Admin)" = yes ] && echo 0 || echo 1) "4 fdf-org started Admin's RAM manager again on A: q's Admin back on A ($S s), its truth there"
echo "--- q"; grep -E "CHANGE|SPACE|bridge:|logged in|door at" $T/q/app.log | cut -c1-200 | sed 's/^/    /' | tail -20
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
