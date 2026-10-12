#!/bin/bash
# netns-lan-only-test.sh -- [LAN_ONLY_NETNS_V1] LAN-only FrogNet: two nodes on one LAN, NO RAM manager anywhere, no
# broker, no WireGuard. The REAL tunnel daemon (python3 -m internet_tunnels_v3, its LAN-only loop) and the REAL merge
# (python3 -m discovery.live: the bring-up-only stage, the LAN walk, the hosts commit, the databasehost election)
# run on each node, against each other's REAL frognet_echo.php / getHosts.php (php -S over the tree's var/www/html and
# usr/local/bin) and the REAL data daemon's :9009 answer (daemon.engine.server.DaemonServer: HELLO + ping-pong; no
# MySQL behind it, the walk only asks it "alive?").
#   a  eth0 10.11.11.1/24, serves that LAN, its lease file names b (10.11.11.20); NO /etc/frognet/tunnel.conf at all
#   b  wlan0 10.11.11.20 (its lease on a's LAN, default via 10.11.11.1); eth0 10.22.22.1/24, its own LAN;
#      tunnel.conf holds only old broker keys (BROKER_URL, GROUP_TOKEN, POND_PASSWORD), which must be named once
#      and ignored
# Checks: each daemon stays up in its LAN-only loop and says its build; b's daemon names the old keys in ONE line;
# the merge's bring-up stage says LAN-only and leaves no wg interface, an empty handshake_rtts.json and the
# tunnel_bringup_ready sentinel; a and b name each other in /etc/hosts and route each other's /24; both elect the same
# databasehost; no WireGuard interface exists in either; nothing asked a broker anything.
# Needs root, ip, php, python3, curl.   usage: tools/netns-lan-only-test.sh [RINAB_DIR]
#   FROGNET_DIR=<opt/frognet_semantic tree> (default: this repo's opt/frognet_semantic); the tree's top (usr/local/bin,
#   var/www/html) is FROGNET_DIR/../..
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
FN=$(cd $FN && pwd); TOP=$(cd $FN/../.. && pwd)
T=${T:-/tmp/claude-0/netns-lan-only}; rm -rf $T; mkdir -p $T/a $T/b
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { for n in a b; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in a b; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done; }
trap cleanup EXIT; cleanup
echo "tree: $FN (top $TOP)"
# ---- the LAN: a's eth0 <-> b's wlan0 (b is a's DHCP guest); b's own LAN on eth0 (a veth pair inside b)
for n in a b; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip link add lan-a type veth peer name lan-b; ip link set lan-a netns a; ip link set lan-b netns b
ip netns exec a ip link set lan-a name eth0; ip netns exec b ip link set lan-b name wlan0
ip netns exec a ip addr add 10.11.11.1/24 dev eth0; ip netns exec a ip link set eth0 up
ip netns exec b ip addr add 10.11.11.20/24 dev wlan0; ip netns exec b ip link set wlan0 up
ip netns exec b ip link add eth0 type veth peer name eth0p; ip netns exec b ip addr add 10.22.22.1/24 dev eth0
ip netns exec b ip link set eth0 up; ip netns exec b ip link set eth0p up
ip netns exec b ip route add default via 10.11.11.1 dev wlan0 metric 600
for n in a b; do ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; done
BMAC=$(ip netns exec b cat /sys/class/net/wlan0/address)
# ---- each node's private filesystem (as netns-region-loop-test.sh), plus the tree's usr/local and var/www/html
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var var-lib-misc:/var/lib/misc"
TREE="$TOP/usr/local/bin:/usr/local/bin $TOP/usr/local/lib:/usr/local/lib $TOP/var/www/html:/var/www/html"
for b in $BINDS $TREE; do mkdir -p ${b#*:}; done
fsnode() { local n=$1 RANGE=$2; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; rm -f $F/etc/frognet/tunnel.conf; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$n" > $F/etc/fnid
  printf 'domain=%s\ndhcp-range=%s\n' $n "$RANGE" > $F/etc/dnsmasq.d/opts_only.conf
  : > $F/var-lib-misc/dnsmasq.leases; }
fsnode a "10.11.11.50,10.11.11.150,12h"; fsnode b "10.22.22.50,10.22.22.150,12h"
printf '%d %s 10.11.11.20 b *\n' $(( $(date +%s) + 3600 )) "$BMAC" > $T/a/fs/var-lib-misc/dnsmasq.leases
printf '# an enrolled broker node, from before the region\nBROKER_URL=https://broker.invalid:8443/frognet-broker\nGROUP_TOKEN=tok\nPOND_PASSWORD=pw\n' > $T/b/fs/etc/frognet/tunnel.conf
PYLIB=$(python3 -c 'import lz4, os; print(os.path.dirname(os.path.dirname(lz4.__file__)))' 2>/dev/null); mkdir -p $T/pylib
# the tree's usr/local/lib hides the system's /usr/local/lib/python3.x (lz4, mysql-connector): keep it on PYTHONPATH
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  [ -n "$PYLIB" ] && m="mount --bind $PYLIB $T/pylib;"
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  for b in $TREE; do m="$m mount --bind ${b%%:*} ${b#*:};"; done
  # [SERVICES_ON_THE_VENV_V1 - T4b] a node runs the bring-up on the venv; this harness has none, so it names its python
  ip netns exec $n env -u RINA_RAM -u RINA_AREA PYTHONPATH=$FN:$T/pylib FROGNET_BRINGUP_PYTHONPATH=$FN:$T/pylib FROGNET_BRINGUP_PY="$(command -v python3)" PYTHONDONTWRITEBYTECODE=1 \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
for n in a b; do
  NSF $n php -S 0.0.0.0:80 -t /var/www/html > $T/$n/php.log 2>&1 &
  NSF $n python3 -u -c 'from daemon.engine.server import DaemonServer; from daemon.engine.resolver import DestinationResolver; DaemonServer("0.0.0.0", 9009, DestinationResolver()).run()' > $T/$n/data-daemon.log 2>&1 &
  NSF $n python3 -u -m internet_tunnels_v3 > $T/$n/daemon.log 2>&1 &
  echo $! > $T/$n/daemon.pid
done
sleep 4
check $(ip netns exec a curl -s --max-time 5 http://10.11.11.20/frognet_echo.php | grep -q '^b,10.22.22.1,10.11.11.20,' && echo 0 || echo 1) \
      "b's real frognet_echo.php answers a: $(ip netns exec a curl -s --max-time 5 http://10.11.11.20/frognet_echo.php)"
# ---- the tunnel daemons: up, LAN-only, saying their build, b naming the old keys once
for n in a b; do
  alive=1; kill -0 $(cat $T/$n/daemon.pid) 2>/dev/null && alive=0
  check $alive "$n: the tunnel daemon is running $(sleep 0; [ $n = a ] && echo '(no tunnel.conf at all)' || echo '(tunnel.conf: old broker keys only)') ($(grep -c 'Shutting down\|Goodbye\|Traceback\|not found' $T/$n/daemon.log) exit/error lines)"
  check $(grep -q "LAN-only loop started" $T/$n/daemon.log && grep -q "build frognet-tunnel-" $T/$n/daemon.log && echo 0 || echo 1) \
        "$n: its log says LAN-only and its build: $(grep -m1 -o 'Starting frognet-tunnel-daemon.*' $T/$n/daemon.log | cut -c1-110) | $(grep -m1 -o 'LAN-only loop started.*' $T/$n/daemon.log | cut -c1-110)"
done
check $([ "$(grep -c 'old broker keys' $T/b/daemon.log)" = 1 ] && grep 'old broker keys' $T/b/daemon.log | grep -q BROKER_URL && grep 'old broker keys' $T/b/daemon.log | grep -q GROUP_TOKEN && grep 'old broker keys' $T/b/daemon.log | grep -q POND_PASSWORD && echo 0 || echo 1) \
      "b: ONE line names the old broker keys and ignores them: $(grep -m1 -o 'old broker keys.*' $T/b/daemon.log | cut -c1-160)"
# ---- the merges: a, b, a, b (b learns a by its upstream, a learns b by its lease)
for r in 1 2; do for n in a b; do
  NSF $n timeout 300 python3 -u -m discovery.live > $T/$n/merge$r.log 2>&1; echo "merge $r on $n: rc $?"
done; done
for n in a b; do
  check $(grep -q 'reconcile_tunnels rc=0' $T/$n/merge2.log && grep -q 'BRINGUP_PHASE: LAN-only .*LAN_ONLY_NO_REGION_V1' $T/$n/merge2.log && echo 0 || echo 1) \
        "$n: the merge's bring-up stage ran and said LAN-only: $(grep -m1 -o 'BRINGUP_PHASE: LAN-only.*' $T/$n/merge2.log | cut -c1-150)"
  check $([ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])))' $T/$n/fs/var-lib-frognet-tunnel/handshake_rtts.json 2>/dev/null)" = "{}" ] \
          && [ -e $T/$n/fs/etc/sentinels/tunnel_bringup_ready ] && echo 0 || echo 1) "$n: handshake_rtts.json is {} and tunnel_bringup_ready is touched"
