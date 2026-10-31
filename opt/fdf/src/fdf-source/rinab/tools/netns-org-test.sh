#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-org-test.sh -- [ORG_LEVELS_V1][LOGIN_RESTRICTED_FIRST_V1][REACH_PER_EDGE_V1][PORTS_BOUND_TO_ADMISSION_V1]
# ORGANIZATIONS.md plan steps 1-3 on one box: levels, the generic exchange, login, reach per edge, revocation, restart.
# Topology (the "Internet" is 198.18.0.0/24, a bridge): hub 198.18.0.1 holds the region (fdf-hub, rina-host on
# 10.253.253.254:8840, fdf-org serve) and "a server at a post" on 10.253.253.254:7777; devices r, q, s, w, v are clients.
# Levels: Responders (no password, reach RAM only), HQ (password, reach list: the server), Ops (no password, open).
# Checks, each with what it saw:
#   1  fdf-org init, three levels, a user, the join lines
#   2  r logs in at Responders with only its line: own edge, admitted, RINA_RAM = hub:<Responders session port>
#   3  r's reach: its session port answers the RAM; the RAM port itself, HQ's session port, the server, and another
#      level's login port do not
#   4  q at HQ with a wrong password: refused, its edge stays restricted (login port only, no session port)
#   5  q at HQ with the right password: admitted; the server answers q; HQ's session port answers the RAM
#   6  s at Ops (open): reaches the server and r's edge; r cannot reach s (RAM only)
#   7  w and v on the generic credential at the same instant: both end on their own tunnels and own edges
#   8  the generic credential reaches the door and nothing else
#   9  revoke r: its peer goes from the hub and its session port stops answering
#  10  the RAM host restarts (rina-host from its region file, fdf-org serve): every level and user is back; q's session
#      returns with no login; r stays revoked
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-org-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-org}; rm -rf $T; mkdir -p $T/hub/admitted $T/bin
for n in r q s w v; do mkdir -p $T/$n; done
export FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY
API=/FawcettInnovations.RINA.ram_interface.php; AREA=D; KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
HA=10.253.253.254; RAM=$HA:8840; U="http://$RAM$API?op=rina"; PUB=198.18.0.1
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="hub r q s w v"
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
export PATH=$T/bin:$PATH
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
# a device's own filesystem: its login state and /etc/fnid (device id)
# its own /run/wireguard (wireguard-go's control sockets), as each real device has; the bind lives in this exec's mount ns
mkdir -p /run/wireguard; for n in r q s w v; do mkdir -p $T/$n/run-wg; done
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_LOGIN_STATE=$T/$n/login.json \"\$@\"" sh "$@"; }
# ---- the region (as fdf-region-create makes it)
printf '%s\n' "$KEY" > $T/hub/site-key-$AREA; chmod 600 $T/hub/site-key-$AREA
cat > $T/hub/hub.env <<EOF
RINA_RAM=$RAM
FDF_AREA=$AREA
FDF_HUB_NAME=hubA
FDF_ENDPOINT=$PUB:51820
FDF_LISTEN=51820
FDF_IFACE=wg-D
EOF
NS hub python3 $T/bin/fdf-hub --area $AREA --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/hub/wg.key --iface-only > $T/hub/iface.log 2>&1
check $? "hub: the region's interface (fdf-hub --iface-only)"
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
host() { NS hub env RINA_REGION_FILE=$T/hub/area.perm RINA_NET_CONFIG=$T/hub/none $T/bin/rina-host --listen $RAM --api $API >> $T/hub/host.log 2>&1 & echo $! > $T/hub/host.pid
         for i in $(seq 100); do NS hub curl -s -m 1 -X POST -d '{"op":"version"}' "$U" > /dev/null && break; sleep 0.05; done; }
