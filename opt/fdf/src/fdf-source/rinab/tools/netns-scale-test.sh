#!/bin/bash
# netns-scale-test.sh -- [SCALE_NETNS_V1] experiment S (experiments/day-claims/EXPERIMENTS.md): S subnets crossing E
# egress points, memory arm (the FD&F region), on the REAL tunnel daemon. Built from netns-region-loop-test.sh's
# pieces, unchanged: the hub (rina-host :8840, fdf-client, fdf-hub wg-hub :51820) on 198.18.0.1; every EGRESS node is
# a daemon node like z (its own netns and mount namespace, `python3 -m internet_tunnels_v3`, fdf-client, fdf-facts
# --watch, an uplink on the "Internet" bridge, a hub tunnel); every other subnet is a CHILD like y (fdf-client and
# fdf-facts --watch, a lease on its egress node's LAN, its own /24 on lan0, a default route to its egress node).
#   subnets 10.20.k.0/24, k = 1..S; egress node i serves 10.20.i.0/24 (i = 1..E); child k (k > E) sits on egress
#   ((k-1) mod E)+1's LAN with the lease 10.20.<egress>.<20+j>.
# Measured (one line each, prefixed RESULT so a driver can collect them):
#   cold:   start of the first daemon -> every egress node routes all S-1 other /24s, each by its expected next hop
#           (a child of its own via the child's lease, everything else over wg0), AND its region_hosts.json names
#           all S subnets' .1 (the last node to get there sets the time)
#   event:  N changes on child S's lan1 (10.21.0.1/24 added / removed); per change, change -> each daemon's
#           `REGION: event p timed` line; reported: time to the LAST daemon, p50 over daemons, and the daemons' own
#           world/bringup/apply/publish/total medians
#   size:   region participants and mappings (count, bytes), RSS of every process in the bed
#   reach:  pings from the .1 of up to 10 children/egress nodes to the .1 of others on OTHER egress nodes
# Two CPUs run every daemon here: an event wakes all E daemons on the same cores, so time-to-last grows with E from
# the bed's contention alone; each daemon's own `total` is the per-node cost. Both are reported.
# Needs root, ip, wg, wg-quick, wireguard-go, python3, curl. usage: S=200 E=10 N=4 tools/netns-scale-test.sh [RINAB_DIR]
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
PK=${PKG_FDF:-$H/../fdf}; FDFPY=$H/../fdf/lib/python; S=${S:-200}; E=${E:-2}; N=${N:-4}; COLD_MAX=${COLD_MAX:-600}
[ $E -ge 1 ] && [ $E -le 120 ] && [ $S -ge $E ] && [ $S -le 250 ] || { echo "need 1 <= E <= 120, E <= S <= 250"; exit 2; }
T=${T:-/tmp/claude-0/netns-scale-S$S-E$E}; rm -rf $T; mkdir -p $T/hub $T/bin      # one directory per (S,E): a run keeps its logs
export RIBBIT_TEMPLATES=$H/../fdf/templates/fdf.prime FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret; RAM=198.18.0.1:8840; U="http://$RAM$API?op=rina"
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
eg() { echo s-e$1; }; ch() { echo s-c$1; }                   # namespace names: egress s-e<i>, child s-c<k> (s-: never a name already in use)
egress_of() { echo $(( ($1 - 1) % E + 1 )); }
NSLIST() { echo s-hub; for i in $(seq $E); do eg $i; done; for k in $(seq $((E+1)) $S); do ch $k; done; }
cleanup() { local L; L=$(NSLIST); for n in $L; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in $L; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for i in $(seq 120); do ip link del v-s-e$i-b 2>/dev/null; ip link del br-s$i 2>/dev/null; done
            ip link del v-s-hub-b 2>/dev/null; ip link del br-s-inet 2>/dev/null; }
