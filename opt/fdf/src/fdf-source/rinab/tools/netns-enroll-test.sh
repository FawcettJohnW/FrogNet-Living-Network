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
#                   (openeoc: team echo; kim leads it; EOCAdmin), the door at 198.18.0.1:8088
#   q1 198.18.0.3   kim's phone: scans the admin's QR (fdfenroll1 <door> openeoc kim), types kim's password
#   q2 198.18.0.5   ann's phone: scans kim's QR (fdfenroll1 http://198.18.0.3:21801/enroll openeoc ann FP: kim's
#                   sponsor key's fingerprint, [SEALED_ENROLL_V1]; what goes between the phones is sealed to that key)
# Checks:
#   1  a wrong password at the door is refused, and said
#   2  kim enrolls at the door: role echo, lead, an fdfinv1 invitation; the region admitted openeoc-kim
#   3  kim's phone joins the region with it (its own key, the hub's tunnel)
#   4  kim's phone sponsors ann: a wrong password there is refused (and the door is not asked)
#   5  ann enrolls at kim's phone: role echo, not a lead, the door in the card; the region admitted openeoc-echo-ann
#   6  ann's phone joins the region with it: a second member, its own key and edge, no login of its own
#   7  EOCAdmin (no team to lead) cannot ask the door for invitations
#   9  [GROUP_PLACE_V1] kim (echo's lead) tells the door where echo lives, here on guid-a: the door writes the place,
#      fdf-app serve adopts it and echo is served there; EOCAdmin (no group to lead) and a place that is neither here
#      nor databasehost are refused
#  10  [CHILDREN_JOIN_V1] a team that does not exist yet joins the EOC by its QR (fdfenroll1 <door> openeoc *), named
#      by whoever joins it ("Red Cross"): a wrong teams password is refused; the EOC's teams password (fdf-app config
#      --join) makes it an entity: its lead, an invitation, (fdf-app, child, openeoc.Red Cross), listed in op=roles
#  11  the new team's lead logs in with the team's name and the teams password (role: the team); it cannot be placed at
#      databasehost (it lives on its lead's phone)
#  12  members join the new team by its own QR (fdfenroll1 <lead's phone> openeoc *) and its team password, each
#      giving its own call sign: a wrong team password is refused; dan is admitted as openeoc-red_cross-dan
#   8  the region does not answer: the door says so (where it asked, how long) before the phone gives up
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
cp $B/fdf-events $B/fdf-client $B/fdf-where $B/fdf-ram $B/ram $T/bin/
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
NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/fdf-ram --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
echo $! >> $T/svc.pids
for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done
NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
NS $n sysctl -q -w net.ipv4.ip_forward=1
svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
svc $T/A/identity.log ip netns exec A sh -c "mount --bind $T/A/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_HUB_ENV=$T/A/hub.env FDF_ADMITTED=$T/A/admitted FDF_SITE_KEYS=$T/A FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-identity --serve"
for i in $(seq 100); do grep -q "answering identity asks" $T/A/identity.log && break; sleep 0.1; done
# ---- A: the app, as a node has it (its own fdf-client; fdf-app config, then serve)
# [APP_SERVICES_V1] A is the one FrogNetHost: its RAM host (198.18.0.1:8841) is databasehost-control and the SystemRam,
# and its election writes the AppHost lines (tools/app_elector.py: the real core/app_hosts.py, as the daemon runs it)
NS A env RINA_REGION_FILE=$T/A/lan.perm RINA_NET_CONFIG=$T/A/none $T/bin/fdf-ram --listen 198.18.0.1:8841 --api $API >> $T/A/lan.log 2>&1 &
echo $! >> $T/svc.pids
for i in $(seq 100); do NS A curl -s -m 1 -X POST -d '{"op":"version"}' "http://198.18.0.1:8841$API?op=rina" > /dev/null && break; sleep 0.05; done
printf '127.0.0.1 localhost\n198.18.0.1 FrogNetHost.Seattle7\n198.18.0.1 databasehost_control.frognet\n198.18.0.1 databasehost.frognet\n' > $T/A/block
( NS A env FDF_LAN_RAM_PORT=8841 FDF_PY=$FDFPY python3 -u $H/tools/app_elector.py $T/A/hosts $T/A/block $T/A/apps >> $T/A/elector.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 100); do [ -s $T/A/hosts ] && break; sleep 0.05; done
AE() { NS A env RINA_RAM=$RAM FDF_HOSTS=$T/A/hosts FDF_LAN_RAM_PORT=8841 FDF_FACTS=$T/A/node-facts.json FDF_VAR=$T/A FDF_APPS=$T/A/apps FDF_BIN=$T/bin FDF_PY=$FDFPY FDF_NAME=Seattle7 "$@"; }
printf '{"name":"Seattle7","guid":"guid-a","role":"node","subnet":"10.170.170.0/24","addrs":[{"ip":"198.18.0.1","kind":"own"}],"capability":{"mem_total_kb":2000000,"cores":4}}\n' > $T/A/node-facts.json
( AE $T/bin/fdf-client >> $T/A/client.log 2>&1 ) & echo $! >> $T/svc.pids
sleep 1.5
AE python3 $T/bin/fdf-app config openeoc general,echo --role eoc --role echo:default --user kim:kim-2026=echo+lead --user EOCAdmin:eoc-pw=eoc \
   --admin boss:adm-2026 --join teams-2026 > $T/A/config.log 2>&1 || { echo "FAIL  setup: fdf-app config: $(tail -2 $T/A/config.log)"; exit 1; }
