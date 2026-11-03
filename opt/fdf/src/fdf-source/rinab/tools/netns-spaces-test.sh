#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-spaces-test.sh -- [SPACES_V1] ORGANIZATIONS.md plan step 5: applications join RAM spaces, not devices. Two
# spaces on two RAM hosts (A primary, B standby), each space its own RAM manager on each (fdf-org space), each with its
# own list and its own moves. Levels: Responders joins the space Responders; HQ joins Responders and Admin.
#   A 198.18.0.1, B 198.18.0.3   RAM hosts: fdf-hub, the region's fdf-ram, fdf-org serve (which runs each space's)
#   r 198.18.0.5   a Responders device; q 198.18.0.7 an HQ device; one application on each, in every space it can join
# Checks:
#   0  each RAM host runs each space's RAM manager
#   1  r and q log in with their level's line; the answers say where each of their spaces is on each RAM host
#   2  q's application is in both spaces on A, r's in Responders only (its level joins no Admin, and it says so)
#   3  reach: q's edge reaches Admin's RAM manager on A, r's does not; r's reaches Responders'
#   4  each truth in its own space: q's in Admin and in Responders, r's in Responders only, nothing crossed
#   5  Admin's RAM manager on A cut off: q's Admin on B, q's and r's Responders still on A
#   6  the cut heals: q's Admin back on A
#   7  Admin's RAM manager on A killed: fdf-org says so and starts it again; q's Admin on A after it, its truth there
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-spaces-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-spaces}; rm -rf $T; mkdir -p $T/bin
API=/FawcettInnovations.RINA.ram_interface.php; HA=10.253.253.254; RAM=$HA:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NODES="A B r q"
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in $NODES; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
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
  printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n
  NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  ORG $n init > $T/$n/org.out 2>&1; ORG $n space Responders >> $T/$n/org.out 2>&1; ORG $n space Admin >> $T/$n/org.out 2>&1
  ORG $n level Responders --reach ram --space Responders >> $T/$n/org.out 2>&1
  ORG $n level HQ --reach ram --space Responders --space Admin >> $T/$n/org.out 2>&1
  printf '{"name":"host%s","guid":"guid-%s","capability":{"mem_total_kb":%d,"cores":4}}\n' $n $n $([ $n = A ] && echo 8000000 || echo 4000000) > $T/$n/node-facts.json   # [SPACE_BY_CAPABILITY_V1] A is the better
  svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_FACTS=$T/$n/node-facts.json FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY FDF_BIN=$T/bin python3 -u $T/bin/fdf-org serve"
done
for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 3 ] && grep -q "SPACE Admin: its RAM manager on" $T/$n/org.log && grep -q "SPACE Responders: its RAM manager on" $T/$n/org.log && break; sleep 0.1; done; done
sport() { ORG $1 list 2>/dev/null | awk -v s="$2" '$1=="space" && $2==s {n=split($NF,a,":"); print a[n]}'; }
PA_ADMIN=$(sport A Admin); PA_RESP=$(sport A Responders); PB_ADMIN=$(sport B Admin)
check $([ -n "$PA_ADMIN" ] && [ -n "$PA_RESP" ] && [ -n "$PB_ADMIN" ] && echo 0 || echo 1) "0 each RAM host runs each space's RAM manager: A Responders :$PA_RESP Admin :$PA_ADMIN; B Responders :$(sport B Responders) Admin :$PB_ADMIN"
LINE() { local lb; lb=$(ORG B line $1 2>>$T/B/line.err); ORG A line $1 --with $lb 2>>$T/A/line.err; }
L_r=$(LINE Responders); L_q=$(LINE HQ)
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
cat > $T/bin/app.py <<'EOF'
import sys, time
sys.path.insert(0, sys.argv[2])
import fdfspace
me, state, names = sys.argv[1], sys.argv[3], sys.argv[4:]
def mk(name):
    box = {}
    def changed(old, new):
        box["sp"].ram.write("app", "pos", me, {"me": me, "space": name, "on": new, "at": time.time()})
        print("CHANGE %.3f %s %s -> %s" % (time.time(), name, old, new), flush=True)
    try:
        box["sp"] = fdfspace.from_login(name, state, on_change=changed, me=me + "/" + name)
    except ValueError as e:
        print("NOT IN %s: %s" % (name, e), flush=True)
        return
    box["sp"].start()
for n in names:
    mk(n)
while True:
    time.sleep(3600)
EOF
cat > $T/bin/spaces.py <<'EOF'
import json, sys
st = json.load(open(sys.argv[1]))
ms = sorted(st["managers"].items(), key=lambda kv: kv[1]["order"])
if len(sys.argv) > 2:
    want = [["Responders"]] * 2 if sys.argv[2] == "r" else [["Responders", "Admin"]] * 2
    sys.exit(0 if [[s["name"] for s in m.get("spaces", [])] for _, m in ms] == want else 1)
print([(k, [(s["name"], s["ram"]) for s in m.get("spaces", [])]) for k, m in ms])
EOF
for n in r q; do mkdir -p $T/$n/run-wg; NS $n ip route add default via 198.18.0.254 dev v-$n 2>/dev/null
  L=$([ $n = r ] && echo "$L_r" || echo "$L_q")
  DEV $n python3 -u $T/bin/fdf-login "$L" < /dev/null > $T/$n/login.log 2>&1
  check $? "1 $n logged in: $(grep -E 'logged in' $T/$n/login.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';' | cut -c1-200)"
  check $(python3 $T/bin/spaces.py $T/$n/login.json $n && echo 0 || echo 1) "1 $n: where each of its spaces is, per RAM host: $(python3 $T/bin/spaces.py $T/$n/login.json 2>&1 | cut -c1-240)"
  svc $T/$n/app.log ip netns exec $n env PYTHONPATH=$FDFPY python3 -u $T/bin/app.py $n $FDFPY $T/$n/login.json Responders Admin
