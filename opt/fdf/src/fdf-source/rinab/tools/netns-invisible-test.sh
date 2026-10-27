#!/bin/bash
# netns-invisible-test.sh -- [RAM_IN_THE_TUNNEL_V1][ADMITTED_BY_INVITE_V1][NOTHING_LISTENS_OUTSIDE_V1] the region as it is
# now built, on one box, with the REAL tunnel daemon on the members: the RAM manager listens only on the hub's tunnel
# address, a member is admitted by invitation before it ever speaks, and from the "Internet" the hub shows one UDP port
# that answers nothing. Topology (the "Internet" is 198.18.0.0/24, a bridge):
#   hub 198.18.0.1   fdf-hub --iface-only, rina-host on 10.253.253.254:8840, map_site, fdf-hub, its fdf-client
#   z   198.18.0.3   invited; serves 10.3.3.0/24;  fdf-region-join --files-only, daemon, fdf-facts --watch, fdf-client
#   a   198.18.0.5   invited; serves 10.7.7.0/24;  the same
#   x   198.18.0.7   never invited: the Internet's view (a scanner, and a WireGuard peer with a key of its own)
# Checks, each with what it saw:
#   1 the hub listens on nothing outside its tunnel but udp 51820 (ss in the hub)
#   2 z and a join THROUGH the tunnel: handshake, RAM session to 10.253.253.254:8840, routes, z <-> a ping
#   3 from x: every TCP port of 198.18.0.1 closed or silent; none open (1..65535); the RAM port refused/silent
#   4 from x: the old plain-HTTP read of the region (participants) fails
#   5 x as a WireGuard peer with an uninvited key: no handshake, and the hub sends x nothing (tcpdump at x)
#   6 with the hub's input firewall (what fdf-region-create --ufw sets: default deny incoming), x sees NOTHING at all:
#     no RST, no ICMP -- the machine itself is silent (IP from the hub; ARP is the shared segment's, not the Internet's)
#   7 revoke a: its peer is gone from the hub at once
#   8 the region restarts: z loses it (routes withdrawn, tunnel down), brings the hub channel back first, rejoins
# Needs root, ip, wg, wireguard-go, python3, curl, tcpdump, nft.  usage: tools/netns-invisible-test.sh [RINAB_DIR]
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
PK=${PKG_FDF:-$H/../fdf}; FDFPY=$PK/lib/python
T=${T:-/tmp/claude-0/netns-invisible}; rm -rf $T; mkdir -p $T/hub/admitted $T/z $T/a $T/x $T/bin
export FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY RIBBIT_TEMPLATES=$PK/templates/fdf.prime
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php; AREA=D; KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
HA=10.253.253.254; RAM=$HA:8840; U="http://$RAM$API?op=rina"; PUB=198.18.0.1
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in hub z a x; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in hub z a x; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for l in v-hub-b v-z-b v-a-b v-x-b; do ip link del $l 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
# a converged member: z routes a's /24 on its hub link, a routes z's (the graph's routes, not wg-quick's 10.0.0.0/8)
conv() { ip netns exec z ip -4 route | grep -q '^10.7.7.0/24 dev wg' && ip netns exec a ip -4 route | grep -q '^10.3.3.0/24 dev wg'; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/fdf-where $B/rina-host $T/bin/ 2>/dev/null; [ -x $T/bin/rina-host ] || cp $H/build/bin/rina-host-static $T/bin/rina-host
cp $PK/bin/fdf-facts $PK/bin/fdf-hub $PK/bin/fdf-invite $PK/bin/fdf-region-join $PK/bin/fdf-invisible-check $T/bin/; cp "$WGGO" $T/bin/wireguard-go
export PATH=$T/bin:$PATH
cat > $T/bin/capability-probe <<'EOF'
#!/bin/sh
echo '{"cores":4,"cpu_mhz":2000,"cpu_bench_total":12000,"mem_total_kb":8388608,"mem_available_kb":4000000,"disk_class":"ssd","mysql":true,"mysql_running":true,"ffmpeg":true,"libvpx":true,"lan_ip":"","loadavg":{"1":0.1,"5":0.1,"15":0.1},"temps_c":[]}'
EOF
chmod +x $T/bin/capability-probe
# ---- the Internet
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
ip addr add 198.18.0.254/24 dev br-inet          # the members' Internet router (their uplink's next hop, which fdf-facts pings)
i=1; for n in hub z a x; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
# ---- the region, as fdf-region-create now makes it
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
check $? "hub: the region's interface first (fdf-hub --iface-only): $(tail -1 $T/hub/iface.log)"
NS hub env RINA_REGION_FILE=$T/hub/area.perm RINA_NET_CONFIG=$T/hub/none $T/bin/rina-host --listen $RAM --api $API > $T/hub/host.log 2>&1 &
for i in $(seq 100); do NS hub curl -s -m 1 -X POST -d '{"op":"version"}' "$U" > /dev/null && break; sleep 0.05; done
R=$(NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$AREA\",\"key\":\"$KEY\"}" "$U"); case "$R" in *'"ok":true'*) ;; *) echo "FAIL  map_site: $R"; exit 1 ;; esac
NS hub sysctl -q -w net.ipv4.ip_forward=1
# every long-lived process restarts as its unit does (Restart=always, RestartSec=2): fdf-hub and the clients end when
# the region does, and come back
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
svc $T/hub/hub.log ip netns exec hub env RINA_RAM=$RAM FDF_BIN=$T/bin python3 $T/bin/fdf-hub --area $AREA --iface wg-D --endpoint $PUB:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json --admitted $T/hub/admitted
for i in $(seq 100); do grep -q "start (adopt)" $T/hub/hub.log && break; sleep 0.05; done
svc $T/hub/client.log ip netns exec hub env RINA_RAM=$RAM FDF_FACTS=$T/hub/facts.json FDF_NAME=hubA $T/bin/fdf-client
# 1 what listens in the hub
L=$(NS hub ss -H -lntu | awk '{print $1, $5}' | sort)
check $(echo "$L" | awk '$2 !~ /^(10\.253\.253\.254|127\.)/ && !($1=="udp" && $2 ~ /:51820$/)' | grep -q . && echo 1 || echo 0) \
      "1 the hub listens on nothing outside its tunnel but udp 51820: $(echo "$L" | tr '\n' ';')"
