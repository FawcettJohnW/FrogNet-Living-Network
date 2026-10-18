#!/bin/bash
# kart-check.sh -- [KART_ON_THE_SESSION_V1] [KART_HELD_READS_V1] [KART_WRITES_ON_CHANGE_V1] [OWNED_CELL_GOES_WITH_THE_SESSION_V1]
# The kart game across machines. Each machine is its own network namespace on one shared network (kb<pid>: per-run names, 10.61.0.0/24);
# the race region (build/bin/rina-host, as fdf-kart-ram.sh runs it) is on the "droplet", 10.61.0.1:8799:
#   m1 10.61.0.11  red: 3 robots, red itself on the autopilot          m2 10.61.0.12  blue: 1 robot, on the autopilot
#   m3 10.61.0.13  a machine that only runs robots: "nyc", headless, 4 robots, on the autopilot
# PASS only if all of these hold, each printed with what was seen:
#   1 every game sees every other machine's karts (11 karts in all: each sees 11 minus its own)
#   2 every game holds >= 55 fps
#   3 a kart writes on change, not per frame: <= 10 writes/s per kart (a frame-rate writer is 60)
#   4 every kart laps on the road: >= 1.5 laps in 25 s, never more than 62 px (the road's half-width) off the centre-line,
#     sampled from the region every 0.25 s
#   5 no packet passes between two player machines: everything goes to the region (tcpdump on the shared network)
#   6 m1's game is killed -9: within 1.5 s every one of its 4 tuples and its tile is {} in the region, and m2 and m3
#     report its karts gone
#   7 the region is killed and started again: the remaining games say FAILING then "working again", and their karts
#     are back in the region within 4 s
#   8 the owned-cell checks (kart/test_owned_cells.py) against this build
#   9 with --fdf, only FD&F members are drawn, and a machine whose fdf-client goes is gone from the others
#  10 a player's path dies silently for 12 s: its game keeps >= 55 fps, says FAILING, and comes back by itself
# Needs root, ip, tcpdump, python3 with pygame, build/ (tools/build.sh) and kart/libkartram.so (kart/build_kartram.sh).
# KART_CHECK_BIN=<dir with rina-host, fdf-client> and KART_CHECK_GAME=<dir with kartdemo.py> run it on shipped files.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=${KART_CHECK_BIN:-$H/build/bin}; K=${KART_CHECK_GAME:-$H/kart}; D=$(mktemp -d /tmp/kart-check.XXXX); echo "diag dir: $D"
RAM=10.61.0.1:8799; FAILS=0; R=$(( $$ % 100000 )); BR=kb$R   # per-run names: no run can delete another's network
check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { kill ${HP:-} ${TD:-} ${FP:-} ${LM:-} 2>/dev/null; for i in 1 2 3; do for p in $(ip netns pids km${R}_$i 2>/dev/null); do kill -9 $p; done; ip netns delete km${R}_$i 2>/dev/null; done; ip link delete $BR 2>/dev/null; }
cleanup; trap cleanup EXIT
[ -f $K/libkartram.so ] || { echo "kart-check: $K/libkartram.so is not built (kart/build_kartram.sh)"; exit 2; }
ip link add $BR type bridge; ip addr add 10.61.0.1/24 dev $BR; ip link set $BR up
ip monitor link > $D/links.log 2>&1 & LM=$!   # [KART_CHECK_LINKS_V1] every link change, timed by the log's order
for i in 1 2 3; do ip netns add km${R}_$i; ip netns exec km${R}_$i ip link set lo up
    ip link add kv$R$i type veth peer name kp$R$i; ip link set kp$R$i master $BR; ip link set kp$R$i up; ip link set kv$R$i netns km${R}_$i
    ip netns exec km${R}_$i ip addr add 10.61.0.1$i/24 dev kv$R$i; ip netns exec km${R}_$i ip link set kv$R$i up; done
tcpdump -i $BR -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
start_region() { env -u RINA_MAP_AREA RINA_REGION_FILE=$D/kart.perm $B/rina-host --listen $RAM --api /ram.php >> $D/host.log 2>&1 & HP=$!
                 for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8799) 2>/dev/null && break; sleep 0.05; done; }
start_region
game() { local m=$1; shift; ip netns exec km${R}_$m env KART_TEST_FPS=1 python3 $K/kartdemo.py --headless --race-host $RAM "$@" > $D/game-$m.log 2>&1 & echo $!; }
G1=$(game 1 --name red --bots 3 --autodrive); G2=$(game 2 --name blue --bots 1 --autodrive); G3=$(game 3 --name nyc --bots 4 --autodrive)
sleep 3
python3 - "$D" "$RAM" 25 <<'PY' > $D/laps.txt
import json, math, sys, time, urllib.request
D, ram, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
CX, CY, RX, RY = 430, 360, 310, 250
cl = [(CX+RX*math.cos(t), CY+RY*math.sin(t)) for t in [2*math.pi*i/720 for i in range(720)]]
st = {}; end = time.time() + secs
while time.time() < end:
    for c in json.load(urllib.request.urlopen(f"http://{ram}/ram.php?op=read&service=race&variable=kart"))["cells"]:
        b = c["bag"]
        if not b: continue
        t = math.atan2((b["y"]-CY)/RY, (b["x"]-CX)/RX); s = st.setdefault(c["instance"], dict(last=t, turn=0.0, worst=0.0))
        s["turn"] += math.remainder(t-s["last"], 2*math.pi); s["last"] = t
        s["worst"] = max(s["worst"], min(math.hypot(b["x"]-x, b["y"]-y) for x, y in cl))
    time.sleep(0.25)
