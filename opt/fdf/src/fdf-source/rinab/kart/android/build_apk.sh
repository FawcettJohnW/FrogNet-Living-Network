#!/bin/bash
# build_apk.sh [OUT.apk] -- FrogNet Kart for Android, built with Maven Central jars and a JDK only (no Android SDK). [KART_ANDROID_V1]
#   org.robolectric:android-all:14-robolectric-10818077  the API 34 framework classes to compile against
#   org.jetbrains.kotlin:kotlin-compiler-embeddable 2.0.21 (and what it runs on): the FrogNet member's Kotlin
#   wireguard-android 1.0.20250531 (GitHub, its source) its crypto (the member's WireGuard key pair) -- as frognet-android
#   com.jakewharton.android.repackaged:dalvik-dx:16.0.1  dx: class files -> classes.dex
#   com.android.tools.build:apksig:2.3.0                 v2 signing (minSdk 26), and apksig's own verifier
# The binary manifest is written by tools/axml.py from AndroidManifest.xml (the one Android Studio uses) with framework
# ids read here from the jar (javap). No resources.arsc (none used). Signed with tools/frognet-kart.p12 (password frognet).
# TOOLS=<dir> caches the jars (default ./.tools).
# One source for everything compiled in from frognet-android (FN=<its directory>, default ../../../../frognet-android):
#   [KART_CALLS_IN_THE_GAME_V1] the Communicator: app/src/main/java/net/frognet/communicator, all but CommunicatorActivity
#   [KART_IS_A_MEMBER_V1] the FrogNet member, so this one app is self-contained: member/src/main/kotlin (the protocol) and
#     app/src/main/kotlin's AppLog, Identity, MemberService, BridgeVpn (+AndroidBridgeHost), AndroidTuner; and its tunnel
#     program, app/src/main/jniLibs/<abi>/libfdftun.so (fdf-tun, run from the native library directory)
set -euo pipefail
cd "$(dirname "$0")"; HERE=$PWD
FN=${FN:-$HERE/../../../../frognet-android}
COMMS_SRC=$FN/app/src/main/java/net/frognet/communicator; MK=$FN/member/src/main/kotlin; AK=$FN/app/src/main/kotlin/com/fawcettinnovations/fdf/android
[ -f "$COMMS_SRC/core/Room.java" ] || { echo "build_apk: no Communicator source at $COMMS_SRC (FN=<frognet-android>)" >&2; exit 1; }
[ -f "$AK/MemberService.kt" ] && [ -d "$MK" ] || { echo "build_apk: no FrogNet member source at $FN (FN=<frognet-android>)" >&2; exit 1; }
for abi in arm64-v8a armeabi-v7a x86_64; do [ -s "$FN/app/src/main/jniLibs/$abi/libfdftun.so" ] || { echo "build_apk: no $abi libfdftun.so in $FN/app/src/main/jniLibs" >&2; exit 1; }; done
OUT=${1:-$HERE/build/FrogNetKart.apk}; T=${TOOLS:-$HERE/.tools}; M=https://repo1.maven.org/maven2
VERSION_CODE=10; VERSION_NAME=$(sed -n 's/.*BUILD = "kart-android-\([^"]*\)".*/\1/p' src/net/frognet/kart/core/Game.java)
[ -n "$VERSION_NAME" ] || { echo "build_apk: no BUILD in Game.java" >&2; exit 1; }
mkdir -p "$T" build
get() { [ -s "$T/$2" ] || curl -sSf --retry 5 --retry-delay 3 -o "$T/$2" "$M/$1"; }
get org/robolectric/android-all/14-robolectric-10818077/android-all-14-robolectric-10818077.jar android-all-34.jar
get com/jakewharton/android/repackaged/dalvik-dx/16.0.1/dalvik-dx-16.0.1.jar dalvik-dx-16.0.1.jar
get com/android/tools/build/apksig/2.3.0/apksig-2.3.0.jar apksig-2.3.0.jar
K=org/jetbrains/kotlin
get $K/kotlin-compiler-embeddable/2.0.21/kotlin-compiler-embeddable-2.0.21.jar kotlinc.jar
get $K/kotlin-stdlib/2.0.21/kotlin-stdlib-2.0.21.jar kotlin-stdlib.jar
get $K/kotlin-script-runtime/2.0.21/kotlin-script-runtime-2.0.21.jar kotlin-script-runtime.jar
get $K/kotlin-daemon-embeddable/2.0.21/kotlin-daemon-embeddable-2.0.21.jar kotlin-daemon.jar
get $K/kotlin-reflect/1.6.10/kotlin-reflect-1.6.10.jar kotlin-reflect.jar
get org/jetbrains/intellij/deps/trove4j/1.0.20200330/trove4j-1.0.20200330.jar trove4j.jar
get org/jetbrains/annotations/13.0/annotations-13.0.jar annotations.jar
get org/jetbrains/kotlinx/kotlinx-coroutines-core-jvm/1.6.4/kotlinx-coroutines-core-jvm-1.6.4.jar coroutines.jar
AA=$T/android-all-34.jar
rm -rf build/classes build/kclasses build/wg build/wg-all build/apk build/sign && mkdir -p build/classes build/kclasses build/wg build/apk build/sign
echo "== the WireGuard crypto (the member's key pair): wireguard-android 1.0.20250531's own source, for Java 8 (dx)"
# the aar's classes are Java 17 class files, which dx cannot read; the same source, compiled here, is (Apache-2.0)
WG=$T/wireguard-android-1.0.20250531
[ -f "$WG/tunnel/src/main/java/com/wireguard/crypto/Key.java" ] || { rm -rf "$WG"
    git clone -q --depth 1 --branch 1.0.20250531 --filter=blob:none --sparse https://github.com/WireGuard/wireguard-android "$WG"
    git -C "$WG" sparse-checkout set tunnel/src/main/java/com/wireguard/crypto; }
