#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-space-test.sh -- [SPACE_FAILOVER_V1][SPACE_BY_CAPABILITY_V1][RAM_ON_ITS_OWN_TUNNEL_V1] ORGANIZATIONS.md plan
# step 4 for devices: one space on two RAM hosts; the space is on the best alive one by capability (John 2026-11-03:
# "There is no fallback list. Fallback is determined by capability"; "failover/failup are automatic"), and a partition.
# Topology (the "Internet" is 198.18.0.0/24, a bridge):
#   A 198.18.0.1   a RAM host: its own region, fdf-hub, fdf-ram on 10.253.253.254:8840, fdf-org serve; 8 GB
#   B 198.18.0.3   the same, its own keys; 4 GB
#   r 198.18.0.5, q 198.18.0.7   devices: logged in at A and B with one line; each runs an application on the space
# Each host's fdf-org writes its capability into its RAM, (fdf-space, can, D). The application (fdfspace.Space) writes
# its truth (app, pos, <device>) when it starts and every time it is told the RAM host changed, and logs each change.
# Checks, each with what it saw and how long it took:
#   1  r and q log in at both with one line: two tunnels each, routes to the RAM address only (metrics 100, 101)
#   2  the applications are on A (the most memory), and their cells are at A
#   3  A's RAM manager dies: both on B within milliseconds (each device's CHANGE time minus the kill), truth at B
#   4  A comes back: both on A again (failup), within the time printed, truth at A
#   5  B dies: nothing moves (A is the best alive)
#   6  A and B both die: each on nothing (no fallback), said; B comes back: both on B; A comes back: both on A
#   7  a partition: q cut from A: q on B, r stays on A, both said; the partition heals: q back on A
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: tools/netns-space-test.sh [RINAB_DIR]
H=$(cd "$(dirname "$0")/.." && pwd); [ -n "$1" ] && H=$1
B=$H/build/static; PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-space}; rm -rf $T; mkdir -p $T/bin
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
# ---- two RAM hosts, each a region of its own (as fdf-region-create makes one)
hostup() { local n=$1; NS $n env RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/fdf-ram --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
           for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done; }
hostdown() { local n=$1; for p in $(ip netns pids $n); do grep -q "fdf-ram" /proc/$p/cmdline 2>/dev/null && kill $p; done
             for i in $(seq 50); do ip netns pids $n | xargs -r -I{} grep -l fdf-ram /proc/{}/cmdline 2>/dev/null | grep -q . || break; sleep 0.1; done; }
# (each RAM host's wireguard-go control sockets: fdf-org sees its hub there [HUB_IS_WHERE_IT_RUNS_V1])
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
for n in A B; do mkdir -p $T/$n/admitted $T/$n/run-wg; PUB=$(NS $n ip -4 -o addr show dev v-$n | awk '{print $4}' | cut -d/ -f1)
  KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'); printf '%s\n' "$KEY" > $T/$n/site-key-D; chmod 600 $T/$n/site-key-D
  printf 'RINA_RAM=%s\nFDF_AREA=D\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $n $PUB > $T/$n/hub.env
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n
  NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"D\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go python3 $T/bin/fdf-hub --area D --iface wg-D --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  ORG $n init > $T/$n/org.out 2>&1; ORG $n level Responders --reach ram >> $T/$n/org.out 2>&1
  printf '{"name":"host%s","guid":"guid-%s","capability":{"mem_total_kb":%d,"cores":4}}\n' $n $n $([ $n = A ] && echo 8000000 || echo 4000000) > $T/$n/node-facts.json
  svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_FACTS=$T/$n/node-facts.json FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY python3 -u $T/bin/fdf-org serve"
done
for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 2 ] && break; sleep 0.1; done; done
LB=$(NS B sh -c "mount --bind $T/B/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/B/hub.env FDF_ADMITTED=$T/B/admitted FDF_SITE_KEYS=$T/B python3 $T/bin/fdf-org line Responders" 2>$T/B/line.err)
LINE=$(NS A sh -c "mount --bind $T/A/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/A/hub.env FDF_ADMITTED=$T/A/admitted FDF_SITE_KEYS=$T/A python3 $T/bin/fdf-org line Responders --with $LB" 2>$T/A/line.err)
# ---- devices: one line, both RAM hosts; then the application
DEV() { local n=$1; shift; ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env PATH=$PATH FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=dev-$n FDF_LOGIN_STATE=$T/$n/login.json FDF_PY=$FDFPY \"\$@\"" sh "$@"; }
cat > $T/bin/app.py <<'EOF'
import json, sys, time
sys.path.insert(0, sys.argv[2])
import fdfram, fdfspace
me, st = sys.argv[1], json.load(open(sys.argv[3]))
mgrs = [dict(name=k, ram=v["ram"], iface=v["iface"]) for k, v in sorted(st["managers"].items(), key=lambda kv: kv[1]["order"])]
sp = None
def changed(old, new):
    sp.ram.write("app", "pos", me, {"me": me, "on": new, "at": time.time()})      # my truth, written again
    print("CHANGE %.3f %s -> %s" % (time.time(), old, new), flush=True)
sp = fdfspace.Space("Responders", mgrs, on_change=changed, me=me)
sp.start()
while True:
    time.sleep(3600)
EOF
for n in r q; do mkdir -p $T/$n/run-wg; NS $n ip route add default via 198.18.0.254 dev v-$n 2>/dev/null
  DEV $n python3 -u $T/bin/fdf-login "$LINE" < /dev/null > $T/$n/login.log 2>&1
  check $? "1 $n logged in at both with one line: $(grep -E 'logged in' $T/$n/login.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';' | cut -c1-240)"
  R=$(NS $n ip -4 route | grep wg-own | tr '\n' ';')
  check $([ "$(NS $n ip -4 route | grep -c 'dev wg-own')" = 2 ] && echo "$R" | grep -q "^$HA dev wg-own0.*metric 100" && echo "$R" | grep -q "$HA dev wg-own1.*metric 101" && echo 0 || echo 1) "1 $n's tunnels route the RAM address only: $R"
  svc $T/$n/app.log ip netns exec $n env PYTHONPATH=$FDFPY python3 -u $T/bin/app.py $n $FDFPY $T/$n/login.json
