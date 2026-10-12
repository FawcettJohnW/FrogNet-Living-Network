#!/bin/bash
# netns-reach-test.sh -- [REACH_V1] [NET_CONFIG_IS_A_TUPLE_V1] [IDENTITY_IN_TUPLES_V1] a node reaching a pond it is a
# PARTICIPANT in, as that pond's (pond, config, net) allows, with a 1:1 alias for what collides with its own network.
# Harness: netns-ponds-daemon-test.sh's (the real daemon, fdf-facts, fdf-client per pond, fdf-hub per pond).
#   ha 198.18.0.1  pond A: rina-host, fdf-hub                       (no net.json: reach none)
#   hb 198.18.0.2  pond B: rina-host (net.json reach all, map allowed), fdf-hub
#   hc 198.18.0.4  pond C: rina-host (net.json reach all, map REFUSED), fdf-hub
#   hd 198.18.0.9  pond D: rina-host, NO net.json                   (reach none)
#   a  198.18.0.5  node in A, serves 10.7.7.0/24
#   b  198.18.0.6  node in B, serves 10.8.8.0/24
#   c  198.18.0.7  node in B, serves 10.7.7.0/24 -- the SAME /24 as a: two networks that collide
#   d  198.18.0.8  node in C, serves 10.7.7.0/24
#   n  198.18.0.3  node, forward in A, serves 10.3.3.0/24; participant in B, C and D with FDF_REACH=1
#   e  198.18.0.11 joins A LATER serving 10.8.8.0/24 (b's /24 in B): n's reach must re-plan and alias it
#   hf 198.18.0.12 pond F: rina-host (net.json reach all, map allowed), fdf-hub
#   g  198.18.0.13 node in F, serves 10.7.7.0/24 -- the THIRD network with that /24 (A's a, B's c): n reaches B and F at once
#   lf 198.18.0.10 a leaf (the phone) in A: A gives it 10.252.0.1 -- the address B gives n [REACH_SELF_ALIAS_V1]
#   ph 198.18.0.14 stage (7), when BRIDGE_KT (fdf-member-kt) and FDF_TUN (fdf-tun) are given: the PHONE's bridge -- the Kotlin
#                  member, forward in A (a leaf), reaching B and F at once through fdf-tun; refused by C [BRIDGE_V1]
# usage: tools/netns-reach-test.sh [RINAB_DIR]
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../repo/opt/frognet_semantic}
PK=${PKG_FDF:-$H/../../pkg-full/opt/fdf}; FDFPY=$H/../../repo/opt/fdf/lib/python
T=${T:-/tmp/claude-0/netns-reach}; rm -rf $T; mkdir -p $T/ha $T/hb $T/hc $T/hd $T/hf $T/a $T/b $T/c $T/d $T/g $T/e $T/n $T/ph $T/bin
export RIBBIT_TEMPLATES=$H/../../repo/opt/fdf/templates/fdf.prime FDF_WG_USERSPACE=1 FDF_PY=$FDFPY
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php
RAMA=198.18.0.1:8840; RAMB=198.18.0.2:8840; RAMC=198.18.0.4:8840; RAMD=198.18.0.9:8840; RAMF=198.18.0.12:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
NSS="ha hb hc hd hf n a b c d lf e g ph"
cleanup() { for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill -9 2>/dev/null; ip netns del $n 2>/dev/null; done
            ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
cp $B/fdf-events $B/fdf-client $B/fdf-attention $B/fdf-where $T/bin/; cp $PK/bin/fdf-facts $PK/bin/fdf-reach $T/bin/
cat > $T/bin/capability-probe <<'EOF'
#!/bin/sh
echo '{"cores":4,"cpu_mhz":2000,"cpu_bench_total":12000,"mem_total_kb":8388608,"mem_available_kb":4000000,"disk_class":"ssd","mysql":true,"mysql_running":true,"ffmpeg":true,"libvpx":true,"lan_ip":"","loadavg":{"1":0.1,"5":0.1,"15":0.1},"temps_c":[]}'
EOF
chmod +x $T/bin/capability-probe
cat > $T/stamp.py <<'EOF'
import sys, time
for l in sys.stdin:
    sys.stdout.write("%d %s" % (int(time.time() * 1000), l)); sys.stdout.flush()
EOF
cat > $T/echo.py <<'EOF'
import http.server, sys
NAME = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):           # answers who it is and who asked: "<name> <client ip>"
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(("%s %s\n" % (NAME, self.client_address[0])).encode())
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
EOF
# ---- the Internet
ip link add br-inet type bridge && ip link set br-inet up
for x in "ha 1" "hb 2" "hc 4" "hd 9" "n 3" "a 5" "b 6" "c 7" "d 8" "lf 10" "e 11" "hf 12" "g 13" "ph 14"; do set -- $x; ip netns add $1; ip netns exec $1 ip link set lo up
  ip link add v-$1 type veth peer name v-$1-b; ip link set v-$1 netns $1; ip link set v-$1-b master br-inet up
  ip netns exec $1 ip addr add 198.18.0.$2/24 dev v-$1; ip netns exec $1 ip link set v-$1 up; done
# ---- ponds: a RAM manager (with its net.json, or none), its map site, and (hub=1) its hub
pond() { local h=$1 ram=$2 area=$3 key=$4 port=$5 netjson=$6 hub=$7
  [ -n "$netjson" ] && echo "$netjson" > $T/$h/net.json
  ip netns exec $h env -u RINA_MAP_AREA RINA_NET_CONFIG=$T/$h/net.json RINA_REGION_FILE=$T/$h/area.perm $B/rina-host --listen $ram --api $API > $T/$h/host.log 2>&1 &
  sleep 1; ip netns exec $h curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$area\",\"key\":\"$key\"}" "http://$ram$API?op=rina" > /dev/null
  [ "$hub" = 1 ] || return 0
  ip netns exec $h env RINA_RAM=$ram FDF_FACTS=$T/$h/facts.json $B/fdf-client > $T/$h/client.log 2>&1 &
  sleep 0.5
  ip netns exec $h env RINA_RAM=$ram FDF_BIN=$B python3 $H/tools/fdf-hub --area $area --iface wg-$h --endpoint ${ram%:*}:$port --name hub-$area \
     --key $T/$h/wg.key --listen $port --facts $T/$h/facts.json > $T/$h/hub.log 2>&1 &
  sleep 2; ip netns exec $h sysctl -q -w net.ipv4.ip_forward=1; }
