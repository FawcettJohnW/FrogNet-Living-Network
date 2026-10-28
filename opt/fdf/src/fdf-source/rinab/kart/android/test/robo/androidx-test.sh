#!/bin/bash
# androidx-test.sh OUT.jar ANDROID_ALL.jar ERROR_PRONE_ANNOTATIONS.jar [KART_ANDROID_V1]
# Robolectric 4.11.1 runs on androidx.test's monitor 1.6.1 and Espresso's idling resource 3.5.1, which are on Google's
# Maven only, and this build cannot reach Google's Maven. So they are compiled here from Google's own source
# (github.com/android/android-test, tag axt_06_26_2023). Left out: FileTestStorage (Kotlin); MonitoringInstrumentation,
# InstrumentationConnection and DefaultInterceptingActivityFactory (hidden framework API; Robolectric has its own
# instrumentation); hidden/ExposedInstrumentationApi (the build-time twin of runtime/). Stand-ins in stubs/:
# androidx.annotation and ExperimentalTestApi (compile-time only, not in the jar) and androidx.tracing.Trace (a no-op).
set -euo pipefail
OUT=$1; AA=$2; EP=$3; HERE=$(cd "$(dirname "$0")" && pwd); W=$(mktemp -d)
git clone -q --depth 1 --branch axt_06_26_2023 --filter=blob:none --sparse https://github.com/android/android-test "$W/axt" 2>/dev/null
git -C "$W/axt" sparse-checkout set runner/monitor/java espresso/idling_resource/java
find "$W/axt/runner/monitor/java" "$W/axt/espresso/idling_resource/java" -name '*.java' \
    | grep -v -e '/DefaultInterceptingActivityFactory.java$' -e '/FileTestStorage.java$' -e '/MonitoringInstrumentation.java$' -e '/InstrumentationConnection.java$' -e '/hidden/ExposedInstrumentationApi.java$' > "$W/src.txt"
find "$HERE/stubs" -name '*.java' >> "$W/src.txt"
mkdir -p "$W/cls"
javac --release 17 -nowarn -encoding UTF-8 -proc:none -classpath "$AA:$EP" -d "$W/cls" @"$W/src.txt" 2>&1 | grep -v '^Note:' || true
[ -f "$W/cls/androidx/test/platform/app/InstrumentationRegistry.class" ] || { echo "androidx-test: the compile failed" >&2; exit 1; }
rm -rf "$W/cls/androidx/annotation" "$W/cls/androidx/test/annotation/ExperimentalTestApi.class"    # stand-ins: compile-time only
jar cf "$OUT" -C "$W/cls" .
rm -rf "$W"
echo "androidx-test: $OUT ($(unzip -l "$OUT" | tail -1 | awk '{print $2}') files)"