# [BED_INOTIFY_V1] every node here is the same kernel user, and each one's fdf-client and fdf-facts holds an inotify
# instance (the daemon more): the default 128 runs out near 70 children, which is this bed's limit, not a node's. Raised
# for the run, put back on exit.
INO0=$(cat /proc/sys/fs/inotify/max_user_instances); sysctl -q -w fs.inotify.max_user_instances=$(( INO0 > 8 * S ? INO0 : 8 * S ))
trap 'cleanup; sysctl -q -w fs.inotify.max_user_instances=$INO0' EXIT; cleanup
cp $B/fdf-events $B/fdf-client $B/fdf-where $T/bin/; cp $PK/bin/fdf-facts $T/bin/
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
echo "=== S=$S subnets, E=$E egress points, $((S-E)) children; $(date -u +%FT%TZ)"
# ---- the Internet: hub at .1, egress node i at 198.18.0.(2+i)
ip link add br-s-inet type bridge && ip link set br-s-inet up || { echo "FAIL  setup: br-s-inet exists"; exit 1; }
ip netns add s-hub; ip netns exec s-hub ip link set lo up
ip link add v-s-hub type veth peer name v-s-hub-b; ip link set v-s-hub netns s-hub; ip link set v-s-hub-b master br-s-inet up
ip netns exec s-hub ip addr add 198.18.0.1/24 dev v-s-hub; ip netns exec s-hub ip link set v-s-hub up
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=198.18.0.1 "$@"; }
NS s-hub env -u RINA_MAP_AREA RINA_REGION_FILE=$T/hub/area.perm $B/rina-host --listen $RAM --api $API > $T/hub/host.log 2>&1 &
sleep 1; NS s-hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "$U" > /dev/null
NS s-hub env FDF_FACTS=$T/hub/facts.json $B/fdf-client > $T/hub/client.log 2>&1 &
sleep 0.5
NS s-hub env FDF_BIN=$B python3 $H/tools/fdf-hub --area $SITE --iface wg-hub --endpoint 198.18.0.1:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json > $T/hub/hub.log 2>&1 &
sleep 2; NS s-hub sysctl -q -w net.ipv4.ip_forward=1
check $([ "$(NS s-hub wg show wg-hub listen-port 2>/dev/null)" = 51820 ] && echo 0 || echo 1) "hub up on 51820"
# ---- egress nodes: uplink, served /24 on its LAN bridge br-s<i> (children join it), NAT out, echo
for i in $(seq $E); do n=$(eg $i); mkdir -p $T/$n; ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-s-inet up
  ip netns exec $n ip addr add 198.18.0.$((2+i))/24 dev v-$n; ip netns exec $n ip link set v-$n up
  ip link add br-s$i type bridge; ip link set br-s$i up
  ip link add l-$n type veth peer name l-$n-b; ip link set l-$n netns $n; ip link set l-$n-b master br-s$i up
  ip netns exec $n ip addr add 10.20.$i.1/24 dev l-$n; ip netns exec $n ip link set l-$n up
  ip netns exec $n ip route add default via 198.18.0.1 dev v-$n
  ip netns exec $n iptables -t nat -A POSTROUTING -o v-$n -j MASQUERADE 2>/dev/null
  ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1
  ip netns exec $n python3 $T/echo.py > /dev/null 2>&1 &
  : > $T/$n/leases; done
# ---- children: on their egress node's LAN, a lease from it, own /24 on lan0
declare -A J
for k in $(seq $((E+1)) $S); do n=$(ch $k); e=$(egress_of $k); mkdir -p $T/$n; J[$e]=$(( ${J[$e]:-0} + 1 )); a=$((19 + ${J[$e]}))
  ip netns add $n; ip netns exec $n ip link set lo up
  ip link add l-$n type veth peer name l-$n-b; ip link set l-$n netns $n; ip link set l-$n-b master br-s$e up
  ip netns exec $n ip addr add 10.20.$e.$a/24 dev l-$n; ip netns exec $n ip link set l-$n up
  ip netns exec $n ip route add default via 10.20.$e.1 dev l-$n metric 600
  ip netns exec $n ip link add lan0 type veth peer name lan0p; ip netns exec $n ip link set lan0 up; ip netns exec $n ip link set lan0p up
  ip netns exec $n ip addr add 10.20.$k.1/24 dev lan0
  ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1
  printf '%d %s 10.20.%d.%d %s *\n' $(( $(date +%s) + 3600 )) "$(ip netns exec $n cat /sys/class/net/l-$n/address)" $e $a $n >> $T/$(eg $e)/leases
  : > $T/$n/leases; echo "$e 10.20.$e.$a" > $T/$n/egress; done