done
check $(grep -q '^10.22.22.1 FrogNetHost.b' $T/a/fs/etc/hosts && echo 0 || echo 1) "a's /etc/hosts names b: $(grep -m1 FrogNetHost.b $T/a/fs/etc/hosts)"
check $(grep -q '^10.11.11.1 FrogNetHost.a' $T/b/fs/etc/hosts && echo 0 || echo 1) "b's /etc/hosts names a: $(grep -m1 FrogNetHost.a $T/b/fs/etc/hosts)"
check $(ip netns exec a ip -4 route | grep -q '^10.22.22.0/24 via 10.11.11.20' && echo 0 || echo 1) "a routes b's /24 via b's lease: $(ip netns exec a ip -4 route | grep '^10.22.22' )"
check $(ip netns exec a ping -c 2 -W 2 -I 10.11.11.1 10.22.22.1 > /dev/null 2>&1 && echo 0 || echo 1) "a -> b's .1 (10.22.22.1) over the LAN"
DA=$(awk '/databasehost.frognet/{print $1}' $T/a/fs/etc/hosts | head -1); DB=$(awk '/databasehost.frognet/{print $1}' $T/b/fs/etc/hosts | head -1)
check $([ -n "$DA" ] && [ "$DA" = "$DB" ] && echo 0 || echo 1) "both elected the same databasehost: a says '${DA:-none}', b says '${DB:-none}'"
for n in a b; do
  check $([ -z "$(ip netns exec $n ip -o link show type wireguard 2>/dev/null)" ] && [ -z "$(NSF $n wg show interfaces 2>/dev/null)" ] && echo 0 || echo 1) "$n: no WireGuard interface"
  check $(cat $T/$n/daemon.log $T/$n/merge*.log | grep -q -i -E 'my-channels|broker host|broker route|api/v4|BROKER_URL=' && echo 1 || echo 0) \
        "$n: nothing asked a broker anything ($(cat $T/$n/daemon.log $T/$n/merge*.log | grep -c -i -E 'my-channels|broker host|broker route|api/v4') broker lines)"
done
FAIL_DETAIL=${FAIL_DETAIL:-1}
[ $FAILS = 0 ] || [ "$FAIL_DETAIL" = 0 ] || for n in a b; do
  echo "--- $n routes"; ip netns exec $n ip -4 route | sed 's/^/    /'
  echo "--- $n /etc/hosts"; sed 's/^/    /' $T/$n/fs/etc/hosts
  echo "--- $n daemon.log (last 15)"; tail -15 $T/$n/daemon.log | cut -c1-240 | sed 's/^/    /'
  echo "--- $n merge2.log (reconcile, HOSTS, SERVICE, WALK lines)"; grep -E 'reconcile|HOSTS|SERVICE|ELECT|WALK|PROBE|echo' $T/$n/merge2.log | head -30 | cut -c1-240 | sed 's/^/    /'
done
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