host
R=$(NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$AREA\",\"key\":\"$KEY\"}" "$U"); case "$R" in *'"ok":true'*) ;; *) echo "FAIL  map_site: $R"; exit 1 ;; esac
NS hub sysctl -q -w net.ipv4.ip_forward=1
svc $T/hub/hub.log ip netns exec hub env RINA_RAM=$RAM FDF_BIN=$T/bin python3 $T/bin/fdf-hub --area $AREA --iface wg-D --endpoint $PUB:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json --admitted $T/hub/admitted
for i in $(seq 100); do grep -q "start (adopt)" $T/hub/hub.log && break; sleep 0.05; done
# "a server at a post": answers one line on 10.253.253.254:7777
NS hub python3 -c "
import socket
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('$HA',7777)); s.listen(8)
while True:
    c,_=s.accept(); c.sendall(b'server\n'); c.close()" > /dev/null 2>&1 &
# ---- 1 the organization
ORG() { NS hub env FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub FDF_PY=$FDFPY python3 $T/bin/fdf-org "$@"; }
ORG init > $T/hub/org-init.log 2>&1 && ORG level Responders --reach ram > $T/hub/org-lv.log 2>&1 \
  && ORG level HQ --password --reach list:$HA:7777 >> $T/hub/org-lv.log 2>&1 && ORG level Ops --reach open >> $T/hub/org-lv.log 2>&1 \
  && echo "s3cret-HQ" | ORG user HQ chief >> $T/hub/org-lv.log 2>&1
