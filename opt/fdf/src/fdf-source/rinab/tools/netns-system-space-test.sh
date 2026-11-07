#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# netns-system-space-test.sh -- [SYSTEM_SPACE_V1] step 4, nodes (opt/fdf/docs/ORGANIZATIONS.md sections 4 and 6): the
# REAL tunnel daemon walking the system space's list -- two WAN RAM managers, then the LAN's from the highest IP down to
# its own -- with failover and failback, in network namespaces.
#   A 198.18.0.1   hubA: fdf-hub, fdf-ram on 10.253.253.254:8840 (the primary)
#   B 198.18.0.2   hubB: the same, the standby; the same area and site key (one system space)
#   z 198.18.0.3   node, serves 10.3.3.0/24 on v-zw; daemon, fdf-facts, fdf-client
#   w              node behind z: lease 10.3.3.20 on z's LAN, serves 10.4.4.0/24; its OWN tunnels (egress through z)
# Both nodes are admitted at both hubs with their one key; /opt/fdf/var/system-space.json lists hubA then hubB.
# Checks: both on hubA; hubA's RAM manager dies -> both on hubB at once (ms from the loss, the daemon's own line); it
# returns -> both stay on hubB [NO_FLAP_V1]; both die -> both on the LAN's highest (w's 10.4.4.1), no hub channel left
# on either, z still routes w's /24; hubA returns -> both off the LAN onto hubA.
# LOGIN=1: the nodes join the way an organization's do (ORGANIZATIONS.md section 3): each hub runs fdf-org with a level
# Nodes (no password, reach open), one join line names both (fdf-org line --with), and each node runs
# fdf-login --node with it -- which writes system-space.json for the daemon. Otherwise: invitations, and the test
# writes system-space.json from them.
# Needs root, ip, wg, wireguard-go, python3, curl, nft.  usage: [LOGIN=1] tools/netns-system-space-test.sh [RINAB_DIR]
set -u; H=$(cd "${1:-$(dirname "$0")/..}" && pwd); B=$H/build/static; FN=${FROGNET_DIR:-$H/../../../../frognet_semantic}
PK=${PKG_FDF:-$H/../fdf}; FDFPY=$H/../fdf/lib/python
T=${T:-/tmp/claude-0/netns-sysspace}; rm -rf $T; mkdir -p $T/A/admitted $T/B/admitted $T/z $T/w $T/bin
export RIBBIT_TEMPLATES=$H/../fdf/templates/fdf.prime FDF_WG_IMPL=wireguard-go FDF_PY=$FDFPY
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
API=/FawcettInnovations.RINA.ram_interface.php; SITE=frognet.test; KEY=s3cret; HA=10.253.253.254; RAM=$HA:8840
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
ms() { date +%s%3N; }
cleanup() { [ -f $T/svc.pids ] && kill $(cat $T/svc.pids) 2>/dev/null; rm -f $T/svc.pids
            for n in A B z w; do ip netns pids $n 2>/dev/null | xargs -r kill 2>/dev/null; done; sleep 0.5
            for n in A B z w; do for k in $(seq 50); do p=$(ip netns pids $n 2>/dev/null); [ -z "$p" ] && break; echo $p | xargs -r kill -9 2>/dev/null; sleep 0.1; done
              ip netns del $n 2>/dev/null; done
            for l in v-A-b v-B-b v-z-b; do ip link del $l 2>/dev/null; done; ip link del br-inet 2>/dev/null; }
trap cleanup EXIT; cleanup
WGGO=$(command -v wireguard-go || ls $H/build/wg/wireguard-go-$(uname -m) 2>/dev/null)
[ -x "$WGGO" ] || { echo "FAIL  setup: wireguard-go not found"; exit 1; }
cp $B/fdf-events $B/fdf-client $B/fdf-where $B/fdf-ram $B/ram $T/bin/ 2>/dev/null; [ -x $T/bin/fdf-ram ] || cp $H/build/bin/fdf-ram-static $T/bin/fdf-ram
cp $PK/bin/fdf-facts $PK/bin/fdf-hub $PK/bin/fdf-invite $PK/bin/fdf-region-join $PK/bin/fdf-org $PK/bin/fdf-login $T/bin/; cp "$WGGO" $T/bin/wireguard-go
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
# ---- the Internet: A, B, z
ip link add br-inet type bridge && ip link set br-inet up || { echo "FAIL  setup: br-inet exists from an earlier run"; exit 1; }
i=1; for n in A B z; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-inet up
  ip netns exec $n ip addr add 198.18.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; [ $n = A ] && i=2 || i=3; done
