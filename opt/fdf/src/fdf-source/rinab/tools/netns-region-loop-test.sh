#!/bin/bash
# netns-region-loop-test.sh -- [REGION_LOOP_NETNS_V1] the REAL tunnel daemon's region loop (run_region_loop: world read,
# bringup, applier, publish, elections) on z and a, in network namespaces, against the real RAM manager and fdf-hub.
# Closes the gap apply_once.py leaves (it bypasses `bringup`), so the daemon's own timed line
#   REGION: event p timed: world=.. bringup=.. apply=.. publish=.. total=..
# is read here, without hardware. Topology as netns-latency-test.sh, with the "Internet" on 198.18.0.0/24 (not 10.x,
# so the daemon's own-subnet rule sees only the served /24):
#   hub 198.18.0.1 (fdf-hub wg-hub :51820, rina-host on its tunnel address 10.253.253.254:8840, fdf-client)
# [REGION_LOOP_BY_INVITE_V1] the region as fdf-region-create makes it since r31: the RAM manager listens only inside the
# hub's tunnel ([RAM_IN_THE_TUNNEL_V1]) and the hub peers only invited members ([ADMITTED_BY_INVITE_V1]). z and a join
# with fdf-invite's line (fdf-region-join --files-only: key, hub.json, site.env, join.env); y reaches the RAM through
# z's tunnel; the phone is an invited leaf (fdf-invite --leaf: its key and edge address from the invitation).
#   z   198.18.0.3, serves 10.3.3.0/24 on v-zy, gave y the lease 10.3.3.20; daemon + fdf-facts --watch + fdf-client
#   a   198.18.0.5, serves 10.7.7.0/24;                                     daemon + fdf-facts --watch + fdf-client
#   y   child of z, serves 10.2.2.0/24, no tunnel;                          fdf-facts --watch + fdf-client
# Each daemon runs in its own mount namespace: /etc/frognet, /etc/wireguard, /etc/sentinels, /etc/hosts, /etc/fnid,
# /etc/dnsmasq.d, /var/lib/frognet, /var/lib/frognet-tunnel, /run/frognet, /opt/fdf/var are per-node directories under
# $T/<node>/fs, bound over the real paths inside that namespace only.
# Then: N changes on y (an address added, then removed, alternately); for each, the timed line on a and on z, and the
# end-to-end time from the change to that line. Prints every sample and the median.
# Needs root, ip, wg, wg-quick, wireguard-go, python3, curl.  usage: tools/netns-region-loop-test.sh [RINAB_DIR]
#   FROGNET_DIR=<opt/frognet_semantic tree>  (default: this repo's opt/frognet_semantic)   N=<changes> (default 6)
#   NOKEY=<node> [DAEMON_OWNS_ITS_KEY_V1]: that node's key (from its invitation) is removed before its daemon starts
#   (and no broker keys in tunnel.conf, as every node here): its daemon must make the key, say so, announce its public
#   half; the hub does not peer it until the operator admits the new key (fdf-invite NAME again does; here its admitted
#   file gets the new public key), then its hub link comes up (pinged across the hub).
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
PK=${PKG_FDF:-$H/../fdf}; FDFPY=$H/../fdf/lib/python; N=${N:-6}
T=${T:-/tmp/claude-0/netns-region}; rm -rf $T; mkdir -p $T/hub/admitted $T/z $T/a $T/y $T/bin
export RIBBIT_TEMPLATES=$H/../fdf/templates/fdf.prime FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret; HA=10.253.253.254; RAM=$HA:8840; PUB=198.18.0.1; U="http://$RAM$API?op=rina"
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
# [CLEANUP_UNTIL_EMPTY_V1] a namespace outlives `ip netns del` while any process is still in it, and so do its veths'
# root-side ends: a process forked between one pid listing and the kill (fdf-comms' per-event fdf-where) kept the last
# run's v-z-b, the next run's `ip link add v-z` said "File exists", and z never reached the hub. So: kill until the
# namespace is empty, then delete it and every root-side end by name.
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in hub z a y lf; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in hub z a y lf; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for l in v-hub-b v-z-b v-a-b v-lf-b; do ip link del $l 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
cp $B/fdf-events $B/fdf-client $B/fdf-where $T/bin/; cp $PK/bin/fdf-facts $PK/bin/fdf-hub $PK/bin/fdf-invite $PK/bin/fdf-region-join $T/bin/
export PATH=$T/bin:$PATH
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
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in hub z a; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=198.18.0.1 "$@"; }
# ---- the region and the hub, as fdf-region-create makes them: the interface first, the RAM manager on its address
printf '%s\n' "$KEY" > $T/hub/site-key-$SITE; chmod 600 $T/hub/site-key-$SITE
printf 'RINA_RAM=%s\nFDF_AREA=%s\nFDF_HUB_NAME=hubA\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-hub\n' $RAM $SITE $PUB > $T/hub/hub.env
NS hub env FDF_BIN=$B python3 $T/bin/fdf-hub --area $SITE --iface wg-hub --endpoint $PUB:51820 --listen 51820 --key $T/hub/wg.key --iface-only > $T/hub/iface.log 2>&1
NS hub env -u RINA_MAP_AREA RINA_REGION_FILE=$T/hub/area.perm RINA_NET_CONFIG=$T/hub/none $B/rina-host --listen $RAM --api $API > $T/hub/host.log 2>&1 &
for i in $(seq 100); do NS hub curl -s -m 1 -X POST -d '{"op":"version"}' "$U" > /dev/null && break; sleep 0.05; done
NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "$U" > /dev/null
# every long-lived process restarts as its unit does (Restart=always, RestartSec=2)
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
svc $T/hub/client.log ip netns exec hub env RINA_RAM=$RAM FDF_FACTS=$T/hub/facts.json FDF_NAME=hubA $B/fdf-client
svc $T/hub/hub.log ip netns exec hub env RINA_RAM=$RAM FDF_BIN=$B python3 $T/bin/fdf-hub --area $SITE --iface wg-hub --endpoint $PUB:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json --admitted $T/hub/admitted
for i in $(seq 100); do grep -q "start (adopt)" $T/hub/hub.log && break; sleep 0.05; done
NS hub sysctl -q -w net.ipv4.ip_forward=1
check $([ "$(NS hub wg show wg-hub listen-port 2>/dev/null)" = 51820 ] && echo 0 || echo 1) "hub up on 51820"
# ---- invitations (fdf-invite, as the operator runs it on the hub): z, a, and the phone (a leaf)
inv() { NS hub env FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub python3 $T/bin/fdf-invite "$@"; }
for n in z a; do inv $n > $T/$n/invite.out 2>&1; grep -m1 '^fdfinv1\.' $T/$n/invite.out > $T/$n/invite; done
inv phone --leaf > $T/lf-invite.out 2>&1; grep -m1 '^fdfinv1\.' $T/lf-invite.out > $T/lf.invite
check $([ -s $T/z/invite ] && [ -s $T/a/invite ] && [ -s $T/lf.invite ] && echo 0 || echo 1) "invited z, a and the phone: $(head -1 $T/z/invite.out | cut -c1-90); $(head -1 $T/lf-invite.out | cut -c1-90)"
# ---- y behind z (z gave y 10.3.3.20), the served /24s on lan0
ip netns add y; ip netns exec y ip link set lo up
ip link add v-zy type veth peer name v-yz; ip link set v-zy netns z; ip link set v-yz netns y
ip netns exec z ip addr add 10.3.3.1/24 dev v-zy; ip netns exec z ip link set v-zy up
ip netns exec y ip addr add 10.3.3.20/24 dev v-yz; ip netns exec y ip link set v-yz up
ip netns exec y ip route add default via 10.3.3.1 dev v-yz metric 600
ip netns exec z iptables -t nat -A POSTROUTING -o v-z -j MASQUERADE 2>/dev/null
YMAC=$(ip netns exec y cat /sys/class/net/v-yz/address)
printf '%d %s 10.3.3.20 y *\n' $(( $(date +%s) + 3600 )) "$YMAC" > $T/z/leases; : > $T/a/leases; : > $T/y/leases
for n in y a; do ip netns exec $n ip link add lan0 type veth peer name lan0p; ip netns exec $n ip link set lan0 up; ip netns exec $n ip link set lan0p up; done
ip netns exec y ip addr add 10.2.2.1/24 dev lan0; ip netns exec a ip addr add 10.7.7.1/24 dev lan0
ip netns exec z ip route add default via 198.18.0.1 dev v-z; ip netns exec a ip route add default via 198.18.0.1 dev v-a
for n in z a y; do ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; ip netns exec $n python3 $T/echo.py > /dev/null 2>&1 & done
# ---- a node's private filesystem, bound over the real paths inside its mount namespace only
# /etc is a whole private copy (the election replaces /etc/hosts by rename, which a bound single file refuses: EBUSY);
# /run/wireguard is private so `wg show` sees this node's interfaces only (wireguard-go's UAPI sockets live there)
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
fsnode() { local n=$1 NAME=$2; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$NAME" > $F/etc/fnid
  echo "domain=$NAME" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns region-loop test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf
  NSF $n bash $T/bin/fdf-region-join --files-only "$(cat $T/$n/invite)" > $T/$n/join.log 2>&1
  check $? "$n: fdf-region-join --files-only: $(grep -m1 'invitation:' $T/$n/join.log | cut -c1-150)"
  [ "${NOKEY:-}" = "$n" ] && rm -f $F/var-lib-frognet-tunnel/node_private.key $F/var-lib-frognet-tunnel/node_public.key   # [DAEMON_OWNS_ITS_KEY_V1]
  return 0; }