( AE python3 $T/bin/fdf-app serve >> $T/A/app.log 2>&1 ) & echo $! >> $T/svc.pids
# ---- A: the door
mkdir -p $T/door/www/dc; cp $PK/www/dc/ram.php $T/door/www/dc/; printf '{"apps": []}\n' > $T/door/www/dc/apps.json
printf 'export RINA_RAM=%s FDF_HOSTS=%s FDF_LAN_RAM_PORT=8841 FDF_BIN=%s FDF_VAR=%s FDF_PY=%s\n' $RAM $T/A/hosts $T/bin $T/A $FDFPY > $T/door/fdf.env
NS A env PHP_CLI_SERVER_WORKERS=8 php -S 198.18.0.1:8088 -t $T/door/www >> $T/door.log 2>&1 & echo $! >> $T/svc.pids
DOOR=198.18.0.1:8088
for i in $(seq 100); do NS A curl -s -m 2 "http://$DOOR/dc/ram.php?op=roles&app=openeoc" | grep -q '"ok":true' && break; sleep 0.2; done
# ---- the phones: each its own WireGuard interface (wireguard-go: not per namespace, so a name of its own)
leafwg() { NS $1 wireguard-go fdf-$1 >/dev/null 2>&1; for j in $(seq 100); do NS $1 wg show fdf-$1 >/dev/null 2>&1 && break; sleep 0.05; done; }
KTL() { local n=$1; shift; mkdir -p $T/$n; [ -f $T/$n/facts.json ] || printf '{"name":"%s","guid":"g-%s"}\n' $n $n > $T/$n/facts.json
        ip netns exec $n env FDF_TUN_BIN=$FT FDF_TUN_NAME=fdft-$n FDF_KEY_FILE=$T/$n/wg.key FDF_FACTS=$T/$n/facts.json FDF_DEVICE_ID=dev-$n \
          FDF_LEAF_WG=fdf-$n FDF_ASSERT_SEC=5 FDF_INVITE=$T/$n/invite "$KT" "$@"; }
J() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(sys.argv[1], ''))" "$1"; }
# 1 a wrong password at the door
L_kim="fdfenroll1 http://$DOOR/dc/ram.php openeoc kim"
EJ q1 enroll "$L_kim" wrong > $T/q1-bad.json 2>&1; BAD=$?
check $([ $BAD = 3 ] && grep -q "not this app's" $T/q1-bad.json && echo 0 || echo 1) "1 a wrong password at the door is refused (exit $BAD): $(cut -c1-120 $T/q1-bad.json)"
# 2 kim enrolls at the door
EJ q1 enroll "$L_kim" kim-2026 > $T/q1.json 2>$T/q1.err
INV1=$(J invite < $T/q1.json 2>/dev/null)
check $([ "$(J role < $T/q1.json)" = echo ] && [ "$(J lead < $T/q1.json)" = True ] && [ "${INV1#fdfinv1.}" != "$INV1" ] && [ -f $T/A/admitted/openeoc-kim.json ] && echo 0 || echo 1) \
  "2 kim enrolls at the door: role $(J role < $T/q1.json 2>/dev/null), lead $(J lead < $T/q1.json 2>/dev/null), invitation ${INV1:0:24}...; admitted: $(ls $T/A/admitted | tr '\n' ' ') $(cut -c1-160 $T/q1.err)"
