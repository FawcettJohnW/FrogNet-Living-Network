#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-enroll-test.sh -- [ENROLL_V1] the common way a device joins a region with an app, end to end on real pieces:
# the region's hub and RAM, fdf-identity answering where the hub is, an app (fdf-app config + serve) with a user who
# leads a team, the app's door (/dc/ram.php under PHP), the Java Enroll library a phone runs (EnrollDrive), and the
# Kotlin member joining with what enrollment gave it.
#   A  198.18.0.1   the hub (region D), its RAM 10.253.253.254:8840, fdf-identity --serve, fdf-client + fdf-app serve
#                   (rangertrak: team echo; kim leads it; EOCAdmin), the door at 198.18.0.1:8088
#   q1 198.18.0.3   kim's phone: scans the admin's QR (fdfenroll1 <door> rangertrak kim), types kim's password
#   q2 198.18.0.5   ann's phone: scans kim's QR (fdfenroll1 http://198.18.0.3:21801/enroll rangertrak ann)
# Checks:
#   1  a wrong password at the door is refused, and said
#   2  kim enrolls at the door: role echo, lead, an fdfinv1 invitation; the region admitted rangertrak-kim
#   3  kim's phone joins the region with it (its own key, the hub's tunnel)
#   4  kim's phone sponsors ann: a wrong password there is refused (and the door is not asked)
#   5  ann enrolls at kim's phone: role echo, not a lead, the door in the card; the region admitted rangertrak-echo-ann
#   6  ann's phone joins the region with it: a second member, its own key and edge, no login of its own
#   7  EOCAdmin (no team to lead) cannot ask the door for invitations
# Needs root, ip, wg, wireguard-go, python3, php, curl, java, the built fdf-member-kt, fdf-tun and frognet-ram-java.
#   usage: tools/netns-enroll-test.sh [RINAB_DIR]
set -u
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "${1:-}" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-enroll}; rm -rf $T; mkdir -p $T/bin
AND=$H/../../frognet-android; KT=${KT_MEMBER:-$AND/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt}; FT=${FDF_TUN:-$AND/tun/out/fdf-tun-$(uname -m)}
RJ=$H/../../frognet-ram-java
[ -x "$KT" ] && [ -x "$FT" ] || { echo "FAIL  setup: $KT or $FT missing (build member-cli: gradle -Pfdf.jvmOnly=true :member-cli:installDist)"; exit 1; }
"$RJ/build.sh" > /dev/null && mkdir -p $T/ej && JAVA_TOOL_OPTIONS= javac -cp $RJ/build/frognet-ram.jar -d $T/ej $RJ/test/EnrollDrive.java || { echo "FAIL  setup: frognet-ram-java"; exit 1; }
EJ() { local n=$1; shift; ip netns exec $n env JAVA_TOOL_OPTIONS= java -cp $RJ/build/frognet-ram.jar:$T/ej EnrollDrive "$@"; }
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="A q1 q2"; mkdir -p $T/q1 $T/q2
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in $NODES; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for n in $NODES; do ip link del v-$n-b 2>/dev/null; done; ip link del br-enr 2>/dev/null; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/fdf-where $B/rina-host $B/ram $T/bin/
cp $PK/bin/fdf-hub $PK/bin/fdf-identity $PK/bin/fdf-invite $PK/bin/fdf-app $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH FDF_PY=$FDFPY
ip link add br-enr type bridge && ip link set br-enr up || { echo "FAIL  setup: br-enr exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-enr up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
mkdir -p /run/wireguard
# ---- A: the hub, its RAM, fdf-identity where the hub is
n=A; mkdir -p $T/$n/admitted $T/$n/run-wg $T/$n/apps; PUB=198.18.0.1
KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env
NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/rina-host --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
echo $! >> $T/svc.pids
for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done
NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
NS $n sysctl -q -w net.ipv4.ip_forward=1
svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
svc $T/A/identity.log ip netns exec A sh -c "mount --bind $T/A/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_HUB_ENV=$T/A/hub.env FDF_ADMITTED=$T/A/admitted FDF_SITE_KEYS=$T/A FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-identity --serve"
for i in $(seq 100); do grep -q "answering identity asks" $T/A/identity.log && break; sleep 0.1; done
# ---- A: the app, as a node has it (its own fdf-client; fdf-app config, then serve)
AE() { NS A env RINA_RAM=$RAM FDF_FACTS=$T/A/node-facts.json FDF_VAR=$T/A FDF_APPS=$T/A/apps FDF_BIN=$T/bin FDF_PY=$FDFPY FDF_NAME=Seattle7 "$@"; }
printf '{"name":"Seattle7","guid":"guid-a","role":"node","subnet":"10.170.170.0/24","addrs":[{"ip":"198.18.0.1","kind":"own"}],"capability":{"mem_total_kb":2000000,"cores":4}}\n' > $T/A/node-facts.json
( AE $T/bin/fdf-client >> $T/A/client.log 2>&1 ) & echo $! >> $T/svc.pids
sleep 1.5
AE python3 $T/bin/fdf-app config rangertrak general,echo --role eoc --role echo:default --user kim:kim-2026=echo+lead --user EOCAdmin:eoc-pw=eoc \
   --admin boss:adm-2026 > $T/A/config.log 2>&1 || { echo "FAIL  setup: fdf-app config: $(tail -2 $T/A/config.log)"; exit 1; }
( AE python3 $T/bin/fdf-app serve >> $T/A/app.log 2>&1 ) & echo $! >> $T/svc.pids
# ---- A: the door
mkdir -p $T/door/www/dc; cp $PK/www/dc/ram.php $T/door/www/dc/; printf '{"apps": []}\n' > $T/door/www/dc/apps.json
printf 'export RINA_RAM=%s FDF_BIN=%s FDF_VAR=%s FDF_PY=%s\n' $RAM $T/bin $T/A $FDFPY > $T/door/fdf.env
NS A env PHP_CLI_SERVER_WORKERS=8 php -S 198.18.0.1:8088 -t $T/door/www >> $T/door.log 2>&1 & echo $! >> $T/svc.pids
DOOR=198.18.0.1:8088
for i in $(seq 100); do NS A curl -s -m 2 "http://$DOOR/dc/ram.php?op=roles&app=rangertrak" | grep -q '"ok":true' && break; sleep 0.2; done
# ---- the phones: each its own WireGuard interface (wireguard-go: not per namespace, so a name of its own)
leafwg() { NS $1 wireguard-go fdf-$1 >/dev/null 2>&1; for j in $(seq 100); do NS $1 wg show fdf-$1 >/dev/null 2>&1 && break; sleep 0.05; done; }
KTL() { local n=$1; shift; mkdir -p $T/$n; [ -f $T/$n/facts.json ] || printf '{"name":"%s","guid":"g-%s"}\n' $n $n > $T/$n/facts.json
        ip netns exec $n env FDF_TUN_BIN=$FT FDF_TUN_NAME=fdft-$n FDF_KEY_FILE=$T/$n/wg.key FDF_FACTS=$T/$n/facts.json FDF_DEVICE_ID=dev-$n \
          FDF_LEAF_WG=fdf-$n FDF_ASSERT_SEC=5 FDF_INVITE=$T/$n/invite "$KT" "$@"; }
J() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(sys.argv[1], ''))" "$1"; }
# 1 a wrong password at the door
L_kim="fdfenroll1 http://$DOOR/dc/ram.php rangertrak kim"
EJ q1 enroll "$L_kim" wrong > $T/q1-bad.json 2>&1; BAD=$?
check $([ $BAD = 3 ] && grep -q "not this app's" $T/q1-bad.json && echo 0 || echo 1) "1 a wrong password at the door is refused (exit $BAD): $(cut -c1-120 $T/q1-bad.json)"
# 2 kim enrolls at the door
EJ q1 enroll "$L_kim" kim-2026 > $T/q1.json 2>$T/q1.err
INV1=$(J invite < $T/q1.json 2>/dev/null)
check $([ "$(J role < $T/q1.json)" = echo ] && [ "$(J lead < $T/q1.json)" = True ] && [ "${INV1#fdfinv1.}" != "$INV1" ] && [ -f $T/A/admitted/rangertrak-kim.json ] && echo 0 || echo 1) \
  "2 kim enrolls at the door: role $(J role < $T/q1.json 2>/dev/null), lead $(J lead < $T/q1.json 2>/dev/null), invitation ${INV1:0:24}...; admitted: $(ls $T/A/admitted | tr '\n' ' ') $(cut -c1-160 $T/q1.err)"