NS() { local n=$1; shift; ip netns exec $n "$@"; }
svc() { local log=$1; shift; ( while :; do "$@" >> $log 2>&1; echo "[svc] $(date +%T) ended rc=$?; restart in 2 s" >> $log; sleep 2; done ) & echo $! >> $T/svc.pids; }
# ---- two RAM hosts of ONE system space (same area, same site key), as fdf-region-create makes each
hostup() { local n=$1; NS $n env -u RINA_MAP_AREA RINA_REGION_FILE=$T/$n/area.perm RINA_NET_CONFIG=$T/$n/none $T/bin/fdf-ram --listen $RAM --api $API >> $T/$n/host.log 2>&1 &
           for i in $(seq 100); do NS $n curl -s -m 1 -X POST -d '{"op":"version"}' "http://$RAM$API?op=rina" > /dev/null && break; sleep 0.05; done
           NS $n curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"$SITE\",\"key\":\"$KEY\"}" "http://$RAM$API?op=rina" > /dev/null; }
hostdown() { local n=$1; for p in $(ip netns pids $n); do grep -q "fdf-ram" /proc/$p/cmdline 2>/dev/null && kill $p; done
             for i in $(seq 50); do ip netns pids $n | xargs -r -I{} grep -l fdf-ram /proc/{}/cmdline 2>/dev/null | grep -q . || break; sleep 0.1; done; }
