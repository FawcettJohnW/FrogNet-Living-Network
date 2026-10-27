#!/bin/bash
# netns-leaf-tunnel-test.sh -- [LEAF_BY_INVITE_V1] a leaf (the Kotlin member, as the Linux leaf, the Windows bridge and
# the phone all run it) joins a region by its invitation alone, in namespaces, with the real programs:
#   hub  198.18.0.1   fdf-hub --iface-only, rina-host on 10.253.253.254:8840 (inside the tunnel only), map_site,
#                     fdf-hub --admitted, its fdf-client; fdf-invite NAME --leaf for each leaf
#   l1, l2            wireguard-go fdf-leaf; `fdf-member-kt leaf fdfinv1.<...>`: the tunnel from the invitation, then
#                     the region inside it
# Checks: the region is not reachable before the tunnel; the leaf's tunnel, handshake and session; its address is its
# edge (the region allocates nothing for it); leaf <-> hub and leaf <-> leaf through the hub; the invitation is kept
# (0600) and a start with no line uses it; a line that is not an invitation is refused before anything is written.
# Needs root, ip, wg, wireguard-go, python3, curl.
# CORE_TGZ=<fdf-core-linux-<arch>-<build>.tgz>: also the core bundle, installed and started as a machine would: a leaf by
# its invitation (l5) and stand-alone (l6).
# usage: netns-leaf-tunnel-test.sh <dir with rina-host, fdf-client> <fdf-member-kt launcher> <fdf package dir (bin/, lib/python/, templates/)>
set -u
B=$1; K=$2; PK=$3; T=${T:-/tmp/claude-0/netns-leaf-invite}; rm -rf $T; mkdir -p $T/hub/admitted $T/l1 $T/l2 $T/l3 $T/l4 $T/l5 $T/l6 $T/bin
API=/FawcettInnovations.RINA.ram_interface.php; AREA=D; KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
HA=10.253.253.254; RAM=$HA:8840; U="http://$RAM$API?op=rina"; PUB=198.18.0.1
export FDF_WG_IMPL=wireguard-go FDF_PY=$PK/lib/python RIBBIT_TEMPLATES=$PK/templates/fdf.prime
ms() { date +%s%3N; }
FAILS=0; PASSES=0; check() { if eval "$2"; then echo "PASS  $1"; PASSES=$((PASSES+1)); else echo "FAIL  $1"; FAILS=$((FAILS+1)); fi; }
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in hub l1 l2 l3 l4 l5 l6; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; ip link del v-$n-b 2>/dev/null; done; ip link del br-leaf 2>/dev/null; }
[ -n "${KEEP:-}" ] || trap cleanup EXIT; cleanup
cp $B/fdf-client $B/fdf-events $B/fdf-where $B/rina-host $T/bin/; cp $PK/bin/fdf-hub $PK/bin/fdf-invite $PK/bin/fdf-identity $T/bin/; cp "$(command -v wireguard-go)" $T/bin/
export PATH=$T/bin:$PATH
ip link add br-leaf type bridge && ip link set br-leaf up
i=1; for n in hub l1 l2 l3 l4 l5 l6; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-leaf up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
# ---- the region, as fdf-region-create makes it
printf '%s\n' "$KEY" > $T/hub/site-key-$AREA; chmod 600 $T/hub/site-key-$AREA
printf 'RINA_RAM=%s\nFDF_AREA=%s\nFDF_HUB_NAME=hubA\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\n' $RAM $AREA $PUB > $T/hub/hub.env
NS hub python3 $T/bin/fdf-hub --area $AREA --iface wg-D --endpoint $PUB:51820 --listen 51820 --key $T/hub/wg.key --iface-only > $T/hub/iface.log 2>&1
NS hub env RINA_REGION_FILE=$T/hub/area.perm RINA_NET_CONFIG=$T/hub/none $T/bin/rina-host --listen $RAM --api $API > $T/hub/host.log 2>&1 &
for i in $(seq 100); do NS hub curl -s -m 1 -X POST -d '{"op":"version"}' "$U" > /dev/null && break; sleep 0.05; done
R=$(NS hub curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$AREA\",\"key\":\"$KEY\"}" "$U"); case "$R" in *'"ok":true'*) ;; *) echo "FAIL  map_site: $R"; exit 1 ;; esac
NS hub sysctl -q -w net.ipv4.ip_forward=1
svc $T/hub/hub.log ip netns exec hub env RINA_RAM=$RAM FDF_BIN=$T/bin python3 $T/bin/fdf-hub --area $AREA --iface wg-D --endpoint $PUB:51820 --name hubA \
   --key $T/hub/wg.key --listen 51820 --facts $T/hub/facts.json --admitted $T/hub/admitted
