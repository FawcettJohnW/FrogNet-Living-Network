#!/bin/bash
# netns-hub-test.sh -- [FDF_HUB_NETNS_V1] PLAN-FROGNET-ON-FDF Stage 1 test (1.4) and the Stage 2 wg_config, for REAL,
# on one Linux box: three network namespaces on a veth "Internet" (10.77.0.0/24), the RAM manager + fdf-hub in `hub`,
# two members n1 (10.1.1.0/24) and n2 (10.2.2.0/24) each with its fdf-client (facts + signed mapping) and a WireGuard
# tunnel to the hub built from region.py's my_channels() -- the daemon's wg_config, not a hand-written one. Then:
# ping n2's .1 from n1 THROUGH the hub, end n2's session, watch the hub drop the peer and the route. WireGuard is
# the kernel's where it exists, wireguard-go where it does not (a sandbox). Needs root, ip, wg, wireguard-go.
#   usage: tools/netns-hub-test.sh [RINAB_DIR]   (run after tools/build.sh + tools/build_rlite.sh; FROGNET_DIR = the
#          repo's opt/frognet_semantic, for region.py)
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/bin; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
T=${T:-/tmp/claude-0/netns-hub}; rm -rf $T; mkdir -p $T/hub $T/n1 $T/n2
R=$H/../fdf; export RIBBIT_TEMPLATES=$R/templates/fdf.prime FDF_WG_USERSPACE=1 FDF_PY=${FDF_PY:-$R/lib/python}   # fdf-hub imports fdf_graph from there ([NO_FALLBACK_V1])
API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
cleanup() { for n in hub hub2 leaf n1 n2; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.3
            for n in hub hub2 leaf n1 n2; do ip netns del $n 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
# ---- the Internet: a bridge, one veth per namespace, public addresses 10.77.0.{1,2,3}
ip link add br-inet type bridge && ip link set br-inet up
i=1; for n in hub n1 n2; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 10.77.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+1)); done
NS() { local n=$1; shift; ip netns exec $n env RINA_RAM=10.77.0.1:8840 "$@"; }
# ---- the region, in hub
NS hub env -u RINA_MAP_AREA RINA_REGION_FILE=$T/hub/area.perm $B/rina-host --listen 10.77.0.1:8840 --api $API > $T/hub/host.log 2>&1 &
sleep 1; U="http://10.77.0.1:8840$API?op=rina"
NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "$U" > /dev/null
# ---- the hub: its fdf-client (facts written by fdf-hub) and fdf-hub itself
NS hub env FDF_FACTS=$T/hub/facts.json $B/fdf-client > $T/hub/client.log 2>&1 &
sleep 0.5
NS hub env FDF_BIN=$B python3 $H/tools/fdf-hub --area $SITE --iface wg-hub --endpoint 10.77.0.1:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json > $T/hub/hub.log 2>&1 &
sleep 2
check $([ "$(NS hub wg show wg-hub listen-port 2>/dev/null)" = 51820 ] && echo 0 || echo 1) "hub: wg-hub up, listening on 51820"
HUBPUB=$(NS hub wg show wg-hub public-key)
# ---- a member: its own /24 on lo, its fdf-client with facts + mapping, its tunnel from region.py's wg_config
member() { local n=$1 A=$2; local sub=10.$A.$A.0/24 one=10.$A.$A.1
  NS $n ip addr add $one/24 dev lo
  NS $n wg genkey > $T/$n/wg.key; local pub=$(wg pubkey < $T/$n/wg.key)
  printf '{"role":"node","name":"Node%s","guid":"g-%s","subnet":"%s","pubkey":"%s","transit":[],"maps":[{"eid":"%s","rlocs":"hubA:1:50"}]}\n' $A $A $sub $pub $sub > $T/$n/facts.json
  NS $n env FDF_FACTS=$T/$n/facts.json RINA_AREA=$SITE RINA_SITE_KEY=$KEY $B/fdf-client > $T/$n/client.log 2>&1 &
  sleep 1
  # the daemon's wg_config for this node, from the region (region.py.my_channels), rendered exactly as _bring_up_tunnel does
  NS $n env FDF_FACTS=$T/$n/facts.json PYTHONPATH=$FN python3 - > $T/$n/wg.conf <<EOF
import json, internet_tunnels_v3.config as c
c.REGION_RAM="10.77.0.1:8840"; c.REGION_AREA="$SITE"; c.LOCAL_SUBNET="$sub"; c.NODE_NAME="Node$A"; c.NODE_GUID="g-$A"; c.PUBKEY="$pub"; c.POND="p"
from internet_tunnels_v3 import region
ch = region.my_channels()["channels"][0]; w = ch["wg_config"]
# _bring_up_tunnel writes Address= for wg-quick; wg setconf takes the rest, the address is added with ip below
print("# Address = %s/%s\n[Interface]\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = %s\nAllowedIPs = %s\nPersistentKeepalive = 25" %
      (w["edge_ip"], w["edge_mask"], open("$T/$n/wg.key").read().strip(), w["droplet_pubkey"], w["droplet_endpoint"], w["allowed_ips"]))
import sys; sys.stderr.write("remote_subnets=%s\n" % w["remote_subnets"])
EOF
  NS $n wireguard-go wg-$n 2>/dev/null; NS $n wg setconf wg-$n $T/$n/wg.conf
  local EDGE=$(sed -n 's/^# Address = //p' $T/$n/wg.conf)
  NS $n ip addr add $EDGE dev wg-$n; NS $n ip link set wg-$n up; NS $n ip route add 10.0.0.0/8 dev wg-$n src $one
}
T0=$(ms); member n1 1; T1=$(ms)
check $(grep -q "PrivateKey" $T/n1/wg.conf && grep -q "Endpoint = 10.77.0.1:51820" $T/n1/wg.conf && echo 0 || echo 1) "n1: wg_config came from region.py (hub's endpoint, edge 10.253.1.1/16)"
sleep 1.5
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.253.1.1/32" && echo 0 || echo 1) "hub: n1's peer present with its edge /32 (facts -> peer), $((T1-T0)) ms after the facts"
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.1.1.0/24" && echo 0 || echo 1) "hub: n1's /24 in allowed-ips (verified mapping -> prefix)"
check $(NS hub ip route show dev wg-hub | grep -q "10.1.1.0/24 via 10.253.1.1" && echo 0 || echo 1) "hub: route 10.1.1.0/24 via 10.253.1.1"
member n2 2; sleep 1.5
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && echo 0 || echo 1) "hub: n2's /24 in allowed-ips"
# ---- the data plane: n1 -> hub -> n2, through two WireGuard tunnels, the hub forwarding
NS hub sysctl -q -w net.ipv4.ip_forward=1
P=$(NS n1 ping -c 3 -W 2 -I 10.1.1.1 10.2.2.1 2>&1); echo "   $(echo "$P" | grep -o "[0-9]* packets transmitted.*")"
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "n1 -> n2: ping 10.2.2.1 from 10.1.1.1 THROUGH the hub (two tunnels, forwarded)"
check $(NS n1 wg show wg-n1 latest-handshakes | awk '{print ($2>0)}' | grep -q 1 && echo 0 || echo 1) "n1: handshake with the hub"
# ---- published != reachable, at the hub: a member whose session is up but tunnel is down keeps its peer (the hub's
#      job is the records); FrogNet's probes decide routes on the nodes. Not tested here (no node engine in the netns).
# ---- leave: end n2's session; the hub drops the peer and the route
T2=$(ms); ip netns pids n2 | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = fdf-client ] && kill $p; done
for i in $(seq 1 40); do NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" || break; sleep 0.1; done; T3=$(ms)
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && echo 1 || echo 0) "hub: n2's prefixes gone $((T3-T2)) ms after its session ended"
check $(NS hub wg show wg-hub peers | grep -q "$(wg pubkey < $T/n2/wg.key)" && echo 1 || echo 0) "hub: n2's peer deleted (gone is gone: no parked peers since r17)"
check $(NS hub ip route show dev wg-hub | grep -q "10.2.2.0/24" && echo 1 || echo 0) "hub: n2's route gone"
P=$(NS n1 ping -c 1 -W 1 -I 10.1.1.1 10.2.2.1 2>&1)
check $(echo "$P" | grep -q "100% packet loss" && echo 0 || echo 1) "n1 -> n2 now fails (control: the hub no longer forwards to a member that left)"
# ---- rejoin: n2 comes back; peer and route return; ping works again
NS n2 env FDF_FACTS=$T/n2/facts.json RINA_AREA=$SITE RINA_SITE_KEY=$KEY $B/fdf-client >> $T/n2/client.log 2>&1 &
T4=$(ms); for i in $(seq 1 60); do NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && break; sleep 0.1; done; T5=$(ms)
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && echo 0 || echo 1) "hub: n2 rejoined, peer + /24 back $((T5-T4)) ms after its session"
# the peer is new on the hub, so n2's tunnel is new too (the daemon tore it down with the session): set it again once the
# hub's facts hold n2's key ([HUB_SAYS_ITS_PEERS_V1]), as my_channels does
retunnel() { local n=$1; local pub=$(wg pubkey < $T/$n/wg.key)
  for i in $(seq 1 100); do grep -q "$pub" $T/hub/facts.json && break; sleep 0.05; done
  NS $n wg set wg-$n peer $HUBPUB remove; NS $n wg setconf wg-$n $T/$n/wg.conf; }