parts() { NS $1 curl -s -m 3 -X POST -d '{"op":"participants"}' "http://$2$API?op=rina" | python3 -c 'import sys,json; print(" ".join(sorted(p["name"] for p in json.load(sys.stdin).get("participants",[]) if p.get("alive") and not p["name"].startswith("hub"))))' 2>/dev/null; }
ORG() { local n=$1; shift; NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY python3 $T/bin/fdf-org \"\$@\"" sh "$@"; }
for n in A B; do mkdir -p $T/$n/run-wg; PUB=$(NS $n ip -4 -o addr show dev v-$n | awk '{print $4}' | cut -d/ -f1)
  printf '%s\n' "$KEY" > $T/$n/site-key-$SITE; chmod 600 $T/$n/site-key-$SITE
  printf 'RINA_RAM=%s\nFDF_AREA=%s\nFDF_HUB_NAME=hub%s\nFDF_ENDPOINT=%s:51820\nFDF_LISTEN=51820\nFDF_IFACE=wg-hub\n' $RAM $SITE $n $PUB > $T/$n/hub.env; echo "FDF_FACTS=$T/$n/facts.json" >> $T/$n/hub.env   # [HUB_SAYS_ITS_PEERS_V1] the door answers once the hub holds the peer
  NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_BIN=$B python3 $T/bin/fdf-hub --area $SITE --iface wg-hub --endpoint $PUB:51820 --listen 51820 --key $T/$n/wg.key --iface-only" > $T/$n/iface.log 2>&1
  hostup $n
  NS $n sysctl -q -w net.ipv4.ip_forward=1
  svc $T/$n/client.log ip netns exec $n env RINA_RAM=$RAM FDF_FACTS=$T/$n/facts.json FDF_NAME=hub$n $B/fdf-client
  svc $T/$n/hub.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env RINA_RAM=$RAM FDF_BIN=$B python3 $T/bin/fdf-hub --area $SITE --iface wg-hub --endpoint $PUB:51820 --name hub$n --key $T/$n/wg.key --listen 51820 --facts $T/$n/facts.json --admitted $T/$n/admitted"
  for i in $(seq 100); do grep -q "start (adopt)" $T/$n/hub.log && break; sleep 0.05; done
  check $([ "$(NS $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && wg show wg-hub listen-port" 2>/dev/null)" = 51820 ] && echo 0 || echo 1) "hub$n up on 51820, its RAM manager at $RAM"
  if [ "${LOGIN:-}" = 1 ]; then ORG $n init > $T/$n/org.out 2>&1; ORG $n level Nodes --reach open >> $T/$n/org.out 2>&1
    svc $T/$n/org.log ip netns exec $n sh -c "mount --bind $T/$n/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$n/hub.env FDF_ADMITTED=$T/$n/admitted FDF_SITE_KEYS=$T/$n FDF_PY=$FDFPY python3 -u $T/bin/fdf-org serve"; fi
done
if [ "${LOGIN:-}" = 1 ]; then
  for n in A B; do for i in $(seq 100); do [ "$(grep -c 'listening on' $T/$n/org.log)" -ge 2 ] && break; sleep 0.1; done; done
  LB=$(ORG B line Nodes 2>$T/B/line.err); LINE=$(ORG A line Nodes --with $LB 2>$T/A/line.err)
  check $([ -n "$LINE" ] && echo 0 || echo 1) "LOGIN=1: one join line for level Nodes names hubA then hubB ($(echo $LINE | cut -c1-40)...)"
fi
inv() { local h=$1; shift; NS $h sh -c "mount --bind $T/$h/run-wg /run/wireguard && exec env FDF_HUB_ENV=$T/$h/hub.env FDF_ADMITTED=$T/$h/admitted FDF_SITE_KEYS=$T/$h python3 $T/bin/fdf-invite $*"; }
[ "${LOGIN:-}" = 1 ] || for n in z w; do for h in A B; do inv $h $n > $T/$n/invite-$h.out 2>&1; grep -m1 '^fdfinv1\.' $T/$n/invite-$h.out > $T/$n/invite-$h; done; done
[ "${LOGIN:-}" = 1 ] || check $([ -s $T/z/invite-A ] && [ -s $T/z/invite-B ] && [ -s $T/w/invite-A ] && [ -s $T/w/invite-B ] && echo 0 || echo 1) "z and w invited at hubA and hubB"
# ---- w behind z: z gave w the lease 10.3.3.20; w serves 10.4.4.0/24; w's Internet is through z (NAT)
ip netns add w; ip netns exec w ip link set lo up
ip link add v-zw type veth peer name v-wz; ip link set v-zw netns z; ip link set v-wz netns w
ip netns exec z ip addr add 10.3.3.1/24 dev v-zw; ip netns exec z ip link set v-zw up
ip netns exec w ip addr add 10.3.3.20/24 dev v-wz; ip netns exec w ip link set v-wz up
ip netns exec w ip route add default via 10.3.3.1 dev v-wz metric 600
ip netns exec z iptables -t nat -A POSTROUTING -o v-z -j MASQUERADE 2>/dev/null
WMAC=$(ip netns exec w cat /sys/class/net/v-wz/address)
printf '%d %s 10.3.3.20 w *\n' $(( $(date +%s) + 3600 )) "$WMAC" > $T/z/leases; : > $T/w/leases
ip netns exec w ip link add lan0 type veth peer name lan0p; ip netns exec w ip link set lan0 up; ip netns exec w ip link set lan0p up
ip netns exec w ip addr add 10.4.4.1/24 dev lan0
ip netns exec z ip route add default via 198.18.0.1 dev v-z
for n in z w; do ip netns exec $n sysctl -q -w net.ipv4.ip_forward=1; done
# ---- a node's private filesystem (as netns-region-loop-test.sh)
BINDS="etc:/etc var-lib-frognet:/var/lib/frognet var-lib-frognet-tunnel:/var/lib/frognet-tunnel run-frognet:/run/frognet
       run-wireguard:/run/wireguard opt-fdf-var:/opt/fdf/var"
for b in $BINDS; do mkdir -p ${b#*:}; done
NSF() { local n=$1; shift; local F=$T/$n/fs m=""
  for b in $BINDS; do m="$m mount --bind $F/${b%%:*} ${b#*:};"; done
  ip netns exec $n env RINA_AREA=$SITE RINA_SITE_KEY=$KEY FDF_UPLINK_PING=198.18.0.1 FDF_BIN=$T/bin \
     FDF_FACTS=/opt/fdf/var/facts.json FDF_LEASES=$T/$n/leases FDF_PY=$FDFPY FROGNET_CAPABILITY_PROBE=$T/bin/capability-probe \
     FROGNET_REGION_HOSTS=/var/lib/frognet/region_hosts PYTHONPATH=$FN \
     FDF_CLIENT_RESTART='kill $(cat /opt/fdf/var/client.pid)' \
     sh -c "$m cd $FN && exec \"\$@\"" sh "$@"; }
fsnode() { local n=$1; local F=$T/$n/fs
  for b in $BINDS; do mkdir -p $F/${b%%:*}; done
  cp -a /etc/. $F/etc/; mkdir -p $F/etc/frognet $F/etc/wireguard $F/etc/sentinels $F/etc/dnsmasq.d
  printf '127.0.0.1 localhost\n' > $F/etc/hosts; echo "g-$n" > $F/etc/fnid; echo "domain=$n" > $F/etc/dnsmasq.d/frognet.conf
  printf '# netns system-space test\n' > $F/etc/frognet/tunnel.conf; printf 'NETWORK_NAME=p\n' > $F/etc/frognet/gateways.conf
  if [ "${LOGIN:-}" = 1 ]; then
    NSF $n env FDF_WG_IMPL=wireguard-go FDF_DEVICE_ID=g-$n bash $T/bin/fdf-region-join --files-only "$LINE" < /dev/null > $T/$n/login.log 2>&1   # [ORG_NODE_JOIN_V1] as the installer runs it
    check $? "$n: fdf-region-join --files-only with the organization's line (fdf-login --node): $(grep -E 'logged in|node:' $T/$n/login.log | sed 's/^\[LOGIN\] [0-9:]* //' | tr '\n' ';' | cut -c1-300)"
    check $(python3 -c 'import json,sys; m=json.load(open(sys.argv[1]))["managers"]; sys.exit(0 if [x["name"] for x in m]==["hubA","hubB"] and all(x["member"]["edge"] and x["ram"] for x in m) else 1)' $F/opt-fdf-var/system-space.json 2>/dev/null && echo 0 || echo 1) \
      "$n: system-space.json from the login: $(python3 -c 'import json,sys; print([(m["name"], m["ram"], m["member"]["edge"]) for m in json.load(open(sys.argv[1]))["managers"]])' $F/opt-fdf-var/system-space.json 2>&1 | cut -c1-200)"
    check $(NSF $n wg show interfaces 2>/dev/null | grep -q . && echo 1 || echo 0) "$n: fdf-login left no interface of its own (the daemon owns the tunnels)"
    return 0; fi
  NSF $n bash $T/bin/fdf-region-join --files-only "$(cat $T/$n/invite-A)" > $T/$n/join.log 2>&1
  check $? "$n: fdf-region-join --files-only with hubA's line"
  # the system space: hubA (from its invitation, as joined) then hubB; this node's one key admitted at hubB too
  python3 -I - $F/opt-fdf-var/system-space.json $T/$n/invite-A $T/$n/invite-B $T/B/admitted/$n.json $F/var-lib-frognet-tunnel/node_private.key <<'PY'
import base64, json, os, subprocess, sys
def dec(p):
    t = open(p).read().strip()[8:]; return json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))
a, b = dec(sys.argv[2]), dec(sys.argv[3])
pub = subprocess.run(["wg", "pubkey"], input=open(sys.argv[5]).read(), capture_output=True, text=True).stdout.strip()
r = json.load(open(sys.argv[4])); r["pubkey"] = pub
open(sys.argv[4] + ".tmp", "w").write(json.dumps(r)); os.replace(sys.argv[4] + ".tmp", sys.argv[4])
ent = lambda i: {"name": i["hub"]["name"], "ram": i["ram"], "hub": i["hub"], "member": {k: v for k, v in i["member"].items() if k != "privkey"}}
json.dump({"area": a["area"], "managers": [ent(a), ent(b)]}, open(sys.argv[1], "w"), indent=1)
PY
  check $? "$n: system-space.json: $(python3 -c 'import json,sys; print([(m["name"], m["ram"], m["member"]["edge"]) for m in json.load(open(sys.argv[1]))["managers"]])' $F/opt-fdf-var/system-space.json)"; }