# 3 kim's phone joins with it
leafwg q1; ( KTL q1 leaf "$INV1" >> $T/q1/member.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q1/member.log 2>/dev/null && break; sleep 0.25; done
check $(grep -q "joined the region at" $T/q1/member.log && NS q1 wg show fdf-q1 | grep -q "endpoint: 198.18.0.1:51820" && echo 0 || echo 1) \
  "3 kim's phone joined the region with its own invitation: $(grep -o 'joined the region at [^,]*' $T/q1/member.log | head -1)"
# 4 kim's phone sponsors ann
( EJ q1 sponsor 21801 rangertrak echo $DOOR kim kim-2026 ann:ann-2026 >> $T/q1/sponsor.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 100); do grep -q "enrolling rangertrak echo's members" $T/q1/sponsor.log && break; sleep 0.1; done
L_ann="fdfenroll1 http://198.18.0.3:21801/enroll rangertrak ann"
EJ q2 enroll "$L_ann" nope > $T/q2-bad.json 2>&1; BAD2=$?
check $([ $BAD2 = 3 ] && grep -q "not a member of echo" $T/q2-bad.json && [ ! -f $T/A/admitted/rangertrak-echo-ann.json ] && echo 0 || echo 1) \
  "4 a wrong password at kim's phone is refused (exit $BAD2), the door not asked: $(cut -c1-110 $T/q2-bad.json)"
# 5 ann enrolls at kim's phone
EJ q2 enroll "$L_ann" ann-2026 > $T/q2.json 2>$T/q2.err
INV2=$(J invite < $T/q2.json 2>/dev/null)
check $([ "$(J role < $T/q2.json)" = echo ] && [ "$(J lead < $T/q2.json)" = False ] && [ "$(J door < $T/q2.json)" = $DOOR ] && [ "${INV2#fdfinv1.}" != "$INV2" ] && [ -f $T/A/admitted/rangertrak-echo-ann.json ] && echo 0 || echo 1) \
  "5 ann enrolls at kim's phone: role $(J role < $T/q2.json 2>/dev/null), lead $(J lead < $T/q2.json 2>/dev/null), door $(J door < $T/q2.json 2>/dev/null), invitation ${INV2:0:24}...; admitted: $(ls $T/A/admitted | tr '\n' ' ') $(cut -c1-160 $T/q2.err)"
# 6 ann's phone joins with it
leafwg q2; ( KTL q2 leaf "$INV2" >> $T/q2/member.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q2/member.log 2>/dev/null && break; sleep 0.25; done
E1=$(python3 -c "import json; print(json.load(open('$T/A/admitted/rangertrak-kim.json'))['edge'])" 2>/dev/null)
E2=$(python3 -c "import json; print(json.load(open('$T/A/admitted/rangertrak-echo-ann.json'))['edge'])" 2>/dev/null)
check $(grep -q "joined the region at" $T/q2/member.log && NS q2 wg show fdf-q2 | grep -q "endpoint: 198.18.0.1:51820" && [ -n "$E2" ] && [ "$E1" != "$E2" ] && echo 0 || echo 1) \
  "6 ann's phone joined the region with its own invitation (edge $E2; kim's $E1): $(grep -o 'joined the region at [^,]*' $T/q2/member.log | head -1)"
# 7 a user who leads no team
R7=$(NS q1 curl -s -m 30 -X POST -d '{"user":"EOCAdmin","password":"eoc-pw","name":"x"}' "http://$DOOR/dc/ram.php?op=identity&app=rangertrak")
check $(echo "$R7" | grep -q "does not lead a team" && [ ! -f $T/A/admitted/rangertrak-eoc-x.json ] && echo 0 || echo 1) "7 EOCAdmin cannot ask for invitations: $R7"
echo "--- logs"; grep -h "ENROLL_V1\|identity for\|refused" $T/A/identity.log $T/q1/sponsor.log $T/door.log 2>/dev/null | cut -c1-200 | sed 's/^/    /' | tail -12
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
