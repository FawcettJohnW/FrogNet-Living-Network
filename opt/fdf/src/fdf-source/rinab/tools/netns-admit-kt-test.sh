#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-admit-kt-test.sh -- [LOGIN_IS_AN_INVITATION_V1] what the Android app does with the EOC's join line (the EOC QR):
# log in at the line's RAM host on the bridge, then use the login exactly as an invitation -- the hub's tunnel with the
# device's own key and the edge the door gave, the region joined at the level's session port inside it. The same Kotlin
# (OrgLogin.admit, fdf-member-kt admit) the app runs, on the JVM.
#   A 198.18.0.1   the RAM host: its hub, the region's RAM manager, fdf-org with levels Team (no password, reach ram)
#                  and HQ (password)
#   q 198.18.0.3   the device: fdf-member-kt admit <line>
# Checks:
#   1  a wrong password at HQ is refused, and said
#   2  q logs in at Team: ADMITTED as the door's name with its edge; the region at 10.253.253.254:<Team's session port>
#   3  q's own tunnel to the hub (its key, its edge) is up and the member joined the region through it
#   4  the region holds q's announcement: role leaf, its key, its edge
#   5  a second run with the same key: the same edge (nothing kept but the line)
# Needs root, ip, wg, wireguard-go, python3, curl, nft, java, the built fdf-member-kt and fdf-tun.
#   usage: tools/netns-admit-kt-test.sh [RINAB_DIR]   KT_MEMBER=<fdf-member-kt launcher> FDF_TUN=<fdf-tun-x86_64>
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-admitkt}; rm -rf $T; mkdir -p $T/bin
AND=$H/../../frognet-android; KT=${KT_MEMBER:-$AND/member-cli/build/install/fdf-member-kt/bin/fdf-member-kt}; FT=${FDF_TUN:-$AND/tun/out/fdf-tun-$(uname -m)}
[ -x "$KT" ] && [ -x "$FT" ] || { echo "FAIL  setup: $KT or $FT missing (build member-cli: gradle -Pfdf.jvmOnly=true :member-cli:installDist)"; exit 1; }
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="A q"
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
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
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_BIN=$T/bin python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
n=A; mkdir -p $T/$n/admitted $T/$n/run-wg; PUB=198.18.0.1
KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env
NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/rina-host --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done
NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
NS $n sysctl -q -w net.ipv4.ip_forward=1
svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
ORG A init > $T/A/org.out 2>&1
ORG A level Team --reach ram >> $T/A/org.out 2>&1
ORG A level HQ --password --reach ram >> $T/A/org.out 2>&1; echo chiefpw | ORG A user HQ chief >> $T/A/org.out 2>&1
svc $T/A/org.log ip netns exec A sh -c "mount --bind $T/A/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/A/hub.env FDF_ADMITTED=$T/A/admitted FDF_SITE_KEYS=$T/A FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-org serve"
for i in $(seq 100); do [ "$(grep -c 'listening on' $T/A/org.log)" -ge 3 ] && break; sleep 0.1; done
SP=$(ORG A list 2>/dev/null | awk '$1=="level" && $2=="Team" {print $6}')
L_team=$(ORG A line Team 2>>$T/A/line.err); L_hq=$(ORG A line HQ 2>>$T/A/line.err)
mkdir -p $T/q; printf '{"name":"q","guid":"g-q"}\n' > $T/q/facts.json
KTQ() { ip netns exec q env FDF_TUN_BIN=$FT FDF_TUN_NAME=fdft0 FDF_KEY_FILE=$T/q/wg.key FDF_FACTS=$T/q/facts.json FDF_DEVICE_ID=dev-q FDF_LEAF_WG=fdf-q FDF_ASSERT_SEC=5 "$KT" "$@"; }
# the device's WireGuard interface, made at start-up as on a Linux leaf (wireguard-go: its socket is not per namespace, so a name of its own)
leafwg() { NS q wireguard-go fdf-q >/dev/null 2>&1; for j in $(seq 100); do NS q wg show fdf-q >/dev/null 2>&1 && break; sleep 0.05; done; }
leafwg
# 1 a wrong password at HQ
echo "chief wrong" | KTQ admit "$L_hq" > $T/q/bad.log 2>&1; BAD=$?
check $([ $BAD = 3 ] && grep -q "wrong user or password" $T/q/bad.log && echo 0 || echo 1) "1 a wrong password at HQ refused (exit $BAD): $(grep -o 'LOGIN FAILED: .*' $T/q/bad.log | cut -c1-120)"
# 2-4 the login at Team, used as an invitation
( KTQ admit "$L_team" >> $T/q/app.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q/app.log && break; grep -q "LOGIN FAILED" $T/q/app.log && break; sleep 0.25; done
ADM=$(grep -o 'ADMITTED as [^:]*: invitation: .*' $T/q/app.log | head -1); EDGE=$(echo "$ADM" | grep -o 'edge [0-9.]*' | head -1 | awk '{print $2}')
check $([ -n "$EDGE" ] && echo "$ADM" | grep -q "region at $HA:$SP inside the tunnel" && echo 0 || echo 1) "2 q logged in at Team, used as an invitation: $(echo "$ADM" | cut -c1-200) (Team's session port $SP)"
WGQ=$(NS q wg show fdf-q 2>/dev/null | tr '\n' ' ')
check $(echo "$WGQ" | grep -q "endpoint: 198.18.0.1:51820" && grep -q "joined the region at $HA:$SP" $T/q/app.log && echo 0 || echo 1) "3 q's own tunnel to the hub is up and the member joined the region through it: $(grep -o 'joined the region at [^,]*' $T/q/app.log | head -1)"
ANN=$(NS A curl -s -m 3 -X POST -d '{"op":"participants"}' "http://$RAM$API?op=rina" | python3 -c "import sys,json; t=json.load(sys.stdin); ps=t.get('participants', t) if isinstance(t, dict) else t; ps=ps.values() if isinstance(ps, dict) else ps; c=[p.get('facts') for p in ps if isinstance(p, dict) and (p.get('facts') or {}).get('edge')]; print(json.dumps(c[0]) if c else json.dumps(t)[:300])" 2>&1)
PUBQ=$(NS q cat $T/q/facts.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('pubkey',''))")
check $(echo "$ANN" | grep -q "\"role\": \"leaf\"" && echo "$ANN" | grep -q "\"edge\": \"$EDGE\"" && echo "$ANN" | grep -q "$PUBQ" && echo 0 || echo 1) "4 the region holds q's announcement: $ANN"
# 5 again, the same key: the same edge
kill $(cat $T/svc.pids | tail -1) 2>/dev/null; for p in $(ip netns pids q); do kill $p 2>/dev/null; done; sleep 1; NS q ip link del fdf-q 2>/dev/null; leafwg
( KTQ admit "$L_team" >> $T/q/app2.log 2>&1 ) & echo $! >> $T/svc.pids
for i in $(seq 160); do grep -q "joined the region at" $T/q/app2.log && break; grep -q "LOGIN FAILED" $T/q/app2.log && break; sleep 0.25; done
E2=$(grep -o 'ADMITTED as [^:]*: invitation: .*' $T/q/app2.log | head -1 | grep -o 'edge [0-9.]*' | head -1 | awk '{print $2}')
check $([ -n "$E2" ] && [ "$E2" = "$EDGE" ] && grep -q "joined the region at" $T/q/app2.log && echo 0 || echo 1) "5 a second run with the same key: edge $E2 (was $EDGE), joined again"
echo "--- q"; grep -E "ADMITTED|door at|logged in|joined the region|tunnel:|LOGIN|refused|REFUSED" $T/q/app.log $T/q/app2.log | cut -c1-220 | sed 's/^/    /' | tail -14
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
