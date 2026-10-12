#!/bin/bash
# netns-graph-test.sh -- [GRAPH_NETNS_V1] PLAN-RAM-DISCOVERY §6 for REAL on one box: the RAM manager + fdf-hub in `hub`
# (the "Internet" is a veth bridge 10.77.0.0/24); two sites z (10.3.3.0/24) and a (10.7.7.0/24) with real uplinks
# (default route toward the hub's address, pinged by fdf-facts); a child y behind z (a veth LAN: z gives y the
# lease 10.3.3.20, y serves 10.2.2.0/24). Every node runs fdf-facts (its rows), fdf-client (announce + signed maps)
# and apply_once.py (the daemon's applier with the real World: admission probes, routes, hosts, maps). Then:
#   y -> a's .1 through z's LAN, z's tunnel, the hub, a's tunnel: the x->y->z->RAM->a chain of the plan;
#   the hub's allowed-ips per peer are exactly the plan's maps; the hosts sentinel names everyone;
#   y leaves: z holds, then withdraws; a duplicate /24 is refused at the RAM manager.
# Needs root, ip, wg, wireguard-go, python3. usage: tools/netns-graph-test.sh [RINAB_DIR]
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
PK=${PKG_FDF:-$H/../fdf}; FDFPY=$H/../fdf/lib/python
T=${T:-/tmp/claude-0/netns-graph}; rm -rf $T; mkdir -p $T/hub $T/z $T/a $T/y $T/dup
export RIBBIT_TEMPLATES=$H/../fdf/templates/fdf.prime FDF_WG_USERSPACE=1 FDF_PY=$FDFPY
API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret; U="http://10.77.0.1:8840$API?op=rina"
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { for n in hub z a y dup; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.3
            for n in hub z a y dup; do ip netns del $n 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
cat > $T/echo.py <<'EOF'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ok\n")
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
EOF
# ---- the Internet
ip link add br-inet type bridge && ip link set br-inet up
i=1; for n in hub z a dup; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 10.77.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=10.77.0.1:8840 RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=10.77.0.1 "$@"; }
# ---- the region and the hub
NS hub env -u RINA_MAP_AREA RINA_REGION_FILE=$T/hub/area.perm $B/rina-host --listen 10.77.0.1:8840 --api $API > $T/hub/host.log 2>&1 &
sleep 1; NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "$U" > /dev/null
NS hub env FDF_FACTS=$T/hub/facts.json $B/fdf-client > $T/hub/client.log 2>&1 &
sleep 0.5
NS hub env FDF_BIN=$B python3 $H/tools/fdf-hub --area $SITE --iface wg-hub --endpoint 10.77.0.1:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json > $T/hub/hub.log 2>&1 &
sleep 2; NS hub sysctl -q -w net.ipv4.ip_forward=1
check $([ "$(NS hub wg show wg-hub listen-port 2>/dev/null)" = 51820 ] && echo 0 || echo 1) "hub up on 51820"
# ---- the LAN behind z: a veth z<->y; z serves 10.3.3.0/24 on it and "gave" y the lease 10.3.3.20
ip netns add y; ip netns exec y ip link set lo up
ip link add v-zy type veth peer name v-yz; ip link set v-zy netns z; ip link set v-yz netns y
ip netns exec z ip addr add 10.3.3.1/24 dev v-zy; ip netns exec z ip link set v-zy up
ip netns exec y ip addr add 10.3.3.20/24 dev v-yz; ip netns exec y ip link set v-yz up
ip netns exec y ip route add default via 10.3.3.1 dev v-yz metric 600          # what the DHCP lease from z gives y
ip netns exec z iptables -t nat -A POSTROUTING -o v-z -j MASQUERADE            # z NATs its LAN to its uplink, as a FrogNet gateway does
YMAC=$(ip netns exec y cat /sys/class/net/v-yz/address)
printf '%d %s 10.3.3.20 y *\n' $(( $(date +%s) + 3600 )) "$YMAC" > $T/z/leases
: > $T/a/leases; : > $T/y/leases
for n in y a; do ip netns exec $n ip link add lan0 type veth peer name lan0p; ip netns exec $n ip link set lan0 up; ip netns exec $n ip link set lan0p up; done     # the served /24 lives on a real-looking interface (no dummy module here)
ip netns exec y ip addr add 10.2.2.1/24 dev lan0
ip netns exec a ip addr add 10.7.7.1/24 dev lan0
# the sites' default routes point at the "Internet" (the hub's address answers the uplink ping)
ip netns exec z ip route add default via 10.77.0.1 dev v-z; ip netns exec a ip route add default via 10.77.0.1 dev v-a
for n in z a y; do ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; ip netns exec $n python3 $T/echo.py > /dev/null 2>&1 & done
# ---- a node: facts (role/name/guid/subnet/pubkey), its fdf-client, fdf-facts, and its tunnel from region.py (sites only)
node() { local n=$1 NAME=$2 SUB=$3; local one=${SUB%.0/24}.1
  local pub=""; if [ -n "${4:-}" ]; then (umask 077; NS $n wg genkey > $T/$n/wg.key); pub=$(wg pubkey < $T/$n/wg.key); fi
  printf '{"role":"node","name":"%s","guid":"g-%s","subnet":"%s","pubkey":"%s","transit":[],"maps":[]}\n' $NAME $NAME $SUB "$pub" > $T/$n/facts.json
  NS $n env FDF_FACTS=$T/$n/facts.json FDF_LEASES=$T/$n/leases FDF_NAME=$NAME python3 $PK/bin/fdf-facts > $T/$n/facts.log 2>&1
  NS $n env FDF_FACTS=$T/$n/facts.json FDF_NAME=$NAME $B/fdf-client > $T/$n/client.log 2>&1 &
  sleep 1
  if [ -n "${4:-}" ]; then
    NS $n env FDF_FACTS=$T/$n/facts.json PYTHONPATH=$FN python3 - > $T/$n/wg.conf <<EOF
import internet_tunnels_v3.config as c
c.REGION_RAM="10.77.0.1:8840"; c.REGION_AREA="$SITE"; c.LOCAL_SUBNET="$SUB"; c.NODE_NAME="$NAME"; c.NODE_GUID="g-$NAME"; c.PUBKEY="$pub"; c.POND="p"
from internet_tunnels_v3 import region
w = region.my_channels()["channels"][0]["wg_config"]
print("# Address = %s/%s\n[Interface]\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = %s\nAllowedIPs = %s\nPersistentKeepalive = 25" %
      (w["edge_ip"], w["edge_mask"], open("$T/$n/wg.key").read().strip(), w["droplet_pubkey"], w["droplet_endpoint"], w["allowed_ips"]))
EOF
    NS $n wireguard-go wg-$n 2>/dev/null; NS $n wg setconf wg-$n $T/$n/wg.conf
    NS $n ip addr add $(sed -n 's/^# Address = //p' $T/$n/wg.conf) dev wg-$n; NS $n ip link set wg-$n up
    NS $n env FDF_FACTS=$T/$n/facts.json FDF_LEASES=$T/$n/leases FDF_NAME=$NAME python3 $PK/bin/fdf-facts >> $T/$n/facts.log 2>&1   # now with its wg row
    sleep 1
  fi
}
node z z 10.3.3.0/24 tunnel; node a a 10.7.7.0/24 tunnel; node y y 10.2.2.0/24
echo "--- rows:"; for n in z a y; do echo "[$n] $(tail -1 $T/$n/facts.log)"; done
check $(grep -q '"kind": "given"' $T/z/facts.json && grep -q '"kind": "held"' $T/y/facts.json && echo 0 || echo 1) "rows: z wrote the lease it gave y, y wrote the lease it holds (mac $YMAC on both)"
check $(grep -q 'uplink eth [0-9]*"' $T/z/facts.json && ! grep -q dead $T/z/facts.json && echo 0 || echo 1) "rows: z's uplink is alive (ping out of v-z answered)"
# ---- apply: the daemon's step on every node, twice (facts first, then everyone's plan; then the maps have landed)
apply() { local n=$1 NAME=$2 SUB=$3; shift 3
  NS $n env FDF_FACTS=$T/$n/facts.json FROGNET_DIR=$FN NODE_NAME=$NAME NODE_GUID=g-$NAME LOCAL_SUBNET=$SUB PUBKEY="$([ -f $T/$n/wg.key ] && wg pubkey < $T/$n/wg.key)" \
     STATE=$T/$n/applier.pkl FROGNET_REGION_HOSTS=$T/$n/region_hosts HOLD_DOWN_SEC=${HOLD:-60} python3 $H/tools/apply_once.py "$@" 2>&1 | sed "s/^/[$n] /" | tee -a $T/$n/apply.log | grep -E "GRAPH|APPLY|refused|conflict" ; }