for n, s in sorted(st.items()): print(n, round(s["turn"]/(2*math.pi), 2), round(s["worst"], 1))
PY
# 1-3 from each game's last per-second lines
python3 - "$D" > $D/games.txt <<'PY'
import re, sys
D = sys.argv[1]; want = {"1": ("red", 4), "2": ("blue", 2), "3": ("nyc", 5)}
for m, (n, mine) in want.items():
    rows = [l for l in open(f"{D}/game-{m}.log") if "[KART_TEST_FPS]" in l][-10:]
    fps = [float(re.search(r" ([\d.]+) fps", l).group(1)) for l in rows]
    seen = [int(re.search(r"(\d+) other kart", l).group(1)) for l in rows]
    wps = [float(re.search(r"([\d.]+) writes/s", l).group(1)) for l in rows]
    print(n, mine, min(fps) if fps else 0, min(seen) if seen else -1, max(seen) if seen else -1, round(sum(wps)/len(wps)/mine, 2) if wps else 99)
PY
cat $D/games.txt | sed 's/^/  game (name, karts, min fps, min seen, max seen, writes\/s per kart): /'
awk '$4!=11-$2 || $5!=11-$2 {bad=1} END {exit bad}' $D/games.txt; check $? "1 every game sees every other machine's karts"
awk '$3<55 {bad=1} END {exit bad}' $D/games.txt; check $? "2 every game holds >= 55 fps"
awk '$6>10 {bad=1} END {exit bad}' $D/games.txt; check $? "3 <= 10 writes/s per kart (written on change)"
sed 's/^/  kart (laps in 25 s, worst px off the centre-line): /' $D/laps.txt
[ "$(wc -l < $D/laps.txt)" = 11 ] && awk '$2<1.5 || $3>62 {bad=1} END {exit bad}' $D/laps.txt; check $? "4 all 11 karts lap on the road"
kill $TD 2>/dev/null; wait $TD 2>/dev/null
M2M=$(tcpdump -nn -r $D/wire.pcap 'src net 10.61.0.0/24 and dst net 10.61.0.0/24 and not host 10.61.0.1' 2>/dev/null | wc -l)
ALL=$(tcpdump -nn -r $D/wire.pcap 2>/dev/null | wc -l); echo "  wire: $ALL packets on the shared network; $M2M between two player machines"
[ "$ALL" -gt 0 ] && [ "$M2M" = 0 ]; check $? "5 no packet between player machines"
# 6 kill -9 m1's game
T0=$(date +%s%N); kill -9 $G1
for i in $(seq 30); do
    n=$(python3 -c "import json,urllib.request
k=json.load(urllib.request.urlopen('http://$RAM/ram.php?op=read&service=race&variable=kart'))['cells']
t=json.load(urllib.request.urlopen('http://$RAM/ram.php?op=read&service=comm&variable=tile'))['cells']
print(sum(1 for c in k if c['instance'].startswith('red') and not c['bag'])+sum(1 for c in t if c['instance']=='red' and not c['bag']))")
    [ "$n" = 5 ] && break; sleep 0.05; done
MS=$(( ($(date +%s%N)-T0)/1000000 )); echo "  red's 4 karts and tile {} in the region after $MS ms ($n of 5)"; sleep 1.5
S2=$(grep KART_TEST_FPS $D/game-2.log | tail -1 | grep -o "[0-9]* other kart"); S3=$(grep KART_TEST_FPS $D/game-3.log | tail -1 | grep -o "[0-9]* other kart")
echo "  after it: blue sees $S2(s), nyc sees $S3(s)"
[ "$n" = 5 ] && [ $MS -le 1500 ] && [ "$S2" = "5 other kart" ] && [ "$S3" = "2 other kart" ]; check $? "6 a killed game's karts and tile are gone everywhere"
# 7 the region dies and comes back
kill -9 $HP; wait $HP 2>/dev/null; sleep 2; start_region; sleep 4
B7=$(python3 -c "import json,urllib.request
k=json.load(urllib.request.urlopen('http://$RAM/ram.php?op=read&service=race&variable=kart'))['cells']
print(sorted(c['instance'] for c in k if c['bag']))")
echo "  karts in the restarted region after 4 s: $B7"
grep -h "FAILING\|working again" $D/game-2.log $D/game-3.log | sort | uniq -c | sed 's/^/  /' | cut -c1-170
grep -q "race region FAILING" $D/game-2.log && grep -q "race region working again" $D/game-2.log && grep -q "race region working again" $D/game-3.log \
  && [ "$B7" = "['blue', 'blue-bot1', 'nyc', 'nyc-bot1', 'nyc-bot2', 'nyc-bot3', 'nyc-bot4']" ]; check $? "7 the games say the region failed, reconnect, and write their karts again"
kill $G2 $G3 2>/dev/null; kill $HP 2>/dev/null; wait $HP 2>/dev/null
python3 $H/kart/test_owned_cells.py $B/rina-host 18899 | sed 's/^/  /'; check ${PIPESTATUS[0]} "8 owned cells"
# 9 [FDF_GATED_V1] with --fdf: FD&F (its own RAM manager on the droplet, :8840) says where the race is and who is a member.
# Each machine has its own hostname. blue (m2) and nyc (m3) run --fdf and start their own fdf-client; red (m1) runs
# without FD&F and is no member. Only members' karts are drawn: blue sees nyc's 5, nyc sees blue's 2, neither draws red's
# 4 (red, not gated, sees all 7). Then nyc's fdf-client is killed: blue stops drawing nyc's karts at FD&F's next answer.
FAPI=/FawcettInnovations.RINA.ram_interface.php
env -u RINA_MAP_AREA RINA_REGION_FILE=$D/fdf.perm $B/rina-host --listen 10.61.0.1:8840 --api $FAPI > $D/fdf-host.log 2>&1 & FP=$!
start_region; for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8840) 2>/dev/null && break; sleep 0.05; done
curl -s -X POST "http://10.61.0.1:8840$FAPI?op=write" -d '{"service":"fdf","variable":"where","instance":"race","bag":{"host":"10.61.0.1","port":8799}}' > /dev/null
gamef() { local m=$1; shift; ip netns exec km${R}_$m unshare --uts bash -c "hostname kbox$m; exec env KART_TEST_FPS=1 python3 $K/kartdemo.py --headless $*" > $D/fgame-$m.log 2>&1 & }
gamef 1 --name red --race-host $RAM --bots 3 --autodrive
gamef 2 --name blue --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 1 --autodrive
gamef 3 --name nyc --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 4 --autodrive
sleep 8
seen() { grep KART_TEST_FPS $D/fgame-$1.log | tail -1 | grep -o "[0-9]* other kart" | cut -d' ' -f1; }
S1=$(seen 1); S2=$(seen 2); S3=$(seen 3); echo "  with FD&F: red (no member, not gated) sees $S1, blue sees $S2, nyc sees $S3"
grep -h "FD&F member: started\|not an FD&F member" $D/fgame-2.log $D/fgame-3.log | sed 's/^/  /' | cut -c1-150
ip netns exec km${R}_3 pkill -9 -f "fdf-client 10.61.0.1:8840"; sleep 2; S2b=$(seen 2); echo "  nyc's fdf-client killed: blue sees $S2b"
[ "$S1" = 7 ] && [ "$S2" = 5 ] && [ "$S3" = 2 ] && [ "$S2b" = 0 ]; check $? "9 with --fdf only FD&F members are drawn, and a machine that leaves FD&F goes"
for m in 1 2 3; do for p in $(ip netns pids km${R}_$m 2>/dev/null); do kill $p 2>/dev/null; done; done; kill $FP $HP 2>/dev/null; wait $FP $HP 2>/dev/null
# 10 a player's path dies silently (its link down 12 s: no reset, the way a WAN path goes): its game keeps its frame rate,
# says FAILING when the session's keepalive gives up, opens a new session when the path is back, and its karts return
start_region
game 2 --name blue --bots 1 --autodrive > /dev/null; sleep 4
ip link set kp${R}2 down; sleep 12; ip link set kp${R}2 up; sleep 8
F10=$(grep KART_TEST_FPS $D/game-2.log | tail -22 | grep -o " [0-9.]* fps" | awk '{print $1}' | sort -n | head -1)
B10=$(python3 -c "import json,urllib.request
print(sorted(c['instance'] for c in json.load(urllib.request.urlopen('http://$RAM/ram.php?op=read&service=race&variable=kart'))['cells'] if c['bag']))")
echo "  path down 12 s: blue's lowest fps $F10; karts in the region after: $B10"
grep -h "race region FAILING\|race region working again" $D/game-2.log | tail -3 | sed 's/^/  /' | cut -c1-160
awk "BEGIN{exit !($F10 >= 55)}" && grep -q "race region FAILING.*keepalive" $D/game-2.log && grep -q "race region working again" $D/game-2.log \
  && [ "$B10" = "['blue', 'blue-bot1']" ]; check $? "10 a silently dead path: the game keeps running, says so, and comes back by itself"
kill $HP 2>/dev/null; wait $HP 2>/dev/null
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL ($FAILS)")"; exit $FAILS