# NSF node cmd...: run in the node's netns AND its mount namespace (ip netns exec gives a private one per call)
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  ip netns exec $n env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=198.18.0.1 FDF_BIN=$T/bin \
     FDF_FACTS=/opt/fdf/var/facts.json FDF_LEASES=$T/$n/leases FDF_PY=$FDFPY FROGNET_CAPABILITY_PROBE=$T/bin/capability-probe \
     FROGNET_REGION_HOSTS=/var/lib/frognet/region_hosts PYTHONPATH=$FN \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
daemon() { local n=$1 NAME=$2
  fsnode $n $NAME
  NSF $n python3 -u -m internet_tunnels_v3 2>&1 | python3 -u $T/stamp.py > $T/$n/daemon.log &      # each line: <epoch ms> <line>
  for i in $(seq 40); do grep -q '"name"' $T/$n/fs/opt-fdf-var/facts.json 2>/dev/null && break; sleep 0.25; done
  if [ "${NOKEY:-}" = "$n" ]; then        # the operator admits the key the daemon made (as fdf-invite NAME again would)
    K=$T/$n/fs/var-lib-frognet-tunnel/node_private.key; for i in $(seq 80); do [ -s $K ] && break; sleep 0.25; done
    python3 -I - $T/hub/admitted/$n.json "$(wg pubkey < $K)" <<'PY2'
import json, os, sys
r = json.load(open(sys.argv[1])); r["pubkey"] = sys.argv[2]
open(sys.argv[1] + ".tmp", "w").write(json.dumps(r)); os.replace(sys.argv[1] + ".tmp", sys.argv[1])
PY2
  fi
  svc $T/$n/client.log NSF $n env FDF_NAME=$NAME $(cat $T/$n/fs/opt-fdf-var/site.env) $T/bin/fdf-client
  NSF $n env FDF_NAME=$NAME python3 $T/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 & }