done
# where is device n's application now: the last CHANGE line, and its cell at that RAM manager
on() { grep '^CHANGE' $T/$1/app.log | tail -1 | awk '{print $NF}'; }
# where device n moved to after T0 (epoch s): its last CHANGE line after it ("" if it has not moved since)
since() { awk -v t0=$2 '$1=="CHANGE" && $2>t0 {w=$NF} END {print w}' $T/$1/app.log; }
waitsince() { local want=$1 t0=$2 s0=$(date +%s.%N); shift 2
  for i in $(seq 120); do ok=1; for n in "$@"; do [ "$(since $n $t0)" = "$want" ] || ok=0; done; [ $ok = 1 ] && break; sleep 0.25; done
  echo "$ok $(python3 -c "print('%.1f' % ($(date +%s.%N) - $s0))")"; }
waiton() { local want=$1 t0=$(date +%s.%N); shift
  for i in $(seq 120); do ok=1; for n in "$@"; do [ "$(on $n)" = "$want" ] || ok=0; done; [ $ok = 1 ] && break; sleep 0.25; done
  echo "$ok $(python3 -c "print('%.1f' % ($(date +%s.%N) - $t0))")"; }
# a read's answer has a cell whose bag has KEY = VALUE (parsed: a bag's text is kept as its writer wrote it)
baghas() { python3 -c 'import json,sys; c=json.load(sys.stdin).get("cells") or []; sys.exit(0 if any((x.get("bag") or {}).get(sys.argv[1])==sys.argv[2] for x in c) else 1)' "$1" "$2" 2>/dev/null; }
cellat() { NS $1 curl -s -m 3 "http://$RAM$API?op=read&service=app&variable=pos&instance=$2" | baghas me "$2" && echo yes || echo no; }
# 2 on A
read OK S <<< "$(waiton hubA r q)"
check $([ $OK = 1 ] && [ "$(cellat A r)" = yes ] && [ "$(cellat A q)" = yes ] && echo 0 || echo 1) "2 both applications on A, their truth at A ($S s)"
# when did device n's application move after T0 (epoch s): its first CHANGE line after it, in ms
moved_ms() { awk -v t0=$2 '$1=="CHANGE" && $2>t0 {printf "%d", ($2-t0)*1000; exit}' $T/$1/app.log; }
now() { date +%s.%N; }
# 3 A dies
T0=$(now); hostdown A; read OK S <<< "$(waiton hubB r q)"
check $([ $OK = 1 ] && [ "$(cellat B r)" = yes ] && [ "$(cellat B q)" = yes ] && echo 0 || echo 1) "3 A's RAM manager died: r on B $(moved_ms r $T0) ms, q $(moved_ms q $T0) ms after the kill; their truth written again at B"
# 4 A comes back: both on A again (failup)
T0=$(now); hostup A; read OK S <<< "$(waitsince hubA $T0 r q)"
check $([ $OK = 1 ] && [ "$(cellat A r)" = yes ] && [ "$(cellat A q)" = yes ] && echo 0 || echo 1) "4 A came back: r on A $(moved_ms r $T0) ms, q $(moved_ms q $T0) ms after it started; their truth at A"
# 5 B dies: nothing moves
hostdown B; sleep 3
check $([ "$(on r)" = hubA ] && [ "$(on q)" = hubA ] && echo 0 || echo 1) "5 B died: 3 s later both still on A (r $(on r), q $(on q))"
# 6 both die; B back; A back
hostup B; sleep 3; T0=$(now); hostdown A; hostdown B; sleep 2
NOT=$(for n in r q; do awk -v t0=$T0 '{print}' $T/$n/app.log | grep -c "no other RAM host is alive: on nothing"; done | tr '\n' ' ')
check $(for n in r q; do grep -q "no other RAM host is alive: on nothing" $T/$n/app.log || echo x; done | grep -q x && echo 1 || echo 0) "6 A and B died: each on nothing, said (r, q: $NOT)"
T0=$(now); hostup B; read OK S <<< "$(waitsince hubB $T0 r q)"; check $([ $OK = 1 ] && echo 0 || echo 1) "6 B came back: both on B in $S s"
T0=$(now); hostup A; read OK S <<< "$(waitsince hubA $T0 r q)"; check $([ $OK = 1 ] && echo 0 || echo 1) "6 A came back: both on A in $S s"
# 7 a partition: q's packets to A's endpoint are dropped
T0=$(now); NS q nft add table inet part; NS q nft add chain inet part out '{ type filter hook output priority 0; }'
NS q nft add rule inet part out ip daddr 198.18.0.1 drop
read OK S <<< "$(waitsince hubB $T0 q)"
check $([ $OK = 1 ] && [ "$(on r)" = hubA ] && echo 0 || echo 1) "7 partition: q (cut from A) on B $(moved_ms q $T0) ms after the cut (TCP keepalive: no socket closes), r still on A"
T0=$(now); NS q nft delete table inet part; read OK S <<< "$(waitsince hubA $T0 q)"
check $([ $OK = 1 ] && [ "$(on r)" = hubA ] && echo 0 || echo 1) "7 the partition healed: q back on A in $S s, r on A"
echo "--- r's application"; grep -E "SPACE|CHANGE" $T/r/app.log | sed 's/^/    /' | tail -20
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
exit $FAILS
