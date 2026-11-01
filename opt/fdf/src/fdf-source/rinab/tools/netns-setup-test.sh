#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-setup-test.sh -- [FDF_SETUP_V1] ORGANIZATIONS.md plan step 6: the manager's wizard, answered as a manager would,
# on a RAM host that holds its region (fdf-region-create's part needs systemd: here the hub and the RAM manager are
# started as the region test beds start them). Then devices join with the lines it printed.
#   A 198.18.0.1   the RAM host: fdf-hub, rina-host, fdf-org serve; fdf-setup run there with its answers on stdin
#   r 198.18.0.5   a First Responders device (no password); q 198.18.0.7 a Headquarters device (user and password)
# Checks:
#   1  the wizard ran to the end and said what it did at each step
#   2  what it made: spaces Responders and EOC Admin; levels First Responders (no password, Responders) and
#      Headquarters (password, Responders + EOC Admin); a user at Headquarters; one line per level, kept root-only
#   3  r joins with its line; q's wrong password is refused; q joins with the right one; each told its spaces
#   4  q's application is in EOC Admin and Responders; r's in Responders
#   5  the wizard again, adding nothing: everything kept, the same lines
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-setup-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-setup}; rm -rf $T; mkdir -p $T/bin
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
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
cp $PK/bin/fdf-hub $PK/bin/fdf-org $PK/bin/fdf-login $PK/bin/fdf-setup $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH FDF_PY=$FDFPY
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in $NODES; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
mkdir -p /run/wireguard $T/A/admitted $T/A/run-wg $T/A/var
AENV="FDF_HUB_ENV=$T/A/hub.env FDF_ADMITTED=$T/A/admitted FDF_SITE_KEYS=$T/A/var FDF_BIN=$T/bin FDF_PY=$FDFPY"
INA() { NS A sh -c "mount --bind $T/A/run-wg /run/wireguard && exec env $AENV \"\$@\"" sh "$@"; }
# ---- the region, as fdf-region-create leaves it
KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/A/var/site-key-D; chmod 600 $T/A/var/site-key-D
printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hubA\nFDF_ENDPOINT=198.18.0.1:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM > $T/A/hub.env
INA env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint 198.18.0.1:51820 --listen 51820 --key $T/A/wg.key --iface-only > $T/A/iface.log 2>&1
NS A env RINA_REGION_FILE=$T/A/area.perm RINA_NET_CONFIG=$T/A/none $T/bin/rina-host --listen $RAM --api $API >> $T/A/host.log 2>&1 &
for i in $(seq 100); do NS A curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done
NS A curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
NS A sysctl -q -w net.ipv4.ip_forward=1
svc $T/A/hub.log INA env RINA_RAM=$RAM FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint 198.18.0.1:51820 --name hubA --key $T/A/wg.key --listen 51820 --facts $T/A/facts.json --admitted $T/A/admitted
for i in $(seq 100); do grep -q "start (adopt)" $T/A/hub.log && break; sleep 0.05; done
svc $T/A/org.log INA python3 -u $T/bin/fdf-org serve          # fdf-org.service: says "no door" until the wizard's step 3
# ---- 1 the wizard, answered as a manager would (names with spaces, one level with a password)
cat > $T/answers <<'EOF'
Responders
y
EOC Admin
y

First Responders
n
ram
Responders
y
Headquarters
y
ram
Responders, EOC Admin
y

chief
hunter2

