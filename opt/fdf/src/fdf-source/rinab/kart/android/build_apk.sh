#!/bin/bash
# build_apk.sh [OUT.apk] -- FrogNet Kart for Android, built with Maven Central jars and a JDK only (no Android SDK). [KART_ANDROID_V1]
#   org.robolectric:android-all:13-robolectric-9030017   the API 33 framework classes to compile against
#   com.jakewharton.android.repackaged:dalvik-dx:16.0.1  dx: class files -> classes.dex
#   com.android.tools.build:apksig:2.3.0                 v2 signing (minSdk 26), and apksig's own verifier
# The binary manifest is written by tools/axml.py with framework ids read here from the jar (javap). The app has no
# resources.arsc (it uses none). Signed with tools/frognet-kart.p12 (password frognet): later builds must use it to
# install over this one. TOOLS=<dir> caches the jars (default ./.tools).
set -euo pipefail
cd "$(dirname "$0")"; HERE=$PWD
OUT=${1:-$HERE/build/FrogNetKart.apk}; T=${TOOLS:-$HERE/.tools}; M=https://repo1.maven.org/maven2
VERSION_CODE=1; VERSION_NAME=$(sed -n 's/.*BUILD = "kart-android-\([^"]*\)".*/\1/p' src/net/frognet/kart/core/Game.java)
[ -n "$VERSION_NAME" ] || { echo "build_apk: no BUILD in Game.java" >&2; exit 1; }
mkdir -p "$T" build
get() { [ -s "$T/$2" ] || curl -sSf --retry 5 --retry-delay 3 -o "$T/$2" "$M/$1"; }
get org/robolectric/android-all/13-robolectric-9030017/android-all-13-robolectric-9030017.jar android-all-33.jar
get com/jakewharton/android/repackaged/dalvik-dx/16.0.1/dalvik-dx-16.0.1.jar dalvik-dx-16.0.1.jar
get com/android/tools/build/apksig/2.3.0/apksig-2.3.0.jar apksig-2.3.0.jar
rm -rf build/classes build/apk build/sign && mkdir -p build/classes build/apk build/sign
echo "== javac (Java 8 API, against the API 33 framework)"
javac --release 8 -Xlint:-options -encoding UTF-8 -classpath "$T/android-all-33.jar" -d build/classes $(find src -name '*.java')
echo "== dx"
java -cp "$T/dalvik-dx-16.0.1.jar" com.android.dx.command.Main --dex --min-sdk-version=26 --output=build/apk/classes.dex build/classes
echo "== manifest ($VERSION_NAME)"
{ javap -constants -cp "$T/android-all-33.jar" 'android.R$attr' | awk '/ (versionCode|versionName|minSdkVersion|targetSdkVersion|name|label|theme|screenOrientation|exported|configChanges|hardwareAccelerated) =/ {gsub(";","",$NF); print $(NF-2), $NF}'
  javap -constants -cp "$T/android-all-33.jar" 'android.R$style' | awk '/ Theme_Material_NoActionBar_Fullscreen =/ {gsub(";","",$NF); print $(NF-2), $NF}'; } > build/ids.txt
[ "$(wc -l < build/ids.txt)" = 12 ] || { echo "build_apk: expected 12 framework ids, got: $(cat build/ids.txt)" >&2; exit 1; }
python3 tools/axml.py build/apk/AndroidManifest.xml "$VERSION_CODE" "$VERSION_NAME" build/ids.txt
echo "== zip"
python3 - <<'EOF'
import zipfile
z = zipfile.ZipFile("build/unsigned.apk", "w")
for path, arc in (("build/apk/AndroidManifest.xml", "AndroidManifest.xml"), ("build/apk/classes.dex", "classes.dex")):
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