for r in 1 2; do apply z z 10.3.3.0/24; apply a a 10.7.7.0/24; apply y y 10.2.2.0/24; sleep 2; done
echo "--- z routes:"; NS z ip route show | sed 's/^/    /'
echo "--- y routes:"; NS y ip route show | sed 's/^/    /'
check $(NS z ip route show | grep -q "10.2.2.0/24 via 10.3.3.20 dev v-zy" && echo 0 || echo 1) "z: y's /24 via y's lease address (admitted: y is alive in the region, no probe)"
check $(NS z ip route show | grep -q "10.7.7.0/24 dev wg-z" && echo 0 || echo 1) "z: a's /24 through the hub"
check $(NS y ip route show | grep -q "default via 10.3.3.1 dev v-yz" && echo 0 || echo 1) "y: default route toward its exit z"
check $(NS a ip route show | grep -q "10.2.2.0/24 dev wg-a" && NS a ip route show | grep -q "10.3.3.0/24 dev wg-a" && echo 0 || echo 1) "a: z's and y's /24s through the hub"
check $(grep -q "10.2.2.1 FrogNetHost.y" $T/z/region_hosts && grep -q "10.7.7.1 FrogNetHost.a" $T/z/region_hosts && echo 0 || echo 1) "z: hosts sentinel names y and a"
sleep 2; echo "--- hub allowed-ips:"; NS hub wg show wg-hub allowed-ips | sed 's/^/    /'
ZPUB=$(wg pubkey < $T/z/wg.key); APUB=$(wg pubkey < $T/a/wg.key)
check $(NS hub wg show wg-hub allowed-ips | grep "$ZPUB" | grep -q "10.2.2.0/24" && NS hub wg show wg-hub allowed-ips | grep "$ZPUB" | grep -q "10.3.3.0/24" && echo 0 || echo 1) "hub: z's peer carries z's AND y's /24 (the plan's maps, signed by z)"
check $(NS hub wg show wg-hub allowed-ips | grep "$APUB" | grep -q "10.7.7.0/24" && ! (NS hub wg show wg-hub allowed-ips | grep "$APUB" | grep -q "10.2.2.0/24") && echo 0 || echo 1) "hub: a's peer carries a's /24 only"
P=$(NS y ping -c 3 -W 2 -I 10.2.2.1 10.7.7.1 2>&1); echo "$P" | tail -2 | sed 's/^/    /'
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "y -> a: LAN hop to z, z's tunnel, the hub, a's tunnel, and back"
P=$(NS a ping -c 2 -W 2 -I 10.7.7.1 10.2.2.1 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "a -> y: the reverse path"
# ---- steady: nothing applied on a third pass
OUT=$(apply z z 10.3.3.0/24); check $(echo "$OUT" | grep -q '"applied": false' && echo 0 || echo 1) "z: steady state applies nothing"
# ---- y leaves: z holds its route, then withdraws after the hold-down (3 s here)
ip netns pids y | while read p; do [ "$(cat /proc/$p/comm)" = fdf-client ] && kill $p; done; sleep 2
OUT=$(HOLD=3 apply z z 10.3.3.0/24); check $(echo "$OUT" | grep -q "held=\[.*10.2.2.0/24" && NS z ip route show | grep -q "10.2.2.0/24 via" && echo 0 || echo 1) "z: y vanished -> route HELD"
sleep 4; OUT=$(HOLD=3 apply z z 10.3.3.0/24); check $(! (NS z ip route show | grep -q "10.2.2.0/24 via") && echo "$OUT" | grep -q "routes_removed.*10.2.2.0/24" && echo 0 || echo 1) "z: still gone after the hold-down -> withdrawn, once"
sleep 2; check $(! (NS hub wg show wg-hub allowed-ips | grep "$ZPUB" | grep -q "10.2.2.0/24") && echo 0 || echo 1) "hub: z no longer carries y's /24 (z withdrew the mapping)"
# ---- a duplicate /24: dup claims 10.7.7.0/24 while a is alive -> refused at the RAM manager, waits
printf '{"role":"node","name":"dup","guid":"g-dup","subnet":"10.7.7.0/24","pubkey":"","addrs":[{"iface":"lo","ip":"10.7.7.1","value":"FrogNetHost.dup","kind":"own","mac":""}]}\n' > $T/dup/facts.json
NS dup env FDF_FACTS=$T/dup/facts.json FDF_NAME=dup $B/fdf-client > $T/dup/client.log 2>&1 &
sleep 2; check $(grep -q "JOIN REFUSED.*10.7.7.0/24 is served by a" $T/dup/client.log && echo 0 || echo 1) "RAM manager: a second claimant of 10.7.7.0/24 is refused, holder named, client waits"
echo "--- hub log:"; grep "FDF-HUB" $T/hub/hub.log | cut -c21- | tail -8
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
