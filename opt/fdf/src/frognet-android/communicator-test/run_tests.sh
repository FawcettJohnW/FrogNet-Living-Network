#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run_tests.sh COMMS_DIR -- every test of the Android Communicator, against a built desktop Communicator
# (COMMS_DIR = ribbit/examples/communicator after ./build.sh: build/comms-ram, comms-media, comms-feed, comms-app,
# room_probe, test_feed_view, libcomms_tuples.so; python/ needs PyAV + numpy). Linux, JDK 17+, python3, ffmpeg, mvn.
#   1 vectors   the Java bearer / abort pad / reassembler == the Python ones (27,095 decisions)
#   2 core      the Java protocol core against comms-ram + comms-media + the C++ and Python Communicators
#   3 frame     the phone's frame (core/ChannelFrame.java) against the C++ library (ribbit/cpp), both directions
#   4 secure    the Java core in a sealed call: nine switches by its owner code, the memory moved, wrong secret, no frame
#   5 app       the app's MainActivity under Robolectric (the framework's own code) against the same, both call ways
set -euo pipefail
cd "$(dirname "$0")/.."
C=$(cd "$1" && pwd); B=$C/build
T=${TOOLS:-$HOME/.cache/frognet-android-tools}
R=${ROBO:-$HOME/.cache/frognet-android-robolectric}
OUT=build/testout; mkdir -p $OUT build/jvm build/testmedia build/robo
# [COMMS_TEST_IN_THIS_TREE_V1] the Communicator's Java core is the app's (app/src/main/java/net/frognet/communicator/core),
# its tests are this directory's
CORE=app/src/main/java/net/frognet/communicator/core; TD=communicator-test

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

javac --release 8 -Xlint:-options -d build/jvm $(find $CORE -name '*.java') $TD/Probe.java $TD/Vectors.java $TD/FrameProbe.java
echo "== 1 vectors";  python3 $TD/gen_vectors.py "$C" build/jvm $OUT | tee $OUT/vectors.log | tail -1
echo "== 2 core";     python3 $TD/test_android_core.py "$B" "$C" build/jvm build/testmedia $OUT | tee $OUT/test_android_core.log | grep -E "FAIL|RESULT"

echo "== 3 frame";    CHANNEL_FRAME_LIB="$B" python3 $TD/test_android_frame.py "$C/third_party/ribbit/cpp" build/jvm | tee $OUT/test_android_frame.log | grep -E "FAIL|RESULT"
echo "== 4 secure";   python3 $TD/test_android_secure.py "$B" "$C" build/jvm build/testmedia | tee $OUT/test_android_secure.log | grep -E "FAIL|RESULT"

echo "== 5 app (Robolectric)"
# the app's own classes come from an Android build (tools/make_apk.sh, with the SDK); the phone's Activity under
# Robolectric is run by rinab/kart/android/test/phone-test.sh (the kart app, which carries this Communicator)
echo "SKIP 5 the app flow: needs the app's classes from an Android SDK build (tools/make_apk.sh); see phone-test.sh"
