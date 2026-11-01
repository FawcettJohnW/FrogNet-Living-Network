#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-regions-test.sh -- [REGIONS_PER_MACHINE_V1][FDF_SETUP_V1] two regions on one machine, each defined whole by
# fdf-setup. John, 2026-11-01: "A machine may host more than one ram segment, as long as they're on different ports."
# "I *WANT* a second region." "make defaults for everything, but it all must be configurable."
#   A 198.18.0.1   holds regions D and E, as fdf-region-create leaves them (its systemd part is started here as the
#                  region test beds start it): /opt/fdf/hub/D and /opt/fdf/hub/E (here $T/A/hub), each with its own
#                  interface (the hub address a /32 on both), hub, RAM manager on its own port, and fdf-org serve
#   r 198.18.0.5   joins D at level Restricted (no password)
#   q 198.18.0.7   joins E at level Admin as chief (a password); a wrong password first
# Checks:
#   1  fdf-setup --define, once per region: each region's levels, user and ports as defined; one line per level
#   2  the two regions' generic credentials and members have different edges (one pool for the machine)
#   3  r joins D, q's wrong password is refused at E, q joins E
#   4  each device reaches its own region's RAM manager, and not the other region's
#   5  fdf-setup --define again: everything kept, the same lines
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-regions-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-regions}; rm -rf $T; mkdir -p $T/bin
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="A x r q"
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in $NODES; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in $NODES; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for n in $NODES; do ip link del v-$n-b 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/rina-host $T/bin/ 2>/dev/null; [ -x $T/bin/rina-host ] || cp $H/build/bin/rina-host-static $T/bin/rina-host
cp $PK/bin/fdf-hub $PK/bin/fdf-org $PK/bin/fdf-login $PK/bin/fdf-setup $PK/bin/fdf-invite $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH FDF_PY=$FDFPY
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
mkdir -p /run/wireguard $T/A/run-wg $T/A/var
NS A sysctl -q -w net.ipv4.ip_forward=1
# ---- the two regions, as fdf-region-create leaves them: D (RAM 51888, hub 50889) and E (RAM 51889, hub 50890)
region() {   # AREA RAMPORT HUBPORT
  local A=$1 RP=$2 HP=$3 D=$T/A/hub/$1; mkdir -p $D/admitted
  local ENV="FDF_HUB_ROOT=$T/A/hub FDF_HUB_ENV=$D/hub.env FDF_ADMITTED=$D/admitted FDF_ORG_KEY=$D/org-key FDF_SITE_KEYS=$T/A/var FDF_BIN=$T/bin FDF_PY=$FDFPY"
  eval "IN$A() { NS A sh -c \"mount --bind $T/A/run-wg /run/wireguard 2>/dev/null; exec env $ENV \\\"\\\$@\\\"\" sh \"\$@\"; }"
  local KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/A/var/site-key-$A; chmod 600 $T/A/var/site-key-$A
  printf 'RINA_RAM=%s:%s\nFDF_AREA=%s\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=198.18.0.1:%s\nFDF_LISTEN=%s\nFDF_IFACE=wg-%s\nFDF_KEY=%s/hub.key\n' $HA $RP $A $A $HP $HP $A $D > $D/hub.env
  IN$A env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area $A --iface wg-$A --endpoint 198.18.0.1:$HP --listen $HP --key $D/hub.key --iface-only > $T/A/iface-$A.log 2>&1
  NS A env RINA_REGION_FILE=$T/A/area-$A.perm RINA_NET_CONFIG=$T/A/none $T/bin/rina-host --listen $HA:$RP --api $API >> $T/A/host-$A.log 2>&1 &
  for i in $(seq 100); do NS A curl -s -m 1 -X POST -d '{"op":"version"}' "http://$HA:$RP$API?op=rina" > /dev/null && break; sleep 0.05; done
  NS A curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$A\",\"key\":\"$KEY\"}" "http://$HA:$RP$API?op=rina" > /dev/null
  svc $T/A/hub-$A.log IN$A env RINA_RAM=$HA:$RP FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area $A --iface wg-$A --endpoint 198.18.0.1:$HP --name hub$A --key $D/hub.key --listen $HP --facts $T/A/facts-$A.json --admitted $D/admitted
  for i in $(seq 100); do grep -q "start (adopt)" $T/A/hub-$A.log && break; sleep 0.05; done
  svc $T/A/org-$A.log IN$A python3 -u $T/bin/fdf-org serve
}
region D 51888 50889
region E 51889 50890
ADDRS=$(NS A ip -o -4 addr show | grep -E "wg-D|wg-E" | awk '{print $2, $4}' | tr '\n' ' ')
# ---- 1 each region defined whole with fdf-setup --define
cat > $T/D.json <<EOF
{"area": "D", "address": "198.18.0.1", "ram_port": 51888, "hub_port": 50889,
 "levels": [{"name": "Restricted", "password": false, "reach": "ram"}], "users": []}