check $? "1 fdf-org init, three levels, a user: $(cat $T/hub/org-init.log $T/hub/org-lv.log | sed 's/^\[ORG\] [0-9:]* //' | tr '\n' ';' | cut -c1-400)"
for lv in Responders HQ Ops; do ORG line $lv > $T/line-$lv 2> $T/line-$lv.err; done
check $([ -s $T/line-Responders ] && [ -s $T/line-HQ ] && [ -s $T/line-Ops ] && echo 0 || echo 1) "1 the join lines: $(head -c 40 $T/line-Responders)..."
svc $T/hub/org.log ip netns exec hub env FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub FDF_PY=$FDFPY python3 -u $T/bin/fdf-org serve
for i in $(seq 100); do [ "$(grep -c 'listening on' $T/hub/org.log)" -ge 4 ] && break; sleep 0.1; done
check $([ "$(grep -c 'listening on' $T/hub/org.log)" -ge 4 ] && echo 0 || echo 1) "1 fdf-org serve: the door and three login ports: $(grep 'listening on' $T/hub/org.log | sed 's/.*listening on //' | tr '\n' ' ')"
PORT() { ORG list | awk -v l=$1 -v k=$2 '$1=="level" && $2==l {for(i=3;i<NF;i++) if($i==k) print $(i+1)}'; }
SR=$(PORT Responders session); SH=$(PORT HQ session); LR=$(PORT Responders login); LH=$(PORT HQ login)
# a TCP probe from a device: "open" or the error
probe() { NS $1 python3 -c "
import socket,sys
s=socket.socket(); s.settimeout(3)
try: s.connect(('$2',$3)); print('open')
except Exception as e: print(type(e).__name__)"; }
ramver() { NS $1 curl -s -m 4 -X POST -d '{"op":"version"}' "http://$HA:$2$API?op=rina" | grep -q '"ok":true' && echo ok || echo no; }
login() { local n=$1 lv=$2; shift 2; DEV $n env FDF_DEVICE_ID=dev-$n python3 -u $T/bin/fdf-login "$(cat $T/line-$lv)" "$@"; }
# ---- 2 r at Responders, with only its line (it starts with no route to the hub at all)
NS r ip route add default via 198.18.0.254 dev v-r 2>/dev/null
login r Responders --nick truck-12 < /dev/null > $T/r/login.log 2>&1
check $? "2 r logged in at Responders: $(grep -E 'door at|logged in' $T/r/login.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';' | cut -c1-300)"
check $(grep -q "RINA_RAM=$HA:$SR" $T/r/login.log && echo 0 || echo 1) "2 r's RINA_RAM is the hub at Responders' session port $SR"
check $(NS r ip route | grep -c "dev wg" | grep -qx 1 && NS r ip route | grep -q "^$HA dev wg-own0" && echo 0 || echo 1) "2 r's tunnel routes the RAM manager's address and nothing else: $(NS r ip route | grep wg | tr '\n' ';')"
# ---- 3 r's reach
check $([ "$(ramver r $SR)" = ok ] && echo 0 || echo 1) "3 r: its session port $SR answers the RAM"
P8840=$(probe r $HA 8840); PSH=$(probe r $HA $SH); P7777=$(probe r $HA 7777); PLH=$(probe r $HA $LH)
check $([ "$P8840" != open ] && [ "$PSH" != open ] && [ "$P7777" != open ] && [ "$PLH" != open ] && echo 0 || echo 1) \
      "3 r cannot reach the RAM port ($P8840), HQ's session port ($PSH), the server ($P7777), HQ's login port ($PLH)"
# ---- 4 q at HQ, wrong password
NS q ip route add default via 198.18.0.254 dev v-q 2>/dev/null
echo "chief wrong" | login q HQ > $T/q/login-bad.log 2>&1; RC=$?
check $([ $RC != 0 ] && grep -q "wrong user or password" $T/q/login-bad.log && echo 0 || echo 1) "4 q with a wrong password: refused ($(grep -o 'refused.*' $T/q/login-bad.log | head -1))"
check $([ "$(probe q $HA $LH)" = open ] && [ "$(probe q $HA $SH)" != open ] && echo 0 || echo 1) "4 q's edge stays restricted: its login port open, its session port not"
# ---- 5 q at HQ, right password
echo "chief s3cret-HQ" | login q HQ > $T/q/login.log 2>&1
check $? "5 q logged in at HQ: $(grep 'logged in' $T/q/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
check $([ "$(ramver q $SH)" = ok ] && [ "$(probe q $HA 7777)" = open ] && [ "$(probe q $HA $SR)" != open ] && echo 0 || echo 1) \
      "5 q reaches HQ's session port and the server on its list, not Responders' session port"
# ---- 6 s at Ops (open)
NS s ip route add default via 198.18.0.254 dev v-s 2>/dev/null
login s Ops < /dev/null > $T/s/login.log 2>&1
check $? "6 s logged in at Ops: $(grep 'logged in' $T/s/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
RE=$(python3 -c "import json;print(list(json.load(open('$T/r/login.json'))['managers'].values())[0]['edge'])")
SE=$(python3 -c "import json;print(list(json.load(open('$T/s/login.json'))['managers'].values())[0]['edge'])")
NS r python3 -c "
import socket
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('$RE',9001)); s.listen(4)
while True:
    c,_=s.accept(); c.sendall(b'r\n'); c.close()" > /dev/null 2>&1 &
NS s python3 -c "
import socket
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('$SE',9002)); s.listen(4)
while True:
    c,_=s.accept(); c.sendall(b's\n'); c.close()" > /dev/null 2>&1 &
sleep 0.5
NS s ip route add $RE/32 dev wg-own0 2>/dev/null; NS r ip route add $SE/32 dev wg-own0 2>/dev/null
P_SR=$(probe s $RE 9001); P_RS=$(probe r $SE 9002)
check $([ "$(probe s $HA 7777)" = open ] && [ "$P_SR" = open ] && [ "$P_RS" != open ] && echo 0 || echo 1) \
      "6 s (open) reaches the server and r ($P_SR); r (RAM only) cannot reach s ($P_RS)"
