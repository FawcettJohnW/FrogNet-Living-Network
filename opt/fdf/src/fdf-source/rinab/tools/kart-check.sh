#!/bin/bash
# kart-check.sh -- [KART_LOBBY_V1] [KART_RACE_SERVER_V1] [PERMANENT_CELL_V1] [OWNED_CELL_GOES_WITH_THE_SESSION_V1]
# Shared memory creating and managing games, across machines. Each machine is its own network namespace on one shared
# network (kb<pid>: per-run names, 10.61.0.0/24). The MAIN memory is a rina-host on the "droplet", 10.61.0.1:8799, its
# file $D/main.perm. Machines:
#   m1 10.61.0.11  red: 3 robots, can host        m2 10.61.0.12  blue: 1 robot, can NOT host (no rina-host)
#   m3 10.61.0.13  nyc: 4 robots, can host
# PASS only if all of these hold, each printed with what was seen:
#   1 the lobby: all three games are in the main memory's lobby, and nothing starts below --min-players 3... until
#   2 blue joins as the third player and starts the race: blue cannot host, so the intent names nyc (the first in the roster that can); the race server
#     runs on m3, and (kart, server, <race>) appears in the main memory with m3's address and a port m3's system picked
#   3 every game was placed from the race memory's grid (filled before the server tuple appeared), sees every other
#     machine's karts (11 in all), and holds >= 55 fps, writing <= 10 times a second per kart
#   4 every kart laps on the road (sampled from the race memory, 2+ laps, <= 62 px off the centre-line)
#   5 player machines talk only to the main memory and to the race host's race port: no other machine-to-machine packet
#   6 red's game is killed -9 mid-race: its 4 karts are {} in the race memory within 1.5 s, and blue and nyc stop
#     drawing them; the race goes on
#   7 the race ends by itself: the server writes the result (11 karts) and the 3 records to the main memory as
#     permanent tuples, writes its server tuple {}, and every game is back in the lobby
#   8 the main memory is killed -9 and started again from its file: the result and the 3 records are back
#   9 race two starts by itself (3 players ready): its server read the 3 records and put the fastest player's kart in
#     slot 0; then the race server is killed -9 on m3: its server tuple goes {} with its session, the race memory goes
#     with it, every game is back in the lobby, and NO result is written for race two (gone is gone)
#  10 with --fdf (FD&F's RAM manager on the droplet is the main memory, each machine its own hostname): red cannot
#     join FD&F, so blue's Start gives a roster of blue and nyc only; the race runs on nyc's FD&F address and returns
#  11 the region's owned- and permanent-cell checks (kart/test_owned_cells.py)
# Needs root, ip, tcpdump, curl, python3 with pygame, build/ (tools/build.sh, build_rlite.sh) and kart/libkartram.so.
# KART_CHECK_BIN=<dir with rina-host, fdf-client>, KART_CHECK_GAME=<dir with kartdemo.py> and KART_CHECK_HOST_RINA=<the
# rina-host a hosting machine runs> run it on shipped files.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=${KART_CHECK_BIN:-$H/build/bin}; K=${KART_CHECK_GAME:-$H/kart}; D=$(mktemp -d /tmp/kart-check.XXXX); echo "diag dir: $D"
MAINM=10.61.0.1:8799; HB=${KART_CHECK_HOST_RINA:-$B/rina-host}; FAILS=0;   # HB: what a hosting machine's race server runs
 R=$(( $$ % 100000 )); BR=kb$R   # per-run names: no run can delete another's network
check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { kill ${HP:-} ${TD:-} ${FP:-} ${LM:-} 2>/dev/null; for i in 1 2 3; do for p in $(ip netns pids km${R}_$i 2>/dev/null); do kill -9 $p; done; ip netns delete km${R}_$i 2>/dev/null; done; ip link delete $BR 2>/dev/null; }
trap cleanup EXIT
[ -f $K/libkartram.so ] || { echo "kart-check: $K/libkartram.so is not built (kart/build_kartram.sh)"; exit 2; }
ip link add $BR type bridge; ip addr add 10.61.0.1/24 dev $BR; ip link set $BR up
ip monitor link > $D/links.log 2>&1 & LM=$!
for i in 1 2 3; do ip netns add km${R}_$i; ip netns exec km${R}_$i ip link set lo up
    ip link add kv$R$i type veth peer name kp$R$i; ip link set kp$R$i master $BR; ip link set kp$R$i up; ip link set kv$R$i netns km${R}_$i
    ip netns exec km${R}_$i ip addr add 10.61.0.1$i/24 dev kv$R$i; ip netns exec km${R}_$i ip link set kv$R$i up; done