daemon z z; daemon a a
ip netns exec y env RINA_RAM=$RAM RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=10.3.3.1 \
   sh -c "printf '{\"role\":\"node\",\"name\":\"y\",\"guid\":\"g-y\",\"subnet\":\"10.2.2.0/24\",\"pubkey\":\"\",\"transit\":[],\"maps\":[]}\n' > $T/y/facts.json"
# y's client reaches the RAM through z's tunnel, so it starts again until z is up (its wrapper is stopped before y's end)
( while :; do ip netns exec y env RINA_RAM=$RAM FDF_FACTS=$T/y/facts.json FDF_NAME=y $B/fdf-client >> $T/y/client.log 2>&1; sleep 2; done ) & YW=$!; echo $YW >> $T/svc.pids
ip netns exec y env FDF_FACTS=$T/y/facts.json FDF_LEASES=$T/y/leases FDF_NAME=y FDF_UPLINK_PING=10.3.3.1 python3 $PK/bin/fdf-facts --watch > $T/y/facts.log 2>&1 &
# ---- converge: z routes a's /24 over its hub tunnel and y's over the LAN; a routes z's and y's over its tunnel
ok=1; for i in $(seq 120); do
  zr=$(ip netns exec z ip -4 route); ar=$(ip netns exec a ip -4 route)
  echo "$zr" | grep -q '^10.7.7.0/24' && echo "$zr" | grep -q '^10.2.2.0/24 via 10.3.3.20' && echo "$ar" | grep -q '^10.3.3.0/24' && echo "$ar" | grep -q '^10.2.2.0/24' && { ok=0; break; }
  sleep 0.5; done
