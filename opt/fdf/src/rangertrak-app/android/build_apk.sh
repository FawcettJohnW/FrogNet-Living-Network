#!/bin/bash
# build_apk.sh OUTDIR [team] -- [RT_APK_NO_GRADLE_V1] RangerTrak-android-<build>.apk without Gradle or Google's SDK download (the
# build machine reaches neither dl.google.com nor Google's Maven): Debian/Ubuntu's own Android tools --
#   apt install android-sdk-build-tools android-sdk-platform-23 dalvik-exchange apksigner zipalign
# -- aapt2 (resources, manifest, the page as assets), javac (Java 8 bytecode, against an android-all jar for the API
# names newer than 23: ANDROID_JAR, default /tmp/aall/aall.jar, Robolectric's android-all from Maven Central), dx
# (min SDK 26), zipalign, apksigner with debug.keystore beside this script (a debug key, kept so a new build installs
# over the last one). The same sources as the Gradle build: this app's MainActivity, the core, the RAM library, ui/.
set -eu
D=$(cd "$(dirname "$0")" && pwd); APP=$(dirname "$D"); OUT=${1:?usage: build_apk.sh OUTDIR [team]}; mkdir -p "$OUT"
# [RT_TEAM_APP_V1] team: RangerTrak Team (team/: its manifest and resources; the same Java, core and page)
MOD=app; PKG=net.frognet.rangertrak; NAME=RangerTrak
if [ "${2:-}" = team ]; then MOD=team; PKG=net.frognet.rangertrak.team; NAME=RangerTrak-Team; fi
BT=$(ls -d /usr/lib/android-sdk/build-tools/[0-9]* | sort -V | tail -1); P23=/usr/lib/android-sdk/platforms/android-23/android.jar
AJ=${ANDROID_JAR:-/tmp/aall/aall.jar}
for f in "$BT/aapt2" "$P23" "$AJ" /usr/bin/dalvik-exchange; do [ -e "$f" ] || { echo "build_apk: missing $f (see the header)" >&2; exit 2; }; done
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $APP/core/src/net/frognet/rangertrak/AppCore.java)
VC=$(date -u +%y%m%d%H)                                   # versionCode: newer every build
T=$(mktemp -d); mkdir -p $T/res $T/classes $T/assets
cp -r $APP/ui $T/assets/ui
# the manifest as Gradle would merge it: the package, the SDK levels, the version
sed -e "s|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">|<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\" package=\"$PKG\" android:versionCode=\"$VC\" android:versionName=\"$BUILD\">\n    <uses-sdk android:minSdkVersion=\"26\" android:targetSdkVersion=\"34\" />|" \
    -e 's|android:name="\.MainActivity"|android:name="net.frognet.rangertrak.android.MainActivity"|' \
    $D/$MOD/src/main/AndroidManifest.xml > $T/AndroidManifest.xml   # (Gradle's namespace, net.frognet.rangertrak.android)
"$BT/aapt2" compile --dir $D/$MOD/src/main/res -o $T/res.zip
"$BT/aapt2" link -o $T/base.apk -I "$P23" --manifest $T/AndroidManifest.xml -A $T/assets $T/res.zip --auto-add-overlay
javac -nowarn --release 8 -Xlint:-options -cp "$AJ" -d $T/classes \
    $(find $D/app/src/main/java $APP/core/src $APP/../frognet-ram-java/src -name '*.java') 2>&1 | grep -v '^Note:' || true
[ -f $T/classes/net/frognet/rangertrak/android/MainActivity.class ] || { echo "build_apk: javac failed" >&2; exit 1; }
dalvik-exchange --dex --min-sdk-version=26 --output=$T/classes.dex $T/classes
(cd $T && zip -qj base.apk classes.dex)
"$BT/zipalign" -f -p 4 $T/base.apk $T/aligned.apk
KS=$D/debug.keystore
[ -f $KS ] || keytool -genkeypair -keystore $KS -storepass android -keypass android -alias rangertrak -keyalg RSA -keysize 2048 \
    -validity 10000 -dname "CN=RangerTrak debug, O=Fawcett Innovations" >/dev/null 2>&1
A="$OUT/$NAME-android-$BUILD.apk"
"$BT/apksigner" sign --ks $KS --ks-pass pass:android --key-pass pass:android --ks-key-alias rangertrak --out "$A" $T/aligned.apk
"$BT/apksigner" verify --print-certs "$A" | head -2; ls -la "$A"; rm -rf $T