retunnel n2
P=$(NS n1 ping -c 2 -W 2 -I 10.1.1.1 10.2.2.1 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "n1 -> n2 works again (a new peer on the hub, a new tunnel on n2)"
# ---- [KEEPALIVE_5S_V1] silent loss: n2's link goes down (no FIN, nothing said). The RAM manager's kernel keepalive
#      (2 s idle, 3 probes 1 s apart) declares the session dead at ~5 s; the hub drops the peer at that instant.
T6=$(ms); NS n2 ip link set v-n2 down
for i in $(seq 1 400); do NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" || break; sleep 0.1; done; T7=$(ms)
D=$(( (T7-T6)/1000 ))
check $([ $D -ge 3 ] && [ $D -le 8 ] && echo 0 || echo 1) "silent loss: hub dropped n2 $((T7-T6)) ms after its link died (keepalive bound 5 s, no FIN was sent)"
grep -q "session down.*after" $T/hub/host.log && echo "   RAM manager: $(grep -c 'session down' $T/hub/host.log) session-down events so far"
NS n2 ip link set v-n2 up
NS n2 env FDF_FACTS=$T/n2/facts.json RINA_AREA=$SITE RINA_SITE_KEY=$KEY $B/fdf-client >> $T/n2/client.log 2>&1 &
T8=$(ms); for i in $(seq 1 60); do NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && break; sleep 0.1; done; T9=$(ms)
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.2.2.0/24" && echo 0 || echo 1) "link back, client back: prefixes restored $((T9-T8)) ms after the new session"
retunnel n2
P=$(NS n1 ping -c 2 -W 2 -I 10.1.1.1 10.2.2.1 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "n1 -> n2 works after the silent loss and return"
# ---- Stage 4: a LEAF. A machine with wg and one record: role=leaf, pubkey. No /24, no FrogNet, no maps, no address:
#      the region gives it its /32 with the announcement ([LEAF_ADDR_FROM_THE_REGION_V1]); the hub installs it from the
#      stored facts (a leaf is reachable by its address, there is nothing to map).
ip netns add leaf; ip netns exec leaf ip link set lo up
ip link add v-leaf type veth peer name v-leaf-b; ip link set v-leaf netns leaf; ip link set v-leaf-b master br-inet up
ip netns exec leaf ip addr add 10.77.0.9/24 dev v-leaf; ip netns exec leaf ip link set v-leaf up; mkdir -p $T/leaf
NS leaf wg genkey > $T/leaf/wg.key; LPUB=$(wg pubkey < $T/leaf/wg.key)
printf '{"role":"leaf","name":"Laptop","guid":"g-leaf","pubkey":"%s"}\n' $LPUB > $T/leaf/facts.json
NS leaf env FDF_FACTS=$T/leaf/facts.json $B/fdf-client > $T/leaf/client.log 2>&1 &
for i in $(seq 1 100); do grep -q "gave this leaf" $T/leaf/client.log && break; sleep 0.05; done
LADDR=$(grep -m1 -o "gave this leaf [0-9./]*" $T/leaf/client.log | awk '{print $4}'); LIP=${LADDR%/32}
check $([ -n "$LIP" ] && echo 0 || echo 1) "leaf: the region gave it ${LADDR:-nothing} (fdf-client printed it; nobody typed one)"
for i in $(seq 1 100); do grep -q "$LPUB" $T/hub/facts.json && break; sleep 0.05; done   # [HUB_SAYS_ITS_PEERS_V1]
NS leaf wireguard-go wg-leaf 2>/dev/null
printf '[Interface]\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = 10.77.0.1:51820\nAllowedIPs = 10.0.0.0/8\nPersistentKeepalive = 25\n' "$(cat $T/leaf/wg.key)" "$HUBPUB" > $T/leaf/wg.conf
NS leaf wg setconf wg-leaf $T/leaf/wg.conf; NS leaf ip addr add $LADDR dev wg-leaf; NS leaf ip link set wg-leaf up; NS leaf ip route add 10.0.0.0/8 dev wg-leaf
T10=$(ms); for i in $(seq 1 60); do NS hub wg show wg-hub allowed-ips | grep -q "$LADDR" && break; sleep 0.1; done; T11=$(ms)
check $(NS hub wg show wg-hub allowed-ips | grep -q "$LADDR" && echo 0 || echo 1) "leaf: hub installed its /32 from the facts ($((T11-T10)) ms), no mapping needed"
NS leaf ping -c 1 -W 3 10.1.1.1 > /dev/null 2>&1          # first packet: the handshake (the leaf speaks first; the hub has no endpoint for it until then)
P=$(NS leaf ping -c 2 -W 2 10.1.1.1 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "leaf -> n1: ping 10.1.1.1 from the leaf through the hub"
P=$(NS n1 ping -c 2 -W 2 -I 10.1.1.1 $LIP 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "n1 -> leaf: ping $LIP (the hub routes the /32 to the leaf's peer)"
# ---- Stage 5 (hub side): a SECOND hub. Members tunnel to both; their locator sets name both; each hub installs the
#      same prefixes independently; traffic flows through whichever hub the member's route points at.
ip netns add hub2; ip netns exec hub2 ip link set lo up
ip link add v-hub2 type veth peer name v-hub2-b; ip link set v-hub2 netns hub2; ip link set v-hub2-b master br-inet up
ip netns exec hub2 ip addr add 10.77.0.4/24 dev v-hub2; ip netns exec hub2 ip link set v-hub2 up; mkdir -p $T/hub2
NS hub2 env FDF_FACTS=$T/hub2/facts.json $B/fdf-client > $T/hub2/client.log 2>&1 &
sleep 0.5
# (wireguard-go's control socket is named by interface and is NOT per-namespace: hub B's interface needs its own name)
NS hub2 env FDF_BIN=$B python3 $H/tools/fdf-hub --area $SITE --iface wg-hubB --endpoint 10.77.0.4:51820 --name hubB \
   --key $T/hub2/wg.key --listen 51820 --facts $T/hub2/facts.json > $T/hub2/hub.log 2>&1 &
sleep 2; NS hub2 sysctl -q -w net.ipv4.ip_forward=1; HUB2PUB=$(NS hub2 wg show wg-hubB public-key)
check $([ -n "$HUB2PUB" ] && echo 0 || echo 1) "hubB: up; its ipcp row (role=hub) is in the region"
second_tunnel() { local n=$1 A=$2
  NS $n wireguard-go wg2-$n 2>/dev/null
  printf '[Interface]\nPrivateKey = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = 10.77.0.4:51820\nAllowedIPs = 10.0.0.0/8\nPersistentKeepalive = 25\n' "$(cat $T/$n/wg.key)" "$HUB2PUB" > $T/$n/wg2.conf
  NS $n wg setconf wg2-$n $T/$n/wg2.conf; NS $n ip addr add 10.253.$A.$A/16 dev wg2-$n 2>/dev/null; NS $n ip link set wg2-$n up
  # the daemon's publish_maps after bringup: both hubs in the locator set, prio by RTT (hubA first here)
  printf '{"role":"node","name":"Node%s","guid":"g-%s","subnet":"10.%s.%s.0/24","pubkey":"%s","transit":[],"maps":[{"eid":"10.%s.%s.0/24","rlocs":"hubA:1:50,hubB:2:50"}]}\n' $A $A $A $A "$(wg pubkey < $T/$n/wg.key)" $A $A > $T/$n/facts.json.tmp && mv $T/$n/facts.json.tmp $T/$n/facts.json
}
second_tunnel n1 1; second_tunnel n2 2; sleep 2
check $(NS hub2 wg show wg-hubB allowed-ips | grep -q "10.1.1.0/24" && NS hub2 wg show wg-hubB allowed-ips | grep -q "10.2.2.0/24" && echo 0 || echo 1) "hubB: both members' /24s installed from the two-locator mappings"
check $(NS hub wg show wg-hub allowed-ips | grep -q "10.1.1.0/24" && echo 0 || echo 1) "hubA: still has them (each hub reads the same mappings independently)"
# the member's route choice is FrogNet's (routes.py, measured RTT, incumbency hold); here we flip it by hand to show
# the hub side: the SAME prefix reachable through hub B when hub A is gone
ip netns pids hub | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = python3 ] && kill $p; done   # hub A's agent dies
NS n1 ip route replace 10.0.0.0/8 dev wg2-n1 src 10.1.1.1; NS n2 ip route replace 10.0.0.0/8 dev wg2-n2 src 10.2.2.1
NS n2 ping -c 1 -W 2 -I 10.2.2.1 10.253.253.254 > /dev/null 2>&1
P=$(NS n1 ping -c 3 -W 2 -I 10.1.1.1 10.2.2.1 2>&1)
check $(echo "$P" | grep -q " 0% packet loss" && echo 0 || echo 1) "hub A gone, members' routes on hub B: n1 -> n2 through hub B"
echo "--- hubB log:"; grep "FDF-HUB" $T/hub2/hub.log | cut -c21- | tail -4
echo "--- hub log:"; grep "FDF-HUB" $T/hub/hub.log | cut -c21- | tail -12
echo "--- hub wg:"; NS hub wg show wg-hub | grep -v "private\|preshared"
[ $FAILS = 0 ] && echo "RESULT PASS" || echo "RESULT FAIL: $FAILS"