# ---- 7 w and v on the generic credential at the same instant
for n in w v; do NS $n ip route add default via 198.18.0.254 dev v-$n 2>/dev/null; done
login w Responders < /dev/null > $T/w/login.log 2>&1 & PW=$!
login v Responders < /dev/null > $T/v/login.log 2>&1 & PV=$!
wait $PW; RW=$?; wait $PV; RV=$?
check $([ $RW = 0 ] && [ $RV = 0 ] && [ "$(ramver w $SR)" = ok ] && [ "$(ramver v $SR)" = ok ] && echo 0 || echo 1) \
      "7 w and v at once: both logged in on their own tunnels ($(grep -h 'door at' $T/w/login.log $T/v/login.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';'); retries: $(grep -hc 'asking again' $T/w/login.log $T/v/login.log | tr '\n' ' '))"
# ---- 8 the generic credential reaches the door and nothing else
DOORP=$(NS hub env FDF_HUB_ENV=$T/hub/hub.env python3 -c "
import sys; sys.path.insert(0,'$FDFPY'); import fdfram
print([c['bag']['port'] for c in fdfram.Ram('$RAM').read('fdf-org','door')][-1])")
python3 - "$(cat $T/line-Responders)" > $T/x-generic.json <<'EOF'
import base64, json, sys
b = sys.argv[1][9:]; j = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))); print(json.dumps(j))
EOF
GPRIV=$(python3 -c "import json;print(json.load(open('$T/x-generic.json'))['generic']['privkey'])")
HUBPUB=$(NS hub wg show wg-D public-key)
NS r ip link del wg-own0 2>/dev/null; echo "$GPRIV" > $T/g.key
DEV r sh -c "wireguard-go wg-g > /dev/null 2>&1; sleep 0.3; wg set wg-g private-key $T/g.key peer $HUBPUB endpoint $PUB:51820 allowed-ips 10.0.0.0/8"
NS r ip addr add 10.253.253.1/32 dev wg-g; NS r ip link set wg-g up; NS r ip route replace $HA/32 dev wg-g
G_DOOR=$(probe r $HA $DOORP); G_SR=$(probe r $HA $SR); G_LR=$(probe r $HA $LR); G_RAM=$(probe r $HA 8840)
check $([ "$G_DOOR" = open ] && [ "$G_SR" != open ] && [ "$G_LR" != open ] && [ "$G_RAM" != open ] && echo 0 || echo 1) \
      "8 the generic credential: the door $G_DOOR; a session port $G_SR, a login port $G_LR, the RAM port $G_RAM"
NS r ip link del wg-g; DEV r python3 $T/bin/fdf-login --up > /dev/null 2>&1
# ---- 9 revoke r
RN=$(python3 -c "import json;print(list(json.load(open('$T/r/login.json'))['managers'].values())[0]['name'])")
ORG revoke $RN > $T/hub/revoke.log 2>&1
sleep 1
check $([ "$(ramver r $SR)" = no ] && ! NS hub wg show wg-D allowed-ips | grep -q "$RE/32" && echo 0 || echo 1) "9 revoke $RN: its peer is gone and its session port does not answer"
# ---- 10 the RAM host restarts
pkill -f "rina-host --listen $RAM"; for i in $(seq 50); do pgrep -f "rina-host --listen $RAM" > /dev/null || break; sleep 0.1; done
NS hub nft delete table inet fdf_org 2>/dev/null   # the rules go with the machine; fdf-org serve rebuilds them from what survived
host; sleep 3
NS q ip link del wg-own0 2>/dev/null; DEV q python3 $T/bin/fdf-login --up > $T/q/up.log 2>&1
for i in $(seq 40); do [ "$(ramver q $SH)" = ok ] && break; sleep 0.5; done
LV=$(ORG list | grep -c '^level')
check $([ "$LV" = 3 ] && [ "$(ramver q $SH)" = ok ] && [ "$(ramver r $SR)" = no ] && echo 0 || echo 1) \
      "10 after the RAM host restarts: $LV levels back, q's session returns with no login, r stays revoked"
echo "--- fdf-org"; sed 's/^/    /' $T/hub/org.log | tail -25
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
