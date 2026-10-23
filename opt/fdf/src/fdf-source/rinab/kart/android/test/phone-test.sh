#!/bin/bash
# phone-test.sh [SHOTS_DIR] -- the Android game, tested with no phone: a kart memory (rina-host), the kart service, a
# dedicated game-serving machine (kart_host.py) and a Python player (red, 1 robot) on this machine, then [KART_ANDROID_V1]
#   1. core: test/Headless.java -- the game core (core/*.java) on a JVM, racing red, 40 s
#   2. phone: test/robo (Robolectric, the Android 14 framework) -- MainActivity and GameView, tapped the way a person does
#      (setup screen, Join, Chat + Send, laps, Ready, Autopilot, the race, Log, Leave), with screenshots in SHOTS_DIR, and
#      [KART_CALLS_IN_THE_GAME_V1] the Communicator in the game: comms-ram and comms-media here, the far end the
#      Communicator's reference core (frognet-android communicator-test/Probe, its own program): the phone calls Home from
#      the lobby; Julie calls the phone while it races, the phone answers, the race ends, the call goes on in the lobby.
# COMMS_BIN=<dir with comms-ram, comms-media> (default rinab/build/comms-x86_64). ffmpeg makes the far end's VP8 and Opus.
# Needs: a JDK 17+, Maven, git (androidx.test's source, see robo/androidx-test.sh), python3 with pygame, rinab/build/bin/rina-host. Prints PASS or FAIL per part; exits 1 on a FAIL.
set -uo pipefail
cd "$(dirname "$0")/.."; A=$PWD; K=$A/..; B=${KART_TEST_BIN:-$K/../build/bin}; CB=${COMMS_BIN:-$K/../build/comms-x86_64}
CT=$(cd "$A/../../../../frognet-android" && pwd)/communicator-test
SHOTS=${1:-$A/build/shots}; D=$(mktemp -d); PORT=$(( 18700 + $$ % 200 )); KM=127.0.0.1:$PORT; FAILS=0; PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup EXIT
[ -x "$B/rina-host" ] || { echo "phone-test: no $B/rina-host (KART_TEST_BIN=<dir>)" >&2; exit 1; }
for p in comms-ram comms-media; do [ -x "$CB/$p" ] || { echo "phone-test: no $CB/$p (COMMS_BIN=<dir>)" >&2; exit 1; }; done
CRAM=$((PORT + 300)); CMED=$((PORT + 301))
echo "== the stack on $KM (logs in $D)"
env -u RINA_MAP_AREA RINA_REGION_FILE=$D/k.perm "$B/rina-host" --listen $KM --api /ram.php > $D/k.log 2>&1 & PIDS+=($!)
sleep 1
python3 $K/kart_service.py --memory $KM > $D/svc.log 2>&1 & PIDS+=($!)
python3 $K/kart_host.py --memory $KM --kind dedicated --machine vm --advertise 127.0.0.1 --rina-host "$B/rina-host" > $D/host.log 2>&1 & PIDS+=($!)
env KART_TEST_FPS=1 KART_TEST_READY_AFTER=3 SDL_VIDEODRIVER=dummy python3 $K/kartdemo.py --headless --name red --kart $KM --bots 1 --autodrive --laps 2 --rina-host /nonexistent > $D/red.log 2>&1 & PIDS+=($!)
echo "== the Communicator on 127.0.0.1:$CRAM (memory) and :$CMED (media)"
"$CB/comms-ram" --listen 127.0.0.1:$CRAM > $D/comms-ram.log 2>&1 & PIDS+=($!)
sleep 1
"$CB/comms-media" --listen 127.0.0.1:$CMED --ram 127.0.0.1:$CRAM > $D/comms-media.log 2>&1 & PIDS+=($!)
echo "== the far end: the Communicator's reference core (Probe) and its media (VP8 1280x720 24 fps, 20 ms Opus 440 Hz)"
mkdir -p build/probe build/testmedia
javac --release 8 -nowarn -d build/probe $(find "$CT/../app/src/main/java/net/frognet/communicator/core" -name '*.java') "$CT/Probe.java"
[ -s build/testmedia/far720.ivf ] || ffmpeg -loglevel error -y -f lavfi -i testsrc=size=1280x720:rate=24 -t 12 -c:v libvpx -b:v 1200k \
    -g 48 -deadline realtime -cpu-used 8 -lag-in-frames 0 build/testmedia/far720.ivf