check $(grep -q "NOTHING_LISTENS_OUTSIDE_V1] nothing listens" $T/hub/hub.log && echo 0 || echo 1) "1 fdf-hub's own check says so: $(grep -m1 -o 'NOTHING_LISTENS_OUTSIDE_V1].*' $T/hub/hub.log | cut -c1-120)"
# ---- invitations (fdf-invite, as the operator runs it on the hub)
inv() { NS hub env FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub python3 $T/bin/fdf-invite "$@"; }
for n in z a; do inv $n > $T/$n/invite.out 2>&1; grep -m1 '^fdfinv1\.' $T/$n/invite.out > $T/$n/invite; done
check $([ -s $T/z/invite ] && [ -s $T/a/invite ] && echo 0 || echo 1) "invited z and a: $(head -1 $T/z/invite.out); $(head -1 $T/a/invite.out)"
for i in $(seq 100); do [ "$(NS hub wg show wg-D peers | wc -l)" = 2 ] && break; sleep 0.05; done
check $([ "$(NS hub wg show wg-D peers | wc -l)" = 2 ] && echo 0 || echo 1) "the hub peered both at once, before either has spoken (inotify on the admitted directory): $(grep -c 'admitted ' $T/hub/hub.log) admitted event(s)"
# ---- members: a private filesystem each (as netns-region-loop-test.sh), then the invitation, the daemon, the clients
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
fsnode() { local n=$1; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$n" > $F/etc/fnid; echo "domain=$n" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns invisible test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf; }
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  ip netns exec $n env PATH=$PATH RINA_RAM=$RAM FDF_BIN=$T/bin FDF_WG_IMPL=wireguard-go \
     WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1 \
     FDF_FACTS=/opt/fdf/var/facts.json FDF_LEASES=$T/$n/leases FDF_PY=$FDFPY FROGNET_CAPABILITY_PROBE=$T/bin/capability-probe \
     FROGNET_REGION_HOSTS=/var/lib/frognet/region_hosts PYTHONPATH=$FN \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
for n in z a; do ip netns exec $n ip link add lan0 type veth peer name lan0p; ip netns exec $n ip link set lan0 up; ip netns exec $n ip link set lan0p up
  ip netns exec $n ip route add default via 198.18.0.254 dev v-$n; ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; : > $T/$n/leases; done
ip netns exec z ip addr add 10.3.3.1/24 dev lan0; ip netns exec a ip addr add 10.7.7.1/24 dev lan0
for n in z a; do fsnode $n
  NSF $n bash $T/bin/fdf-region-join --files-only "$(cat $T/$n/invite)" > $T/$n/join.log 2>&1
  check $? "$n: fdf-region-join --files-only: $(grep -m1 'invitation:' $T/$n/join.log | cut -c1-150)"
  NSF $n python3 -u -m internet_tunnels_v3 > $T/$n/daemon.log 2>&1 &
  for i in $(seq 40); do grep -q '"name"' $T/$n/fs/opt-fdf-var/facts.json 2>/dev/null && break; sleep 0.25; done
  svc $T/$n/client.log NSF $n env FDF_NAME=$n $(cat $T/$n/fs/opt-fdf-var/site.env) $T/bin/fdf-client   # as fdf-client.service: EnvironmentFile=site.env
  NSF $n env FDF_NAME=$n python3 $T/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 &
done
# 2 joined through the tunnel
ok=1; for i in $(seq 120); do conv && { ok=0; break; }; sleep 0.5; done
check $ok "2 converged through the tunnel: z routes a's /24, a routes z's (after $((i/2)) s)"
[ -n "${STOP_AT_2:-}" ] && { echo "STOP_AT_2: holding ${STOP_AT_2}s"; sleep $STOP_AT_2; }
[ $ok = 0 ] || for n in z a; do echo "--- $n routes"; ip netns exec $n ip -4 route | sed 's/^/    /'; echo "--- $n daemon (last 30)"; tail -30 $T/$n/daemon.log | cut -c1-260 | sed 's/^/    /'; echo "--- $n client"; tail -5 $T/$n/client.log | sed 's/^/    /'; done
check $(ip netns exec z ping -c 2 -W 2 -I 10.3.3.1 10.7.7.1 > /dev/null 2>&1 && echo 0 || echo 1) "2 z -> a (10.3.3.1 -> 10.7.7.1) through the hub"
S=$(ip netns exec z ss -H -tn state established "( dport = :8840 )" | awk '{print $3" -> "$4}' | sort -u | tr '\n' ' ')
check $(echo "$S" | grep -q "10.253.[0-9]*.[0-9]*:[0-9]* -> $HA:8840" && ! echo "$S" | grep -q "198.18" && echo 0 || echo 1) "2 z's region sessions run inside the tunnel: $S"
check $(grep -q "my-channels: hub=hubA edge=10.253" $T/z/daemon.log && echo 0 || echo 1) "2 z's daemon built its hub channel from the invitation: $(grep -m1 -o 'my-channels: hub=.*' $T/z/daemon.log | cut -c1-120)"
# 3 the Internet's view of the hub: a TCP connect to every port
cat > $T/scan.py <<'EOF'
import errno, socket, sys
host, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
res = {"open": [], "refused": 0, "silent": 0}
for p in range(lo, hi + 1):
    s = socket.socket(); s.settimeout(float(sys.argv[4]) if len(sys.argv) > 4 else 0.3)
    try:
        s.connect((host, p)); res["open"].append(p)
    except ConnectionRefusedError: res["refused"] += 1
    except (socket.timeout, OSError): res["silent"] += 1
    finally: s.close()
print("open=%s refused=%d silent=%d" % (res["open"], res["refused"], res["silent"]))
EOF
SC=$(ip netns exec x python3 $T/scan.py $PUB 1 65535 0.3)
check $(echo "$SC" | grep -q "^open=\[\] " && echo 0 || echo 1) "3 from x, a TCP connect to all 65535 ports of $PUB: $SC"
# 4 the old way in: the region over plain HTTP at the public address
R4=$(ip netns exec x curl -s -m 3 -X POST -d '{"op":"participants"}' "http://$PUB:8840$API?op=rina"; echo "rc=$?")
check $(echo "$R4" | grep -q participants && echo 1 || echo 0) "4 from x, the region's participants over HTTP at $PUB:8840: nothing ($R4)"
# 5 x as a WireGuard peer with its own (uninvited) key, knowing the hub's public key and endpoint
HUBPUB=$(NS hub wg show wg-D public-key)
(umask 077; wg genkey > $T/x/wg.key)
ip netns exec x wireguard-go wg-x > /dev/null 2>&1; for i in $(seq 100); do ip netns exec x wg show wg-x > /dev/null 2>&1 && break; sleep 0.05; done
ip netns exec x wg set wg-x private-key $T/x/wg.key peer $HUBPUB endpoint $PUB:51820 allowed-ips $HA/32 persistent-keepalive 1
ip netns exec x ip addr add 10.253.99.99/16 dev wg-x; ip netns exec x ip link set wg-x up
ip netns exec x timeout 12 tcpdump -n -i v-x -c 50 "ip and src host $PUB" > $T/x/from-hub.txt 2>/dev/null &
TD=$!; sleep 1; ip netns exec x ping -c 5 -W 1 $HA > /dev/null 2>&1; sleep 5
HS=$(ip netns exec x wg show wg-x latest-handshakes | awk '{print $2}')
kill $TD 2>/dev/null; wait $TD 2>/dev/null
check $([ "$HS" = 0 ] && ! grep -q "$PUB.51820 >" $T/x/from-hub.txt && echo 0 || echo 1) \
      "5 x with an uninvited key: no handshake (latest $HS), and nothing from the hub's udp 51820 to x ($(grep -c "$PUB.51820 >" $T/x/from-hub.txt) packets)"
# the hardware tool, as the operator runs it: fdf-invisible-check member (on z) and outside (from x, with the hub's key)
NSF z python3 $T/bin/fdf-invisible-check member > $T/z/check-member.txt 2>&1
check $? "fdf-invisible-check member on z: $(grep -c '^PASS' $T/z/check-member.txt) PASS, $(grep -c '^FAIL' $T/z/check-member.txt) FAIL"
grep -E "^(FAIL|NOTE)" $T/z/check-member.txt | cut -c1-200 | sed 's/^/      /'
ip netns exec x python3 $T/bin/fdf-invisible-check outside $PUB 51820 --hub-key $(NS hub wg show wg-D public-key) --ram-port 8840 > $T/x/check-outside.txt 2>&1
check $? "fdf-invisible-check outside from x (no firewall): $(grep -c '^PASS' $T/x/check-outside.txt) PASS, $(grep -c '^FAIL' $T/x/check-outside.txt) FAIL"
grep -E "^(FAIL|PASS  TCP|NOTE  [0-9]|NOTE  ICMP|PASS  an )" $T/x/check-outside.txt | cut -c1-200 | sed 's/^/      /'
# 6 the hub's input firewall (default deny incoming, the one udp port allowed): the machine itself says nothing
NS hub nft -f - <<EOF
table inet s4fw {
  chain in {
    type filter hook input priority 0; policy drop;
    iifname "lo" accept
    ct state established,related accept
    iifname "wg-D" accept
    udp dport 51820 accept
  }
}
EOF
ip netns exec x timeout 15 tcpdump -n -i v-x "ip and src host $PUB" > $T/x/from-hub-fw.txt 2>/dev/null & TD=$!; sleep 1
SC6=$(ip netns exec x python3 $T/scan.py $PUB 8830 8850 0.3); ip netns exec x python3 -c "
import socket
for p in (53, 51820, 8840):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b'hello', ('$PUB', p)); s.close()"
ip netns exec x ping -c 2 -W 1 $PUB > /dev/null 2>&1; sleep 2; kill $TD 2>/dev/null; wait $TD 2>/dev/null
NP=$(grep -c " IP " $T/x/from-hub-fw.txt)
check $([ "$NP" = 0 ] && echo "$SC6" | grep -q "refused=0 " && echo 0 || echo 1) \
      "6 with the input firewall: x gets no RST, no ICMP, no echo reply -- nothing at all ($SC6; $NP IP packets from the hub)"