check $ok "converged: z has a's /24 and y's (via y's lease); a has z's and y's (after $((i/2)) s)"
[ $ok = 0 ] || { for n in z a; do echo "--- $n routes"; ip netns exec $n ip -4 route | sed 's/^/    /'; echo "--- $n daemon (last 25)"; tail -25 $T/$n/daemon.log | sed 's/^/    /'; done
  # [DIAG-CONVERGE] everything the graph decides from, at the moment it did not converge: the region's records (what was
  # announced, not what the files say), every facts file, every client and facts-writer log, z's graph line history
  echo "--- region participants (as announced)"; NS hub curl -s -X POST -d '{"op":"participants"}' "$U" > $T/diag-participants.json; python3 -m json.tool $T/diag-participants.json | sed 's/^/    /'
  echo "--- region mappings"; NS hub curl -s -X POST -d '{"op":"map_list"}' "$U" | sed 's/^/    /'; echo
  for f in $T/y/facts.json $T/z/fs/opt-fdf-var/facts.json $T/a/fs/opt-fdf-var/facts.json; do echo "--- $f"; sed 's/^/    /' $f; done
  for n in y z a; do echo "--- $n client.log"; sed 's/^/    /' $T/$n/client.log; echo "--- $n facts.log"; sed 's/^/    /' $T/$n/facts.log; done
  echo "--- z GRAPH lines"; grep -E "GRAPH (build|membership|changed)" $T/z/daemon.log | cut -c1-400 | sed 's/^/    /'
  echo "--- y leases / z leases"; cat $T/y/leases $T/z/leases 2>&1 | sed 's/^/    /'; }
ip netns exec y ping -c 2 -W 2 -I 10.2.2.1 10.7.7.1 > $T/ping.log 2>&1; check $? "y -> a over z's LAN, z's tunnel, the hub, a's tunnel (ping from y's .1)"
check $(grep -q 'REGION: loop started' $T/z/daemon.log && grep -q 'REGION: loop started' $T/a/daemon.log && echo 0 || echo 1) "both daemons in the region loop"
# ---- [DAEMON_OWNS_ITS_KEY_V1] the node that started with no key: its daemon made it, said so, announced it, and its
#      hub link carries traffic (ping from its .1 to the other daemon node's .1, across the hub)
if [ -n "${NOKEY:-}" ]; then
  KF=$T/$NOKEY/fs/var-lib-frognet-tunnel/node_private.key
  KPUB=$( [ -s $KF ] && wg pubkey < $KF 2>/dev/null )
  check $([ -n "$KPUB" ] && [ "$(stat -c %a $KF)" = 600 ] && echo 0 || echo 1) \
        "$NOKEY: node_private.key exists now, mode $(stat -c %a $KF 2>/dev/null || echo none) (it started with none)"
  check $(grep -q "WG_KEY: made .*public key $KPUB" $T/$NOKEY/daemon.log 2>/dev/null && [ -n "$KPUB" ] && echo 0 || echo 1) \
        "$NOKEY: the daemon says it made the key and names its public half: $(grep -m1 -o 'WG_KEY: .*' $T/$NOKEY/daemon.log | cut -c1-160)"
  check $(python3 -c 'import sys,json; sys.exit(0 if json.load(open(sys.argv[1])).get("pubkey")==sys.argv[2] and sys.argv[2] else 1)' $T/$NOKEY/fs/opt-fdf-var/facts.json "$KPUB" 2>/dev/null && echo 0 || echo 1) \
        "$NOKEY: the public key it announced (facts.pubkey) is the one it made"
  if [ "$NOKEY" = a ]; then SRC=10.7.7.1 DST=10.3.3.1; else SRC=10.3.3.1 DST=10.7.7.1; fi
  ip netns exec $NOKEY ping -c 2 -W 2 -I $SRC $DST > $T/nokey-ping.log 2>&1
  check $? "$NOKEY: its hub link is up: $SRC -> $DST across the hub ($(NSF $NOKEY wg show all latest-handshakes 2>/dev/null | awk '{print $1": handshake "$3}' | tr '\n' ' '))"