[ -s build/testmedia/tone440.opus ] || { ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=440:sample_rate=16000" -t 10 -ac 1 -ar 16000 \
    -c:a libopus -b:a 24k -frame_duration 20 build/testmedia/tone440.ogg && python3 - build/testmedia/tone440.ogg build/testmedia/tone440.opus <<'EOF'
import struct, sys                                   # Ogg pages -> Opus packets, each prefixed by its length (Probe's format)
d = open(sys.argv[1], "rb").read(); o = 0; pkts = []; cur = b""
while o < len(d):
    n = d[o + 26]; segs = d[o + 27:o + 27 + n]; o += 27 + n
    for L in segs:
        cur += d[o:o + L]; o += L
        if L < 255: pkts.append(cur); cur = b""
with open(sys.argv[2], "wb") as f:
    for p in pkts[2:]:                               # after OpusHead and OpusTags
        f.write(struct.pack(">I", len(p)) + p)
EOF
}
sleep 1

echo "== 1. the core on a JVM (Headless, 40 s)"
mkdir -p build/test-classes
javac -d build/test-classes $(find src/net/frognet/kart/core -name '*.java') test/Headless.java
java -cp build/test-classes Headless 127.0.0.1 $PORT jvm 1 2 40 > $D/core.log 2>&1
grep -E "race .* up|placed on the grid|\[TEST\].*racing" $D/core.log | head -4 | sed 's/^/  /'
if grep -q "\[TEST\] jvm racing.*[1-9][0-9]* other kart" $D/core.log && grep -q "placed on the grid" $D/core.log; then echo "PASS 1 the core races"; else echo "FAIL 1 the core races (see $D/core.log)"; FAILS=$((FAILS+1)); fi

echo "== 2. the phone's Activity and view (Robolectric)"
cd test/robo
mvn -B -q dependency:copy -Dartifact=org.robolectric:android-all-instrumented:14-robolectric-10818077-i4 -DoutputDirectory=target/robo-deps > $D/mvn-deps.log 2>&1 \
    || { echo "FAIL 2 fetching the Android 14 framework for Robolectric (see $D/mvn-deps.log)"; exit 1; }
[ -s target/robo-deps/androidx-test.jar ] || ./androidx-test.sh target/robo-deps/androidx-test.jar \
    ~/.m2/repository/org/robolectric/android-all/13-robolectric-9030017/android-all-13-robolectric-9030017.jar \
    "$(ls ~/.m2/repository/com/google/errorprone/error_prone_annotations/2.11.0/error_prone_annotations-2.11.0.jar)" > $D/axt.log 2>&1 \
    || { echo "FAIL 2 building androidx.test from source (see $D/axt.log)"; exit 1; }
mvn -B test -Dkart=$KM -Dshots="$SHOTS" -Dcomms=$CRAM,$CMED -Dprobe="$A/build/probe" -Divf="$A/build/testmedia/far720.ivf" \
    -Dopus="$A/build/testmedia/tone440.opus" -Dtmp=$D > $D/robo.log 2>&1; R=$?
cd "$A"
grep "\[PHONE_TEST\]" $D/robo.log | sed 's/^/  /'
if [ $R = 0 ] && grep -q "Tests run: 1, Failures: 0, Errors: 0" $D/robo.log; then echo "PASS 2 the phone plays a race"; else
    echo "FAIL 2 the phone plays a race (see $D/robo.log)"; grep -E "AssertionError|Exception|FAIL" $D/robo.log | head -8 | sed 's/^/  /'; FAILS=$((FAILS+1)); fi

echo "== the other side: what red and the kart service saw"
grep -E "hello from the phone|phone" $D/svc.log | head -4 | sed 's/^/  svc: /'
grep -E "race .* up|result" $D/red.log | tail -3 | sed 's/^/  red: /'
[ $FAILS = 0 ] && echo "ALL PASS" || { echo "$FAILS FAIL(S); logs in $D"; exit 1; }