tcpdump -i $BR -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
start_main() { env -u RINA_MAP_AREA RINA_REGION_FILE=$D/main.perm $B/rina-host --listen $MAINM --api /ram.php >> $D/main.log 2>&1 & HP=$!
               for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8799) 2>/dev/null && break; sleep 0.05; done; }
rd() { curl -s "http://$MAINM/ram.php?op=read&service=kart&variable=$1"; }          # the check reads the main memory directly
start_main
game() { local m=$1 log=$2; shift 2; ip netns exec km${R}_$m unshare --uts bash -c "hostname kbox$m; exec env KART_TEST_FPS=1 ${KT:-} python3 $K/kartdemo.py --headless $*" >> $D/$log.log 2>&1 & echo $!; }
G1=$(game 1 g1 --name red --memory $MAINM --bots 3 --autodrive --min-players 3 --laps 3 --rina-host $HB)
G3=$(game 3 g3 --name nyc --memory $MAINM --bots 4 --autodrive --min-players 3 --laps 3 --rina-host $HB)
sleep 3
# 1 two players: nothing starts (min 3)
N1=$(rd lobby | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))"); S1=$(rd server | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))")
G2=$(KT="KART_TEST_START=blue KART_TEST_START_AFTER=3" game 2 g2 --name blue --memory $MAINM --bots 1 --autodrive --min-players 3 --laps 3 --rina-host /nonexistent)
sleep 2; N1b=$(rd lobby | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))")
echo "  lobby: $N1 players and $S1 race(s) with two machines in it; $N1b players once blue joins"
[ "$N1" = 2 ] && [ "$S1" = 0 ] && [ "$N1b" = 3 ]; check $? "1 the lobby is in the main memory, and two players do not start a race"
# 2 blue presses Start (3 s after it began); the host must be nyc on m3
for i in $(seq 100); do SV=$(rd server); echo "$SV" | grep -q '"port"' && break; sleep 0.1; done
RACE1=$(echo "$SV" | python3 -c "import json,sys; c=[c for c in json.load(sys.stdin)['cells'] if c['bag']][0]; print(c['instance'])")
echo "$SV" | python3 -c "import json,sys; c=[c for c in json.load(sys.stdin)['cells'] if c['bag']][0]['bag']; print(c['addr'], c['port'], c['host'], ','.join(c['players']), len(c['karts']))" > $D/server1.txt
read A1 P1 HOST1 PL1 NK1 < $D/server1.txt; echo "  race $RACE1: on $HOST1 at $A1:$P1, players $PL1, $NK1 karts"
grep -h "start: race\|hosting race" $D/g*.log | sed 's/^/  /' | cut -c1-140
ON3=$(ip netns pids km${R}_3 | xargs -r ps -o args= -p | grep -c "kart_server.py")
[ "$HOST1" = nyc ] && [ "$A1" = 10.61.0.13 ] && [ "$P1" -gt 1024 ] && [ "$ON3" = 1 ] && grep -q "start: race .* host nyc" $D/g2.log; check $? "2 blue (which cannot host) starts it: the race server runs on m3, and the main memory says where"
sleep 12
# 3 placed from the grid, see each other, frame rate, writes
python3 - "$D" > $D/games.txt <<'PY'
import re, sys
D = sys.argv[1]
for g, n, mine in (("g1","red",4),("g2","blue",2),("g3","nyc",5)):
    L = open(f"{D}/{g}.log").read().splitlines(); placed = any("placed on the grid from the race memory" in l for l in L)
    rows = [l for l in L if "[KART_TEST_FPS]" in l and " racing " in l][-6:]
    fps = [float(re.search(r" ([\d.]+) fps", l).group(1)) for l in rows]; seen = [int(re.search(r"(\d+) other kart", l).group(1)) for l in rows]
    wps = [float(re.search(r"([\d.]+) writes/s", l).group(1)) for l in rows]
    print(n, mine, int(placed), min(fps) if fps else 0, min(seen) if seen else -1, max(seen) if seen else -1, round(sum(wps)/len(wps)/mine, 2) if wps else 99)