fi
sleep 3
# ---- the measurement: N changes on y, each timed on a and z by the daemon's own line
ip netns exec y ip link add lan1 type veth peer name lan1p; ip netns exec y ip link set lan1 up; ip netns exec y ip link set lan1p up
echo "=== $N changes on y (10.9.9.1/24 added / removed); per change: the timed line on a and z, and change -> that line"
: > $T/samples
for k in $(seq $N); do
  la=$(wc -l < $T/a/daemon.log); lz=$(wc -l < $T/z/daemon.log)
  if [ $((k % 2)) = 1 ]; then T_CHANGE=$(ms); ip netns exec y ip addr add 10.9.9.1/24 dev lan1; else T_CHANGE=$(ms); ip netns exec y ip addr del 10.9.9.1/24 dev lan1; fi
  for n in a z; do
    l0=$([ $n = a ] && echo $la || echo $lz); line=""
    for i in $(seq 100); do line=$(tail -n +$((l0+1)) $T/$n/daemon.log | grep -m1 'REGION: event p timed'); [ -n "$line" ] && break; sleep 0.05; done
    if [ -z "$line" ]; then echo "  #$k $n: no timed event within 5 s"; continue; fi
    t=${line%% *}
    st=$(echo "$line" | sed 's/.*timed: //')
    echo "  #$k $n: change -> timed line $((t - T_CHANGE)) ms | $st"
    echo "$n $((t - T_CHANGE)) $(echo "$st" | sed -E 's/.*world=([0-9]+)ms.*bringup=([0-9]+)ms apply=([0-9]+)ms.*publish=([0-9]+)ms total=([0-9]+)ms/\1 \2 \3 \4 \5/')" >> $T/samples
  done
  sleep 2
done
python3 - $T/samples <<'PY'
import sys, statistics as st
rows = [l.split() for l in open(sys.argv[1]) if len(l.split()) == 7]
for n in ("a", "z"):
    r = [list(map(int, x[1:])) for x in rows if x[0] == n]
    if not r: print("  %s: no samples" % n); continue
    med = [int(st.median(c)) for c in zip(*r)]
    print("  median %s (n=%d): change->line %d ms | world=%d bringup=%d apply=%d publish=%d total=%d ms" % ((n, len(r)) + tuple(med)))
PY
echo "--- per-event region reads on a (one event): 'my-channels' lines logged between two timed lines"
awk '/REGION: event p timed/{c++} c==2 && /my-channels/{m++} END{print "    my-channels computations in one event on a: " m+0}' $T/a/daemon.log
grep -c 'REGION: event p timed' $T/a/daemon.log | sed 's/^/    timed events on a: /'
# ---- [HOSTJSON_FROM_THE_REGION_V1] getHosts.php's input, written by the daemon from the region: a lists z, y and itself
HJ=$T/a/fs/var-lib-frognet/region_hosts.json
check $(python3 -c 'import sys,json; h=json.load(open(sys.argv[1])); ips=[x["ip"] for x in h]; sys.exit(0 if {"10.3.3.1","10.2.2.1","10.7.7.1"} <= set(ips) and all(x["echo"].split(",")[1] == x["ip"] for x in h) else 1)' $HJ 2>/dev/null && echo 0 || echo 1) \
      "a: region_hosts.json names z, y and a, each echo from its facts ($(python3 -c 'import sys,json; print([(x["ip"],x["echo"]) for x in json.load(open(sys.argv[1]))])' $HJ 2>&1 | cut -c1-200))"
check $(python3 -c 'import sys,json; e={x["ip"]:x["echo"].split(",") for x in json.load(open(sys.argv[1]))}; sys.exit(0 if e["10.3.3.1"][2:] == ["198.18.0.3","0.0.0.0"] and e["10.2.2.1"][2:] == ["10.3.3.20","0.0.0.0"] else 1)' $HJ 2>/dev/null && echo 0 || echo 1) \
      "a: each echo's fields 3-4 are that member's uplink rows: z 198.18.0.3, y its lease 10.3.3.20 on z's LAN [ECHO_UPSTREAM_FROM_THE_ROWS_V1]"
