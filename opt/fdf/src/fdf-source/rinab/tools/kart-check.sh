#!/bin/bash
# kart-check.sh -- [KART_SERVICE_V1] [KART_HOST_AGENT_V1] [KART_RACE_SERVER_V1] [PERMANENT_CELL_V1] [OWNED_CELL_GOES_WITH_THE_SESSION_V1]
# Shared memory creating and managing games, across machines. Each machine is its own network namespace with its own
# hostname on one shared network (kb<pid>: per-run names, 10.61.0.0/24). On the "droplet", 10.61.0.1: the kart memory
# (rina-host :8799, its file $D/kart.perm) and the kart service beside it (--min-players 3). Machines:
#   m1 kbox1 10.61.0.11  red: 3 robots, wants 2 laps, can host (its game runs a player host agent)
#   m2 kbox2 10.61.0.12  blue: 1 robot, wants 2 laps, cannot host
#   m3 kbox3 10.61.0.13  nyc: 4 robots, wants 4 laps, can host
#   m4 kbox4 10.61.0.14  a dedicated game-serving machine (kart_host.py --kind dedicated), started during race one
# The games ready themselves 3 s in and again whenever they are back in the lobby (KART_TEST_READY_AFTER).
# PASS only if all of these hold, each printed with what was seen:
#   1 the lobby and chat are in the kart memory: three players, blue's chat line, the service's lines
#   2 the parameters are agreed by the service: 2 laps (two players want 2, one wants 4)
#   3 no dedicated machine: the service launches race one on a player machine (kbox1, the first by load and name);
#     the race server runs on m1 and the kart memory says where
#   4 race one: every game takes its slots from the race memory, sees the other machines' karts (11), >= 55 fps,
#     <= 10 writes/s per kart
#   5 race one ends by itself: the result and three records are written back, permanent; the service says who won
#   6 a dedicated machine is up: the service launches race two on it (kbox4), although two player machines can host
#   7 the dedicated machine is replaced by one that cannot start a race server: race three is launched there, its host
#     agent says it failed, and the service launches it on the next machine (kbox1), saying so in the chat
#   8 players talk only to the kart memory and to the race hosts' race ports: no other machine-to-machine packet
#   9 the kart memory (and with it the service's view) killed -9 and started again from its file: results and records
#     are back, the service and every game reconnect by themselves, and the lobby is whole again
#  10 through FD&F: fdf.where says where the kart memory is; red cannot join FD&F and is not in the lobby; blue and
#     nyc are, and race on a machine the service chose
#  11 the region's owned- and permanent-cell checks (kart/test_owned_cells.py)
# Needs root, ip, tcpdump, curl, python3 with pygame, build/ (tools/build.sh, build_rlite.sh) and kart/libkartram.so.
# KART_CHECK_BIN=<dir with rina-host, fdf-client>, KART_CHECK_GAME=<dir with the kart programs> and
# KART_CHECK_HOST_RINA=<the rina-host a hosting machine runs> run it on shipped files.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=${KART_CHECK_BIN:-$H/build/bin}; K=${KART_CHECK_GAME:-$H/kart}; D=$(mktemp -d /tmp/kart-check.XXXX); echo "diag dir: $D"
KM=10.61.0.1:8799; HB=${KART_CHECK_HOST_RINA:-$B/rina-host}; FAILS=0; R=$(( $$ % 100000 )); BR=kb$R
check() { if [ "$1" = 0 ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILS=$((FAILS+1)); fi; }
cleanup() { kill ${HP:-} ${SV:-} ${TD:-} ${FP:-} ${LM:-} 2>/dev/null; for i in 1 2 3 4; do for p in $(ip netns pids km${R}_$i 2>/dev/null); do kill -9 $p; done; ip netns delete km${R}_$i 2>/dev/null; done; ip link delete $BR 2>/dev/null; }
trap cleanup EXIT
[ -f $K/libkartram.so ] || { echo "kart-check: $K/libkartram.so is not built (kart/build_kartram.sh)"; exit 2; }
ip link add $BR type bridge; ip addr add 10.61.0.1/24 dev $BR; ip link set $BR up
ip monitor link > $D/links.log 2>&1 & LM=$!
for i in 1 2 3 4; do ip netns add km${R}_$i; ip netns exec km${R}_$i ip link set lo up
    ip link add kv$R$i type veth peer name kp$R$i; ip link set kp$R$i master $BR; ip link set kp$R$i up; ip link set kv$R$i netns km${R}_$i
    ip netns exec km${R}_$i ip addr add 10.61.0.1$i/24 dev kv$R$i; ip netns exec km${R}_$i ip link set kv$R$i up; done
tcpdump -i $BR -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
start_kart() { env -u RINA_MAP_AREA RINA_REGION_FILE=$D/kart.perm $B/rina-host --listen $KM --api /ram.php >> $D/kartmem.log 2>&1 & HP=$!
               for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8799) 2>/dev/null && break; sleep 0.05; done; }