daemon() { local n=$1
  fsnode $n
  NSF $n python3 -u -m internet_tunnels_v3 2>&1 | python3 -u $T/stamp.py > $T/$n/daemon.log &
  for i in $(seq 40); do grep -q '"name"' $T/$n/fs/opt-fdf-var/facts.json 2>/dev/null && break; sleep 0.25; done
  # fdf-client as its unit runs it: RINA_RAM configured, then system-ram.env (where the system space is now) after it
  svc $T/$n/client.log NSF $n env FDF_NAME=$n $(cat $T/$n/fs/opt-fdf-var/site.env) sh -c \
    'R=$(sed -n "s/^export RINA_RAM=//p" /opt/fdf/var/join.env); [ -f /opt/fdf/var/system-ram.env ] && R=$(sed -n "s/^RINA_RAM=//p" /opt/fdf/var/system-ram.env); echo $$ > /opt/fdf/var/client.pid; exec fdf-client $R'
  NSF $n env FDF_NAME=$n python3 $T/bin/fdf-facts --watch > $T/$n/facts.log 2>&1 &
  }
daemon z; daemon w
on() { grep "system space on" $T/$1/daemon.log | tail -1 | sed 's/.*system space on \([^(]*\) (.*/\1/' | sed 's/ *$//'; }
# wait until both daemons' last "system space on" names $1, and the RAM manager they are on lists both; prints seconds
both_on() { local want=$1 t0=$(ms) ok=1; for i in $(seq ${3:-240}); do
    [ "$(on z)" = "$want" ] && [ "$(on w)" = "$want" ] && [ "$(eval "$2")" = "w z" ] && { ok=0; break; }; sleep 0.25; done
  echo "$ok $(( ($(ms) - t0) / 100 ))"; }
