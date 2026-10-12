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
T=${T:-/tmp/claude-0/netns-lat}; rm -rf $T; mkdir -p $T/hub $T/z $T/a $T/y $T/dup
export RIBBIT_TEMPLATES=$H/../fdf/templates/fdf.prime FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY
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
# ---- apply helper (the daemon's step), timed
apply() { local n=$1 NAME=$2 SUB=$3; shift 3
  NS $n env FDF_FACTS=$T/$n/facts.json FROGNET_DIR=$FN NODE_NAME=$NAME NODE_GUID=g-$NAME LOCAL_SUBNET=$SUB PUBKEY="$([ -f $T/$n/wg.key ] && wg pubkey < $T/$n/wg.key)" \
     STATE=$T/$n/applier.pkl FROGNET_REGION_HOSTS=$T/$n/region_hosts HOLD_DOWN_SEC=${HOLD:-60} python3 $H/tools/apply_once.py "$@" 2>&1 | sed "s/^/[$n] /" | tee -a $T/$n/apply.log | grep -E "GRAPH|APPLY|refused|conflict|GEN" ; }
for r in 1 2; do apply z z 10.3.3.0/24 >/dev/null; apply a a 10.7.7.0/24 >/dev/null; apply y y 10.2.2.0/24 >/dev/null; sleep 2; done
echo "=== steady. Now the measurements."
ms() { date +%s%3N; }
# ---- (1) the region's answer time: participants and map_list, 20 calls each, from a
for op in participants map_list; do
  t0=$(ms); for i in $(seq 20); do NS a curl -s -X POST -d "{\"op\":\"$op\",\"site\":\"$SITE\"}" "$U" > /dev/null; done; t1=$(ms)
  echo "region $op: $(( (t1-t0)/20 )) ms per call (curl, incl. process start)"; done
NS a env FDF_FACTS=$T/a/facts.json FROGNET_DIR=$FN PYTHONPATH=$FN python3 - <<PY
import time, os, sys
sys.path.insert(0, os.environ["FROGNET_DIR"])
from internet_tunnels_v3 import config as c, region
c.REGION_RAM = os.environ["RINA_RAM"]; c.REGION_AREA = os.environ["RINA_AREA"]
for op in ("participants", "mappings"):
    f = getattr(region, op); f()
    t0 = time.monotonic(); [f() for _ in range(20)]; print("region %s: %.1f ms per call (urllib, in-process)" % (op, (time.monotonic() - t0) / 20 * 1000))
PY
# ---- (2) fdf-facts one-shot cost on y (ip/iw/ping subprocesses)
t0=$(ms); NS y env FDF_FACTS=$T/y/facts.json FDF_LEASES=$T/y/leases FDF_NAME=y python3 $PK/bin/fdf-facts > /dev/null 2>&1; t1=$(ms); echo "fdf-facts publish (one shot, y): $((t1-t0)) ms"
# ---- (3) the pipeline: a stamped fdf-events reader on a; y's facts change; stamps at: lease written, facts.json written (watch loop),
#          client re-announced (client.log grows), event `p` seen on a, apply on a done
NS y env FDF_FACTS=$T/y/facts.json FDF_LEASES=$T/y/leases FDF_NAME=y python3 $PK/bin/fdf-facts --watch > $T/y/facts-watch.log 2>&1 &
sleep 2.5
NS a env RINA_RAM=10.77.0.1:8840 $B/fdf-events 2>/dev/null | while IFS= read -r l; do echo "$(ms) $l"; done > $T/a/events.log &
RDR_A=$!
sleep 1
CL0=$(wc -l < $T/y/client.log); M0=$(stat -c %Y $T/y/facts.json)
ip netns exec y ip link add lan1 type veth peer name lan1p; ip netns exec y ip link set lan1 up; ip netns exec y ip link set lan1p up
T_CHANGE=$(ms); ip netns exec y ip addr add 10.9.9.1/24 dev lan1          # y grows an address: a real `addrs` row change
T_FACTS=""; for i in $(seq 100); do [ "$(stat -c %Y $T/y/facts.json)" != "$M0" ] && grep -q 10.9.9.1 $T/y/facts.json && { T_FACTS=$(ms); break; }; sleep 0.05; done
T_ANN=""; for i in $(seq 100); do [ "$(wc -l < $T/y/client.log)" != "$CL0" ] && { T_ANN=$(ms); break; }; sleep 0.05; done
T_EVT=""; for i in $(seq 100); do l=$(grep -m1 ' p$' $T/a/events.log 2>/dev/null); [ -n "$l" ] && { T_EVT=${l%% *}; break; }; sleep 0.05; done
T_AP0=$(ms); apply a a 10.7.7.0/24 | grep -E 'APPLY_TIMED' | sed 's/^/    /'; T_AP1=$(ms)
echo "pipeline (y address added -> a replanned):"
echo "    change -> facts.json written (fdf-facts --watch: poll+debounce+publish): $((T_FACTS-T_CHANGE)) ms"
echo "    facts.json -> re-announced (fdf-client inotify + announce):            $((T_ANN-T_FACTS)) ms"
echo "    announce -> p event on a (region fanout + fdf-events):                 $((T_EVT-T_ANN)) ms"
echo "    apply on a (participants + map_list + plan + kernel):                  $((T_AP1-T_AP0)) ms (process start included)"
echo "    end to end:                                                             $((T_AP1-T_CHANGE)) ms"
grep -E 'timed|GEN' $T/a/apply.log | tail -2 | sed 's/^/    /'
echo "--- y facts-watch log:"; tail -3 $T/y/facts-watch.log | sed 's/^/    /'
# ---- (4) member death -> `m` on the others: the databasehost switchover's clock. Two ways to die.
kill $RDR_A 2>/dev/null; sleep 0.3
NS z env RINA_RAM=10.77.0.1:8840 $B/fdf-events 2>/dev/null | while IFS= read -r l; do echo "$(ms) $l"; done > $T/z/death1.log &
RDR_Z=$!; sleep 1
APID=$(ip netns pids a | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && echo $p; done | head -1)
T_KILL=$(ms); kill -9 $APID
T_M=""; for i in $(seq 600); do l=$(grep -m1 " m$" $T/z/death1.log 2>/dev/null | awk '{print $1}'); [ -n "$l" ] && { T_M=$l; break; }; sleep 0.05; done
echo "death, clean (fdf-client killed; the socket closes): m on z after $((T_M-T_KILL)) ms"
kill $RDR_Z 2>/dev/null
# a comes back, then dies silently (its link goes down: no FIN, the region must notice on its own)
NS a env FDF_FACTS=$T/a/facts.json FDF_NAME=a $B/fdf-client > $T/a/client2.log 2>&1 &
sleep 2
NS z env RINA_RAM=10.77.0.1:8840 $B/fdf-events 2>/dev/null | while IFS= read -r l; do echo "$(ms) $l"; done > $T/z/death2.log &
RDR_Z=$!; sleep 1
T_DOWN=$(ms); ip netns exec a ip link set v-a down
T_M2=""; for i in $(seq 1800); do l=$(grep -m1 " m$" $T/z/death2.log 2>/dev/null | awk '{print $1}'); [ -n "$l" ] && { T_M2=$l; break; }; sleep 0.05; done
echo "death, silent (link down; no FIN; TCP keepalive 5+3x5 s):  m on z after $((T_M2-T_DOWN)) ms"
kill $RDR_Z 2>/dev/null; ip netns exec a ip link set v-a up
echo "RESULT DONE"
