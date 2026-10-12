#!/bin/bash
# netns-fdftun-test.sh -- [BRIDGE_V1] fdf-tun alone, against three plain WireGuard hubs whose LANs ALL serve 10.7.7.0/24,
# and which ALL gave the phone 10.252.0.1: the forward pond (F), and islands A and B reached at once through 1:1 aliases.
# No region here: the set message is what the Kotlin planner would send. Proves the Go bridge before the planner drives it.
#   p    the phone: fdf-tun on TUN "tunp" (local 100.127.255.1, as the app's VpnService has it, whatever the ponds gave;
#        routes 10.0.0.0/8 and 100.64.0.0/10 into it). Every pond gave it 10.252.0.1: F (forward), A and B.
#   hF hA hB  WireGuard hubs (wireguard-go), 198.18.0.11/.12/.13, each 10.253.253.254 on its wg, LAN to sF/sA/sB
#   sF sA sB  10.7.7.1 each, an HTTP echo answering "<name> <client ip>"
# usage: tools/netns-fdftun-test.sh FDF_TUN_BINARY
set -u; FT=${1:?usage: netns-fdftun-test.sh FDF_TUN_BINARY}; T=${T:-/tmp/claude-0/netns-fdftun}; rm -rf $T; mkdir -p $T
export WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
FAILS=0; check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
NSS="p hF hA hB sF sA sB"
cleanup() { for n in $NSS; do ip netns pids $n 2>/dev/null | xargs -r kill -9 2>/dev/null; ip netns del $n 2>/dev/null; done; ip link del br-ft 2>/dev/null; for l in v-p-b v-hF-b v-hA-b v-hB-b; do ip link del $l 2>/dev/null; done
  # a wireguard-go outlives a deleted namespace, and its UAPI socket (/var/run/wireguard) is not per namespace: a stale
  # wg-A would be the one `wg set wg-A` configures
  for h in F A B; do ps -eo pid,args | awk -v n=wg-$h '$2=="wireguard-go" && $3==n {print $1}' | xargs -r kill -9; rm -f /var/run/wireguard/wg-$h.sock; done; }
trap cleanup EXIT; cleanup
cat > $T/echo.py <<'EOF'
import http.server, sys
NAME = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(("%s %s\n" % (NAME, self.client_address[0])).encode())
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
EOF
ip link add br-ft type bridge && ip link set br-ft up
for x in "p 10" "hF 11" "hA 12" "hB 13"; do set -- $x; ip netns add $1; ip netns exec $1 ip link set lo up
  ip link add v-$1 type veth peer name v-$1-b; ip link set v-$1 netns $1; ip link set v-$1-b master br-ft up
  ip netns exec $1 ip addr add 198.18.0.$2/24 dev v-$1; ip netns exec $1 ip link set v-$1 up; done
umask 077; wg genkey > $T/p.key; PPUB=$(wg pubkey < $T/p.key)
for h in F A B; do
  ip netns add s$h; ip netns exec s$h ip link set lo up
  ip link add l-$h type veth peer name l-$h-s; ip link set l-$h netns h$h; ip link set l-$h-s netns s$h
  ip netns exec h$h ip addr add 10.7.7.254/24 dev l-$h; ip netns exec h$h ip link set l-$h up
  ip netns exec s$h ip addr add 10.7.7.1/24 dev l-$h-s; ip netns exec s$h ip link set l-$h-s up
  ip netns exec s$h ip route add default via 10.7.7.254
  ip netns exec s$h python3 $T/echo.py s$h > /dev/null 2>&1 &
  ip netns exec h$h sysctl -q -w net.ipv4.ip_forward=1
  wg genkey > $T/h$h.key; wg pubkey < $T/h$h.key > $T/h$h.pub
  ip netns exec h$h wireguard-go wg-$h > /dev/null 2>&1
  for i in $(seq 50); do ip netns exec h$h wg show wg-$h > /dev/null 2>&1 && break; sleep 0.05; done
  P=$((51820 + $(printf '%d' "'$h") - 64))
  ip netns exec h$h wg set wg-$h private-key $T/h$h.key listen-port $P peer $PPUB allowed-ips 10.252.0.1/32
  ip netns exec h$h ip addr add 10.253.253.254/16 dev wg-$h; ip netns exec h$h ip link set wg-$h up
  ip netns exec h$h ip route add 10.252.0.1/32 dev wg-$h
  echo $P > $T/h$h.port