javac --release 8 -Xlint:-options -nowarn -encoding UTF-8 -d build/wg-all $(find "$WG/tunnel/src/main/java/com/wireguard/crypto" -name '*.java') $(find tools/stubs -name '*.java')
mkdir -p build/wg/com/wireguard && cp -r build/wg-all/com/wireguard/crypto build/wg/com/wireguard/     # the stand-ins stay out
echo "== kotlinc: the FrogNet member (frognet-android, as it is there)"
java -cp "$T/kotlinc.jar:$T/kotlin-stdlib.jar:$T/kotlin-script-runtime.jar:$T/kotlin-daemon.jar:$T/kotlin-reflect.jar:$T/trove4j.jar:$T/annotations.jar:$T/coroutines.jar" \
    org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -no-stdlib -no-reflect -jvm-target 1.8 -Xlambdas=class -Xsam-conversions=class -nowarn \
    -classpath "$AA:$T/kotlin-stdlib.jar:build/wg" -d build/kclasses \
    $(find "$MK" -name '*.kt') "$AK/AppLog.kt" "$AK/Identity.kt" "$AK/MemberService.kt" "$AK/BridgeVpn.kt" "$AK/AndroidTuner.kt" "$AK/Telemetry.kt"
echo "== javac (Java 8 API, against the API 34 framework)"
javac --release 8 -Xlint:-options -nowarn -encoding UTF-8 -classpath "$AA:build/kclasses:$T/kotlin-stdlib.jar" -d build/classes $(find src -name '*.java') \
    $(find "$COMMS_SRC" -name '*.java' ! -name CommunicatorActivity.java)
echo "== dx (the game, the Communicator, the member, its crypto, the Kotlin library)"
rm -rf build/kstd && mkdir -p build/kstd && ( cd build/kstd && unzip -q "$T/kotlin-stdlib.jar" 'kotlin/*' )
java -Xmx2g -cp "$T/dalvik-dx-16.0.1.jar" com.android.dx.command.Main --dex --min-sdk-version=26 --output=build/apk/classes.dex \
    build/classes build/kclasses build/wg build/kstd
echo "== manifest ($VERSION_NAME)"
ATTRS="versionCode versionName minSdkVersion targetSdkVersion $(python3 tools/axml.py --attrs AndroidManifest.xml)"
{ javap -constants -cp "$AA" 'android.R$attr' | awk -v want=" $ATTRS " '/ = [0-9]+;/ { n=$(NF-2); v=$NF; gsub(";","",v); if (index(want, " " n " ")) print n, v }'
  javap -constants -cp "$AA" 'android.R$style' | awk '/ Theme_Material_NoActionBar_Fullscreen =/ {gsub(";","",$NF); print $(NF-2), $NF}'; } > build/ids.txt
for a in $ATTRS Theme_Material_NoActionBar_Fullscreen; do grep -q "^$a " build/ids.txt || { echo "build_apk: no framework id for $a" >&2; exit 1; }; done
python3 tools/axml.py AndroidManifest.xml build/apk/AndroidManifest.xml "$VERSION_CODE" "$VERSION_NAME" build/ids.txt
echo "== zip"
FN="$FN" python3 - <<'EOF'
import os, zipfile
z = zipfile.ZipFile("build/unsigned.apk", "w")
files = [("build/apk/AndroidManifest.xml", "AndroidManifest.xml"), ("build/apk/classes.dex", "classes.dex")]
for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):        # [KART_IS_A_MEMBER_V1] fdf-tun, extracted at install (extractNativeLibs)
    files.append((os.path.join(os.environ["FN"], "app/src/main/jniLibs", abi, "libfdftun.so"), "lib/%s/libfdftun.so" % abi))
for path, arc in files:
    zi = zipfile.ZipInfo(arc, date_time=(2026, 10, 22, 0, 0, 0)); zi.compress_type = zipfile.ZIP_DEFLATED; zi.external_attr = 0o644 << 16
    z.writestr(zi, open(path, "rb").read())
z.close()
EOF
echo "== sign"
[ -f tools/frognet-kart.p12 ] || keytool -genkeypair -keystore tools/frognet-kart.p12 -storetype PKCS12 -storepass frognet -keypass frognet \
    -alias frognet-kart -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=FrogNet Kart, O=Fawcett Innovations LLC" 2>&1 | grep -v JAVA_TOOL || true
javac -Xlint:-options -cp "$T/apksig-2.3.0.jar" -d build/sign tools/Sign.java
java --add-exports java.base/sun.security.x509=ALL-UNNAMED --add-exports java.base/sun.security.pkcs=ALL-UNNAMED --add-exports java.base/sun.security.util=ALL-UNNAMED -cp "build/sign:$T/apksig-2.3.0.jar" Sign build/unsigned.apk "$OUT" tools/frognet-kart.p12 frognet
echo "built $OUT ($(stat -c %s "$OUT") bytes, sha256 $(sha256sum "$OUT" | cut -c1-16)), kart-android-$VERSION_NAME"