# [APP_SERVICES_V1] the app as the design has it: its one configuration tuple in databasehost-control, each machine's
# capability tuples in the SystemRam, rangertrakHost.frognet written by the daemon's own election on every node
hostsof() { sed -n "s/^\([0-9.]*\) $2\$/\1/p" $T/$1/fs/etc/hosts | head -1; }
cells() { NS $1 curl -s -m 3 "http://$2$API?op=read&service=fdf-app&variable=$3" | python3 -c 'import sys,json; print(" ".join(sorted(c["instance"] for c in json.load(sys.stdin).get("cells",[]) if c.get("bag") and not c.get("gone"))))' 2>/dev/null; }
appok() {   # every node names one AppHost, the same; the one tuple in databasehost-control; z's and w's capability in the SystemRam; nothing on hubA
  local zc=$(hostsof z databasehost_control.frognet) zs=$(hostsof z databasehost.frognet) za=$(hostsof z rangertrakHost.frognet) wa=$(hostsof w rangertrakHost.frognet)
  [ -n "$za" ] && [ "$za" = "$wa" ] && [ "$(cells z $zc:8841 config)" = rangertrak ] && [ "$(cells z $zs:8841 can)" = "rangertrak.general.g-w rangertrak.general.g-z" ] \
    && [ -z "$(NS z curl -s -m 3 "http://$RAM$API?op=read&service=fdf-app" 2>/dev/null | grep -o '"instance"')" ]; }
appsay() { echo "AppHost z:$(hostsof z rangertrakHost.frognet) w:$(hostsof w rangertrakHost.frognet); control $(hostsof z databasehost_control.frognet): $(cells z $(hostsof z databasehost_control.frognet):8841 config); SystemRam $(hostsof z databasehost.frognet): $(cells z $(hostsof z databasehost.frognet):8841 can)"; }
app_on() { local t0=$(ms); for i in $(seq 160); do appok && { echo "0 $(( ($(ms) - t0) / 100 ))"; return; }; sleep 0.25; done; echo "1 $(( ($(ms) - t0) / 100 ))"; }
secs() { echo "$(( $1 / 10 )).$(( $1 % 10 ))"; }
dump() { for n in z w; do echo "--- $n: system space lines"; grep -E "system space|SPACE_|LAN RAM|follows|lost" $T/$n/daemon.log | tail -12 | cut -c1-260 | sed 's/^/    /'
         echo "--- $n client.log (last 6)"; tail -6 $T/$n/client.log | sed 's/^/    /'; echo "--- $n routes"; ip netns exec $n ip -4 route | sed 's/^/    /'; done; }
r=($(both_on hubA "parts A $RAM")); check ${r[0]} "both on hubA, the first on the list; hubA's RAM manager lists both ($(secs ${r[1]}) s)"; [ ${r[0]} = 0 ] || dump
ok=1; for i in $(seq 120); do ip netns exec z ip -4 route | grep -q '^10.4.4.0/24 via 10.3.3.20' && { ok=0; break; }; sleep 0.5; done
check $ok "z routes w's 10.4.4.0/24 over the LAN (via its lease 10.3.3.20)"
# the app: its first install on z (databasehost-control named), installed on w from its one tuple, served on both
for i in $(seq 120); do grep -q databasehost_control.frognet $T/z/fs/etc/hosts && grep -q databasehost_control.frognet $T/w/fs/etc/hosts && break; sleep 0.25; done
NSF z env FDF_APPS=/opt/fdf/var/apps python3 $PK/bin/fdf-app config rangertrak general --role eoc:default > $T/z/app-config.log 2>&1
check $? "the app's first install on z: $(tail -1 $T/z/app-config.log | cut -c1-200)"
NSF w env FDF_APPS=/opt/fdf/var/apps FDF_APP_RESTART=true python3 $PK/bin/fdf-app install rangertrak < /dev/null > $T/w/app-install.log 2>&1
check $? "the app installed on w from its one configuration tuple: $(tail -2 $T/w/app-install.log | head -1 | cut -c1-200)"
for n in z w; do svc $T/$n/app.log NSF $n env FDF_NAME=$n FDF_APPS=/opt/fdf/var/apps python3 $PK/bin/fdf-app serve; done
a=($(app_on)); check ${a[0]} "the app: $(appsay); nothing of it on the conduit hubA ($(secs ${a[1]}) s)"
check $(NSF w wg show interfaces 2>/dev/null | grep -q wg && echo 0 || echo 1) "w has its own tunnel to hubA, through z: $(NSF w wg show all latest-handshakes 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
sleep 4
# ---- failover: hubA's RAM manager dies
hostdown A
took() { grep "system space on $2 .*ms after the loss" $T/$1/daemon.log | tail -1 | grep -o '[0-9]* ms after the loss' | cut -d' ' -f1; }
r=($(both_on hubB "parts B $RAM")); check ${r[0]} "hubA's RAM manager gone: both on hubB, the next on the list, hubB lists both: z $(took z hubB) ms, w $(took w hubB) ms from the loss to the new session ($(secs ${r[1]}) s to hubB listing both)"; [ ${r[0]} = 0 ] || dump
# ---- [NO_FLAP_V1] it returns: nothing moves while hubB lives
hostup A; sleep 12
check $([ "$(on z)" = hubB ] && [ "$(on w)" = hubB ] && echo 0 || echo 1) "hubA's RAM manager back: 12 s later both still on hubB (z $(on z), w $(on w)): nothing goes back up while hubB lives"
sleep 2
# ---- both WAN RAM managers gone: the LAN's highest (w's 10.4.4.1) for both; the tunnels go (John)
hostdown A; hostdown B
r=($(both_on "lan 10.4.4.1" "parts w 10.4.4.1:8841")); check ${r[0]} "both WAN RAM managers gone: both on the LAN's highest, w's 10.4.4.1:8841, which lists both ($(secs ${r[1]}) s)"; [ ${r[0]} = 0 ] || dump
check $( (NSF z wg show interfaces; NSF w wg show interfaces) 2>/dev/null | tr ' ' '\n' | grep -q '^wg' && echo 1 || echo 0) "on the LAN: no hub channel on z or w; only the standing sessions' probe tunnels, routing nothing ($( (NSF z wg show interfaces; NSF w wg show interfaces) 2>/dev/null | tr '\n' ' '))"
check $(ip netns exec z ip -4 route | grep -q '^10.4.4.0/24 via 10.3.3.20' && echo 0 || echo 1) "on the LAN: z still routes w's /24"
check $(ip netns exec w ping -c 1 -W 2 -I 10.4.4.1 10.3.3.1 > /dev/null 2>&1 && echo 0 || echo 1) "on the LAN: w's .1 -> z's .1"
a=($(app_on)); check ${a[0]} "on the LAN: $(appsay) ($(secs ${a[1]}) s)"
AH=$(hostsof z rangertrakHost.frognet); PT=$(python3 -c "import json; print(json.load(open('$T/z/fs/opt-fdf-var/apps/rangertrak.json'))['ports']['general'])")
check $(NS z curl -s -m 3 -X POST -d '{"op":"version"}' "http://$AH:$PT$API?op=rina" | grep -q '"ok":true' && echo 0 || echo 1) "on the LAN: the general AppRam answers at its AppHost $AH, its port $PT"
# ---- the primary returns: both on hubA
hostup A
r=($(both_on hubA "parts A $RAM")); check ${r[0]} "hubA back: both on hubA from the LAN ($(secs ${r[1]}) s)"; [ ${r[0]} = 0 ] || dump
check $(NSF z wg show interfaces 2>/dev/null | grep -q wg && echo 0 || echo 1) "z's hub channel is back up"
a=($(app_on)); check ${a[0]} "hubA back: $(appsay); nothing of it on hubA ($(secs ${a[1]}) s)"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
[ -n "${PAUSE:-}" ] && { echo "PAUSE: holding the namespaces ${PAUSE}s (T=$T)"; sleep $PAUSE; }