done
on() { grep "^CHANGE .* $2 " $T/$1/app.log 2>/dev/null | tail -1 | awk '{print $NF}'; }
waiton() { local want=$1 sp=$2 t0=$(date +%s.%N); shift 2
  for i in $(seq 160); do ok=1; for n in "$@"; do [ "$(on $n $sp)" = "$want" ] || ok=0; done; [ $ok = 1 ] && break; sleep 0.25; done
  echo "$ok $(python3 -c "print('%.1f' % ($(date +%s.%N) - $t0))")"; }
# a read's answer has a cell whose bag has KEY = VALUE (parsed: a bag's text is kept as its writer wrote it)
baghas() { python3 -c 'import json,sys; c=json.load(sys.stdin).get("cells") or []; sys.exit(0 if any((x.get("bag") or {}).get(sys.argv[1])==sys.argv[2] for x in c) else 1)' "$1" "$2" 2>/dev/null; }
cellin() { NS $1 curl -s -m 3 "http://$HA:$2$API?op=read&service=app&variable=pos&instance=${3%%/*}" | baghas space "${3#*/}" && echo yes || echo no; }
# 2
read OK1 S1 <<< "$(waiton hubA Responders r q)"; read OK2 S2 <<< "$(waiton hubA Admin q)"
check $([ $OK1 = 1 ] && [ $OK2 = 1 ] && echo 0 || echo 1) "2 q's application on A in Responders and Admin, r's in Responders ($S1 s, $S2 s)"
check $(grep -q "^NOT IN Admin" $T/r/app.log && ! grep -q "^CHANGE .* Admin " $T/r/app.log && echo 0 || echo 1) "2 r's level joins no Admin, and it says so: $(grep -m1 '^NOT IN' $T/r/app.log | cut -c1-140)"
# 3 reach
cat > $T/bin/probe.py <<'EOF'
import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"wg-own0"); s.settimeout(3)
try:
    s.connect((sys.argv[1], int(sys.argv[2]))); print("open")
except OSError:
    print("closed")
EOF
probe() { NS $1 python3 $T/bin/probe.py $HA $2; }
QA=$(probe q $PA_ADMIN); RA=$(probe r $PA_ADMIN); RR=$(probe r $PA_RESP)
check $([ "$QA" = open ] && [ "$RA" = closed ] && [ "$RR" = open ] && echo 0 || echo 1) "3 reach on A: q's edge to Admin $QA, r's to Admin $RA, r's to Responders $RR"
# 4 each truth in its own space
check $([ "$(cellin A $PA_ADMIN q/Admin)" = yes ] && [ "$(cellin A $PA_RESP q/Responders)" = yes ] && [ "$(cellin A $PA_RESP r/Responders)" = yes ] \
        && [ "$(cellin A $PA_ADMIN r/Admin)" = no ] && [ "$(cellin A $PA_ADMIN q/Responders)" = no ] && echo 0 || echo 1) \
  "4 each truth in its own space on A: q's in Admin and Responders, r's in Responders only, nothing crossed"
# 5 Admin's RAM manager on A cut off (its port only)
NS A nft add table inet cut; NS A nft add chain inet cut in '{ type filter hook input priority -10; }'; NS A nft add rule inet cut in tcp dport $PA_ADMIN drop
read OK S <<< "$(waiton hubB Admin q)"
check $([ $OK = 1 ] && [ "$(on q Responders)" = hubA ] && [ "$(on r Responders)" = hubA ] && echo 0 || echo 1) \
  "5 Admin on A cut off: q's Admin on B in $S s; q's Responders on $(on q Responders), r's on $(on r Responders): each space its own list"
for i in $(seq 40); do [ "$(cellin B $PB_ADMIN q/Admin)" = yes ] && break; sleep 0.25; done
check $([ "$(cellin B $PB_ADMIN q/Admin)" = yes ] && echo 0 || echo 1) "5 q's Admin truth written again on B"
# 6 the cut heals
NS A nft delete table inet cut; read OK S <<< "$(waiton hubA Admin q)"
check $([ $OK = 1 ] && echo 0 || echo 1) "6 the cut healed: q's Admin back on A in $S s"
# 7 Admin's RAM manager on A killed: fdf-org starts it again
N0=$(grep -c "SPACE Admin: its RAM manager on" $T/A/org.log); C0=$(grep -c "^CHANGE .* Admin " $T/q/app.log)
for p in $(ip netns pids A); do tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q "fdf-ram --listen $HA:$PA_ADMIN" && kill $p; done
ok=1; for i in $(seq 80); do [ "$(grep -c "SPACE Admin: its RAM manager on" $T/A/org.log)" -gt $N0 ] && { ok=0; break; }; sleep 0.25; done
check $ok "7 Admin's RAM manager on A killed: fdf-org said so and started it again ($(grep 'SPACE Admin: its RAM manager exited' $T/A/org.log | tail -1 | cut -c1-120))"
ok=1; for i in $(seq 160); do [ "$(grep -c "^CHANGE .* Admin " $T/q/app.log)" -gt $C0 ] && [ "$(on q Admin)" = hubA ] && [ "$(cellin A $PA_ADMIN q/Admin)" = yes ] && { ok=0; break; }; sleep 0.25; done
check $ok "7 q's Admin on A after it, its truth there again (moves: $(grep '^CHANGE .* Admin ' $T/q/app.log | tail -n +$((C0+1)) | awk '{print $4"->"$6}' | tr '\n' ' '))"
echo "--- q's application"; grep -E "CHANGE|NOT IN" $T/q/app.log | sed 's/^/    /' | tail -12
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