for i in $(seq 100); do grep -q "start (adopt)" $T/hub/hub.log && break; sleep 0.05; done
svc $T/hub/client.log ip netns exec hub env RINA_RAM=$RAM FDF_FACTS=$T/hub/facts.json FDF_NAME=hubA $T/bin/fdf-client
# the region's identity answerer (fdf-identity.service on the machine that holds the region)
svc $T/hub/identity.log ip netns exec hub env RINA_RAM=$RAM FDF_BIN=$T/bin FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub \
   python3 $T/bin/fdf-identity --serve
# ---- invitations: what the admin runs on the region's machine
inv() { NS hub env FDF_HUB_ENV=$T/hub/hub.env FDF_ADMITTED=$T/hub/admitted FDF_SITE_KEYS=$T/hub python3 $T/bin/fdf-invite "$@"; }
for n in l1 l2; do inv $n --leaf > $T/$n/invite.out 2>&1; grep -m1 '^fdfinv1\.' $T/$n/invite.out > $T/$n/line; done
check "invited l1 and l2 as leaves: $(head -1 $T/l1/invite.out | cut -c1-100)" "[ -s $T/l1/line ] && [ -s $T/l2/line ]"
E1=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l1.json'))['edge'])"); E2=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l2.json'))['edge'])")
P1=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l1.json'))['pubkey'])")
# ---- the leaves: an interface (what the start-up makes), then the member with the line
# one interface name per namespace: wireguard-go's control socket (/var/run/wireguard/<iface>.sock) is not per namespace
leafwg() { NS $1 wireguard-go fdf-$1 >/dev/null 2>&1; for j in $(seq 100); do NS $1 wg show fdf-$1 >/dev/null 2>&1 && break; sleep 0.05; done; }
leafwg l1; leafwg l2
check "before any tunnel the region is out of reach from a leaf" "! NS l1 curl -s -m 1 -X POST -d '{\"op\":\"version\"}' '$U' >/dev/null"
# red: not an invitation -> refused, nothing kept
NS l1 env FDF_INVITE=$T/l1/kept FDF_LEAF_WG=fdf-l1 $K leaf "fdfinv1.bm90IGpzb24" > $T/l1/bad.log 2>&1
check "a line that does not decode is refused and nothing is kept: $(grep -v JAVA_TOOL $T/l1/bad.log | grep -m1 -o 'does not decode.*' | cut -c1-60)" "[ ! -e $T/l1/kept ] && grep -q 'does not decode' $T/l1/bad.log"
T0=$(ms)
svc $T/l1/member.log ip netns exec l1 env FDF_INVITE=$T/l1/kept FDF_LEAF_WG=fdf-l1 $K leaf "$(cat $T/l1/line)"
svc $T/l2/member.log ip netns exec l2 env FDF_INVITE=$T/l2/kept FDF_LEAF_WG=fdf-l2 $K leaf "$(cat $T/l2/line)"
for j in $(seq 400); do grep -q "joined the region" $T/l1/member.log && grep -q "joined the region" $T/l2/member.log && break; sleep 0.05; done; T_J=$(ms)
echo "      both leaves joined in $((T_J-T0)) ms from their start (JVM start included)"
check "l1: tunnel from the invitation: $(grep -m1 -o 'tunnel: fdf-l1.*' $T/l1/member.log | cut -c1-110)" "grep -q 'tunnel: fdf-l1 $E1/16, peer hubA' $T/l1/member.log"
check "l1: joined the region inside the tunnel" "grep -q 'joined the region' $T/l1/member.log"
check "l1: a WireGuard handshake with the hub" "[ \"\$(NS l1 wg show fdf-l1 latest-handshakes | awk '{print \$2}')\" != 0 ]"
PJ=$(NS hub curl -s -X POST -d '{"op":"participants"}' "$U")
echo "$PJ" > $T/participants.json
A1=$(python3 -c "
import json,sys; j=json.load(open('$T/participants.json'))
print(next((p['facts'].get('addr','') for p in j['participants'] if (p.get('facts') or {}).get('name')=='l1'),''))")
check "l1's address in the region is its edge, given by no allocation ($A1)" "[ '$A1' = '$E1/32' ]"
check "the hub admits l1 (no NOT ADMITTED for it)" "! grep -q 'NOT ADMITTED: l1' $T/hub/hub.log"
check "hub peer for l1: allowed $E1/32" "NS hub wg show wg-D allowed-ips | grep -q '^$P1.$E1/32$'"
check "hub routes $E1 on-link" "NS hub ip route show dev wg-D | grep -q '^$E1 '"
check "l1 -> hub $HA" "NS l1 ping -c3 -W1 -q $HA >/dev/null"
check "hub -> l1 $E1" "NS hub ping -c3 -W1 -q $E1 >/dev/null"
check "l1 -> l2 $E2 (leaf to leaf through the hub)" "NS l1 ping -c3 -W1 -q $E2 >/dev/null"
check "l2 -> l1 $E1" "NS l2 ping -c3 -W1 -q $E1 >/dev/null"
check "the invitation is kept, 0600: $(stat -c '%a %n' $T/l1/kept 2>&1)" "[ \"\$(stat -c %a $T/l1/kept)\" = 600 ] && cmp -s <(tr -d '\n' < $T/l1/kept) <(tr -d '\n' < $T/l1/line)"
# ---- a start with no line: the kept invitation (as at boot)
J1=$(ip netns pids l1 | while read p; do [ "$(cat /proc/$p/comm 2>/dev/null)" = java ] && echo $p; done | head -1)
L1SVC=$(sed -n 3p $T/svc.pids); kill $L1SVC; kill -9 $J1
for j in $(seq 100); do kill -0 $J1 2>/dev/null || break; sleep 0.05; done
: > $T/l1/again.log
svc $T/l1/again.log ip netns exec l1 env FDF_INVITE=$T/l1/kept FDF_LEAF_WG=fdf-l1 $K leaf
for j in $(seq 400); do grep -q "joined the region" $T/l1/again.log && break; sleep 0.05; done
check "a start with no line joins with the kept invitation" "grep -q 'joined the region' $T/l1/again.log && ! grep -q 'invitation kept' $T/l1/again.log"
check "l1 -> l2 again after the restart" "NS l1 ping -c3 -W1 -q $E2 >/dev/null"
# ---- [IDENTITY_FROM_ANY_MEMBER_V1] the admin, at a terminal on a connected machine (l2), asks for a new identity
ID() { local n=$1; shift; NS $n env RINA_RAM=$RAM FDF_PY=$PK/lib/python python3 $T/bin/fdf-identity "$@"; }
ID l3 l3 --leaf > $T/l3/id.out 2> $T/l3/id.err
check "an unconnected machine cannot ask: $(tail -1 $T/l3/id.err | cut -c1-90)" "! grep -q '^fdfinv1\.' $T/l3/id.out"
ID l2 l1 --leaf > $T/l2/again.out 2>&1
check "an existing name is refused without --again: $(grep -m1 -o 'refused.*' $T/l2/again.out | cut -c1-90)" "grep -q 'already admitted' $T/l2/again.out && grep -q 'tunnel: fdf-l1' $T/l1/again.log"
T0=$(ms); ID l2 l3 --leaf > $T/l2/id.out 2> $T/l2/id.err; T1=$(ms)
grep -m1 '^fdfinv1\.' $T/l2/id.out > $T/l3/line
check "from l2: a new identity for l3 in $((T1-T0)) ms: $(head -1 $T/l2/id.out | cut -c1-90)" "[ -s $T/l3/line ] && [ -f $T/hub/admitted/l3.json ]"
check "nothing of the exchange is left in the RAM (the secret was there only between the two)" \
      "[ \"\$(NS hub curl -s '$(echo http://$RAM)/FawcettInnovations.RINA.ram_interface.php?op=read&service=fdf-identity')\" = '{\"ok\":true,\"cells\":[]}' ]"
E3=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l3.json'))['edge'])")
leafwg l3
svc $T/l3/member.log ip netns exec l3 env FDF_INVITE=$T/l3/kept FDF_LEAF_WG=fdf-l3 $K leaf "$(cat $T/l3/line)"
for j in $(seq 400); do grep -q "joined the region" $T/l3/member.log && break; sleep 0.05; done
check "l3 joins with the identity l2 fetched" "grep -q 'joined the region' $T/l3/member.log"
check "l3 -> l1 $E1" "NS l3 ping -c3 -W1 -q $E1 >/dev/null"
check "l1 -> l3 $E3" "NS l1 ping -c3 -W1 -q $E3 >/dev/null"
# ---- the bridge (the Windows bridge's and the phone's path: Bridge + fdf-tun on one TUN), from an invitation
TUNB=$(dirname "$0")/../tun/out/fdf-tun-$(uname -m)
ID l1 l4 --leaf > $T/l1/id4.out 2>&1; grep -m1 '^fdfinv1\.' $T/l1/id4.out > $T/l4/line
E4=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l4.json'))['edge'])")
svc $T/l4/member.log ip netns exec l4 env FDF_INVITE=$T/l4/kept FDF_FORWARD_FILE=$T/l4/forward FDF_LEDGER=$T/l4/aliases.json \
   FDF_TUN_BIN=$TUNB FDF_TUN_NAME=fdft4 $K bridge "$(cat $T/l4/line)"
for j in $(seq 400); do grep -q "joined the region" $T/l4/member.log && break; sleep 0.05; done
check "bridge l4 joins with its invitation: $(grep -m1 -o 'tunnel: .*invitation.*' $T/l4/member.log | cut -c1-90)" "grep -q 'joined the region' $T/l4/member.log"
check "bridge l4 -> l1 $E1" "NS l4 ping -c3 -W1 -q $E1 >/dev/null"
check "l1 -> bridge l4 $E4" "NS l1 ping -c3 -W1 -q $E4 >/dev/null"
# ---- [CORE_BUNDLE_LINUX_V1] the core bundle: unpacked, installed, started by fdf-start, as on a machine
if [ -n "${CORE_TGZ:-}" ]; then
  for n in l5 l6; do mkdir -p $T/$n/pkg; tar -C $T/$n/pkg -xzf "$CORE_TGZ"; done
  ID l1 l5 --leaf > $T/l1/id5.out 2>&1; grep -m1 '^fdfinv1\.' $T/l1/id5.out > $T/l5/line
  E5=$(python3 -c "import json;print(json.load(open('$T/hub/admitted/l5.json'))['edge'])")
  NS l5 env FDF_HOME=$T/l5/fdf FDF_NO_SYSTEMD=1 sh $T/l5/pkg/fdf-core-*/install --invite "$(cat $T/l5/line)" > $T/l5/install.out 2>&1
  check "core install (leaf): $(grep -m1 'starts as' $T/l5/install.out)" "grep -q 'starts as: leaf' $T/l5/install.out && [ \"\$(stat -c %a $T/l5/fdf/var/invite)\" = 600 ]"
  NS l5 env FDF_HOME=$T/l5/fdf FDF_NO_SYSTEMD=1 sh $T/l6/pkg/fdf-core-*/install --invite "fdfinv1.bm90" > $T/l5/bad.out 2>&1
  check "core install refuses a bad invitation and keeps the good one" "grep -q 'refused' $T/l5/bad.out && cmp -s <(tr -d '\n' < $T/l5/fdf/var/invite) <(tr -d '\n' < $T/l5/line)"
  svc $T/l5/start.log ip netns exec l5 env FDF_TUN_NAME=fdft5 $T/l5/fdf/bin/fdf-start
  for j in $(seq 400); do grep -q "joined the region" $T/l5/start.log && break; sleep 0.05; done
  check "core leaf: fdf-start joins by the kept invitation: $(grep -m1 'control:' $T/l5/start.log | cut -c1-90)" "grep -q 'joined the region' $T/l5/start.log && grep -q '^FDF_MODE=leaf' $T/l5/fdf/var/control.env"
  check "core leaf l5 -> l1 $E1" "NS l5 ping -c3 -W1 -q $E1 >/dev/null"
  check "l1 -> core leaf l5 $E5" "NS l1 ping -c3 -W1 -q $E5 >/dev/null"
  NS l6 env FDF_HOME=$T/l6/fdf FDF_NO_SYSTEMD=1 sh $T/l6/pkg/fdf-core-*/install > $T/l6/install.out 2>&1
  check "core install (no invitation): $(grep -m1 'starts as' $T/l6/install.out)" "grep -q 'starts as: standalone' $T/l6/install.out"
  svc $T/l6/start.log ip netns exec l6 $T/l6/fdf/bin/fdf-start
  for j in $(seq 100); do NS l6 curl -s -m 1 -X POST -d '{"op":"version"}' "http://127.0.0.1:8840$API?op=rina" > /dev/null && break; sleep 0.05; done
  check "core stand-alone: its own control RAM answers on 127.0.0.1:8840" "NS l6 curl -s -m 1 -X POST -d '{\"op\":\"version\"}' 'http://127.0.0.1:8840$API?op=rina' | grep -q '\"ok\":true' && grep -q '^FDF_MODE=standalone' $T/l6/fdf/var/control.env"
  check "core stand-alone: nothing listens outside loopback: $(NS l6 ss -H -lntu | awk '{print $5}' | tr '\n' ' ')" "! NS l6 ss -H -lntu | awk '{print \$5}' | grep -vq '^127\.'"
fi
echo "--- identity answerer"; grep -v "^\[svc" $T/hub/identity.log | tail -5
echo "--- l1 log"; grep -v JAVA_TOOL $T/l1/member.log | cut -c1-170 | head -12
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo FAIL) ($PASSES passed, $FAILS failed) $T"
[ $FAILS = 0 ]