PY
sed 's/^/  game (name, karts, placed from grid, min fps, min seen, max seen, writes\/s per kart): /' $D/games.txt
awk '$3!=1 || $5!=11-$2 || $6!=11-$2 || $4<55 || $7>10 {bad=1} END {exit bad}' $D/games.txt; check $? "3 placed from the race memory's grid; each sees the others' karts; >= 55 fps; <= 10 writes/s per kart"
# 4 laps on the road, from the race memory
python3 - "$A1:$P1" 8 <<'PY' > $D/laps.txt
import json, math, sys, time, urllib.request
ram, secs = sys.argv[1], float(sys.argv[2]); CX, CY, RX, RY = 430, 360, 310, 250
cl = [(CX+RX*math.cos(t), CY+RY*math.sin(t)) for t in [2*math.pi*i/720 for i in range(720)]]; st = {}; end = time.time()+secs
while time.time() < end:
    for c in json.load(urllib.request.urlopen(f"http://{ram}/ram.php?op=read&service=race&variable=kart"))["cells"]:
        b = c["bag"]
        if not b: continue
        t = math.atan2((b["y"]-CY)/RY, (b["x"]-CX)/RX); s = st.setdefault(c["instance"], dict(last=t, turn=0.0, worst=0.0, lap=0))
        s["turn"] += math.remainder(t-s["last"], 2*math.pi); s["last"] = t; s["lap"] = b["lap"]
        s["worst"] = max(s["worst"], min(math.hypot(b["x"]-x, b["y"]-y) for x, y in cl))
    time.sleep(0.25)