ok6=1; for i in $(seq 40); do ip netns exec z ping -c 1 -W 1 -I 10.3.3.1 10.7.7.1 > /dev/null 2>&1 && { ok6=0; break; }; done
check $ok6 "6 and the members still work through it (z -> a)"
ip netns exec x python3 $T/bin/fdf-invisible-check outside $PUB 51820 --hub-key $(NS hub wg show wg-D public-key) > $T/x/check-outside-fw.txt 2>&1
check $(grep -q "REFUSED 0, SILENT 65535" $T/x/check-outside-fw.txt && grep -q "ICMP echo: no answer" $T/x/check-outside-fw.txt && grep -q "^RESULT PASS" $T/x/check-outside-fw.txt && echo 0 || echo 1) \
      "fdf-invisible-check outside from x (firewall): $(grep -E '^PASS  TCP' $T/x/check-outside-fw.txt | cut -c7-120); $(grep -o 'ICMP echo: .*' $T/x/check-outside-fw.txt)"
# 7 revoke a
inv a --revoke > $T/a/revoke.out 2>&1
APUB=$(wg pubkey < $T/a/fs/var-lib-frognet-tunnel/node_private.key)
ok=1; for i in $(seq 100); do NS hub wg show wg-D peers | grep -q "$APUB" || { ok=0; break; }; sleep 0.05; done
check $ok "7 revoke a: its peer is gone from the hub ($(cat $T/a/revoke.out))"
# 8 the region restarts: z withdraws, brings its hub channel back FIRST, rejoins through it
HP=$(ip netns pids hub | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = rina-host ] && echo $p; done)
L0=$(wc -l < $T/z/daemon.log); kill $HP
ok=1; for i in $(seq 80); do tail -n +$L0 $T/z/daemon.log | grep -q "REGION: lost" && { ok=0; break; }; sleep 0.25; done
check $ok "8 region gone: z says so ($(tail -n +$L0 $T/z/daemon.log | grep -m1 -o 'REGION: lost.*' | cut -c1-120))"
NS hub env RINA_REGION_FILE=$T/hub/area.perm RINA_NET_CONFIG=$T/hub/none $T/bin/rina-host --listen $RAM --api $API >> $T/hub/host.log 2>&1 &
ok=1; for i in $(seq 120); do ip netns exec z ping -c 1 -W 1 -I 10.3.3.1 $HA > /dev/null 2>&1 && tail -n +$L0 $T/z/daemon.log | grep -q "REGION: loop\|event .* timed\|GRAPH build" && { ok=0; break; }; sleep 0.5; done
check $ok "8 region back: z's hub channel came up again and z rejoined through it ($(tail -n +$L0 $T/z/daemon.log | grep -c 'my-channels') channel builds after the loss)"
[ $ok = 0 ] || { echo "[DIAG-8] z wg: $(NSF z wg show all latest-handshakes 2>&1 | tr '\n' ' ')"; echo "[DIAG-8] z routes: $(ip netns exec z ip -4 route | tr '\n' ';')"
  echo "[DIAG-8] hub peers: $(NS hub wg show wg-D latest-handshakes | tr '\n' ' ')"; ip netns exec z ping -c 1 -W 1 -I 10.3.3.1 $HA 2>&1 | tail -2 | sed 's/^/[DIAG-8] ping: /'
  tail -n +$L0 $T/z/daemon.log | grep -E "REGION|BRING" | cut -c1-200 | tail -12 | sed 's/^/[DIAG-8] /'; tail -5 $T/hub/hub.log | sed 's/^/[DIAG-8] hub: /'; }
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
[ -n "${PAUSE:-}" ] && { echo "PAUSE: holding the namespaces ${PAUSE}s (T=$T)"; sleep $PAUSE; }
exit $([ $FAILS = 0 ] && echo 0 || echo 1)