done
# ---- the phone: fdf-tun, then the TUN addressed and routed as Android's VpnService.Builder does
mkfifo $T/ctl
ip netns exec p bash -c "exec $FT --tun tunp < $T/ctl > $T/out 2> $T/err" &
sleep 100000 > $T/ctl & HOLD=$!   # the one writer that keeps fdf-tun's stdin open; killing it closes stdin
for i in $(seq 100); do grep -q '"tun"' $T/out 2>/dev/null && break; sleep 0.05; done
ip netns exec p ip addr add 100.127.255.1/32 dev tunp; ip netns exec p ip link set tunp up
ip netns exec p ip route add 10.0.0.0/8 dev tunp; ip netns exec p ip route add 100.64.0.0/10 dev tunp
ip netns exec p python3 $T/echo.py phone > /dev/null 2>&1 &
check $(grep -q '"build":"fdf-tun-' $T/out && echo 0 || echo 1) "fdf-tun up: $(head -1 $T/out)"
K=$(cat $T/p.key)
peer() { printf '{"pub":"%s","endpoint":"198.18.0.%s:%s","allowed":%s,"keepalive":25}' "$(cat $T/h$1.pub)" "$2" "$(cat $T/h$1.port)" "$3"; }
SET=$(printf '{"op":"set","local":"100.127.255.1","forward":{"key":"%s","given":"10.252.0.1","peer":%s},"islands":[{"area":"A","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24"],"alias":{"100.64.0.0/24":"10.7.7.0/24"}},{"area":"B","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24"],"alias":{"100.64.1.0/24":"10.7.7.0/24"}}]}' \
  "$K" "$(peer F 11 '["10.0.0.0/8"]')" "$K" "$(peer A 12 '["10.7.7.0/24"]')" "$K" "$(peer B 13 '["10.7.7.0/24"]')")