# ---- [LEAF_ROW_FROM_ADDR_V1] a leaf off the mesh (the phone: fdf-client only, facts = role, name, guid, pubkey; no rows,
#      no subnet): the region gives it its /32; a and z route it on their hub link and name it; y (no daemon here) reaches
#      it over its default via z
ip netns add lf; ip netns exec lf ip link set lo up
ip link add v-lf type veth peer name v-lf-b; ip link set v-lf netns lf; ip link set v-lf-b master br-inet up
ip netns exec lf ip addr add 198.18.0.9/24 dev v-lf; ip netns exec lf ip link set v-lf up
# [LEAF_BY_INVITE_V1] its key and edge address are the invitation's; the hub peered it at fdf-invite
python3 -I - $T/lf.invite $T/lf.key $T/lf.edge <<'PY2'
import base64, json, os, sys
t = open(sys.argv[1]).read().strip()[8:]; j = json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))
os.umask(0o077); open(sys.argv[2], "w").write(j["member"]["privkey"] + "\n"); open(sys.argv[3], "w").write(j["member"]["edge"])
PY2
LPUB=$(wg pubkey < $T/lf.key); LIP=$(cat $T/lf.edge); LADDR=$LIP/32; HUBPUB=$(NS hub wg show wg-hub public-key)
ip netns exec lf wireguard-go wg-lf >/dev/null 2>&1; for i in $(seq 100); do ip netns exec lf wg show wg-lf >/dev/null 2>&1 && break; sleep 0.05; done
ip netns exec lf wg set wg-lf private-key $T/lf.key; ip netns exec lf ip link set wg-lf up
ip netns exec lf ip addr add $LADDR dev wg-lf; ip netns exec lf ip route add 10.0.0.0/8 dev wg-lf
ip netns exec lf wg set wg-lf peer $HUBPUB endpoint $PUB:51820 allowed-ips 10.0.0.0/8 persistent-keepalive 25
printf '{"role":"leaf","name":"phone","guid":"g-phone","pubkey":"%s","edge":"%s"}\n' "$LPUB" "$LIP" > $T/lf.json
TL0=$(ms); ip netns exec lf env RINA_RAM=$RAM FDF_FACTS=$T/lf.json $B/fdf-client > $T/lf-client.log 2>&1 &
okl=1; for i in $(seq 200); do ip netns exec a ip -4 route get $LIP 2>/dev/null | grep -q " dev wg" && ip netns exec z ip -4 route get $LIP 2>/dev/null | grep -q " dev wg" \
  && NS hub curl -s -X POST -d '{"op":"participants"}' "$U" | grep -q '"phone"' && { okl=0; break; }; sleep 0.05; done; TL1=$(ms)
check $okl "leaf $LADDR (its invitation's edge): a and z (daemons, hub links) route it on their hub link; the region lists it ($((TL1-TL0)) ms after the leaf's fdf-client started)"
[ $okl = 0 ] || for n in a z y; do echo "--- $n routes"; ip netns exec $n ip -4 route | sed 's/^/    /'; done
check $(ip netns exec a ping -c 2 -W 2 -I 10.7.7.1 $LIP > /dev/null 2>&1 && echo 0 || echo 1) "a -> the leaf $LIP (a's tunnel, the hub, the leaf's tunnel)"
check $(ip netns exec y ping -c 2 -W 2 -I 10.2.2.1 $LIP > /dev/null 2>&1 && echo 0 || echo 1) "y -> the leaf (y's default to z, z's tunnel, the hub)"
check $(for i in $(seq 100); do grep -q "^$LIP " $T/a/fs/etc/hosts && { echo 0; exit; }; sleep 0.05; done; echo 1) "a's /etc/hosts names the leaf"
# [LEAVES_NAMED_BY_EVERY_WRITER_V1] runMerge on a (what John runs by hand): it rewrites /etc/hosts, and the leaf must
# still be named after it (2026-10-11 on Seattle5: every device was dropped until the service restarted)
NSF a env FROGNET_DISCOVERY_SOURCE=region timeout 240 python3 -u -m discovery.live > $T/a/runmerge.log 2>&1; RM=$?
check $(grep -q "^$LIP " $T/a/fs/etc/hosts && echo 0 || echo 1) "runMerge on a (rc $RM): its /etc/hosts still names the leaf ($(grep -o 'LEAVES named: [0-9]*' $T/a/runmerge.log | tail -1); $(grep -c "^$LIP " $T/a/fs/etc/hosts) line)"
[ $RM = 0 ] || tail -15 $T/a/runmerge.log | sed 's/^/    runMerge: /'
check $(python3 -c 'import sys,json; h={x["ip"]:x for x in json.load(open(sys.argv[1]))}; l=h.get(sys.argv[2],{}); sys.exit(0 if l.get("role")=="leaf" and l.get("alive") is True and h["10.7.7.1"].get("role")=="node" else 1)' $T/a/fs/var-lib-frognet/region_hosts.json $LIP 2>/dev/null && echo 0 || echo 1) \
      "a: region_hosts.json (getHosts.php) lists the leaf role=leaf alive=true: the monitor's DEVICE ONLINE row [DEVICE_ROW_V1]"