EOF
cat > $T/E.json <<EOF
{"area": "E", "address": "198.18.0.1", "ram_port": 51889, "hub_port": 50890,
 "levels": [{"name": "Restricted", "password": false, "reach": "ram"},
            {"name": "Admin", "password": true, "reach": "ram", "login_port": 41001, "session_port": 41002}],
 "users": [{"level": "Admin", "user": "chief", "password": "hunter2"}]}
EOF
for A in D E; do IN$A env FDF_SETUP_NO_SYSTEMD=1 FDF_ORG=$T/bin/fdf-org python3 -u $T/bin/fdf-setup --define $T/$A.json > $T/setup1-$A.log 2>&1
  check $? "1 fdf-setup --define $A: $(grep '^==\|fdf-setup:' $T/setup1-$A.log | cut -c1-60 | tr '\n' '|')"; done
JD=$(IND python3 $T/bin/fdf-org list --json); JE=$(INE python3 $T/bin/fdf-org list --json)
check $(python3 -c 'import sys,json; d=json.loads(sys.argv[1])["levels"]; e=json.loads(sys.argv[2])["levels"]
sys.exit(0 if list(d)==["Restricted"] and not d["Restricted"]["password"] and sorted(e)==["Admin","Restricted"] and e["Admin"]["password"] else 1)' "$JD" "$JE" && echo 0 || echo 1) "1 D has Restricted; E has Restricted and Admin (a password): D $JD, E $JE"
check $(INE python3 $T/bin/fdf-org list | grep -q "login 41001 session 41002" && echo 0 || echo 1) "1 E's Admin on the ports defined: $(INE python3 $T/bin/fdf-org list | grep '^level Admin')"
check $(NS A curl -s "http://$HA:51889$API?op=read&service=fdf-org&variable=user" | grep -q 'Admin/chief' && echo 0 || echo 1) "1 the user chief at E's Admin (its verifier only)"
check $([ -s $T/A/var/lines/D/Restricted.line ] && [ -s $T/A/var/lines/E/Admin.line ] && [ -s $T/A/var/lines/E/Restricted.line ] && echo 0 || echo 1) "1 one line per level, per region: $(cd $T/A/var/lines && ls */* | tr '\n' ' ')"
# ---- 2 one edge pool for the machine
GD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["edge"])' $T/A/hub/D/admitted/_generic.json 2>/dev/null)
GE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["edge"])' $T/A/hub/E/admitted/_generic.json 2>/dev/null)
check $([ -n "$GD" ] && [ -n "$GE" ] && [ "$GD" != "$GE" ] && echo 0 || echo 1) "2 the generic credentials' edges differ: D $GD, E $GE; the hub address on both interfaces: $ADDRS"
# ---- 3 devices join
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
for n in r q; do mkdir -p $T/$n/run-wg; NS $n ip route add default via 198.18.0.254 dev v-$n 2>/dev/null; done
DEV r python3 -u $T/bin/fdf-login "$(cat $T/A/var/lines/D/Restricted.line)" < /dev/null > $T/r/login.log 2>&1
check $? "3 r joined D with Restricted's line, no password: $(grep 'logged in' $T/r/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
echo "chief wrong" | DEV q python3 -u $T/bin/fdf-login "$(cat $T/A/var/lines/E/Admin.line)" > $T/q/login-bad.log 2>&1; BAD=$?
check $([ $BAD != 0 ] && grep -q "wrong user or password" $T/q/login-bad.log && echo 0 || echo 1) "3 q's wrong password refused at E: $(grep -o 'refused: .*' $T/q/login-bad.log)"
echo "chief hunter2" | DEV q python3 -u $T/bin/fdf-login "$(cat $T/A/var/lines/E/Admin.line)" > $T/q/login.log 2>&1
check $? "3 q joined E at Admin as chief: $(grep 'logged in' $T/q/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
edges() { python3 -c 'import json,os,sys; d=sys.argv[1]; print(" ".join(sorted(json.load(open(os.path.join(d,f)))["edge"] for f in os.listdir(d) if f.endswith(".json") and f != "_generic.json")))' $1; }
ER=$(edges $T/A/hub/D/admitted); EQ=$(edges $T/A/hub/E/admitted)
check $([ -n "$ER" ] && [ -n "$EQ" ] && [ -z "$(comm -12 <(echo $ER $GD | tr ' ' '\n' | sort) <(echo $EQ $GE | tr ' ' '\n' | sort))" ] && echo 0 || echo 1) "2 every edge from the one pool, none given twice: D $GD $ER; E $GE $EQ"
# ---- 4 each device reaches its own region (its level's session port), and not the other region's
ram_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["RINA_RAM"].rsplit(":",1)[1])' $1 2>/dev/null || grep -o 'RINA_RAM=[0-9.:]*' $2 | head -1 | cut -d: -f2; }
PR=$(ram_of $T/r/login.json $T/r/login.log); PQ=$(ram_of $T/q/login.json $T/q/login.log)
ok() { NS $1 curl -s -m 3 -X POST -d '{"op":"version"}' "http://$HA:$2$API?op=rina" | grep -q '"ok":true' && echo yes || echo no; }
RD=$(ok r $PR); RE=$(ok r $PQ); RE2=$(ok r 51889); QE=$(ok q $PQ); QD=$(ok q $PR); QD2=$(ok q 51888)
check $([ $RD = yes ] && [ $QE = yes ] && [ $RE = no ] && [ $RE2 = no ] && [ $QD = no ] && [ $QD2 = no ] && echo 0 || echo 1) \
  "4 r reaches its D session ($PR: $RD), not E's session ($PQ: $RE) or E's RAM manager ($RE2); q reaches its E session ($PQ: $QE), not D's ($PR: $QD) or D's RAM manager ($QD2)"
# ---- 5 again: everything kept
cp $T/A/var/lines/E/Admin.line $T/l1
for A in D E; do IN$A env FDF_SETUP_NO_SYSTEMD=1 FDF_ORG=$T/bin/fdf-org python3 -u $T/bin/fdf-setup --define $T/$A.json > $T/setup2-$A.log 2>&1 || echo "rc $? $A" >> $T/setup2.rc; done
check $([ ! -s $T/setup2.rc ] && [ "$(IND python3 $T/bin/fdf-org list --json)" = "$JD" ] && [ "$(INE python3 $T/bin/fdf-org list --json)" = "$JE" ] && cmp -s $T/l1 $T/A/var/lines/E/Admin.line && echo 0 || echo 1) \
  "5 fdf-setup --define again: both regions kept, the same lines ($(grep -h 'kept' $T/setup2-*.log | head -2 | tr '\n' '|'))"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