echo "$SET"  > $T/ctl; sleep 0.5
[ -n "${PAUSE:-}" ] && { echo "PAUSED (T=$T)"; sleep $PAUSE; exit 0; }
check $(tail -1 $T/out | grep -q '"ok":true' && echo 0 || echo 1) "set: forward F + islands A and B accepted: $(tail -1 $T/out)"
get() { ip netns exec p curl -s -m 3 "http://$1:8080/"; }
for i in $(seq 40); do [ -n "$(get 100.64.1.1)" ] && break; sleep 0.25; done
check $([ "$(get 10.7.7.1)" = "sF 10.252.0.1" ] && echo 0 || echo 1) "10.7.7.1 -> sF (the forward pond): $(get 10.7.7.1)"
check $([ "$(get 100.64.0.1)" = "sA 10.252.0.1" ] && echo 0 || echo 1) "100.64.0.1 -> sA (island A's 10.7.7.1, aliased): $(get 100.64.0.1)"
check $([ "$(get 100.64.1.1)" = "sB 10.252.0.1" ] && echo 0 || echo 1) "100.64.1.1 -> sB (island B's 10.7.7.1, aliased): $(get 100.64.1.1)"
check $(ip netns exec sA curl -s -m 3 http://10.252.0.1:8080/ | grep -q "^phone 100.64.0.1$" && echo 0 || echo 1) "sA -> 10.252.0.1 (the phone's address in A) answers phone, which saw sA as 100.64.0.1: $(ip netns exec sA curl -s -m 3 http://10.252.0.1:8080/)"
check $(ip netns exec sF curl -s -m 3 http://10.252.0.1:8080/ | grep -q "^phone 10.7.7.1$" && echo 0 || echo 1) "sF -> 10.252.0.1 (the phone's address in F, the forward pond) answers phone, which saw sF as 10.7.7.1: $(ip netns exec sF curl -s -m 3 http://10.252.0.1:8080/)"
check $(ip netns exec sB curl -s -m 3 http://10.252.0.1:8080/ | grep -q "^phone 100.64.1.1$" && echo 0 || echo 1) "sB -> 10.252.0.1 answers phone, which saw sB as 100.64.1.1: $(ip netns exec sB curl -s -m 3 http://10.252.0.1:8080/)"
# concurrent: 3 parallel streams of 200 requests, each must answer from its own island every time
CP=""; for d in 10.7.7.1:sF 100.64.0.1:sA 100.64.1.1:sB; do (for i in $(seq 200); do ip netns exec p curl -s -m 3 http://${d%%:*}:8080/ | awk '{print $1}'; done > $T/conc-${d##*:}) & CP="$CP $!"; done; wait $CP  # only the streams: the servers are children too
check $(for s in sF sA sB; do [ "$(sort -u $T/conc-$s)" = "$s" ] && [ $(wc -l < $T/conc-$s) = 200 ] || echo bad; done | grep -q bad && echo 1 || echo 0) "concurrent: 600 requests in 3 parallel streams, every answer from its own network ($(for s in sF sA sB; do echo -n "$s:$(grep -c . $T/conc-$s) "; done))"
# a bulk transfer through an alias: checksums right on full-size packets both ways
head -c 3000000 /dev/urandom > $T/blob; (cd $T && ip netns exec sB python3 -m http.server 8081 > /dev/null 2>&1 &)
sleep 0.5; H1=$(sha256sum < $T/blob); H2=$(ip netns exec p curl -s -m 20 http://100.64.1.1:8081/blob | sha256sum)
check $([ "$H1" = "$H2" ] && echo 0 || echo 1) "3 MB from island B through its alias, byte for byte"
ST=$(echo '{"op":"status"}'  > $T/ctl; sleep 0.3; tail -1 $T/out)
check $(echo "$ST" | python3 -c 'import json,sys; s=json.load(sys.stdin)["status"]; ok=all(s["islands"][a].get("handshake_age_s") is not None for a in "AB") and s["forward"].get("handshake_age_s") is not None; sys.exit(0 if ok else 1)' && echo 0 || echo 1) "status: a handshake on all three devices"
# B leaves the set: gone is gone, A and F untouched
SET2=$(printf '{"op":"set","local":"100.127.255.1","forward":{"key":"%s","given":"10.252.0.1","peer":%s},"islands":[{"area":"A","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24"],"alias":{"100.64.0.0/24":"10.7.7.0/24"}}]}' \
  "$K" "$(peer F 11 '["10.0.0.0/8"]')" "$K" "$(peer A 12 '["10.7.7.0/24"]')")
echo "$SET2"  > $T/ctl; sleep 0.5
check $([ -z "$(get 100.64.1.1)" ] && [ "$(get 100.64.0.1)" = "sA 10.252.0.1" ] && [ "$(get 10.7.7.1)" = "sF 10.252.0.1" ] && echo 0 || echo 1) "B removed: 100.64.1.1 answers nothing; A and F still answer"
# a re-plan with the same hub key keeps the session: A gains a route, its peer (and handshake) stays [SET_OUTSIDE_THE_PACKET_LOCK_V1]
SET3=$(printf '{"op":"set","local":"100.127.255.1","forward":{"key":"%s","given":"10.252.0.1","peer":%s},"islands":[{"area":"A","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24","10.7.8.0/24"],"alias":{"100.64.0.0/24":"10.7.7.0/24"}}]}' \
  "$K" "$(peer F 11 '["10.0.0.0/8"]')" "$K" "$(peer A 12 '["10.7.7.0/24","10.7.8.0/24"]')")
echo "$SET3" > $T/ctl; sleep 0.3
ST3=$(echo '{"op":"status"}' > $T/ctl; sleep 0.3; tail -1 $T/out)
check $(echo "$ST3" | python3 -c 'import json,sys; s=json.load(sys.stdin)["status"]; sys.exit(0 if s["islands"]["A"].get("handshake_age_s") is not None else 1)' && [ "$(get 100.64.0.1)" = "sA 10.252.0.1" ] && echo 0 || echo 1) "A re-planned (a route added, same hub): its session kept (handshake $(echo "$ST3" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["islands"]["A"].get("handshake_age_s"))') s ago), sA answers"
# a bad set changes nothing
echo '{"op":"set","local":"100.127.255.1","islands":[{"area":"X","key":"nope","given":"1.2.3.4","peer":{"pub":"x"},"routes":[]}]}'  > $T/ctl; sleep 0.3
check $(tail -1 $T/out | grep -q '"ok":false' && [ "$(get 100.64.0.1)" = "sA 10.252.0.1" ] && echo 0 || echo 1) "a bad set is refused and changes nothing: $(tail -1 $T/out)"
OV=$(printf '{"op":"set","local":"100.127.255.1","islands":[{"area":"A","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24"],"alias":{"100.64.0.0/24":"10.7.7.0/24"}},{"area":"B","key":"%s","given":"10.252.0.1","peer":%s,"routes":["10.7.7.0/24"],"alias":{"100.64.0.0/23":"10.7.6.0/23"}}]}' \
  "$K" "$(peer A 12 '["10.7.7.0/24"]')" "$K" "$(peer B 13 '["10.7.7.0/24"]')")
echo "$OV" > $T/ctl; sleep 0.3
check $(tail -1 $T/out | grep -q '"ok":false' && tail -1 $T/out | grep -q overlaps && [ "$(get 100.64.0.1)" = "sA 10.252.0.1" ] && echo 0 || echo 1) "two islands' aliases overlapping: refused, nothing changed: $(tail -1 $T/out)"
kill $HOLD; sleep 0.5
check $(ip netns exec p ip link show tunp > /dev/null 2>&1 && echo 1 || echo 0) "stdin closed: fdf-tun gone, its TUN with it ($(tail -1 $T/err))"
# ---- the app's way in: --fd-socket. A stand-in for BridgeVpn: makes the TUN, sends its fd over the abstract
# SOCK_SEQPACKET socket, answers "protect <n>" -- the FIRST one 6 s late (past fdf-tun's 5 s): that set must fail and
# change nothing, the late answer must be dropped, and the same set sent again must work (a failed Up is retried).
cat > $T/app.py <<'EOF'
import array, fcntl, os, socket, struct, sys, time
name, tun = sys.argv[1], sys.argv[2]
fd = os.open("/dev/net/tun", os.O_RDWR)
fcntl.ioctl(fd, 0x400454ca, struct.pack("16sH", tun.encode(), 0x0001 | 0x1000))      # TUNSETIFF, IFF_TUN|IFF_NO_PI
s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
for i in range(100):
    try: s.connect("\0" + name); break
    except OSError: time.sleep(0.05)
s.sendmsg([b"tun"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))]); os.close(fd)
first = True
while True:
    msg, anc, _, _ = s.recvmsg(64, socket.CMSG_SPACE(16))
    if not msg: break
    for lvl, typ, data in anc:
        for f in array.array("i", data[:len(data) - len(data) % 4]): os.close(f)
    n = msg.decode().split(" ", 1)[1]
    print("protect", n, "late" if first else "", flush=True)
    if first: time.sleep(6); first = False
    s.send(("ok " + n).encode())
EOF
mkfifo $T/ctl2
ip netns exec p bash -c "exec $FT --fd-socket fdftun-t < $T/ctl2 > $T/out2 2> $T/err2" &
sleep 100000 > $T/ctl2 & HOLD2=$!
for i in $(seq 100); do grep -q listening $T/out2 2>/dev/null && break; sleep 0.05; done
ip netns exec p python3 $T/app.py fdftun-t tunq > $T/app.log 2>&1 &
for i in $(seq 100); do grep -q '"tun":"tunq"' $T/out2 2>/dev/null && break; sleep 0.05; done
check $(grep -q '"listening":"@fdftun-t"' $T/out2 && grep -q '"tun":"tunq"' $T/out2 && echo 0 || echo 1) "--fd-socket: listened, took the app's TUN fd: $(tail -1 $T/out2)"
ip netns exec p ip addr add 100.127.255.1/32 dev tunq; ip netns exec p ip link set tunq up; ip netns exec p ip route add 10.0.0.0/8 dev tunq
FSET=$(printf '{"op":"set","local":"100.127.255.1","forward":{"key":"%s","given":"10.252.0.1","peer":%s},"islands":[]}' "$K" "$(peer F 11 '["10.0.0.0/8"]')")
echo "$FSET" > $T/ctl2; for i in $(seq 200); do [ $(grep -c '"ok"' $T/out2) -ge 3 ] && break; sleep 0.05; done   # 2 hellos, then the answer
check $(tail -1 $T/out2 | grep -q '"ok":false' && tail -1 $T/out2 | grep -q protect && echo 0 || echo 1) "the first protect answered late: the set fails, saying why: $(tail -1 $T/out2 | cut -c1-160)"
echo "$FSET" > $T/ctl2; for i in $(seq 300); do [ $(grep -c '"ok"' $T/out2) -ge 4 ] && break; sleep 0.05; done
for i in $(seq 40); do [ "$(get 10.7.7.1)" = "sF 10.252.0.1" ] && break; sleep 0.25; done
check $(tail -1 $T/out2 | grep -q '"ok":true' && [ "$(get 10.7.7.1)" = "sF 10.252.0.1" ] && grep -q "late answer" $T/err2 && echo 0 || echo 1) "the same set again: up (the failed Up was retried), the late answer dropped, sF answers through the app's TUN: $(get 10.7.7.1)"
grep -e protect -e late $T/err2 | sed 's/^/    /' | head -4
kill $HOLD2; sleep 0.5
check $(ip netns exec p ip link show tunq > /dev/null 2>&1 && echo 1 || echo 0) "--fd-socket: stdin closed, fdf-tun gone (the TUN with it once the app closes its side)"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL: $FAILS")"
