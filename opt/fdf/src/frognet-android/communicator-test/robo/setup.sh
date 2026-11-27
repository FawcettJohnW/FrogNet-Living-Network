#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# setup.sh DIR -- what the app's activity needs to run under Robolectric on a machine with no Android SDK and no
# Google Maven, fetched once into DIR (run_tests.sh's $ROBO): Robolectric 4.13 and JUnit (Maven Central) in DIR/lib,
# the Android 13 and 14 frameworks Robolectric runs (DIR/deps), the Android 13 framework to compile against
# (DIR/android-all-13.jar), and androidx.test's monitor 1.7.1, which is on Google's Maven only, compiled from Google's
# source (github.com/android/android-test, tag axt_2024_05_30) with the stand-ins in axstub/. Needs mvn, git, JDK 17+.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); D=$(mkdir -p "$1" && cd "$1" && pwd)
[ -s "$D/lib/androidx-test.jar" ] && [ -s "$D/android-all-13.jar" ] && [ -s "$D/lib/core-3.5.3.jar" ] && exit 0
mkdir -p "$D/lib" "$D/deps"
(cd "$HERE" && mvn -B -q dependency:copy-dependencies -DoutputDirectory="$D/lib")
for a in 13-robolectric-9030017-i6 14-robolectric-10818077-i6; do
    (cd "$D" && mvn -B -q dependency:copy -Dartifact=org.robolectric:android-all-instrumented:$a -DoutputDirectory="$D/deps"); done
(cd "$D" && mvn -B -q dependency:copy -Dartifact=com.google.zxing:core:3.5.3 -DoutputDirectory="$D/lib")   # the app's QR reader (QrRead.java)
for x in org.robolectric:android-all:13-robolectric-9030017 com.google.errorprone:error_prone_annotations:2.11.0; do
    (cd "$D" && mvn -B -q dependency:copy -Dartifact=$x -DoutputDirectory="$D/compile"); done
W=$(mktemp -d)
git clone -q --depth 1 --branch axt_2024_05_30 --filter=blob:none --sparse https://github.com/android/android-test "$W/axt" 2>/dev/null
git -C "$W/axt" sparse-checkout set runner/monitor/java espresso/idling_resource/java
find "$W/axt/runner/monitor/java" "$W/axt/espresso/idling_resource/java" -name '*.java' \
    | grep -v -e '/DefaultInterceptingActivityFactory.java$' -e '/FileTestStorage.java$' -e '/MonitoringInstrumentation.java$' -e '/InstrumentationConnection.java$' \
              -e '/hidden/ExposedInstrumentationApi.java$' -e '/PlatformTestStorageRegistry.java$' -e '/NoOpPlatformTestStorage.java$' > "$W/src.txt"
find "$HERE/axstub" -name '*.java' >> "$W/src.txt"
mkdir -p "$W/cls"
javac --release 17 -nowarn -encoding UTF-8 -proc:none -classpath "$D/compile/android-all-13-robolectric-9030017.jar:$D/compile/error_prone_annotations-2.11.0.jar" -d "$W/cls" @"$W/src.txt" 2>&1 | grep -v '^Note:' || true
[ -f "$W/cls/androidx/test/platform/app/InstrumentationRegistry.class" ] || { echo "robo/setup.sh: androidx.test did not compile" >&2; exit 1; }
rm -rf "$W/cls/androidx/annotation"                                    # stand-ins: compile-time only
jar cf "$D/lib/androidx-test.jar" -C "$W/cls" .
cp "$D/compile/android-all-13-robolectric-9030017.jar" "$D/android-all-13.jar"
rm -rf "$W"
echo "robo/setup.sh: $D ready"