n
EOF
INA env FDF_SETUP_NO_SYSTEMD=1 FDF_ORG=$T/bin/fdf-org python3 -u $T/bin/fdf-setup < $T/answers > $T/setup1.log 2>&1; RC=$?
check $([ $RC = 0 ] && grep -q "fdf-setup: done" $T/setup1.log && echo 0 || echo 1) "1 the wizard ran to the end (rc $RC): $(grep '^==' $T/setup1.log | cut -c4-40 | tr '\n' '|')"
[ $RC = 0 ] || sed 's/^/    /' $T/setup1.log | tail -30
# ---- 2 what it made
J=$(INA python3 $T/bin/fdf-org list --json)
check $(echo "$J" | python3 -c 'import sys,json; j=json.load(sys.stdin); l=j["levels"]
sys.exit(0 if j["spaces"]==["EOC Admin","Responders"] and l["First Responders"]=={"password":False,"reach":{"kind":"ram"},"spaces":["Responders"]} and l["Headquarters"]["password"] and l["Headquarters"]["spaces"]==["Responders","EOC Admin"] else 1)' && echo 0 || echo 1) "2 spaces and levels as answered: $J"
check $(NS A curl -s "http://$RAM$API?op=read&service=fdf-org&variable=user" | grep -q 'Headquarters/chief' && echo 0 || echo 1) "2 the user chief at Headquarters (its verifier, never the password)"
check $([ -s "$T/A/var/lines/First Responders.line" ] && [ -s $T/A/var/lines/Headquarters.line ] && [ "$(stat -c %a $T/A/var/lines)" = 700 ] && echo 0 || echo 1) "2 one line per level, kept in $T/A/var/lines ($(ls $T/A/var/lines | tr '\n' ' '), mode $(stat -c %a $T/A/var/lines))"
for i in $(seq 100); do grep -q "SPACE EOC Admin: its RAM manager on" $T/A/org.log && grep -q "SPACE Responders: its RAM manager on" $T/A/org.log && break; sleep 0.1; done
# ---- 3 devices join with the printed lines
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
for n in r q; do mkdir -p $T/$n/run-wg; NS $n ip route add default via 198.18.0.254 dev v-$n 2>/dev/null; done
DEV r python3 -u $T/bin/fdf-login "$(cat "$T/A/var/lines/First Responders.line")" < /dev/null > $T/r/login.log 2>&1
check $? "3 r joined with First Responders' line, no password: $(grep 'logged in' $T/r/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
echo "chief wrong" | DEV q python3 -u $T/bin/fdf-login "$(cat $T/A/var/lines/Headquarters.line)" > $T/q/login-bad.log 2>&1; BAD=$?
check $([ $BAD != 0 ] && grep -q "wrong user or password" $T/q/login-bad.log && echo 0 || echo 1) "3 q's wrong password refused: $(grep -o 'refused: .*' $T/q/login-bad.log)"
echo "chief hunter2" | DEV q python3 -u $T/bin/fdf-login "$(cat $T/A/var/lines/Headquarters.line)" > $T/q/login.log 2>&1
check $? "3 q joined with Headquarters' line as chief: $(grep 'logged in' $T/q/login.log | sed 's/^\[LOGIN\] [0-9:]* //')"
# ---- 4 the applications in their spaces
cat > $T/bin/app.py <<'EOF'
import sys, time
sys.path.insert(0, sys.argv[2])
import fdfspace
for name in sys.argv[4:]:
    try:
        sp = fdfspace.from_login(name, sys.argv[3], me=sys.argv[1] + "/" + name)
    except ValueError as e:
        print("NOT IN %s" % name, flush=True); continue
    sp.on_change = (lambda nm: lambda old, new: print("CHANGE %s %s" % (nm, new), flush=True))(name)
    sp.start()
time.sleep(3600)
EOF
for n in r q; do svc $T/$n/app.log ip netns exec $n env PYTHONPATH=$FDFPY python3 -u $T/bin/app.py $n $FDFPY $T/$n/login.json Responders "EOC Admin"; done
for i in $(seq 80); do grep -q "CHANGE EOC Admin hubA" $T/q/app.log && grep -q "CHANGE Responders hubA" $T/q/app.log && grep -q "CHANGE Responders hubA" $T/r/app.log && break; sleep 0.25; done
check $(grep -q "CHANGE EOC Admin hubA" $T/q/app.log && grep -q "CHANGE Responders hubA" $T/q/app.log && grep -q "CHANGE Responders hubA" $T/r/app.log && grep -q "NOT IN EOC Admin" $T/r/app.log && echo 0 || echo 1) \
  "4 q's application in EOC Admin and Responders, r's in Responders only ($(cat $T/q/app.log $T/r/app.log | grep -E 'CHANGE|NOT IN' | tr '\n' ';'))"
# ---- 5 the wizard again, adding nothing
cp "$T/A/var/lines/First Responders.line" $T/l1; cp $T/A/var/lines/Headquarters.line $T/l2
printf '\n\n\nn\n' | INA env FDF_SETUP_NO_SYSTEMD=1 FDF_ORG=$T/bin/fdf-org python3 -u $T/bin/fdf-setup > $T/setup2.log 2>&1; RC=$?
check $([ $RC = 0 ] && [ "$(INA python3 $T/bin/fdf-org list --json)" = "$J" ] && cmp -s $T/l1 "$T/A/var/lines/First Responders.line" && cmp -s $T/l2 $T/A/var/lines/Headquarters.line && echo 0 || echo 1) \
  "5 the wizard again, nothing added (rc $RC): everything kept, the same lines; it showed what is here: $(grep -c 'here now' $T/setup2.log) line(s)"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