pond ha $RAMA pondA keyA 51820 "" 1
pond hb $RAMB pondB keyB 51821 '{"reach":"all","map":"allowed"}' 1
pond hc $RAMC pondC keyC 51822 '{"reach":"all","map":"refused"}' 1
pond hd $RAMD pondD keyD 51823 "" 0
pond hf $RAMF pondF keyF 51824 '{"reach":"all","map":"allowed"}' 1
for h in ha hb hc hd; do grep NET_CONFIG $T/$h/host.log | sed "s/^/    $h: /"; done
# ---- served /24s, default routes, echo servers that say who they are
for x in "n 10.3.3.1" "a 10.7.7.1" "b 10.8.8.1" "c 10.7.7.1" "d 10.7.7.1" "g 10.7.7.1"; do set -- $x
  ip netns exec $1 ip link add lan0 type veth peer name lan0p; ip netns exec $1 ip link set lan0 up; ip netns exec $1 ip link set lan0p up
  ip netns exec $1 ip addr add $2/24 dev lan0; ip netns exec $1 ip route add default via 198.18.0.1 dev v-$1
  ip netns exec $1 sysctl -q -w net.ipv4.ip_forward=1; ip netns exec $1 python3 $T/echo.py $1 > /dev/null 2>&1 &
  : > $T/$1/leases; done
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
fsnode() { local n=$1 ram=$2 area=$3 key=$4; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$n" > $F/etc/fnid
  echo "domain=$n" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns reach test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf
  printf 'export RINA_RAM=%s\n' $ram > $F/opt-fdf-var/join.env; printf 'RINA_AREA=%s\nRINA_SITE_KEY=%s\n' $area $key > $F/opt-fdf-var/site.env
  (umask 077; wg genkey > $F/var-lib-frognet-tunnel/node_private.key); wg pubkey < $F/var-lib-frognet-tunnel/node_private.key > $F/var-lib-frognet-tunnel/node_public.key; }
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  ip netns exec $n env -u RINA_RAM -u RINA_AREA -u RINA_SITE_KEY FDF_UPLINK_PING=198.18.0.1 FDF_BIN=$T/bin \
     FDF_FACTS=/opt/fdf/var/facts.json FDF_LEASES=$T/$n/leases FDF_PY=$FDFPY FROGNET_CAPABILITY_PROBE=$T/bin/capability-probe \
     FROGNET_REGION_HOSTS=/var/lib/frognet/region_hosts PYTHONPATH=$FN \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
daemon() { local n=$1
  NSF $n python3 -u -m internet_tunnels_v3 2>&1 | python3 -u $T/stamp.py > $T/$n/daemon.log &
  for i in $(seq 40); do grep -q '"name"' $T/$n/fs/opt-fdf-var/facts.json 2>/dev/null && break; sleep 0.25; done
  NSF $n env FDF_NAME=$n python3 $T/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 & }
client() { local n=$1 ram=$2 area=$3 key=$4 log=$5; shift 5     # one pond's session (fdf-client@<area> on a node)
  NSF $n env RINA_RAM=$ram RINA_AREA=$area RINA_SITE_KEY=$key FDF_FORWARD_FILE=/opt/fdf/var/forward FDF_ATTENTION_DIR=/opt/fdf/var/attention \
      FDF_NAME=$n "$@" $T/bin/fdf-client $ram 2>&1 | python3 -u $T/stamp.py > $T/$n/$log & }
fsnode a $RAMA pondA keyA; daemon a; client a $RAMA pondA keyA client.log
fsnode b $RAMB pondB keyB; daemon b; client b $RAMB pondB keyB client.log
fsnode c $RAMB pondB keyB; daemon c; client c $RAMB pondB keyB client.log
fsnode d $RAMC pondC keyC; daemon d; client d $RAMC pondC keyC client.log
fsnode g $RAMF pondF keyF; daemon g; client g $RAMF pondF keyF client.log
fsnode n $RAMA pondA keyA; F=$T/n/fs/opt-fdf-var; mkdir -p $F/ponds
for x in "pondA $RAMA keyA" "pondB $RAMB keyB" "pondC $RAMC keyC" "pondD $RAMD keyD" "pondF $RAMF keyF"; do set -- $x
  printf 'RINA_RAM=%s\nRINA_AREA=%s\nRINA_SITE_KEY=%s\nFDF_REACH=1\n' $2 $1 $3 > $F/ponds/$1.env; done