# ---- a daemon node's private filesystem (as netns-region-loop-test.sh)
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
fsnode() { local n=$1 NAME=$2; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$NAME" > $F/etc/fnid
  echo "domain=$NAME" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns scale test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf
  printf 'export RINA_RAM=%s\n' $RAM > $F/opt-fdf-var/join.env; printf 'RINA_AREA=%s\nRINA_SITE_KEY=%s\n' $SITE $KEY > $F/opt-fdf-var/site.env
  (umask 077; wg genkey > $F/var-lib-frognet-tunnel/node_private.key); wg pubkey < $F/var-lib-frognet-tunnel/node_private.key > $F/var-lib-frognet-tunnel/node_public.key; }
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  ip netns exec $n env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=198.18.0.1 FDF_BIN=$T/bin \
     FDF_FACTS=/opt/fdf/var/facts.json FDF_LEASES=$T/$n/leases FDF_PY=$FDFPY FROGNET_CAPABILITY_PROBE=$T/bin/capability-probe \
     FROGNET_REGION_HOSTS=/var/lib/frognet/region_hosts PYTHONPATH=$FN \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
for i in $(seq $E); do fsnode $(eg $i) $(eg $i); done
# ---- start: every daemon node, then every child (the clock starts at the first daemon)
T0=$(ms)
for i in $(seq $E); do n=$(eg $i)
  NSF $n python3 -u -m internet_tunnels_v3 2>&1 | python3 -u $T/stamp.py > $T/$n/daemon.log &
done
for i in $(seq $E); do n=$(eg $i)
  for w in $(seq 80); do grep -q '"name"' $T/$n/fs/opt-fdf-var/facts.json 2>/dev/null && break; sleep 0.25; done
  NSF $n env FDF_NAME=$n $T/bin/fdf-client > $T/$n/client.log 2>&1 &
  NSF $n env FDF_NAME=$n python3 $T/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 &
done
for k in $(seq $((E+1)) $S); do n=$(ch $k); read e ip < $T/$n/egress
  printf '{"role":"node","name":"%s","guid":"g-%s","subnet":"10.20.%d.0/24","pubkey":"","transit":[],"maps":[]}\n' $n $n $k > $T/$n/facts.json
  ip netns exec $n env RINA_RAM=$RAM FDF_FACTS=$T/$n/facts.json FDF_NAME=$n $B/fdf-client > $T/$n/client.log 2>&1 &
  ip netns exec $n env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_FACTS=$T/$n/facts.json FDF_LEASES=$T/$n/leases FDF_NAME=$n \
     FDF_UPLINK_PING=10.20.$e.1 python3 $PK/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 &
done
T1=$(ms); echo "started $E daemon nodes and $((S-E)) children in $((T1-T0)) ms"
# ---- cold: every egress node routes all S-1 other /24s and names all S
# [READY_MEANS_RIGHT_NEXT_HOP_V1] a route counts only with the expected next hop: a child of this node via its lease
# address on this node's LAN, every other subnet over the hub tunnel (wg0). A child routed over the hub is NOT ready.
for i in $(seq $E); do : > $T/$(eg $i)/expect; done
for k in $(seq $((E+1)) $S); do read e ip < $T/$(ch $k)/egress; echo "10.20.$k.0/24 via $ip dev l-$(eg $e)" >> $T/$(eg $e)/expect
  for i in $(seq $E); do [ $i = $e ] || echo "10.20.$k.0/24 dev wg0" >> $T/$(eg $i)/expect; done; done
