#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-targets-test.sh -- [ONE_ORG_ON_EVERY_TARGET_V1][LOGIN_WHERE_IT_CAN_V1] (John 2026-11-02) a device is given the
# list of RAM hosts (EOC targets) at initialization, makes its tunnels to the ones it reaches, and logs in at any one it
# can reach -- with the same user and password everywhere. Two RAM hosts, each a region of its own with its own keys:
#   A 198.18.0.1   where the admin set the users (fdf-org user)
#   B 198.18.0.3   another target: the admin carries A's organization to it by hand (fdf-org export on A, import on B)
#   q 198.18.0.5   the device: one line naming A then B
# Checks, each with what it saw:
#   1  import on B is refused while B holds a user the export does not (it would stop verifying), and names it
#   2  import --replace: B's credential key is A's, B's stray user is gone, B's levels keep B's own ports
#   3  A unreachable: q logs in with chief's password (set on A only) at B; A is said and left for the next run
#   4  A back: the same line logs q in at both
#   5  a wrong password: refused at both, and fdf-login fails saying so
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-targets-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-targets}; rm -rf $T; mkdir -p $T/bin $T/q
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
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
hostup() { local n=$1; NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/rina-host --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
           for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done; }
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
# ---- two RAM hosts, each a region of its own (as fdf-region-create makes one), each with its own keys
for n in A B; do mkdir -p $T/$n/admitted $T/$n/run-wg; PUB=$(NS $n ip -4 -o addr show dev v-$n | awk '{print $4}' | cut -d/ -f1)
  KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
  printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n
  NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  ORG $n init > $T/$n/org.out 2>&1; ORG $n level HQ --password --reach ram >> $T/$n/org.out 2>&1
  svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-org serve"
done
for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 2 ] && break; sleep 0.1; done; done
echo "chief-s3cret" | ORG A user HQ chief >> $T/A/org.out 2>&1          # the admin's users: on A
echo "old-pw" | ORG B user HQ stray >> $T/B/org.out 2>&1               # a user B was given before, under B's own key
PORTS() { ORG $1 list 2>/dev/null | awk '$1=="level" && $2=="HQ"'; }
BPORTS=$(PORTS B)
# ---- 1 import refused while B holds a user the export does not
ORG A export > $T/export.line 2> $T/export.err
ORG B import < $T/export.line > $T/B/import1.log 2>&1; RC=$?
check $([ -s $T/export.line ] && [ $RC != 0 ] && grep -q "stray" $T/B/import1.log && echo 0 || echo 1) \
      "1 import refused (exit $RC) while B holds a user the export does not: $(tail -1 $T/B/import1.log | cut -c1-200)"
# ---- 2 import --replace
ORG B import --replace < $T/export.line > $T/B/import2.log 2>&1; RC=$?
ORG B list > $T/B/list.txt 2>&1
check $([ $RC = 0 ] && cmp -s $T/A/org-key $T/B/org-key && ! grep -q stray $T/B/list.txt && [ "$(PORTS B)" = "$BPORTS" ] && echo 0 || echo 1) \
      "2 import --replace (exit $RC): B's key is A's ($(cmp -s $T/A/org-key $T/B/org-key && echo same || echo DIFFERENT)), stray gone, B's ports kept: $(grep -o 'import of.*' $T/B/import2.log | cut -c1-200)"
# ---- the line: A then B
LB=$(ORG B line HQ 2>$T/B/line.err); LINE=$(ORG A line HQ --with $LB 2>$T/A/line.err)
NS q ip route add default via 198.18.0.254 dev v-q 2>/dev/null
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
mkdir -p $T/q/run-wg
ramver() { NS q curl -s -m 4 -X POST -d '{"op":"version"}' "http://$HA:$1$API?op=rina" --interface $2 | grep -q '"ok":true' && echo ok || echo no; }
# ---- 3 A unreachable
ip link set v-A-b down
echo "chief chief-s3cret" | DEV q python3 -u $T/bin/fdf-login "$LINE" > $T/q/login3.log 2>&1; RC=$?
SB=$(python3 -c "import json;m=json.load(open('$T/q/login.json'))['managers'];print([v['session_port'] for v in m.values() if v.get('endpoint','').startswith('198.18.0.3')][0])" 2>/dev/null)
check $([ $RC = 0 ] && grep -q "NOT logged in at hubA" $T/q/login3.log && grep -q "logged in at 1 of 2" $T/q/login3.log && [ "$(ramver "$SB" wg-own1)" = ok ] && echo 0 || echo 1) \
      "3 A unreachable: q logged in at B with the password set on A (exit $RC; B's session :$SB answers): $(grep -E 'NOT logged|logged in at [0-9]' $T/q/login3.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';' | cut -c1-260)"
# ---- 4 A back
ip link set v-A-b up; sleep 1
echo "chief chief-s3cret" | DEV q python3 -u $T/bin/fdf-login "$LINE" > $T/q/login4.log 2>&1; RC=$?
check $([ $RC = 0 ] && grep -q "logged in at 2 of 2" $T/q/login4.log && echo 0 || echo 1) \
      "4 A back: q logged in at both (exit $RC): $(grep -E 'logged in at [0-9]' $T/q/login4.log | sed 's/^\[LOGIN\] [0-9:]* //')"
# ---- 5 a wrong password
rm -f $T/q/login.json; for i in 0 1; do NS q ip link del wg-own$i 2>/dev/null; done
echo "chief wrong" | DEV q python3 -u $T/bin/fdf-login "$LINE" > $T/q/login5.log 2>&1; RC=$?
check $([ $RC != 0 ] && grep -q "logged in at none of the 2" $T/q/login5.log && [ "$(grep -c 'wrong user or password' $T/q/login5.log)" -ge 2 ] && echo 0 || echo 1) \
      "5 a wrong password: refused at both, fdf-login fails (exit $RC): $(grep -o 'logged in at none.*' $T/q/login5.log | cut -c1-200)"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