echo pondA > $F/forward
daemon n
client n $RAMA pondA keyA client-A.log
route() { ip netns exec $1 ip -4 route | grep -q "^$2"; }
ok=1; for i in $(seq 120); do route n 10.7.7.0/24 && route a 10.3.3.0/24 && route b 10.7.7.0/24 && route c 10.8.8.0/24 && { ok=0; break; }; sleep 0.5; done
echo "=== (1) the networks (after $((i/2)) s)"
check $ok "pond A: n and a route each other; pond B: b and c route each other (c serves 10.7.7.0/24, as a does in A)"
# ---- a leaf in A (as netns-region-loop-test.sh's): A gives it 10.252.0.1, which B will give n
LPUB=$( (umask 077; wg genkey > $T/lf.key); wg pubkey < $T/lf.key)
ip netns exec lf wireguard-go wg-lf >/dev/null 2>&1; for i in $(seq 100); do ip netns exec lf wg show wg-lf >/dev/null 2>&1 && break; sleep 0.05; done
ip netns exec lf wg set wg-lf private-key $T/lf.key; ip netns exec lf ip link set wg-lf up
printf '{"role":"leaf","name":"phone","guid":"g-phone","pubkey":"%s"}\n' "$LPUB" > $T/lf.json
ip netns exec lf env RINA_RAM=$RAMA FDF_FACTS=$T/lf.json $B/fdf-client > $T/lf-client.log 2>&1 &
for i in $(seq 100); do grep -q "gave this leaf" $T/lf-client.log && break; sleep 0.05; done
LADDR=$(grep -m1 -o "gave this leaf [0-9./]*" $T/lf-client.log | awk '{print $4}'); LIP=${LADDR%/32}
HUBPUB=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pubkey"])' $T/ha/facts.json)
ip netns exec lf ip addr add $LADDR dev wg-lf; ip netns exec lf ip route add 10.0.0.0/8 dev wg-lf
for i in $(seq 200); do grep -q "$LPUB" $T/ha/facts.json && break; sleep 0.05; done
ip netns exec lf wg set wg-lf peer $HUBPUB endpoint 198.18.0.1:51820 allowed-ips 10.0.0.0/8 persistent-keepalive 25
okl=1; for i in $(seq 200); do route n "$LIP dev wg0" && { okl=0; break; }; sleep 0.05; done
check $okl "pond A's leaf has $LADDR (given by A); n routes it on wg0"
check $(ip netns exec n ping -c 2 -W 2 -q -I 10.3.3.1 $LIP > /dev/null 2>&1 && echo 0 || echo 1) "n -> A's leaf $LIP"
MAIN0=$(ip netns exec n ip -4 route show table main | sort)
echo "=== (2) n reaches pond B (reach all, map allowed)"
T0=$(ms); client n $RAMB pondB keyB client-B.log FDF_REACH=1
okr=1; for i in $(seq 300); do grep -q "reach into pondB (all, map allowed)" $T/n/client-B.log && { okr=0; break; }; sleep 0.05; done; TR=$(ms)
check $okr "n's fdf-client@pondB applied the reach ($((TR-T0)) ms after it started): $(grep -m1 -o 'reach into pondB.*' $T/n/client-B.log)"
grep "fdf-client:" $T/n/client-B.log | cut -d' ' -f2- | sed 's/^/    /' | head -8
NB=$(grep -m1 -o "gave this machine [0-9.]*" $T/n/client-B.log | awk '{print $4}')
get() { ip netns exec n curl -s -m 3 "http://$1:8080/" 2>/dev/null; }
check $([ "$(get 10.8.8.1 | awk '{print $1}')" = b ] && echo 0 || echo 1) "n -> 10.8.8.1:8080 answers b (pond B, no collision): $(get 10.8.8.1)"
check $([ "$(get 10.7.7.1 | awk '{print $1}')" = a ] && echo 0 || echo 1) "n -> 10.7.7.1:8080 answers a (its forward pond keeps 10.7.7.0/24): $(get 10.7.7.1)"
check $([ "$(get 100.64.0.1)" = "c $NB" ] && echo 0 || echo 1) "n -> 100.64.0.1:8080 answers c (pond B's 10.7.7.1, aliased 1:1), and c saw n as $NB, n's address in B: $(get 100.64.0.1)"
check $(ip netns exec b curl -s -m 3 "http://$NB:8080/" | grep -q "^n " && echo 0 || echo 1) "b -> $NB:8080 (n's address in B) answers n: $(ip netns exec b curl -s -m 3 http://$NB:8080/)"
check $([ "$NB" = "$LIP" ] && echo 0 || echo 1) "B gave n $NB -- the address A gave its leaf ($LIP): the same address, two networks"
check $(ip netns exec n ip -4 addr | grep -q " $NB/" && echo 1 || echo 0) "n holds no address $NB (it holds $(ip netns exec n ip -4 -o addr show dev $(ip netns exec n ip -o link | grep -o 'rch-[0-9a-f]*' | head -1) | awk '{print $4}') on its reach interface) [REACH_SELF_ALIAS_V1]"
check $(ip netns exec n ping -c 2 -W 2 -q -I 10.3.3.1 $LIP > /dev/null 2>&1 && echo 0 || echo 1) "n -> A's leaf $LIP still answers (A's, not n's own)"
RB=$(NSF n $T/bin/fdf-reach resolve pondB w:g-b:8080); RC=$(NSF n $T/bin/fdf-reach resolve pondB w:g-c:8080)
check $([ "$RB" = 10.8.8.1:8080 ] && [ "$RC" = 100.64.0.1:8080 ] && echo 0 || echo 1) "resolve on n: w:g-b:8080 -> $RB, w:g-c:8080 -> $RC (the alias) [IDENTITY_IN_TUPLES_V1]"
MAIN1=$(ip netns exec n ip -4 route show table main | sort)
check $([ "$MAIN0" = "$MAIN1" ] && echo 0 || echo 1) "n's main routing table is unchanged by the reach (pond A's daemon owns it)"
echo "=== (2a) every network on one screen: getPonds.php, the monitor's Networks section [PONDS_ARE_LISTED_V1]"
WWW=$H/../frognet/var/www/html
# the monitor as a node has it: the public repo's package with this tree's files over it (FROGNET_REPO = a checkout)
MON=$T/mon; mkdir -p $MON; cp -r ${FROGNET_REPO:-$H/../../../abuild/fullrepo}/usr/local/bin/frognet_monitor_py $MON/; cp $H/../frognet/usr/local/bin/frognet_monitor_py/*.py $MON/frognet_monitor_py/
NSF n php $WWW/getPonds.php > $T/ponds.json 2>&1
check $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); p={x["area"]:x for x in d["ponds"]}
ok = d["forward"]=="pondA" and p["pondA"]["forward"] and p["pondA"]["me"]["role"]=="node" and p["pondB"]["me"]["role"]=="reacher" \
     and p["pondB"]["reach"]["alias"]=={"100.64.0.0/24":"10.7.7.0/24"} and p["pondB"]["config"]=={"reach":"all","map":"allowed"} \
     and {m["name"] for m in p["pondB"]["members"] if m["alive"]} >= {"b","c","n"} and p["pondA"]["config"]["reach"]=="none"
sys.exit(0 if ok else 1)' $T/ponds.json && echo 0 || echo 1) "getPonds.php on n: A forward (n a node), B participant (n a reacher, its alias, B's config, b and c alive), A's config reach none"
(cd $MON && python3 -c 'import json,sys; sys.path.insert(0,"."); from frognet_monitor_py.draw_ponds import pond_lines; [print("    | "+t) for t,_ in pond_lines(json.load(open(sys.argv[1])))]' $T/ponds.json) 2>&1 | tee $T/monitor-ponds.txt
check $(grep -q "c .*node .*ONLINE .*10.7.7.1 -> 100.64.0.1" $T/monitor-ponds.txt && grep -q "pondB .*participant .*reacher .*reach all as" $T/monitor-ponds.txt && echo 0 || echo 1) "the monitor's Networks section shows pond B's members, c at its alias"
echo "=== (2b) pond A changes under the reach: e joins A serving 10.8.8.0/24, b's /24 in B"
ip netns exec e ip link add lan0 type veth peer name lan0p; ip netns exec e ip link set lan0 up; ip netns exec e ip link set lan0p up
ip netns exec e ip addr add 10.8.8.1/24 dev lan0; ip netns exec e ip route add default via 198.18.0.1 dev v-e
ip netns exec e sysctl -q -w net.ipv4.ip_forward=1; ip netns exec e python3 $T/echo.py e > /dev/null 2>&1 &
: > $T/e/leases; fsnode e $RAMA pondA keyA; TE0=$(ms); daemon e; client e $RAMA pondA keyA client.log
okp=1; for i in $(seq 400); do grep -q "aliased.*-> 10.8.8.0/24" $T/n/daemon.log && { okp=0; break; }; sleep 0.05; done; TE1=$(ms)
check $okp "n's daemon re-planned the reach when A's routes changed ($((TE1-TE0)) ms after e started): $(grep -o 'REACH: reach into pondB.*' $T/n/daemon.log | tail -1)"
A8=$(NSF n $T/bin/fdf-reach resolve pondB w:g-b:8080)
check $([ "$(get 10.8.8.1 | awk '{print $1}')" = e ] && echo 0 || echo 1) "n -> 10.8.8.1 now answers e (A's, its forward pond): $(get 10.8.8.1)"
check $([ "$(get ${A8%:*} | awk '{print $1}')" = b ] && echo 0 || echo 1) "b is reached at its alias: resolve w:g-b:8080 -> $A8 answers $(get ${A8%:*})"
check $([ "$(get 100.64.0.1 | awk '{print $1}')" = c ] && echo 0 || echo 1) "c keeps its alias 100.64.0.1: $(get 100.64.0.1)"
echo "=== (2c) bring pond B forward while n reaches it, then A again"
CBP=$(ip netns pids n | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && tr '\0' ' ' < /proc/$p/cmdline | grep -q "$RAMB" && echo $p; done | head -1)
echo pondB > $F/forward.want.tmp && mv $F/forward.want.tmp $F/forward.want
okf=1; for i in $(seq 400); do [ "$(cat $F/forward)" = pondB ] && ! ip netns exec n ip -o link | grep -q rch- && { okf=0; break; }; sleep 0.05; done
check $okf "B forward: the reach into B is gone (the daemon routes B now)"
sleep 3
check $(kill -0 $CBP 2>/dev/null && [ ! -e $F/reach/pondB.refused ] && echo 0 || echo 1) "fdf-client@pondB still holds its session, nothing refused (the forward pond is never refused)"
check $(for i in $(seq 200); do route n 10.8.8.0/24 && { echo 0; exit; }; sleep 0.05; done; echo 1) "n routes B's 10.8.8.0/24 as B's node now"
echo pondA > $F/forward.want.tmp && mv $F/forward.want.tmp $F/forward.want
okg=1; for i in $(seq 600); do [ "$(cat $F/forward)" = pondA ] && grep -q "\"area\": \"pondB\"" $F/reach/pondB.json 2>/dev/null && ip netns exec n ip -o link | grep -q rch- && { okg=0; break; }; sleep 0.05; done
check $okg "A forward again: the reach into B is applied again"
for i in $(seq 100); do [ "$(get 100.64.0.1 | awk '{print $1}')" = c ] && break; sleep 0.1; done
check $([ "$(get 100.64.0.1 | awk '{print $1}')" = c ] && echo 0 || echo 1) "c answers at its alias again: $(get 100.64.0.1)"
echo "=== (2d) two colliding ponds reached AT ONCE: B's c and F's g both serve 10.7.7.0/24 (as A's a does)"
pids_to() { ip netns pids n | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && tr '\0' ' ' < /proc/$p/cmdline | grep -q "$1" && echo $p; done | head -1; }
ALB0=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["alias"], sort_keys=True))' $F/reach/pondB.json)
client n $RAMF pondF keyF client-F.log FDF_REACH=1
okF=1; for i in $(seq 300); do grep -q "reach into pondF (all, map allowed)" $T/n/client-F.log && { okF=0; break; }; sleep 0.05; done
check $okF "n reaches F too: $(grep -m1 -o 'reach into pondF.*' $T/n/client-F.log)"
AF=$(python3 -c 'import json,sys; a=json.load(open(sys.argv[1]))["alias"]; print(next(k for k,v in a.items() if v=="10.7.7.0/24"))' $F/reach/pondF.json 2>/dev/null)
AB=$(python3 -c 'import json,sys; a=json.load(open(sys.argv[1]))["alias"]; print(next(k for k,v in a.items() if v=="10.7.7.0/24"))' $F/reach/pondB.json 2>/dev/null)
SB=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["self"])' $F/reach/pondB.json); SF=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["self"])' $F/reach/pondF.json 2>/dev/null)
NF=$(grep -m1 -o "gave this machine [0-9.]*" $T/n/client-F.log | awk '{print $4}')
check $([ -n "$AF" ] && [ "$AF" != "$AB" ] && [ "$SF" != "$SB" ] && echo 0 || echo 1) "each pond's 10.7.7.0/24 has its own alias (B: $AB, F: $AF) and each reach its own side here (B: $SB, F: $SF)"
GF=${AF%.0/24}.1; GB=${AB%.0/24}.1
check $([ "$(get $GF)" = "g $NF" ] && echo 0 || echo 1) "n -> $GF answers g (F's 10.7.7.1), which saw n as $NF, n's address in F: $(get $GF)"
check $([ "$(get $GB)" = "c $NB" ] && echo 0 || echo 1) "n -> $GB answers c (B's 10.7.7.1), which saw n as $NB: $(get $GB)"
check $([ "$(get 10.7.7.1 | awk '{print $1}')" = a ] && echo 0 || echo 1) "n -> 10.7.7.1 answers a (A, the forward pond): three networks, one /24, each reached: $(get 10.7.7.1)"
check $(ip netns exec g curl -s -m 3 "http://$NF:8080/" | grep -q "^n " && echo 0 || echo 1) "g -> $NF (n's address in F) answers n: $(ip netns exec g curl -s -m 3 http://$NF:8080/)"
RG=$(NSF n $T/bin/fdf-reach resolve pondF w:g-g:8080)
check $([ "$RG" = "$GF:8080" ] && echo 0 || echo 1) "resolve on n: w:g-g:8080 in F -> $RG (F's alias), w:g-c:8080 in B -> $(NSF n $T/bin/fdf-reach resolve pondB w:g-c:8080)"
# re-plan F (its member g leaves, then comes back): B's aliases do not move
GC=$(ip netns pids g | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && echo $p; done | head -1); kill $GC
okq=1; for i in $(seq 300); do python3 -c 'import json,sys; sys.exit(0 if "10.7.7.0/24" not in json.load(open(sys.argv[1]))["routes"] else 1)' $F/reach/pondF.json 2>/dev/null && { okq=0; break; }; sleep 0.05; done
ALB1=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["alias"], sort_keys=True))' $F/reach/pondB.json)
check $([ $okq = 0 ] && [ "$ALB1" = "$ALB0" ] && [ "$(get $GB)" = "c $NB" ] && echo 0 || echo 1) "g left F: F's reach re-planned without 10.7.7.0/24; B's aliases unchanged ($ALB1) and c still answers at $GB"
TG0=$(ms); client g $RAMF pondF keyF client.log
okr2=1; while [ $(( $(ms) - TG0 )) -lt 90000 ]; do [ "$(ip netns exec n curl -s -m 1 http://$GF:8080/ 2>/dev/null)" = "g $NF" ] && { okr2=0; break; }; sleep 0.2; done; TG1=$(ms)
echo "    g $([ $okr2 = 0 ] && echo answered || echo DID NOT answer) at $GF $((TG1-TG0)) ms after its fdf-client started again; F's reach: $(cut -d' ' -f2- $T/n/client-F.log | grep -o 'reach into pondF.*' | tail -1 | cut -c1-140)"
if [ $okr2 != 0 ]; then   # [DIAG-REACH] everything that decides the path g -> n's address in F, at the moment it failed
  echo "--- g routes";        ip netns exec g ip -4 route | sed 's/^/    /'
  echo "--- g wg";            ip netns exec g wg show 2>&1 | sed 's/^/    /'
  echo "--- hubF wg";         ip netns exec hf wg show 2>&1 | sed 's/^/    /'
  echo "--- n wg (reach)";    ip netns exec n wg show 2>&1 | sed -n '/rch-/,$p' | sed 's/^/    /'
  echo "--- g -> hub, g -> n_F"; ip netns exec g ping -c1 -W1 10.253.253.254 2>&1 | tail -2 | sed 's/^/    /'; ip netns exec g ping -c1 -W1 -I 10.7.7.1 $NF 2>&1 | tail -2 | sed 's/^/    /'
  echo "--- F participants";  ip netns exec n curl -s -X POST -d '{"op":"participants"}' "http://$RAMF$API?op=rina" | python3 -c 'import json,sys; [print("    ",p["name"],p["alive"],(p.get("facts") or {}).get("role"),(p.get("facts") or {}).get("addr","")) for p in json.load(sys.stdin)["participants"]]'
  echo "--- g daemon (last 12)"; tail -12 $T/g/daemon.log | cut -c1-240 | sed 's/^/    /'
  echo "--- g client";        cut -d' ' -f2- $T/g/client.log | tail -6 | sed 's/^/    /'
  echo "--- hubF log";        tail -8 $T/hf/hub.log | sed 's/^/    /'
fi
ALB2=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["alias"], sort_keys=True))' $F/reach/pondB.json)
check $([ $okr2 = 0 ] && [ "$ALB2" = "$ALB0" ] && echo 0 || echo 1) "g back in F: F's reach re-planned, g answers at $GF again; B's aliases still $ALB2"
# remove F's reach: B's is untouched
CF=$(pids_to "$RAMF"); IFF=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["names"]["if"])' $F/reach/pondF.json)
kill $CF; sleep 1.5
check $(ip netns exec n ip link show $IFF > /dev/null 2>&1 && echo 1 || echo 0) "n's session to F ended: its interface $IFF is gone"
check $([ ! -e $F/reach/pondF.json ] && [ -z "$(get $GF)" ] && echo 0 || echo 1) "F's state is gone and $GF answers nothing"
check $([ "$(get $GB)" = "c $NB" ] && [ "$(get 10.8.8.1 | awk '{print $1}')" = e ] && [ "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["alias"], sort_keys=True))' $F/reach/pondB.json)" = "$ALB0" ] && echo 0 || echo 1) "B's reach is untouched: c at $GB, its aliases $ALB0"
echo "=== (2e) the same network again after a session loss: the same alias [ALIAS_QUARANTINE_V1]"
check $(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); e=r.get(sys.argv[2]) or {}; sys.exit(0 if (e.get("area"),e.get("real"))==("pondF","10.7.7.0/24") else 1)' $F/reach/.released "$AF" && echo 0 || echo 1) "F's $AF is remembered as released: $(cat $F/reach/.released 2>/dev/null | cut -c1-200)"
client n $RAMF pondF keyF client-F2.log FDF_REACH=1
okF2=1; for i in $(seq 300); do grep -q "reach into pondF (all, map allowed)" $T/n/client-F2.log && { okF2=0; break; }; sleep 0.05; done
AF2=$(python3 -c 'import json,sys; a=json.load(open(sys.argv[1]))["alias"]; print(next(k for k,v in a.items() if v=="10.7.7.0/24"))' $F/reach/pondF.json 2>/dev/null)
check $([ $okF2 = 0 ] && [ "$AF2" = "$AF" ] && echo 0 || echo 1) "n reaches F again: F's 10.7.7.0/24 is at $AF2 again, as before the session loss"
for i in $(seq 100); do [ "$(get $GF)" = "g $NF" ] && break; sleep 0.1; done
check $([ "$(get $GF)" = "g $NF" ] && echo 0 || echo 1) "g answers at $GF again: $(get $GF)"
echo "=== (2f) resolve by identity alone: every network asked, exactly one may answer [RESOLVE_AMBIGUOUS_REFUSED_V1]"
RU=$(NSF n $T/bin/fdf-reach resolve w:g-c:8080); RA=$(NSF n $T/bin/fdf-reach resolve w:g-a:8080); RGU=$(NSF n $T/bin/fdf-reach resolve w:g-g:8080)
check $([ "$RU" = "$GB:8080" ] && [ "$RA" = "10.7.7.1:8080" ] && [ "$RGU" = "$GF:8080" ] && echo 0 || echo 1) "w:g-c:8080 -> $RU (B), w:g-a:8080 -> $RA (A, forward), w:g-g:8080 -> $RGU (F): three 10.7.7.1s, each named by identity"
# g-g joins B too (a leaf there, by its own key): the same identity in two networks
DPUB=$(wg genkey | wg pubkey); printf '{"role":"leaf","name":"g-again","guid":"g-g","pubkey":"%s"}\n' "$DPUB" > $T/dup.json
ip netns exec lf env RINA_RAM=$RAMB FDF_FACTS=$T/dup.json $B/fdf-client > $T/dup-client.log 2>&1 &
DUP=$!; for i in $(seq 200); do grep -q "gave this leaf" $T/dup-client.log && break; sleep 0.05; done
DIP=$(grep -m1 -o "gave this leaf [0-9.]*" $T/dup-client.log | awk '{print $4}')
for i in $(seq 300); do python3 -c 'import json,sys; sys.exit(0 if sys.argv[2]+"/32" in json.load(open(sys.argv[1]))["routes"] else 1)' $F/reach/pondB.json "$DIP" 2>/dev/null && break; sleep 0.05; done
RAMB_OUT=$(NSF n $T/bin/fdf-reach resolve w:g-g:8080); RAMB_RC=$?
check $([ $RAMB_RC = 7 ] && echo "$RAMB_OUT" | grep -q "ambiguous: in pondB as" && echo "$RAMB_OUT" | grep -q "in pondF as" && echo 0 || echo 1) "w:g-g in F and (as $DIP) in B: refused, exit $RAMB_RC: $RAMB_OUT"
check $([ "$(NSF n $T/bin/fdf-reach resolve pondF w:g-g:8080)" = "$GF:8080" ] && echo 0 || echo 1) "named with its network it resolves: resolve pondF w:g-g:8080 -> $(NSF n $T/bin/fdf-reach resolve pondF w:g-g:8080)"
kill $DUP; okd2=1; for i in $(seq 300); do [ "$(NSF n $T/bin/fdf-reach resolve w:g-g:8080 2>/dev/null)" = "$GF:8080" ] && { okd2=0; break; }; sleep 0.1; done
check $okd2 "the second g-g gone from B: w:g-g:8080 resolves alone again -> $(NSF n $T/bin/fdf-reach resolve w:g-g:8080)"
CF=$(pids_to "$RAMF"); kill $CF; sleep 1.5
check $(ip netns exec n ip -o link | grep -c rch- | grep -qx 1 && echo 0 || echo 1) "F's session ended again: one reach left (B's)"
echo "=== (3) gone is gone: n's session to pond B ends"
CB=$(ip netns pids n | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && tr '\0' ' ' < /proc/$p/cmdline | grep -q "$RAMB" && echo $p; done | head -1)
IFB=$(ip netns exec n ip -o link | grep -o "rch-[0-9a-f]*" | head -1)
kill $CB; sleep 1.5
check $(ip netns exec n ip link show $IFB > /dev/null 2>&1 && echo 1 || echo 0) "its interface $IFB is gone"
check $(ip netns exec n ip rule | grep -q "lookup 2[0-9][0-9][0-9][0-9]" && echo 1 || echo 0) "its rules are gone ($(ip netns exec n ip rule | grep -c 'lookup 2[0-9][0-9][0-9][0-9]'))"
check $(ip netns exec n iptables -w -t nat -S | grep -q FDFR && echo 1 || echo 0) "its NETMAP/SNAT chains are gone"
check $([ -e $F/reach/pondB.json ] && echo 1 || echo 0) "its state file is gone"
check $([ -z "$(get 100.64.0.1)" ] && echo 0 || echo 1) "100.64.0.1 answers nothing now"
echo "=== (4) pond C: reach all, but map REFUSED -- and C's 10.7.7.0/24 collides with A's"
client n $RAMC pondC keyC client-C.log FDF_REACH=1
okc=1; for i in $(seq 300); do grep -q "REACH REFUSED into pondC" $T/n/client-C.log && { okc=0; break; }; sleep 0.05; done
check $okc "refused, saying why: $(grep -m1 -o 'REACH REFUSED into pondC.*' $T/n/client-C.log)"
CC=$(ip netns pids n | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && tr '\0' ' ' < /proc/$p/cmdline | grep -q "$RAMC" && echo $p; done | head -1)
check $([ -z "$CC" ] && echo 0 || echo 1) "the session to pond C is closed (a failed reach closes it)"
check $(ip netns exec n curl -s -X POST -d '{"op":"participants"}' "http://$RAMC$API?op=rina" | grep -q '"name":"n"' && echo 1 || echo 0) "n is not in pond C's participants"
check $(ip netns exec n ip -o link | grep -q rch- && echo 1 || echo 0) "nothing of it was left on n"
echo "=== (5) pond D: no net.json -- reach none"
client n $RAMD pondD keyD client-D.log FDF_REACH=1
okd=1; for i in $(seq 200); do grep -q "REACH REFUSED into pondD" $T/n/client-D.log && { okd=0; break; }; sleep 0.05; done
check $okd "refused, saying why: $(grep -m1 -o 'REACH REFUSED into pondD.*' $T/n/client-D.log)"
echo "=== (6) a member may not rewrite a network's config"
W=$(ip netns exec n curl -s -X POST -d '{"service":"pond","variable":"config","instance":"net","bag":{"reach":"all","map":"allowed"}}' "http://$RAMC$API?op=write")
check $(echo "$W" | grep -q '"ok":false' && echo 0 || echo 1) "pond C refused the write: $W"
if [ -n "${BRIDGE_KT:-}" ] && [ -n "${FDF_TUN:-}" ]; then
echo "=== (7) the phone's bridge: the Kotlin member + fdf-tun, forward in A, reaching B and F at once (all three serve 10.7.7.0/24) [BRIDGE_V1]"
P=$T/ph; (umask 077; wg genkey > $P/key); PPUB=$(wg pubkey < $P/key)
printf '{"name":"ph","guid":"g-ph","pubkey":"%s"}\n' "$PPUB" > $P/facts.json; echo pondA > $P/forward
ip netns exec ph python3 $T/echo.py ph > /dev/null 2>&1 &
ip netns exec ph env FDF_PONDS=pondA=$RAMA,pondB=$RAMB,pondF=$RAMF,pondC=$RAMC FDF_FORWARD_FILE=$P/forward FDF_FACTS=$P/facts.json \
   FDF_KEY_FILE=$P/key FDF_TUN_BIN=$FDF_TUN FDF_TUN_NAME=fdft0 FDF_CTL_PORT=7999 $BRIDGE_KT bridge > $P/bridge.log 2>&1 &
KTP=$!
ctl() { ip netns exec ph python3 -c 'import socket,sys; s=socket.create_connection(("127.0.0.1",7999),timeout=20); s.sendall((sys.argv[1]+"\n").encode()); print(s.makefile().readline().strip())' "$1" 2>&1; }
pget() { ip netns exec ph curl -s -m 3 "http://$1/" 2>/dev/null; }
T7=$(ms); ok7=1; for i in $(seq 600); do grep -q "bridge: forward via hub" $P/bridge.log && grep -q "reach into pondB (all, map allowed)" $P/bridge.log && grep -q "reach into pondF (all, map allowed)" $P/bridge.log && { ok7=0; break; }; sleep 0.1; done
check $ok7 "the phone is up in $(( $(ms) - T7 )) ms: $(grep -m1 -o 'bridge: build.*' $P/bridge.log)"
PA=$(grep -o "bridge: forward via hub [^ ]* as [0-9.]*" $P/bridge.log | tail -1 | awk '{print $NF}')
# [PLAN_UNDER_THE_BRIDGE_LOCK_V1] B and F joined at the same moment: no alias block was ever applied to both
check $(python3 - $P/bridge.log <<'EOF' && echo 0 || echo 1
import re, sys
cur = {}; bad = []
for ln in open(sys.argv[1]):
    m = re.search(r"bridge: reach into (\S+) .*?; aliased (.*)$", ln)
    if not m: continue
    cur[m.group(1)] = {a.split(" -> ")[0] for a in m.group(2).strip().split(", ")}
    for o, al in cur.items():
        if o != m.group(1) and al & cur[m.group(1)]: bad.append((m.group(1), o, al & cur[m.group(1)]))
print("    overlaps:", bad or "none", file=sys.stderr); sys.exit(1 if bad else 0)
EOF
) "B and F, planned at once: no alias block ever held by both"
PB=$(grep -o "reach into pondB ([^)]*) as [0-9.]*" $P/bridge.log | tail -1 | awk '{print $NF}'); PF=$(grep -o "reach into pondF ([^)]*) as [0-9.]*" $P/bridge.log | tail -1 | awk '{print $NF}')
grep -e "bridge:" -e "REACH" $P/bridge.log | cut -d' ' -f3- | cut -c1-220 | sed 's/^/    /' | head -12
okc7=1; for i in $(seq 300); do grep -q "REACH REFUSED into pondC" $P/bridge.log && grep -q "\[pondC\] not rejoined" $P/bridge.log && { okc7=0; break; }; sleep 0.05; done
check $okc7 "C (map refused, colliding) refused the phone and its session is closed: $(grep -m1 -o 'REACH REFUSED into pondC.*' $P/bridge.log | cut -c1-200)"
check $(ip netns exec ph curl -s -X POST -d '{"op":"participants"}' "http://$RAMC$API?op=rina" | grep -q '"name":"ph"' && echo 1 || echo 0) "the phone is not in C's participants"
for i in $(seq 100); do [ "$(pget 10.7.7.1:8080)" = "a $PA" ] && break; sleep 0.1; done
check $([ "$(pget 10.7.7.1:8080)" = "a $PA" ] && echo 0 || echo 1) "phone -> 10.7.7.1 answers a (A, forward), which saw the phone as $PA, its address in A: $(pget 10.7.7.1:8080)"
RC7=$(ctl "resolve w:g-c:8080"); RG7=$(ctl "resolve w:g-g:8080"); RA7=$(ctl "resolve w:g-a:8080")
ASK=$(ip netns exec ph env FDF_CTL_PORT=7999 $BRIDGE_KT ask resolve w:g-c:8080 2>/dev/null | grep -v JAVA_TOOL)
check $([ "$ASK" = "$RC7" ] && echo 0 || echo 1) "fdf-member-kt ask resolve w:g-c:8080 (what a terminal on Windows runs) -> $ASK"
# [COMMS_FROM_THE_POND_V1] c says it runs a Communicator (its where cell, by identity); the phone/Windows asks for B's
NSF c env RINA_RAM=$RAMB $T/bin/fdf-where comms ram=self:8080 media=self:8080 > $T/c-where.log 2>&1
for i in $(seq 50); do AC=$(ip netns exec ph env FDF_CTL_PORT=7999 $BRIDGE_KT ask comms pondB 2>/dev/null | grep -v JAVA_TOOL); echo "$AC" | grep -q "^OK" && break; sleep 0.2; done
ACR=$(echo "$AC" | awk '{print $3}')
check $(echo "$AC" | grep -q "^OK --ram ${RC7#OK } --media ${RC7#OK } " && [ "$(pget $ACR | awk '{print $1}')" = c ] && echo 0 || echo 1) "ask comms pondB -> $AC; its ram answers $(pget $ACR | awk '{print $1}')"
for i in $(seq 100); do [ "$(pget ${RC7#OK })" = "c $PB" ] && break; sleep 0.1; done
check $([ "$RA7" = "OK 10.7.7.1:8080" ] && [ "$(pget ${RC7#OK })" = "c $PB" ] && [ "$(pget ${RG7#OK })" = "g $PF" ] && echo 0 || echo 1) "by identity: w:g-a -> ${RA7#OK }, w:g-c -> ${RC7#OK } answers '$(pget ${RC7#OK })', w:g-g -> ${RG7#OK } answers '$(pget ${RG7#OK })': three 10.7.7.1s at once"
ACA=${RC7#OK }; ACA=${ACA%:*}; AGA=${RG7#OK }; AGA=${AGA%:*}
check $([ "$(ip netns exec c curl -s -m 3 http://$PB:8080/)" = "ph $ACA" ] && [ "$(ip netns exec g curl -s -m 3 http://$PF:8080/)" = "ph $AGA" ] && [ "$(ip netns exec a curl -s -m 3 http://$PA:8080/)" = "ph 10.7.7.1" ] && echo 0 || echo 1) \
  "into the phone: c -> $PB sees '$(ip netns exec c curl -s -m 3 http://$PB:8080/)', g -> $PF sees '$(ip netns exec g curl -s -m 3 http://$PF:8080/)', a -> $PA sees '$(ip netns exec a curl -s -m 3 http://$PA:8080/)'"
CP7=""; for d in "10.7.7.1:8080 a" "${RC7#OK } c" "${RG7#OK } g"; do set -- $d; (for i in $(seq 100); do pget $1 | awk '{print $1}'; done > $T/ph/conc-$2) & CP7="$CP7 $!"; done; wait $CP7
check $(for s in a c g; do [ "$(sort -u $T/ph/conc-$s)" = "$s" ] && [ $(wc -l < $T/ph/conc-$s) = 100 ] || echo bad; done | grep -q bad && echo 1 || echo 0) "concurrent: 300 requests in 3 streams, each answered by its own network ($(for s in a c g; do echo -n "$s:$(grep -c . $T/ph/conc-$s) "; done))"
# the same identity in two of the phone's networks: refused, naming both
DPUB2=$(wg genkey | wg pubkey); printf '{"role":"leaf","name":"g-again","guid":"g-g","pubkey":"%s"}\n' "$DPUB2" > $T/dup2.json
ip netns exec lf env RINA_RAM=$RAMB FDF_FACTS=$T/dup2.json $B/fdf-client > $T/dup2-client.log 2>&1 &
DUP2=$!; for i in $(seq 200); do grep -q "gave this leaf" $T/dup2-client.log && break; sleep 0.05; done
for i in $(seq 200); do ctl "resolve w:g-g:8080" | grep -q "^AMBIGUOUS" && break; sleep 0.1; done
AMB=$(ctl "resolve w:g-g:8080")
check $(echo "$AMB" | grep -q "^AMBIGUOUS w:g-g is ambiguous: in pondB as .*; in pondF as 10.7.7.1" && echo 0 || echo 1) "w:g-g in B and in F: $AMB"
kill $DUP2; okd7=1; for i in $(seq 200); do [ "$(ctl 'resolve w:g-g:8080')" = "$RG7" ] && { okd7=0; break; }; sleep 0.1; done
check $okd7 "the second g-g gone: w:g-g resolves alone again -> $(ctl 'resolve w:g-g:8080')"
# session loss: B's RAM manager unreachable from the phone; the reach goes; back, the SAME alias [ALIAS_QUARANTINE_V1]
ALB7=$(ctl set | python3 -c 'import json,sys; t=sys.stdin.read()[3:]; print(json.dumps(next(i["alias"] for i in json.loads(t)["islands"] if i["area"]=="pondB"), sort_keys=True))')
ip netns exec ph iptables -w -I OUTPUT -d ${RAMB%:*} -p tcp --dport ${RAMB#*:} -j REJECT --reject-with tcp-reset
okl7=1; for i in $(seq 300); do grep -q "\[pondB\] reach into pondB taken down: the session ended" $P/bridge.log && { okl7=0; break; }; sleep 0.1; done
check $([ $okl7 = 0 ] && [ -z "$(pget ${RC7#OK })" ] && [ "$(pget ${RG7#OK } | awk '{print $1}')" = g ] && echo 0 || echo 1) "B's session lost: its reach is gone (${RC7#OK } answers nothing); F's untouched (g answers)"
ip netns exec ph iptables -w -D OUTPUT -d ${RAMB%:*} -p tcp --dport ${RAMB#*:} -j REJECT --reject-with tcp-reset
okb7=1; for i in $(seq 400); do [ "$(pget ${RC7#OK } | awk '{print $1}')" = c ] && { okb7=0; break; }; sleep 0.1; done
ALB8=$(ctl set | python3 -c 'import json,sys; t=sys.stdin.read()[3:]; print(json.dumps(next(i["alias"] for i in json.loads(t)["islands"] if i["area"]=="pondB"), sort_keys=True))' 2>/dev/null)
check $([ $okb7 = 0 ] && [ "$ALB8" = "$ALB7" ] && echo 0 || echo 1) "B back: the same aliases ($ALB8), c answers at ${RC7#OK } again"
# gone is gone: the member ends, fdf-tun with it, the TUN with that
kill $KTP; sleep 2
check $(ip netns exec ph ip link show fdft0 > /dev/null 2>&1 && echo 1 || echo 0) "the member ended: fdf-tun and its TUN are gone"
else echo "=== (7) SKIPPED: BRIDGE_KT and FDF_TUN not given"; fi
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
[ -n "${PAUSE:-}" ] && { echo "PAUSE: holding the namespaces ${PAUSE}s (T=$T)"; sleep $PAUSE; }