for i in $(seq $E); do for j in $(seq $E); do [ $i = $j ] || echo "10.20.$j.0/24 dev wg0" >> $T/$(eg $i)/expect; done; done
right() { ip netns exec $1 ip -4 route | python3 -c 'import sys
exp=[l.split() for l in open(sys.argv[1]) if l.strip()]; got=[l.split() for l in sys.stdin]
def ok(e):
    for g in got:
        if g[0]==e[0] and all(t in g and g[g.index(t)+1]==v for t,v in zip(e[1::2],e[2::2])): return True
    return False
bad=[" ".join(e) for e in exp if not ok(e)]; print(len(exp)-len(bad), len(exp), *bad[:3], sep="|")' $T/$1/expect; }
ready() { local n=$1 i=$2 r h
  r=$(right $n); echo "$r" > $T/$n/right; [ "${r%%|*}" = "$(echo "$r" | cut -d'|' -f2)" ] && r=$S || r=0
  h=$(python3 -c 'import sys,json
try: print(len({x["ip"] for x in json.load(open(sys.argv[1])) if x["ip"].startswith("10.20.") and x["ip"].endswith(".1")}))
except Exception: print(0)' $T/$n/fs/var-lib-frognet/region_hosts.json)
  [ $r -ge $S ] && [ $h -ge $S ]; }          # S /24 routes: S-1 others plus its own connected 10.20.i.0/24
declare -A DONE; left=$E; while [ $left -gt 0 ] && [ $(( $(ms) - T0 )) -lt $((COLD_MAX*1000)) ]; do
  for i in $(seq $E); do [ -n "${DONE[$i]:-}" ] && continue; if ready $(eg $i) $i; then DONE[$i]=$(( $(ms) - T0 )); left=$((left-1)); fi; done
  sleep 0.5; done
TL=0; for i in $(seq $E); do v=${DONE[$i]:-}; [ -n "$v" ] && [ $v -gt $TL ] && TL=$v; done
# [DAEMON_CPU_V1] each daemon's CPU, not its wall time: utime+stime+cutime+cstime of the daemon process (its waited-for
# ip/wg children included), from /proc. Wall time on this bed includes waiting for a CPU; this does not.
TICK=$(getconf CLK_TCK); declare -A DPID
for i in $(seq $E); do for p in $(ip netns pids $(eg $i)); do tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q '^python3 -u -m internet_tunnels_v3' && DPID[$i]=$p; done; done
cpu_ms() { awk -v t=$TICK '{print int(($14+$15+$16+$17)*1000/t)}' /proc/$1/stat 2>/dev/null || echo 0; }
: > $T/cpu_cold; for i in $(seq $E); do echo "$i $(cpu_ms ${DPID[$i]:-0})" >> $T/cpu_cold; done
echo "daemon pids found: ${#DPID[@]}/$E"
python3 - $T/cpu_cold $S $E <<'PY'
import sys, statistics as st
v = [int(l.split()[1]) for l in open(sys.argv[1]) if len(l.split()) == 2]
print("RESULT cpu_cold S=%s E=%s per_daemon_p50_ms=%d max_ms=%d all_daemons_ms=%d" % (sys.argv[2], sys.argv[3], st.median(v), max(v), sum(v)) if v else "RESULT cpu_cold none")
PY
check $([ $left = 0 ] && echo 0 || echo 1) "cold: all $E egress nodes route all $S /24s and name all $S (last at $TL ms; $left not ready)"
[ $left = 0 ] || for i in $(seq $E); do [ -n "${DONE[$i]:-}" ] && continue; n=$(eg $i)
  echo "--- not ready: $n right/expected|first wrong: $(cat $T/$n/right) hosts $(python3 -c 'import sys,json;print(len(json.load(open(sys.argv[1]))))' $T/$n/fs/var-lib-frognet/region_hosts.json 2>&1)"
  tail -5 $T/$n/daemon.log | cut -c1-300 | sed 's/^/    /'
  # [DIAG-SCALE-MISS] the first wrong route's subnet: what that child logged, what the region holds for it, what this
  # node routes it by, and what the graph line says about links
  w=$(cut -d'|' -f3 $T/$n/right | awk '{print $1}'); k=$(echo "$w" | cut -d. -f3); c=$(ch $k)
  echo "--- [DIAG-SCALE-MISS] $w (child $c): $n routes it: $(ip netns exec $n ip -4 route | grep "^$w" | tr '\n' ' ')"
  for f in client.log facts.log; do echo "    $c/$f:"; sed 's/^/      /' $T/$c/$f 2>/dev/null | cut -c1-300 | tail -8; done
  NS s-hub curl -s -X POST -d '{"op":"participants"}' "$U" | python3 -c 'import sys,json
P=json.load(sys.stdin)["participants"]; x=[p for p in P if p.get("name")==sys.argv[1]]
print("    region:", json.dumps(x[0])[:600] if x else "NOT IN THE REGION")' $c
  grep "GRAPH build" $T/$n/daemon.log | tail -1 | grep -o "links=[0-9]*" | sed 's/^/    graph /'; break; done
echo "RESULT cold S=$S E=$E last_ms=$TL ready=$((E-left))/$E p50_ms=$(for i in $(seq $E); do echo ${DONE[$i]:-}; done | grep . | sort -n | awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}')"
# ---- reach: up to 10 sources, each to a .1 on another egress node's side
P=0; Q=0; for s in $(seq 1 $(( S > 10 ? S/10 : 1 )) $S | head -10); do d=$(( (s + S/2 - 1) % S + 1 ))
  [ $(egress_of $s) = $(egress_of $d) ] && d=$(( d % S + 1 )); src=$([ $s -le $E ] && eg $s || ch $s)
  Q=$((Q+1)); ip netns exec $src ping -c 2 -W 2 -I 10.20.$s.1 10.20.$d.1 > /dev/null 2>&1 && P=$((P+1)); done
check $([ $P = $Q ] && echo 0 || echo 1) "reach: $P/$Q pings between .1s on different egress nodes"
echo "RESULT reach S=$S E=$E ok=$P/$Q"
# ---- size: the region, and the bed
PJ=$(NS s-hub curl -s -X POST -d '{"op":"participants"}' "$U"); MJ=$(NS s-hub curl -s -X POST -d '{"op":"map_list"}' "$U")
echo "$PJ" > $T/participants.json; echo "$MJ" > $T/mappings.json
RSS=$(for n in $(NSLIST); do ip netns pids $n; done | xargs -r ps -o rss= -p 2>/dev/null | awk '{s+=$1} END{print int(s/1024)}')
echo "RESULT size S=$S E=$E participants_bytes=${#PJ} mappings_bytes=${#MJ} bed_rss_mb=$RSS"
# ---- event: N changes on the last child; each daemon's timed line
LAST=$(ch $S); [ $S -gt $E ] || LAST=$(eg $S)
ip netns exec $LAST ip link add lan1 type veth peer name lan1p; ip netns exec $LAST ip link set lan1 up; ip netns exec $LAST ip link set lan1p up
sleep 3; : > $T/samples; : > $T/cpu_event
for k in $(seq $N); do
  declare -A L0; for i in $(seq $E); do L0[$i]=$(wc -l < $T/$(eg $i)/daemon.log); done
  declare -A C0; for i in $(seq $E); do C0[$i]=$(cpu_ms ${DPID[$i]:-0}); done
  T_CHANGE=$(ms); if [ $((k % 2)) = 1 ]; then ip netns exec $LAST ip addr add 10.21.0.1/24 dev lan1; else ip netns exec $LAST ip addr del 10.21.0.1/24 dev lan1; fi
  declare -A GOT; got=0; while [ $got -lt $E ] && [ $(( $(ms) - T_CHANGE )) -lt 20000 ]; do
    for i in $(seq $E); do [ -n "${GOT[$i]:-}" ] && continue
      line=$(tail -n +$(( ${L0[$i]} + 1 )) $T/$(eg $i)/daemon.log | grep -m1 'REGION: event p timed')
      [ -z "$line" ] && continue; GOT[$i]=1; got=$((got+1)); t=${line%% *}
      echo "$k $i $((t - T_CHANGE)) $(echo "$line" | sed -E 's/.*world=([0-9]+)ms.*bringup=([0-9]+)ms apply=([0-9]+)ms.*publish=([0-9]+)ms total=([0-9]+)ms.*/\1 \2 \3 \4 \5/')" >> $T/samples; done
    sleep 0.05; done
  sleep 1                                              # the daemons finish the event (publish) before the CPU is read
  for i in $(seq $E); do echo "$k $i $(( $(cpu_ms ${DPID[$i]:-0}) - ${C0[$i]} ))" >> $T/cpu_event; done
  echo "  change #$k: $got/$E daemons applied"; unset GOT; unset L0; unset C0; sleep 2
done
python3 - $T/cpu_event $S $E <<'PY'
import sys, statistics as st, collections
r = [list(map(int, l.split())) for l in open(sys.argv[1]) if len(l.split()) == 3]
per = [x[2] for x in r]; byk = collections.defaultdict(int)
for k, i, c in r: byk[k] += c
print("RESULT cpu_event S=%s E=%s per_daemon_p50_ms=%d max_ms=%d all_daemons_per_change_p50_ms=%d" % (sys.argv[2], sys.argv[3], st.median(per), max(per), st.median(byk.values())) if r else "RESULT cpu_event none")
PY
python3 - $T/samples $S $E <<'PY'
import sys, statistics as st
rows = [list(map(int, l.split())) for l in open(sys.argv[1]) if len(l.split()) == 8]
S, E = sys.argv[2], int(sys.argv[3])
if not rows: print("RESULT event S=%s E=%d none" % (S, E)); sys.exit()
last = [max(r[2] for r in rows if r[0] == k) for k in sorted({r[0] for r in rows})]
per = [r[2] for r in rows]; cols = list(zip(*[r[3:] for r in rows]))
print("RESULT event S=%s E=%d changes=%d samples=%d to_last_daemon_p50_ms=%d max_ms=%d per_daemon_p50_ms=%d daemon_world=%d bringup=%d apply=%d publish=%d total=%d"
      % ((S, E, len(last), len(rows), st.median(last), max(last), st.median(per)) + tuple(int(st.median(c)) for c in cols)))
PY
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
