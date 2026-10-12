#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run_tests.sh COMMS_DIR -- every test of the Android Communicator, against a built desktop Communicator
# (COMMS_DIR = ribbit/examples/communicator after ./build.sh: build/comms-ram, comms-media, comms-feed, comms-app,
# room_probe, test_feed_view, libcomms_tuples.so; python/ needs PyAV + numpy). Linux, JDK 17+, python3, ffmpeg, mvn.
#   1 vectors   the Java bearer / abort pad / reassembler == the Python ones (27,095 decisions)
#   2 core      the Java protocol core against comms-ram + comms-media + the C++ and Python Communicators
#   3 app       the app's MainActivity under Robolectric (the framework's own code) against the same, both call ways
set -euo pipefail
cd "$(dirname "$0")/.."
C=$(cd "$1" && pwd); B=$C/build
T=${TOOLS:-$HOME/.cache/frognet-android-tools}
R=${ROBO:-$HOME/.cache/frognet-android-robolectric}
OUT=build/testout; mkdir -p $OUT build/jvm build/testmedia build/robo
[ -f build/classes/net/frognet/communicator/MainActivity.class ] || bash build_apk.sh

echo "== test media: VP8 1280x720 (fnav's settings) and 20 ms Opus at 660 Hz"
[ -f build/testmedia/phone720.ivf ] || ffmpeg -loglevel error -y -stream_loop 1 -i "$C/media/stock-720p.mp4" -t 12 -an -vf scale=1280:720,fps=24 \
    -c:v libvpx -b:v 1200k -maxrate 1200k -bufsize 600k -g 48 -deadline realtime -cpu-used 8 -lag-in-frames 0 build/testmedia/phone720.ivf
if [ ! -f build/testmedia/tone660.opus ]; then
    ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=660:sample_rate=16000" -t 10 -ac 1 -ar 16000 -c:a libopus -b:a 24k -frame_duration 20 build/testmedia/tone660.ogg
    python3 -c "
import av, struct
c = av.open('build/testmedia/tone660.ogg')
with open('build/testmedia/tone660.opus', 'wb') as f:
    for p in c.demux(c.streams.audio[0]):
        b = bytes(p)
        if b: f.write(struct.pack('>I', len(b)) + b)"
fi

javac --release 8 -Xlint:-options -d build/jvm $(find src/net/frognet/communicator/core -name '*.java') test/Probe.java test/Vectors.java
echo "== 1 vectors";  python3 test/gen_vectors.py "$C" build/jvm $OUT | tee $OUT/vectors.log | tail -1
echo "== 2 core";     python3 test/test_android_core.py "$B" "$C" build/jvm build/testmedia $OUT | tee $OUT/test_android_core.log | grep -E "FAIL|RESULT"

echo "== 3 app (Robolectric)"
mkdir -p "$R/deps"
if [ ! -d "$R/lib" ]; then
    (cd "$R" && cp "$OLDPWD/test/robo/pom.xml" . && mvn -q -B dependency:copy-dependencies -DoutputDirectory=lib)
fi
if [ ! -f "$R/lib/androidx-test-monitor-from-source.jar" ]; then
    # androidx.test:monitor is on Google's Maven only: compiled here from its source (android/android-test, Apache-2.0)
    [ -d "$R/android-test" ] || git clone -q --depth 1 --filter=blob:none --sparse https://github.com/android/android-test.git "$R/android-test"
    (cd "$R/android-test" && git sparse-checkout set runner/monitor/java espresso/idling_resource/java)
    rm -rf "$R/axbuild" && mkdir -p "$R/axbuild"
    SRC=$(find "$R/android-test" -name '*.java' | grep -v -E "runner/hidden/|MonitoringInstrumentation.java|InstrumentationConnection.java|FileTestStorage.java|DefaultInterceptingActivityFactory.java|io/PlatformTestStorageRegistry.java")
    javac --release 17 -nowarn -Xlint:none -cp "$T/android-all-33.jar:$(ls $R/lib/error_prone_annotations-*.jar | head -1)" -d "$R/axbuild" \
          $(find test/robo/axstub -name '*.java') $SRC
    (cd "$R/axbuild" && jar cf "$R/lib/androidx-test-monitor-from-source.jar" androidx)
fi
for v in 13-robolectric-9030017-i6 14-robolectric-10818077-i6; do
    [ -f "$R/deps/android-all-instrumented-$v.jar" ] || curl -sf -o "$R/deps/android-all-instrumented-$v.jar" \
        https://repo1.maven.org/maven2/org/robolectric/android-all-instrumented/$v/android-all-instrumented-$v.jar
done
RL=$(ls $R/lib/*.jar | tr '\n' ':')
javac --release 17 -nowarn -Xlint:none -cp "build/classes:$RL$T/android-all-33.jar" -d build/robo test/robo/AppFlowTest.java
ROBO_DEPS=$R/deps python3 test/test_app_flow.py "$B" "$C" "build/robo:build/classes:$RL$T/android-all-33.jar" | tee $OUT/test_app_flow.log | grep -E "\[home\]|FAIL|RESULT"