for n, s in sorted(st.items()): print(n, s["lap"], round(s["turn"]/(2*math.pi), 2), round(s["worst"], 1))
PY
sed 's/^/  kart (lap now, laps in 8 s, worst px off the centre-line): /' $D/laps.txt
[ "$(wc -l < $D/laps.txt)" = 11 ] && awk '$3<0.5 || $4>62 {bad=1} END {exit bad}' $D/laps.txt; check $? "4 all 11 karts lap on the road"
# 6 kill red's game mid-race
T0=$(date +%s%N); kill -9 $G1          # ip netns exec, unshare, bash and env all exec: $G1 is the game itself
for i in $(seq 30); do n=$(curl -s "http://$A1:$P1/ram.php?op=read&service=race&variable=kart" | python3 -c "import json,sys; print(sum(1 for c in json.load(sys.stdin)['cells'] if c['instance'].startswith('red') and not c['bag']))"); [ "$n" = 4 ] && break; sleep 0.05; done
MS=$(( ($(date +%s%N)-T0)/1000000 )); sleep 1.5
S2=$(grep "racing" $D/g2.log | grep KART_TEST_FPS | tail -1 | grep -o "[0-9]* other kart" | cut -d' ' -f1); S3=$(grep "racing" $D/g3.log | grep KART_TEST_FPS | tail -1 | grep -o "[0-9]* other kart" | cut -d' ' -f1)
echo "  red's game killed: its 4 karts {} in the race memory after $MS ms ($n of 4); blue now sees $S2, nyc $S3"
[ "$n" = 4 ] && [ $MS -le 1500 ] && [ "$S2" = 5 ] && [ "$S3" = 2 ]; check $? "6 a killed game's karts are gone from the race, everywhere"
# 7 the race ends by itself; the server writes back
for i in $(seq 900); do grep -q "race $RACE1 is over" $D/g2.log && grep -q "race $RACE1 is over" $D/g3.log && break; sleep 0.1; done
RES=$(rd result); REC=$(rd record); SV=$(rd server)
echo "$RES" | python3 -c "import json,sys; r=[c['bag'] for c in json.load(sys.stdin)['cells'] if c['instance']=='$RACE1'][0]; print('  result:', len(r['order']), 'karts;', ', '.join(s['kart']+'/'+str(s['laps']) for s in r['order']))" 2>&1 | cut -c1-200
NREC=$(echo "$REC" | python3 -c "import json,sys; print(sorted(c['instance'] for c in json.load(sys.stdin)['cells'] if c['bag']))")
NSV=$(echo "$SV" | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))")
NRES=$(echo "$RES" | python3 -c "import json,sys; r=[c['bag'] for c in json.load(sys.stdin)['cells'] if c['instance']=='$RACE1']; print(len(r[0]['order']) if r else 0)")
echo "  records: $NREC; race servers running: $NSV"; grep -h "kart_server: \[$RACE1\] \(race over\|written back\)" $D/g3.log | sed 's/^/  /' | cut -c1-160
[ "$NRES" = 11 ] && [ "$NREC" = "['blue', 'nyc', 'red']" ] && [ "$NSV" = 0 ] && grep -q "back in the lobby" $D/g2.log; check $? "7 the race server wrote the result and the records back, and everyone is in the lobby"
# 8 the main memory dies and comes back from its file
kill -9 $HP; wait $HP 2>/dev/null; sleep 1; start_main; sleep 2
NREC8=$(rd record | python3 -c "import json,sys; print(sorted(c['instance'] for c in json.load(sys.stdin)['cells'] if c['bag']))"); NRES8=$(rd result | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))")
grep "PERMANENT_CELL_V1" $D/main.log | tail -1 | sed 's/^/  /'; echo "  after the restart: records $NREC8, results $NRES8"
[ "$NREC8" = "['blue', 'nyc', 'red']" ] && [ "$NRES8" = 1 ]; check $? "8 the main memory restarted from its file: the result and the records are back"
# 9 race two by itself, pre-populated with the records; then its server is killed -9
G1=$(game 1 g1b --name red --memory $MAINM --bots 3 --autodrive --min-players 3 --laps 3 --rina-host $HB)
for i in $(seq 400); do SV=$(rd server); echo "$SV" | grep -q '"port"' && break; sleep 0.1; done
read RACE2 A2 P2 < <(echo "$SV" | python3 -c "import json,sys; c=[c for c in json.load(sys.stdin)['cells'] if c['bag']][0]; print(c['instance'], c['bag']['addr'], c['bag']['port'])")
FAST=$(rd record | python3 -c "import json,sys; r={c['instance']:c['bag'] for c in json.load(sys.stdin)['cells'] if c['bag']}; print(min(r, key=lambda p: r[p].get('best_lap') or 1e9))")
SLOT0=$(curl -s "http://$A2:$P2/ram.php?op=read&service=race&variable=grid" | python3 -c "import json,sys; print([c['bag']['owner'] for c in json.load(sys.stdin)['cells'] if c['bag'].get('slot')==0][0])")
RREAD=$(grep -h "kart_server: \[$RACE2\] race memory filled" $D/g*.log | grep -o "[0-9]* record(s) read" | head -1)
echo "  race two $RACE2 started by itself; its server: $RREAD; fastest player by record: $FAST; slot 0: $SLOT0's kart"
sleep 3; ip netns pids km${R}_3 | xargs -r ps -o pid=,args= -p | grep kart_server.py | awk '{print $1}' | xargs -r kill -9
for i in $(seq 100); do grep -q "race $RACE2 is over" $D/g2.log && grep -q "race $RACE2 is over" $D/g3.log && break; sleep 0.1; done
NSV9=$(rd server | python3 -c "import json,sys; print(len([c for c in json.load(sys.stdin)['cells'] if c['bag']]))"); RES9=$(rd result | python3 -c "import json,sys; print(any(c['instance']=='$RACE2' and c['bag'] for c in json.load(sys.stdin)['cells']))")
RM9=$( (exec 3<>/dev/tcp/$A2/$P2) 2>/dev/null && echo up || echo gone ); grep -h "race $RACE2 is over" $D/g2.log | sed 's/^/  /' | cut -c1-140
echo "  race servers in the main memory: $NSV9; a result for race two: $RES9; its race memory: $RM9"
[ -n "$RACE2" ] && [ "$RREAD" = "3 record(s) read" ] && [ "$SLOT0" = "$FAST" ] && [ "$NSV9" = 0 ] && [ "$RES9" = False ] && [ "$RM9" = gone ]; check $? "9 race two: pre-populated from the records; its server killed -9 is gone everywhere, with no result"
for m in 1 2 3; do ip netns pids km${R}_$m | xargs -r ps -o pid=,args= -p | grep kartdemo | awk '{print $1}' | xargs -r kill; done; sleep 1
kill $TD 2>/dev/null; wait $TD 2>/dev/null
# 5 (from the whole capture, races one and two): machine-to-machine packets only to or from a race host's race port
PORTS=$(grep -ho "race memory on port [0-9]*" $D/g*.log | awk '{print $5}' | sort -u | tr '\n' ' ')   # every race server m3 ran
python3 - "$D/wire.pcap" $PORTS <<'PY' > $D/m2m.txt
import subprocess, sys
pcap, ports = sys.argv[1], set(sys.argv[2:])
out = subprocess.run(["tcpdump","-nn","-r",pcap,"ip and src net 10.61.0.0/24 and dst net 10.61.0.0/24 and not host 10.61.0.1"], capture_output=True, text=True).stdout.splitlines()
bad = [l for l in out if not any(f"10.61.0.13.{p} " in l or f"10.61.0.13.{p}:" in l for p in ports)]
print(len(out), len(bad)); [print(" ", l[:150]) for l in bad[:5]]
PY
read M2M M2MBAD < <(head -1 $D/m2m.txt); echo "  machine-to-machine IP packets: $M2M; not to or from a race port on m3 ($PORTS): $M2MBAD"; tail -n +2 $D/m2m.txt
[ "$M2M" -gt 0 ] && [ "$M2MBAD" = 0 ]; check $? "5 machines talk only to the main memory and to the race host's race port"
kill $HP 2>/dev/null; wait $HP 2>/dev/null
# 10 the same through FD&F
FAPI=/FawcettInnovations.RINA.ram_interface.php
env -u RINA_MAP_AREA RINA_REGION_FILE=$D/fdf.perm $B/rina-host --listen 10.61.0.1:8840 --api $FAPI > $D/fdf-host.log 2>&1 & FP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8840) 2>/dev/null && break; sleep 0.05; done
game 1 f1 --name red --fdf 10.61.0.1:8840 --fdf-client /nonexistent --bots 1 --autodrive --min-players 9 --laps 1 --rina-host $HB > /dev/null
game 3 f3 --name nyc --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 1 --autodrive --min-players 9 --laps 1 --rina-host $HB > /dev/null
KT="KART_TEST_START=blue KART_TEST_START_AFTER=6" game 2 f2 --name blue --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 1 --autodrive --min-players 9 --laps 1 --rina-host /nonexistent > /dev/null
for i in $(seq 600); do grep -q "is over" $D/f2.log && grep -q "is over" $D/f3.log && break; sleep 0.1; done
grep -h "start: race\|hosting race\|is up on\|is over" $D/f2.log $D/f3.log | sed 's/^/  /' | cut -c1-150
F10=$(curl -s "http://10.61.0.1:8840$FAPI?op=read&service=kart&variable=result" | python3 -c "import json,sys; r=[c['bag'] for c in json.load(sys.stdin)['cells'] if c['bag']]; print(','.join(sorted(r[0]['players'])) if r else '')")
echo "  FD&F's main memory now holds a result for players: $F10"
grep -q "start: race .* host nyc, 2 player(s)" $D/f2.log && grep -q "is up on nyc at 10.61.0.13:" $D/f2.log && [ "$F10" = "blue,nyc" ]; check $? "10 through FD&F: only members race, on nyc's FD&F address, and the result is kept"
for m in 1 2 3; do ip netns pids km${R}_$m | xargs -r ps -o pid=,args= -p | grep -v fdf-client | awk '{print $1}' | xargs -r kill 2>/dev/null; done; kill $FP 2>/dev/null; wait $FP 2>/dev/null
python3 $H/kart/test_owned_cells.py $B/rina-host 18899 | sed 's/^/  /'; check ${PIPESTATUS[0]} "11 owned and permanent cells"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL ($FAILS)")"; exit $FAILS