# ---- [COMMS_ON_A_NODE_V1] the Communicator's memory and media server on node z (not the droplet): z says where they are
#      in the pond's memory, (pond, where, w:g-z); the leaf (the phone) finds them there and streams to them over its
#      tunnel; when z's session ends the cell is gone, when z is back fdf-comms writes it again. COMMS_BUILD = the
#      Communicator's build/ (comms-ram, comms-media, comms-feed); without it this stage is skipped, and says so.
if [ -n "${COMMS_BUILD:-}" ] && [ -x "$COMMS_BUILD/comms-media" ]; then
  whr() { NS hub curl -s "http://$RAM$API?op=read&service=pond&variable=where"; }
  TC0=$(ms); NSF z env COMMS_BIN=$COMMS_BUILD bash $PK/bin/fdf-comms.sh > $T/z/comms.log 2>&1 &
  okc=1; for i in $(seq 200); do whr | grep -q '"ram":"w:g-z:8800"' && { okc=0; break; }; sleep 0.05; done; TC1=$(ms)
  check $okc "z said where its Communicator is BY IDENTITY: (pond, where, w:g-z) comms ram w:g-z:8800, media w:g-z:8994 ($((TC1-TC0)) ms after fdf-comms started) [IDENTITY_IN_TUPLES_V1]"
  # [IDENTITY_IN_TUPLES_V1] the region's resolver: z's identity is its .1 here, the leaf's its given /32; gone is ENOENT
  RES=$(NS hub curl -s -X POST -d '{"op":"resolve","ids":["w:g-z","w:g-phone","w:g-nobody"]}' "$U")
  check $(echo "$RES" | python3 -c 'import sys,json; r=json.load(sys.stdin)["resolved"]; sys.exit(0 if r["w:g-z"].get("addr")=="10.3.3.1" and r["w:g-phone"].get("addr")==sys.argv[1] and r["w:g-nobody"].get("error")=="ENOENT" else 1)' "$LIP" && echo 0 || echo 1) \
        "resolve: w:g-z -> 10.3.3.1 (its .1), w:g-phone -> $LIP (its given /32), w:g-nobody -> ENOENT: $RES"
  rsv() { echo "$RES" | python3 -c 'import sys,json; print(json.load(sys.stdin)["resolved"][sys.argv[1]].get("addr",""))' "$1"; }
  cel() { whr | python3 -c 'import sys,json; b=json.load(sys.stdin); r=b.get("rows") or b.get("cells") or []; print(next((c["bag"]["comms"][sys.argv[1]] for c in r if "comms" in (c.get("bag") or {})),""))' "$1" 2>/dev/null; }
  RID=$(cel ram); MID=$(cel media)
  RAMC=$(rsv ${RID%:*}):${RID##*:}; MEDC=$(rsv ${MID%:*}):${MID##*:}
  ip netns exec lf timeout 10 $COMMS_BUILD/comms-feed --ram "$RAMC" --media "$MEDC" --name phone --synthetic 640x360 --tone 440 --seconds 4 > $T/lf-feed.log 2>&1
  check $(grep -q "phone is live" $T/lf-feed.log && grep -q "sent [1-9][0-9]* frames" $T/lf-feed.log && echo 0 || echo 1) \
        "the leaf found it there ($RAMC / $MEDC) and streamed to it through its tunnel and the hub: $(grep -o 'sent [0-9]* frames ([0-9]* dropped), [0-9]* audio' $T/lf-feed.log)"
  check $(grep -q "phone declared video plane" $T/z/comms.log && echo 0 || echo 1) "z's media server took the leaf's video and audio planes"
  # [COMMS_ON_A_NODE_V1] the phone's own code: its Kotlin member finds the Communicator in the pond (Member.services), and
  # its Java Communicator core (PROBE_JVM: the Android app's core + test/Probe) watches a feed that node a streams to z
  # -- the phone off the mesh, a on its own LAN, z hosting: three sites, the hub between them
  if [ -n "${KT_MEMBER:-}" ] && [ -n "${PROBE_JVM:-}" ]; then
    printf '{"role":"leaf","name":"phone-kt","guid":"g-phone-kt","pubkey":"%s"}\n' "$(wg genkey | wg pubkey)" > $T/lfk.json
    ip netns exec lf env RINA_RAM=$RAM FDF_FACTS=$T/lfk.json FDF_SERVICES=comms $KT_MEMBER 2>&1 | grep --line-buffered -v JAVA_TOOL > $T/lf-kt.log &
    KTW="services: comms in this pond: z ram=w:g-z:8800 -> 10.3.3.1:8800 media=w:g-z:8994 -> 10.3.3.1:8994"
    for i in $(seq 200); do grep -qF "$KTW" $T/lf-kt.log && break; sleep 0.05; done
    check $(grep -qF "$KTW" $T/lf-kt.log && echo 0 || echo 1) \
          "the phone's Kotlin member found it: $(grep -m1 -o 'services: comms in this pond: .*' $T/lf-kt.log)"
    NSF a env HOME=/tmp timeout 24 $COMMS_BUILD/comms-feed --ram 10.3.3.1:8800 --media 10.3.3.1:8994 --name Chapel --synthetic 1280x720 --tone 440 --seconds 20 > $T/a-feed.log 2>&1 &
    sleep 2; mkdir -p $T/watch
    ip netns exec lf env PROBE_HOST=10.3.3.1 timeout 30 java -cp $PROBE_JVM Probe watch 8800 Chapel 5 $T/watch/dump 2>&1 | grep -v JAVA_TOOL > $T/lf-watch.log
    check $(grep -q "^VIDEO .* frames=[1-9]" $T/lf-watch.log && grep -q "^AUDIO .* packets=[1-9]" $T/lf-watch.log && echo 0 || echo 1) \
          "the phone's Java core watched a's feed through z's media server: $(grep -E '^(WATCHING|VIDEO|AUDIO)' $T/lf-watch.log | tr '\n' ' ')"
  else echo "SKIP  the phone's own code (set KT_MEMBER and PROBE_JVM)"; fi
  ZC=$(ip netns pids z | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && echo $p; done | head -1); kill $ZC
  okg=1; for i in $(seq 100); do whr | grep -q 'w:g-z:8800' || { okg=0; break; }; sleep 0.05; done
  check $okg "z's session ended: its where cell is gone with it (gone is gone)"
  # its unit starts it again (svc)
  okb=1; for i in $(seq 300); do whr | grep -q 'w:g-z:8800' && { okb=0; break; }; sleep 0.05; done
  check $okb "z back: fdf-comms wrote it again on the event that showed z ($(grep -c 'writing it again' $T/z/comms.log) rewrite)"
else echo "SKIP  the Communicator on a node (set COMMS_BUILD to the Communicator's build/)"; fi
# ---- [ANN_GOES_WITH_THE_SESSION_V1] y's client ends: y's announcement is gone from the region, not left behind dead
kill $YW; YPID=$(ip netns pids y | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && echo $p; done | head -1)
kill $YPID; sleep 1
P=$(NS hub curl -s -X POST -d '{"op":"participants"}' "$U")
check $(echo "$P" | python3 -c 'import sys,json; ps=json.load(sys.stdin)["participants"]; sys.exit(0 if not [p for p in ps if p.get("name")=="y"] and all(p.get("alive") for p in ps) else 1)' && echo 0 || echo 1) \
      "y's session ended: its announcement is gone, and no dead row is listed ($(echo "$P" | python3 -c 'import sys,json; print([(p["name"],p["alive"]) for p in json.load(sys.stdin)["participants"]])'))"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
[ -n "${PAUSE:-}" ] && { echo "PAUSE: holding the namespaces ${PAUSE}s (T=$T)"; sleep $PAUSE; }
