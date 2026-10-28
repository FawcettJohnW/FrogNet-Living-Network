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
# ADDINS=<dir with fdf-addin-kart-*.tgz and fdf-addin-comms-*.tgz> (with CORE_TGZ): the add-ins on those cores: their
# own RAMs, their own tuples, and what each does on a network event.
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
printf 'RINA_RAM=%s\nFDF_AREA=%s\nFDF_HUB_NAME=hubA\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-D\nFDF_FACTS=%s\n' $RAM $AREA $PUB $T/hub/facts.json > $T/hub/hub.env
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
  svc $T/l5/start.log ip netns exec l5 env FDF_TUN_NAME=fdft5 $T/l5/fdf/bin/fdf-start; L5SVC=$(tail -1 $T/svc.pids)
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
# ---- [ADDIN_V1] the add-ins, each its own bundle: kart (the service on the region holder and on a stand-alone machine,
# a game host and a player on the leaf), comms and DisasterComm on the leaf. Every tuple is its holder's own
# ([ADDIN_HOLDS_ITS_TUPLE_V1]): it is there while the holder runs and {} the moment it does not.
if [ -n "${CORE_TGZ:-}" ] && [ -n "${ADDINS:-}" ]; then
  # HAS <ns> <ram> <instance>: a live (non-empty) (pond, where, <instance>) cell, as read there
  HAS() { NS $1 curl -s -m 2 "http://$2$API?op=read&service=pond&variable=where" | python3 -c "import json,sys; i=sys.argv[1]
c=[x for x in json.load(sys.stdin).get('cells',[]) if x['instance']==i and x.get('bag')]; sys.exit(0 if c else 1)" "$3"; }
  ADD() { ls $ADDINS/fdf-addin-$1-linux-$(uname -m)-*.tgz $ADDINS/fdf-addin-$1-linux-any-*.tgz 2>/dev/null | head -1; }
  inst_addin() { local n=$1 a=$2 home=$3; shift 3; mkdir -p $T/$n/$a; tar -C $T/$n/$a -xzf "$(ADD $a)"
    NS $n env FDF_HOME=$home FDF_NO_SYSTEMD=1 "$@" sh $T/$n/$a/fdf-addin-*/install > $T/$n/$a/install.out 2>&1; }
  # the region holder: a core in the hub's sense (hub.env), the programs add-ins use from it
  mkdir -p $T/hub/fdf/bin; for b in fdf-where fdf-events rina-host; do ln -sf $T/bin/$b $T/hub/fdf/bin/$b; done
  mkdir -p $T/hub/fdf/lib; ln -sfn $PK/lib/python $T/hub/fdf/lib/python
  inst_addin hub kart $T/hub/fdf FDF_HUB_ENV=$T/hub/hub.env
  for a in kart comms dc; do inst_addin l5 $a $T/l5/fdf; done
  inst_addin l6 kart $T/l6/fdf
  check "add-ins installed (region holder, leaf, stand-alone): $(grep -h -m1 'files in' $T/hub/kart/install.out | cut -c1-70)" \
        "grep -q 'files in' $T/hub/kart/install.out && grep -q 'files in' $T/l5/dc/install.out && grep -q 'pages in' $T/l5/dc/install.out && grep -q 'files in' $T/l6/kart/install.out"
  mkdir -p $T/l7/kart; tar -C $T/l7/kart -xzf "$(ADD kart)"; FDF_HOME=$T/l7/fdf FDF_NO_SYSTEMD=1 sh $T/l7/kart/fdf-addin-*/install > $T/l7/kart/install.out 2>&1
  check "an add-in refuses a machine with no core: $(grep -m1 -o 'no FD&F core.*' $T/l7/kart/install.out | cut -c1-70)" "grep -q 'no FD&F core' $T/l7/kart/install.out"
  # stand-alone: the kart memory and service, its tuple in its own control RAM, a player finds it
  svc $T/l6/kart.log ip netns exec l6 env FDF_HOME=$T/l6/fdf $T/l6/fdf/addins/kart/start
  for j in $(seq 200); do HAS l6 127.0.0.1:8840 local/kart && break; sleep 0.05; done
  check "stand-alone: role $(grep -m1 -o 'role [a-z]*' $T/l6/kart.log); (pond, where, local/kart) held in its own control RAM" "HAS l6 127.0.0.1:8840 local/kart"
  NS l6 env FDF_HOME=$T/l6/fdf KART_PLAY_DRY=1 $T/l6/fdf/addins/kart/kart-play > $T/l6/play.out 2>&1
  check "stand-alone: kart-play finds the kart memory: $(head -1 $T/l6/play.out | cut -c1-70)" "grep -q 'at 127.0.0.1:8799' $T/l6/play.out"
  # the region: the kart service on its holder; the leaf a game host (KART_ROLE=host) and a player; comms and dc there too
  svc $T/hub/kart.log ip netns exec hub env FDF_HOME=$T/hub/fdf FDF_HUB_ENV=$T/hub/hub.env $T/hub/fdf/addins/kart/start; HUBKART=$(tail -1 $T/svc.pids)
  for j in $(seq 200); do HAS hub $RAM w:hubA/kart && break; sleep 0.05; done
  check "region holder: role $(grep -m1 -o 'role [a-z]*' $T/hub/kart.log); (pond, where, w:hubA/kart) held in the region" "HAS hub $RAM w:hubA/kart"
  svc $T/l5/kart.log ip netns exec l5 env FDF_HOME=$T/l5/fdf KART_ROLE=host $T/l5/fdf/addins/kart/start
  svc $T/l5/comms.log ip netns exec l5 env FDF_HOME=$T/l5/fdf $T/l5/fdf/addins/comms/start
  svc $T/l5/dc.log ip netns exec l5 env FDF_HOME=$T/l5/fdf $T/l5/fdf/addins/dc/start; L5DC=$(tail -1 $T/svc.pids)
  for j in $(seq 300); do HAS hub $RAM w:l5/comms && HAS hub $RAM w:l5/dc && break; sleep 0.05; done
  check "leaf: (pond, where, w:l5/comms) and w:l5/dc in the region, each its own cell" "HAS hub $RAM w:l5/comms && HAS hub $RAM w:l5/dc"
  NS l5 env FDF_HOME=$T/l5/fdf KART_PLAY_DRY=1 $T/l5/fdf/addins/kart/kart-play > $T/l5/play.out 2>&1
  check "leaf: kart-play resolves the kart memory on the region holder: $(head -1 $T/l5/play.out | cut -c1-60)" "grep -q 'at 10.253.253.254:8799' $T/l5/play.out"
  KM() { NS hub curl -s -m 2 "http://127.0.0.1:8799/ram.php?op=read&service=kart&variable=$1"; }
  for j in $(seq 300); do KM gamehost | grep -q '"l5"' && break; sleep 0.05; done
  check "leaf as a game host: (kart, gamehost, l5) in the kart memory: $(KM gamehost | grep -o '"bag":{[^}]*}' | head -1 | cut -c1-80)" "KM gamehost | grep -q '\"l5\"'"
  check "l1 reaches the kart memory through the hub (10.253.253.254:8799)" "NS l1 curl -s -m 2 'http://10.253.253.254:8799/ram.php?op=read&service=kart' | grep -q '\"ok\":true'"
  # a network event: l5's core goes (its member and tunnel): every tuple l5's add-ins hold goes with their sessions
  kill $L5SVC; for p in $(ip netns pids l5); do c=$(cat /proc/$p/comm 2>/dev/null); [ "$c" = java ] || [ "$c" = fdf-tun ] && kill -9 $p; done
  for j in $(seq 400); do HAS hub $RAM w:l5/comms || HAS hub $RAM w:l5/dc || break; sleep 0.05; done
  check "l5's core gone: w:l5/comms and w:l5/dc are {} (gone with their holders' sessions)" "! HAS hub $RAM w:l5/comms && ! HAS hub $RAM w:l5/dc"
  for j in $(seq 300); do grep -q 'on-loss stay' $T/l5/dc.log && grep -q 'on-loss rediscover' $T/l5/comms.log && break; sleep 0.05; done
  check "dc (on-loss stay) and comms (on-loss rediscover) each did its own: $(grep -c 'on-loss' $T/l5/dc.log $T/l5/comms.log | tr '\n' ' ')" "grep -q 'on-loss stay' $T/l5/dc.log && grep -q 'on-loss rediscover' $T/l5/comms.log"
  svc $T/l5/start2.log ip netns exec l5 env FDF_TUN_NAME=fdft5 $T/l5/fdf/bin/fdf-start
  for j in $(seq 600); do HAS hub $RAM w:l5/comms && HAS hub $RAM w:l5/dc && break; sleep 0.05; done
  check "the core back: both tuples held again (dc stayed; comms rediscovered)" "HAS hub $RAM w:l5/comms && HAS hub $RAM w:l5/dc"
  # a crash: the holder killed outright, nothing cleared by hand -- its tuple goes with its session
  DCP=$(ps -o pid= --ppid $L5DC | head -1); kill $L5DC; kill -9 $(pgrep -P $DCP) $DCP 2>/dev/null
  for j in $(seq 200); do HAS hub $RAM w:l5/dc || break; sleep 0.05; done
  check "dc killed -9: its tuple is {} at once, comms' stays (nothing stale, nobody swept)" "! HAS hub $RAM w:l5/dc && HAS hub $RAM w:l5/comms"
  KP=$(ps -o pid= --ppid $HUBKART | head -1); kill $HUBKART; [ -n "$KP" ] && kill -TERM $KP
  for j in $(seq 200); do HAS hub $RAM w:hubA/kart || break; sleep 0.05; done
  check "the kart service stopped: w:hubA/kart is {}" "! HAS hub $RAM w:hubA/kart"
fi
echo "--- identity answerer"; grep -v "^\[svc" $T/hub/identity.log | tail -5
echo "--- l1 log"; grep -v JAVA_TOOL $T/l1/member.log | cut -c1-170 | head -12
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo FAIL) ($PASSES passed, $FAILS failed) $T"
[ $FAILS = 0 ]
