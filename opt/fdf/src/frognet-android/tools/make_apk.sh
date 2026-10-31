#!/bin/bash
# make_apk.sh -- the Android app as fdf-android-<build>.apk, on a machine WITH the Android SDK (Android Studio's, or
# ANDROID_HOME) and Google's Maven repository reachable (AGP comes from there). fdf-tun is already in app/src/main/jniLibs
# (tun/build.sh rebuilds it). The APK is the debug-signed one: install it with `adb install -r` or by opening it on the
# phone (allow "install unknown apps" for the app you open it with). An earlier fdf-android-*.apk installs over the same
# way; the kept invitation survives.
# usage: tools/make_apk.sh OUTDIR
set -eu
A=$(cd "$(dirname "$0")/.." && pwd); O=$(mkdir -p "${1:?usage: make_apk.sh OUTDIR}" && cd "$1" && pwd)
[ -n "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ] || [ -f "$A/local.properties" ] || {
  echo "make_apk: no Android SDK: set ANDROID_HOME (Android Studio: File > Project Structure > SDK Location) or open frognet-android in Android Studio once" >&2; exit 1; }
B=$(sed -n 's/.*versionName = "\(.*\)".*/\1/p' "$A/app/build.gradle.kts")
(cd "$A" && ./gradlew -q :app:assembleDebug)
cp "$A/app/build/outputs/apk/debug/app-debug.apk" "$O/fdf-android-$B.apk"
ls -la "$O/fdf-android-$B.apk"