# 3 kim's phone joins with it
leafwg q1; ( KTL q1 leaf "$INV1" >> $T/q1/member.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q1/member.log 2>/dev/null && break; sleep 0.25; done
check $(grep -q "joined the region at" $T/q1/member.log && NS q1 wg show fdf-q1 | grep -q "endpoint: 198.18.0.1:51820" && echo 0 || echo 1) \
  "3 kim's phone joined the region with its own invitation: $(grep -o 'joined the region at [^,]*' $T/q1/member.log | head -1)"
# 4 kim's phone sponsors ann
( EJ q1 sponsor 21801 openeoc echo $DOOR kim kim-2026 ann:ann-2026 >> $T/q1/sponsor.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 100); do grep -q "enrolling openeoc echo's members" $T/q1/sponsor.log && break; sleep 0.1; done
KEY_kim=$(grep -o "its QR's key [^ ]*" $T/q1/sponsor.log | head -1 | cut -d' ' -f4)   # [SEALED_ENROLL_V1] what kim's QR carries: its key's fingerprint
L_ann="fdfenroll1 http://198.18.0.3:21801/enroll openeoc ann $KEY_kim"
EJ q2 enroll "$L_ann" nope > $T/q2-bad.json 2>&1; BAD2=$?
check $([ $BAD2 = 3 ] && grep -q "not a member of echo" $T/q2-bad.json && [ ! -f $T/A/admitted/openeoc-echo-ann.json ] && echo 0 || echo 1) \
  "4 a wrong password at kim's phone is refused (exit $BAD2), the door not asked: $(cut -c1-110 $T/q2-bad.json)"
# 5 ann enrolls at kim's phone
EJ q2 enroll "$L_ann" ann-2026 > $T/q2.json 2>$T/q2.err
INV2=$(J invite < $T/q2.json 2>/dev/null)
check $([ "$(J role < $T/q2.json)" = echo ] && [ "$(J lead < $T/q2.json)" = False ] && [ "$(J at < $T/q2.json)" = $DOOR ] && [ "${INV2#fdfinv1.}" != "$INV2" ] && [ -f $T/A/admitted/openeoc-echo-ann.json ] && echo 0 || echo 1) \
  "5 ann enrolls at kim's phone: role $(J role < $T/q2.json 2>/dev/null), lead $(J lead < $T/q2.json 2>/dev/null), at $(J at < $T/q2.json 2>/dev/null), invitation ${INV2:0:24}...; admitted: $(ls $T/A/admitted | tr '\n' ' ') $(cut -c1-160 $T/q2.err)"
# 6 ann's phone joins with it
leafwg q2; ( KTL q2 leaf "$INV2" >> $T/q2/member.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q2/member.log 2>/dev/null && break; sleep 0.25; done
E1=$(python3 -c "import json; print(json.load(open('$T/A/admitted/openeoc-kim.json'))['edge'])" 2>/dev/null)
E2=$(python3 -c "import json; print(json.load(open('$T/A/admitted/openeoc-echo-ann.json'))['edge'])" 2>/dev/null)
check $(grep -q "joined the region at" $T/q2/member.log && NS q2 wg show fdf-q2 | grep -q "endpoint: 198.18.0.1:51820" && [ -n "$E2" ] && [ "$E1" != "$E2" ] && echo 0 || echo 1) \
  "6 ann's phone joined the region with its own invitation (edge $E2; kim's $E1): $(grep -o 'joined the region at [^,]*' $T/q2/member.log | head -1)"
# 7 a user who leads no team
R7=$(NS q1 curl -s -m 30 -X POST -d '{"user":"EOCAdmin","password":"eoc-pw","name":"x"}' "http://$DOOR/dc/ram.php?op=identity&app=openeoc")
check $(echo "$R7" | grep -q "does not lead a team" && [ ! -f $T/A/admitted/openeoc-eoc-x.json ] && echo 0 || echo 1) "7 EOCAdmin cannot ask for invitations: $R7"
# 9 the lead places its group
P0=$(NS q1 curl -s -m 10 -X POST -d '{"user":"EOCAdmin","password":"eoc-pw","at":"databasehost"}' "http://$DOOR/dc/ram.php?op=place&app=openeoc")
P1=$(NS q1 curl -s -m 10 -X POST -d '{"user":"kim","password":"kim-2026","at":"moon"}' "http://$DOOR/dc/ram.php?op=place&app=openeoc")
P2=$(NS q1 curl -s -m 10 -X POST -d '{"user":"kim","password":"kim-2026","at":"here","guid":"guid-a"}' "http://$DOOR/dc/ram.php?op=place&app=openeoc")
for i in $(seq 60); do grep -q "Team echo placed here on guid-a by its lead kim (adopted)" $T/A/app.log && grep -q "^198.18.0.1 echoHost.openeoc" $T/A/hosts && break; sleep 0.25; done
check $(echo "$P0" | grep -q "does not lead a group" && echo "$P1" | grep -q '"ok":false' && echo "$P2" | grep -q '"ok":true' \
        && grep -q "Team echo placed here on guid-a by its lead kim (adopted)" $T/A/app.log && grep -q '"places": {"echo"' $T/A/apps/openeoc.json \
        && grep -q "^198.18.0.1 echoHost.openeoc" $T/A/hosts && echo 0 || echo 1) \
  "9 kim placed echo here on guid-a: $(echo "$P2" | cut -c1-110); adopted: $(grep -o 'Team echo placed.*' $T/A/app.log | head -1); $(grep echoHost $T/A/hosts); EOCAdmin: $(echo "$P0" | grep -o 'does not lead a group'); at moon: $(echo "$P1" | grep -o '"why":"[^"]*' | cut -c1-60)"
# 10 [CHILDREN_JOIN_V1] a team the EOC did not have joins it, named by its joiner
L_eoc="fdfenroll1 http://$DOOR/dc/ram.php openeoc *"
EJ q1 enroll "$L_eoc" wrong "Red Cross" > $T/rc-bad.json 2>&1; BAD10=$?
EJ q1 enroll "$L_eoc" teams-2026 "Red Cross" > $T/rc.json 2>$T/rc.err
INV10=$(J invite < $T/rc.json 2>/dev/null)
R10=$(NS q1 curl -s -m 10 "http://$DOOR/dc/ram.php?op=roles&app=openeoc")
K10=$(echo "$R10" | python3 -c "import sys,json; r=json.load(sys.stdin)['roles'].get('Red Cross') or {}; print('%s %s %s' % (r.get('child'), r.get('category'), r.get('kind')))" 2>/dev/null)
check $([ $BAD10 = 3 ] && grep -q "not the EOC's teams password" $T/rc-bad.json && [ "$(J lead < $T/rc.json)" = True ] && [ "$(J child < $T/rc.json)" = True ] \
        && [ "$(J role < $T/rc.json)" = "Red Cross" ] && [ "${INV10#fdfinv1.}" != "$INV10" ] && [ -f $T/A/admitted/openeoc-red_cross.json ] && [ "$K10" = "True Teams team" ] && echo 0 || echo 1) \
  "10 Red Cross joined the EOC by its QR and the teams password: role $(J role < $T/rc.json 2>/dev/null), lead $(J lead < $T/rc.json 2>/dev/null), invitation ${INV10:0:24}...; op=roles: $K10; wrong password: $(grep -o "not the EOC's teams password" $T/rc-bad.json) $(cut -c1-160 $T/rc.err)"
# 11 its lead logs in as the team; not placed at databasehost
L11=$(NS q1 curl -s -m 10 -X POST -d '{"user":"Red Cross","password":"teams-2026"}' "http://$DOOR/dc/ram.php?op=login&app=openeoc")
L11b=$(NS q1 curl -s -m 10 -X POST -d '{"user":"Red Cross","password":"wrong"}' "http://$DOOR/dc/ram.php?op=login&app=openeoc")
P11=$(NS q1 curl -s -m 10 -X POST -d '{"user":"Red Cross","password":"teams-2026","at":"databasehost"}' "http://$DOOR/dc/ram.php?op=place&app=openeoc")
check $(echo "$L11" | grep -q '"role":"Red Cross"' && echo "$L11b" | grep -q '"ok":false' && echo "$P11" | grep -q "lives on its lead's phone" && echo 0 || echo 1) \
  "11 Red Cross's lead logs in: $(echo "$L11" | cut -c1-80); wrong: $(echo "$L11b" | cut -c1-60); at databasehost: $(echo "$P11" | grep -o '"why":"[^"]*' | cut -c1-90)"
# 12 members join Red Cross by its team QR and team password, each with its own call sign
( EJ q1 sponsor 21802 openeoc "Red Cross" $DOOR "Red Cross" teams-2026 '*:rc-team' >> $T/q1/sponsor-rc.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 100); do grep -q "enrolling openeoc" $T/q1/sponsor-rc.log && break; sleep 0.1; done
KEY_rc=$(grep -o "its QR's key [^ ]*" $T/q1/sponsor-rc.log | head -1 | cut -d' ' -f4)
L_rc="fdfenroll1 http://198.18.0.3:21802/enroll openeoc * $KEY_rc"
EJ q2 enroll "$L_rc" nope dan > $T/dan-bad.json 2>&1; BAD12=$?
EJ q2 enroll "$L_rc" rc-team dan > $T/dan.json 2>$T/dan.err
INV12=$(J invite < $T/dan.json 2>/dev/null)
check $([ $BAD12 = 3 ] && [ "$(J role < $T/dan.json)" = "Red Cross" ] && [ "$(J lead < $T/dan.json)" = False ] && [ "${INV12#fdfinv1.}" != "$INV12" ] \
        && [ -f $T/A/admitted/openeoc-red_cross-dan.json ] && echo 0 || echo 1) \
  "12 dan joined Red Cross by its QR and team password: role $(J role < $T/dan.json 2>/dev/null), lead $(J lead < $T/dan.json 2>/dev/null), invitation ${INV12:0:24}...; wrong: $(cut -c1-90 $T/dan-bad.json); admitted: $(ls $T/A/admitted | tr '\n' ' ') $(cut -c1-160 $T/dan.err)"
# 8 [IDENTITY_ASK_IS_SAID_V1][IDENTITY_WAIT_IS_SAID_V1] the region does not answer (its fdf-identity stopped; seen on
#   Seattle7 2026-11-02: the phone sat 60 s and said "timeout"): the door says so, in less time than the phone waits
for p in $(pgrep -f "fdf-identity --serve"); do kill $(ps -o ppid= -p $p) $p 2>/dev/null; done; sleep 0.5
T8=$(date +%s.%N); R8=$(NS q1 curl -s -m 60 -X POST -d '{"user":"kim","password":"kim-2026"}' "http://$DOOR/dc/ram.php?op=enroll&app=openeoc")
S8=$(python3 -c "print('%.1f' % ($(date +%s.%N) - $T8))")
check $(echo "$R8" | grep -q "did not answer in 40 s" && python3 -c "import sys; sys.exit(0 if $S8 < 55 else 1)" \
        && grep -q "IDENTITY_ASK_IS_SAID_V1.*asking the region for openeoc-kim" $T/door.log && grep -q "openeoc-kim: NO identity after" $T/door.log && echo 0 || echo 1) \
  "8 the region does not answer: the door says so in $S8 s (the phone waits 60): $(echo "$R8" | cut -c1-200); door log: $(grep -o 'openeoc-kim: NO identity.*' $T/door.log | tail -1 | cut -c1-160)"
echo "--- logs"; grep -h "ENROLL_V1\|identity for\|refused" $T/A/identity.log $T/q1/sponsor.log $T/door.log 2>/dev/null | cut -c1-200 | sed 's/^/    /' | tail -12
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