rd() { curl -s "http://$KM/ram.php?op=read&service=kart&variable=$1"; }
live() { python3 -c "import json,sys; print(json.dumps({c['instance']:c['bag'] for c in json.load(sys.stdin)['cells'] if c['bag']}))"; }
start_kart
python3 $K/kart_service.py --memory $KM --min-players 3 >> $D/service.log 2>&1 & SV=$!
inns() { local m=$1; shift; ip netns exec km${R}_$m unshare --uts bash -c "hostname kbox$m; exec $*"; }
game() { local m=$1 log=$2; shift 2; inns $m "env KART_TEST_FPS=1 KART_TEST_READY_AFTER=3 ${KT:-} python3 $K/kartdemo.py --headless $*" >> $D/$log.log 2>&1 & echo $!; }
G1=$(game 1 g1 --name red --kart $KM --bots 3 --autodrive --laps 2 --rina-host $HB)
G2=$(KT="KART_TEST_CHAT='hi from blue'" game 2 g2 --name blue --kart $KM --bots 1 --autodrive --laps 2 --rina-host /nonexistent)
G3=$(game 3 g3 --name nyc --kart $KM --bots 4 --autodrive --laps 4 --rina-host $HB)
sleep 2.5
# 1-2 the lobby, chat, and the agreed parameters (read before anyone is ready)
LOB=$(rd lobby | live); CH=$(rd chat | live); AG=$(rd agreed | live)
NL=$(echo "$LOB" | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"); BL=$(echo "$CH" | python3 -c "import json,sys; print(any(b['who']=='blue' and b['text']=='hi from blue' for b in json.load(sys.stdin).values()))")
SL=$(echo "$CH" | python3 -c "import json,sys; print(any(b['who']=='kart service' for b in json.load(sys.stdin).values()))"); LAPS=$(echo "$AG" | python3 -c "import json,sys; print(json.load(sys.stdin).get('lobby',{}).get('laps'))")
echo "  lobby $NL; blue's chat line there: $BL; the service's: $SL; agreed laps: $LAPS"
[ "$NL" = "['blue', 'nyc', 'red']" ] && [ "$BL" = True ] && [ "$SL" = True ]; check $? "1 the lobby and chat are in the kart memory"
[ "$LAPS" = 2 ]; check $? "2 the service agreed the parameters: 2 laps"
# 3 race one, on a player machine
for i in $(seq 150); do S=$(rd server | live); [ "$S" != "{}" ] && break; sleep 0.1; done
read RACE1 A1 P1 HOST1 L1 < <(echo "$S" | python3 -c "import json,sys; r,b=list(json.load(sys.stdin).items())[0]; print(r, b['addr'], b['port'], b['host'], b['laps'])")
ON1=$(ip netns pids km${R}_1 | xargs -r ps -o args= -p | grep -c kart_server.py)
echo "  race $RACE1 on $HOST1 at $A1:$P1, $L1 laps; kart_server.py processes on m1: $ON1"; grep -h "chat: launching" $D/service.log | head -1 | sed 's/^/  /'
[ "$A1" = 10.61.0.11 ] && [ "$ON1" = 1 ] && [ "$L1" = 2 ] && grep -q "on kbox1 (player)" $D/service.log; check $? "3 no dedicated machine: race one on a player machine, kbox1"
# the dedicated machine joins now, for race two
inns 4 "python3 $K/kart_host.py --memory $KM --kind dedicated --rina-host $HB" >> $D/dedi.log 2>&1 & DH=$!
sleep 9
python3 - "$D" > $D/games.txt <<'PY'
import re, sys
D = sys.argv[1]
for g, n, mine in (("g1","red",4),("g2","blue",2),("g3","nyc",5)):
    L = open(f"{D}/{g}.log").read().splitlines(); placed = any("placed on the grid from the race memory" in l for l in L)
    rows = [l for l in L if "[KART_TEST_FPS]" in l and " racing " in l][-5:]
    fps = [float(re.search(r" ([\d.]+) fps", l).group(1)) for l in rows]; seen = [int(re.search(r"(\d+) other kart", l).group(1)) for l in rows]
    wps = [float(re.search(r"([\d.]+) writes/s", l).group(1)) for l in rows]
    print(n, mine, int(placed), min(fps) if fps else 0, min(seen) if seen else -1, max(seen) if seen else -1, round(sum(wps)/len(wps)/mine, 2) if wps else 99)
PY
sed 's/^/  game (name, karts, placed from grid, min fps, min seen, max seen, writes\/s per kart): /' $D/games.txt
awk '$3!=1 || $5!=11-$2 || $6!=11-$2 || $4<55 || $7>10 {bad=1} END {exit bad}' $D/games.txt; check $? "4 race one: placed from the race memory; each sees the others' karts; >= 55 fps; <= 10 writes/s per kart"
# 5 race one ends; written back
for i in $(seq 900); do grep -q "race $RACE1 is over" $D/g2.log && grep -q "race $RACE1 is over" $D/g3.log && break; sleep 0.1; done
R5=$(rd result | python3 -c "import json,sys; r={c['instance']:c['bag'] for c in json.load(sys.stdin)['cells'] if c['bag']}; print(len(r.get('$RACE1',{}).get('order',[])))")
N5=$(rd record | live | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"); W5=$(grep -c "chat: race $RACE1 won by" $D/service.log)
echo "  race one's result: $R5 karts; records $N5; the service said who won: $W5"
[ "$R5" = 11 ] && [ "$N5" = "['blue', 'nyc', 'red']" ] && [ "$W5" = 1 ]; check $? "5 race one written back, permanent; the service says who won"
# 6 race two on the dedicated machine
for i in $(seq 300); do S=$(rd server | live); [ "$S" != "{}" ] && echo "$S" | grep -qv "$RACE1" && break; sleep 0.1; done
read RACE2 A2 HOST2 < <(echo "$S" | python3 -c "import json,sys; r,b=list(json.load(sys.stdin).items())[0]; print(r, b['addr'], b['host'])")
echo "  race $RACE2 on $HOST2 at $A2"; grep -h "chat: launching race $RACE2" $D/service.log | sed 's/^/  /'
[ "$A2" = 10.61.0.14 ] && grep -q "chat: launching race $RACE2.* on kbox4 (dedicated)" $D/service.log; check $? "6 a dedicated machine is up: race two runs on it"
# 7 the dedicated machine is replaced by one whose race server cannot start: race three goes there, fails, then kbox1
kill $DH 2>/dev/null; ip netns pids km${R}_4 | xargs -r ps -o pid=,args= -p | grep kart_host | awk '{print $1}' | xargs -r kill
inns 4 "python3 $K/kart_host.py --memory $KM --kind dedicated --rina-host /bin/false" >> $D/dedi2.log 2>&1 & DH=$!
for i in $(seq 900); do grep -q "race $RACE2 is over" $D/g2.log && break; sleep 0.1; done
for i in $(seq 300); do grep -q "is up on" <(grep -h "chat: race .* is up on" $D/service.log | tail -1 | grep -v "$RACE2\|$RACE1") && break; sleep 0.1; done
RACE3=$(grep -h "chat: launching race" $D/service.log | tail -1 | awk '{print $5}' | tr -d ':')
grep -h "chat: .*$RACE3" $D/service.log | sed 's/^/  /' | cut -c1-160
grep -q "chat: launching race $RACE3.* on kbox4 (dedicated)" $D/service.log && grep -q "chat: race $RACE3: kbox4 could not serve it .*trying kbox1" $D/service.log && grep -q "chat: race $RACE3 is up on red" $D/service.log
check $? "7 a machine that cannot serve a race: its agent says so, and the service launches it on the next one"
for m in 1 2 3 4; do ip netns pids km${R}_$m | xargs -r ps -o pid=,args= -p | grep "kartdemo\|kart_host\|kart_server" | awk '{print $1}' | xargs -r kill; done; sleep 2
kill $TD 2>/dev/null; wait $TD 2>/dev/null
# 8 the wire: players only to the kart memory and to race ports
PORTS=$(grep -ho "race memory on port [0-9]*" $D/*.log | awk '{print $5}' | sort -u | tr '\n' ' ')
python3 - "$D/wire.pcap" $PORTS <<'PY' > $D/m2m.txt
import subprocess, sys
pcap, ports = sys.argv[1], set(sys.argv[2:])
out = subprocess.run(["tcpdump","-nn","-r",pcap,"ip and src net 10.61.0.0/24 and dst net 10.61.0.0/24 and not host 10.61.0.1"], capture_output=True, text=True).stdout.splitlines()
bad = [l for l in out if not any(f".{p} " in l or f".{p}:" in l for p in ports)]
print(len(out), len(bad)); [print(" ", l[:150]) for l in bad[:5]]
PY
read M2M M2MBAD < <(head -1 $D/m2m.txt); echo "  machine-to-machine IP packets: $M2M; not to or from a race port ($PORTS): $M2MBAD"; tail -n +2 $D/m2m.txt
[ "$M2M" -gt 0 ] && [ "$M2MBAD" = 0 ]; check $? "8 players talk only to the kart memory and the race hosts' race ports"
# 9 the kart memory dies and comes back; everyone reconnects by itself
G1=$(game 1 g1b --name red --kart $KM --bots 1 --laps 2 --rina-host $HB); G2=$(game 2 g2b --name blue --kart $KM --laps 2 --rina-host /nonexistent)
sleep 3; kill -9 $HP; wait $HP 2>/dev/null; sleep 2; start_kart; sleep 5
REC9=$(rd record | live | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"); RES9=$(rd result | live | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
LOB9=$(rd lobby | live | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"); AG9=$(rd agreed | live | python3 -c "import json,sys; print('lobby' in json.load(sys.stdin))")
grep -h "PERMANENT_CELL_V1" $D/kartmem.log | tail -1 | sed 's/^/  /'; echo "  after the restart: records $REC9, results $RES9, lobby $LOB9, the service's agreed tuple back: $AG9"
grep -h "working again" $D/service.log | tail -1 | sed 's/^/  /'
[ "$REC9" = "['blue', 'nyc', 'red']" ] && [ "$RES9" -ge 3 ] && [ "$LOB9" = "['blue', 'red']" ] && [ "$AG9" = True ]; check $? "9 the kart memory restarted from its file: everything kept is back, and everyone reconnected by itself"
for m in 1 2; do ip netns pids km${R}_$m | xargs -r ps -o pid=,args= -p | grep "kartdemo\|kart_host" | awk '{print $1}' | xargs -r kill; done; sleep 1
# 10 through FD&F: fdf.where says where the kart memory is; only members are in the lobby
FAPI=/FawcettInnovations.RINA.ram_interface.php
env -u RINA_MAP_AREA RINA_REGION_FILE=$D/fdf.perm $B/rina-host --listen 10.61.0.1:8840 --api $FAPI > $D/fdf-host.log 2>&1 & FP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/10.61.0.1/8840) 2>/dev/null && break; sleep 0.05; done
curl -s -X POST "http://10.61.0.1:8840$FAPI?op=write" -d '{"service":"fdf","variable":"where","instance":"race","bag":{"host":"10.61.0.1","port":8799}}' > /dev/null
game 1 f1 --name red --fdf 10.61.0.1:8840 --fdf-client /nonexistent --bots 1 --laps 1 --rina-host $HB > /dev/null
game 3 f3 --name nyc --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 1 --autodrive --laps 1 --rina-host $HB > /dev/null
KT="KART_TEST_START_AFTER=8" game 2 f2 --name blue --fdf 10.61.0.1:8840 --fdf-client $B/fdf-client --bots 1 --autodrive --laps 1 --rina-host /nonexistent > /dev/null
sleep 6; LOB10=$(rd lobby | live | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))")
for i in $(seq 600); do grep -q "is over" $D/f2.log && grep -q "is over" $D/f3.log && break; sleep 0.1; done
grep -h "is up on\|is over" $D/f2.log | sed 's/^/  /' | cut -c1-150; echo "  lobby through FD&F: $LOB10"
grep -q "is up on nyc at 10.61.0.13" $D/f2.log && [ "$LOB10" = "['blue', 'nyc']" ] && ! grep -q "is up on" $D/f1.log; check $? "10 through FD&F: only members are in the lobby, and they race"
for m in 1 2 3; do ip netns pids km${R}_$m | xargs -r ps -o pid=,args= -p | grep -v fdf-client | awk '{print $1}' | xargs -r kill 2>/dev/null; done; kill $FP 2>/dev/null; wait $FP 2>/dev/null
python3 $H/kart/test_owned_cells.py $B/rina-host 18899 | sed 's/^/  /'; check ${PIPESTATUS[0]} "11 owned and permanent cells"
echo "RESULT $([ $FAILS = 0 ] && echo PASS || echo "FAIL ($FAILS)")"; exit $FAILS
