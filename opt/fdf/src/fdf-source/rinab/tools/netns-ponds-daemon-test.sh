#!/bin/bash
# netns-ponds-daemon-test.sh -- [PONDS_V1] PLAN-PONDS §4 with the REAL tunnel daemon (r19's region loop): one node in two
# ponds, the forward one switched in place. Harness: tools/netns-region-loop-test.sh's (private /etc, /run/wireguard,
# /opt/fdf/var per node; the real daemon, fdf-facts, fdf-client).
#   ha 198.18.0.1  pond A: rina-host, fdf-hub (hubA, wg-ha :51820), its fdf-client
#   hb 198.18.0.2  pond B: rina-host, fdf-hub (hubB, wg-hb :51821), its fdf-client
#   a  198.18.0.5  an r19 node in pond A only, serves 10.7.7.0/24
#   b  198.18.0.6  an r19 node in pond B only, serves 10.8.8.0/24; posts the notice (fdf-attention)
#   n  198.18.0.3  the node in BOTH: serves 10.3.3.0/24; daemon + fdf-facts + fdf-client@pondA + fdf-client@pondB
# usage: tools/netns-ponds-daemon-test.sh [RINAB_DIR]   (FROGNET_DIR as netns-region-loop-test.sh; S=<switches> default 6)
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../repo/opt/frognet_semantic}
PK=${PKG_FDF:-$H/../../pkg-full/opt/fdf}; FDFPY=$H/../../repo/opt/fdf/lib/python; S=${S:-6}
T=${T:-/tmp/claude-0/netns-ponds}; rm -rf $T; mkdir -p $T/ha $T/hb $T/a $T/b $T/n $T/bin
export RIBBIT_TEMPLATES=$H/../../repo/opt/fdf/templates/fdf.prime FDF_WG_USERSPACE=1 FDF_PY=$FDFPY
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php; KEYA=keyA; KEYB=keyB; RAMA=198.18.0.1:8840; RAMB=198.18.0.2:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
NSS="ha hb n a b"
cleanup() { for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill -9 2>/dev/null; ip netns del $n 2>/dev/null; done
            ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
cp $B/fdf-events $B/fdf-client $B/fdf-attention $T/bin/; cp $PK/bin/fdf-facts $T/bin/
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
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ok\n")
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
EOF
# ---- the Internet
ip link add br-inet type bridge && ip link set br-inet up
for x in "ha 1" "hb 2" "n 3" "a 5" "b 6"; do set -- $x; ip netns add $1; ip netns exec $1 ip link set lo up
  ip link add v-$1 type veth peer name v-$1-b; ip link set v-$1 netns $1; ip link set v-$1-b master br-inet up
  ip netns exec $1 ip addr add 198.18.0.$2/24 dev v-$1; ip netns exec $1 ip link set v-$1 up; done
# ---- two ponds
pond() { local h=$1 ram=$2 area=$3 key=$4 port=$5
  ip netns exec $h env -u RINA_MAP_AREA RINA_REGION_FILE=$T/$h/area.perm $B/rina-host --listen $ram --api $API > $T/$h/host.log 2>&1 &
  sleep 1; ip netns exec $h curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$area\",\"key\":\"$key\"}" "http://$ram$API?op=rina" > /dev/null
  ip netns exec $h env RINA_RAM=$ram FDF_FACTS=$T/$h/facts.json $B/fdf-client > $T/$h/client.log 2>&1 &
  sleep 0.5
  ip netns exec $h env RINA_RAM=$ram FDF_BIN=$B python3 $H/tools/fdf-hub --area $area --iface wg-$h --endpoint ${ram%:*}:$port --name hub-$area \
     --key $T/$h/wg.key --listen $port --facts $T/$h/facts.json > $T/$h/hub.log 2>&1 &
  sleep 2; ip netns exec $h sysctl -q -w net.ipv4.ip_forward=1; }
pond ha $RAMA pondA $KEYA 51820; pond hb $RAMB pondB $KEYB 51821
check $([ "$(ip netns exec ha wg show wg-ha listen-port 2>/dev/null)" = 51820 ] && [ "$(ip netns exec hb wg show wg-hb listen-port 2>/dev/null)" = 51821 ] && echo 0 || echo 1) "two hubs up"
# ---- served /24s on lan0, default routes, echo servers
for x in "n 10.3.3.1" "a 10.7.7.1" "b 10.8.8.1"; do set -- $x
  ip netns exec $1 ip link add lan0 type veth peer name lan0p; ip netns exec $1 ip link set lan0 up; ip netns exec $1 ip link set lan0p up
  ip netns exec $1 ip addr add $2/24 dev lan0; ip netns exec $1 ip route add default via 198.18.0.1 dev v-$1
  ip netns exec $1 sysctl -q -w net.ipv4.ip_forward=1; ip netns exec $1 python3 $T/echo.py > /dev/null 2>&1 &
  : > $T/$1/leases; done
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
fsnode() { local n=$1 ram=$2 area=$3 key=$4; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$n" > $F/etc/fnid
  echo "domain=$n" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns ponds test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf
  printf 'export RINA_RAM=%s\n' $ram > $F/opt-fdf-var/join.env; printf 'RINA_AREA=%s\nRINA_SITE_KEY=%s\n' $area $key > $F/opt-fdf-var/site.env
  (umask 077; wg genkey > $F/var-lib-frognet-tunnel/node_private.key); wg pubkey < $F/var-lib-frognet-tunnel/node_private.key > $F/var-lib-frognet-tunnel/node_public.key; }
# NSF node cmd...: the node's netns AND its mount namespace; the daemon reads its pond from join.env/site.env (no RINA_* env)
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
client() { local n=$1 ram=$2 area=$3 key=$4 log=$5    # one pond's session (fdf-client@<area> on a node)
  NSF $n env RINA_RAM=$ram RINA_AREA=$area RINA_SITE_KEY=$key FDF_FORWARD_FILE=/opt/fdf/var/forward FDF_ATTENTION_DIR=/opt/fdf/var/attention \
      FDF_NAME=$n $T/bin/fdf-client $ram 2>&1 | python3 -u $T/stamp.py > $T/$n/$log & }
# a and b: plain r19 nodes, one pond each (no forward file: they behave as before)
fsnode a $RAMA pondA $KEYA; daemon a; client a $RAMA pondA $KEYA client.log
fsnode b $RAMB pondB $KEYB; daemon b; client b $RAMB pondB $KEYB client.log
# n: both ponds, A forward
fsnode n $RAMA pondA $KEYA; F=$T/n/fs/opt-fdf-var; mkdir -p $F/ponds
printf 'RINA_RAM=%s\nRINA_AREA=pondA\nRINA_SITE_KEY=%s\n' $RAMA $KEYA > $F/ponds/pondA.env
printf 'RINA_RAM=%s\nRINA_AREA=pondB\nRINA_SITE_KEY=%s\n' $RAMB $KEYB > $F/ponds/pondB.env
echo pondA > $F/forward
daemon n; DPID=$(ip netns pids n | while read p; do tr '\0' ' ' < /proc/$p/cmdline | grep -q "internet_tunnels_v3" && echo $p; done | head -1)
client n $RAMA pondA $KEYA client-A.log; client n $RAMB pondB $KEYB client-B.log
NPUB=$(cat $T/n/fs/var-lib-frognet-tunnel/node_public.key)
route() { ip netns exec $1 ip -4 route | grep -q "^$2"; }
peer() { ip netns exec $1 wg show wg-$1 peers 2>/dev/null | grep -q "$NPUB"; }
maps() { curl -s -X POST -d "{\"op\":\"map_list\",\"site\":\"$2\"}" "http://$1$API?op=rina" --interface v-n 2>/dev/null; }
mapped() { ip netns exec n curl -s -X POST -d "{\"op\":\"map_list\",\"site\":\"$2\"}" "http://$1$API?op=rina" | grep -q '10.3.3.0/24'; }
role_in() { ip netns exec n curl -s -X POST -d '{"op":"participants"}' "http://$1$API?op=rina" | python3 -c "import json,sys; r=[p['facts'].get('role','?') for p in json.load(sys.stdin)['participants'] if p['name']=='n' and p['alive']]; print(r[0] if r else 'absent')"; }
ok=1; for i in $(seq 120); do route n 10.7.7.0/24 && route a 10.3.3.0/24 && { ok=0; break; }; sleep 0.5; done
echo "=== (1) n forward in A (after $((i/2)) s)"
check $ok "n and a route each other's /24 (pond A's graph on n)"
check $(route n 10.8.8.0/24 && echo 1 || echo 0) "n has no route from pond B"
check $(peer ha && ! peer hb && echo 0 || echo 1) "hubA holds n's peer, hubB does not"
check $(mapped $RAMA pondA && ! mapped $RAMB pondB && echo 0 || echo 1) "n's /24 is mapped in A only"
check $([ "$(role_in $RAMA)" = node ] && [ "$(role_in $RAMB)" = participant ] && echo 0 || echo 1) "n is a node in A ($(role_in $RAMA)), a participant in B ($(role_in $RAMB))"
check $(ip netns exec n ping -c2 -W1 -q -I 10.3.3.1 10.7.7.1 >/dev/null 2>&1 && echo 0 || echo 1) "n -> a (10.7.7.1) over hubA"
echo "=== (2) a notice in pond B"
T0=$(ms); NSF b $T/bin/fdf-attention "match starting" > /dev/null 2>&1 < /dev/null || ip netns exec b env RINA_RAM=$RAMB FDF_FACTS=$T/b/fs/opt-fdf-var/facts.json $T/bin/fdf-attention "match starting" > /dev/null
for j in $(seq 200); do grep -q "ATTENTION pondB: match starting" $T/n/client-B.log && break; sleep 0.01; done
TA=$(grep -m1 "ATTENTION pondB" $T/n/client-B.log | awk '{print $1}')
check $([ -n "$TA" ] && echo 0 || echo 1) "n's fdf-client@pondB said it ($(( ${TA:-0} - T0 )) ms after fdf-attention started); file: $(cat $T/n/fs/opt-fdf-var/attention/pondB.json 2>/dev/null)"
check $(grep -q "ATTENTION" $T/n/client-A.log && echo 1 || echo 0) "the forward pond's client said nothing"
echo "=== (3) bring pond B forward (fdf-forward = forward.want)"
L0=$(wc -l < $T/n/daemon.log)
T0=$(ms); echo pondB > $F/forward.want.tmp && mv $F/forward.want.tmp $F/forward.want
for j in $(seq 500); do [ "$(cat $F/forward)" = pondB ] && break; sleep 0.01; done; TF=$(ms)
for j in $(seq 500); do peer ha || break; sleep 0.01; done; TA=$(ms)
for j in $(seq 1000); do peer hb && break; sleep 0.01; done; TB=$(ms)
for j in $(seq 1000); do route n 10.8.8.0/24 && break; sleep 0.01; done; TR=$(ms)
for j in $(seq 500); do ip netns exec n ping -c1 -W0.2 -q -I 10.3.3.1 10.8.8.1 >/dev/null 2>&1 && break; sleep 0.01; done; TP=$(ms)
echo "    forward.want -> forward written $((TF-T0)) ms; hubA dropped n $((TA-T0)) ms; hubB holds n $((TB-T0)) ms; B's route on n $((TR-T0)) ms; n -> b answered $((TP-T0)) ms"
tail -n +$((L0+1)) $T/n/daemon.log | grep "REGION: forward" | cut -d' ' -f2- | sed 's/^/    /'
check $(ip netns exec n ping -c2 -W1 -q -I 10.3.3.1 10.8.8.1 >/dev/null 2>&1 && echo 0 || echo 1) "n -> b (10.8.8.1) over hubB"
check $(route n 10.7.7.0/24 && echo 1 || echo 0) "pond A's route is gone from n (gone is gone)"
check $(peer hb && ! peer ha && echo 0 || echo 1) "hubB holds n's peer, hubA does not"
check $(mapped $RAMB pondB && ! mapped $RAMA pondA && echo 0 || echo 1) "n's /24 is mapped in B only"
check $([ "$(role_in $RAMB)" = node ] && [ "$(role_in $RAMA)" = participant ] && echo 0 || echo 1) "n is a node in B ($(role_in $RAMB)), a participant in A ($(role_in $RAMA))"
check $(for j in $(seq 200); do route b 10.3.3.0/24 && { echo 0; exit; }; sleep 0.05; done; echo 1) "b routes n's /24"
NOWPID=$(ip netns pids n | while read p; do tr '\0' ' ' < /proc/$p/cmdline | grep -q "internet_tunnels_v3" && echo $p; done | head -1)
check $([ "$NOWPID" = "$DPID" ] && echo 0 || echo 1) "the daemon was not restarted (pid $DPID)"
check $([ $(grep -c "joined the region" $T/n/client-A.log) = 1 ] && [ $(grep -c "joined the region" $T/n/client-B.log) = 1 ] && echo 0 || echo 1) "one session per pond throughout"
echo "=== (4) $S more switches"
: > $T/sw
for k in $(seq $S); do
  to=$([ $((k % 2)) = 1 ] && echo pondA || echo pondB); dst=$([ $to = pondA ] && echo 10.7.7.1 || echo 10.8.8.1)
  T0=$(ms); echo $to > $F/forward.want.tmp && mv $F/forward.want.tmp $F/forward.want
  okp=1; for j in $(seq 1000); do [ "$(cat $F/forward)" = $to ] && ip netns exec n ping -c1 -W0.2 -q -I 10.3.3.1 $dst >/dev/null 2>&1 && { okp=0; break; }; sleep 0.01; done
  TP=$(ms); [ $okp = 0 ] && echo $((TP-T0)) >> $T/sw || echo "  switch $k to $to: $dst did not answer"
done
echo "    forward.want -> the new pond's member answered, $S switches: $(sort -n $T/sw | tr '\n' ' ')ms"
check $([ $(wc -l < $T/sw) = $S ] && echo 0 || echo 1) "every switch answered"
LH=$([ $((S % 2)) = 1 ] && echo ha || echo hb); OH=$([ $LH = ha ] && echo hb || echo ha)   # the last switch's pond
check $(for j in $(seq 200); do peer $LH && ! peer $OH && { echo 0; exit; }; sleep 0.01; done; echo 1) "after them: only $LH holds n (the last forward pond)"
check $(grep -q '"pubkey"' $F/facts.json && grep -q '"guid"' $F/facts.json && echo 0 || echo 1) "facts.json still carries n's identity (name, guid, pubkey) after every writer"
NOWPID=$(ip netns pids n | while read p; do tr '\0' ' ' < /proc/$p/cmdline | grep -q "internet_tunnels_v3" && echo $p; done | head -1)
check $([ "$NOWPID" = "$DPID" ] && echo 0 || echo 1) "still the same daemon"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS") ($T)"
