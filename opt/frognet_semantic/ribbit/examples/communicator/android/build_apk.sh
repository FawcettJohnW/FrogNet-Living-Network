#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build_apk.sh -- the FrogNet Communicator for Android, built without the Android SDK (Google's repositories are not
# reachable from the build machine). Everything comes from Maven Central:
#   org.robolectric:android-all:13-robolectric-9030017   the framework classes to compile against (API 33)
#   com.jakewharton.android.repackaged:dalvik-dx:16.0.1  dx, class files -> classes.dex
#   com.android.tools.build:apksig:2.3.0                 v2 signing, and apksig's own verifier
# The binary manifest and resources.arsc are written by tools/apkres.py. Output: build/FrogNetCommunicator.apk
set -euo pipefail
cd "$(dirname "$0")"
T=${TOOLS:-$HOME/.cache/frognet-android-tools}
VERSION_CODE=2
VERSION_NAME="2026.10.11-r2"
M=https://repo1.maven.org/maven2
mkdir -p "$T"
[ -f "$T/android-all-33.jar" ] || curl -sf -o "$T/android-all-33.jar" $M/org/robolectric/android-all/13-robolectric-9030017/android-all-13-robolectric-9030017.jar
[ -f "$T/dalvik-dx-16.0.1.jar" ] || curl -sf -o "$T/dalvik-dx-16.0.1.jar" $M/com/jakewharton/android/repackaged/dalvik-dx/16.0.1/dalvik-dx-16.0.1.jar
[ -f "$T/apksig-2.3.0.jar" ] || curl -sf -o "$T/apksig-2.3.0.jar" $M/com/android/tools/build/apksig/2.3.0/apksig-2.3.0.jar
grep -q "BUILD = \"comms-android-" src/net/frognet/communicator/Diag.java

rm -rf build/classes build/apk && mkdir -p build/classes build/apk/res/drawable
echo "== javac (API 33 framework, Java 8 bytecode for dx)"
javac --release 8 -Xlint:-options -encoding UTF-8 -cp "$T/android-all-33.jar" -d build/classes \
      $(find src -name '*.java')
echo "== dx"
java -cp "$T/dalvik-dx-16.0.1.jar" com.android.dx.command.Main --dex --min-sdk-version=26 --output=build/apk/classes.dex build/classes
echo "== manifest, resources, icon"
THEME=$(javap -constants -cp "$T/android-all-33.jar" 'android.R$style' | awk '/ Theme_Material_NoActionBar =/ {gsub(";","",$NF); print $NF}')
python3 tools/apkres.py manifest build/apk/AndroidManifest.xml "$THEME" "$VERSION_CODE" "$VERSION_NAME"
python3 tools/apkres.py arsc build/apk/resources.arsc
python3 tools/make_icon.py build/apk/res/drawable/icon.png
echo "== zip"
rm -f build/unsigned.apk
python3 - <<'EOF'
import zipfile
z = zipfile.ZipFile("build/unsigned.apk", "w")
def add(path, arc, stored):
    zi = zipfile.ZipInfo(arc, date_time=(2026, 10, 10, 0, 0, 0))
    zi.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    z.writestr(zi, open(path, "rb").read())
add("build/apk/AndroidManifest.xml", "AndroidManifest.xml", False)
add("build/apk/classes.dex", "classes.dex", False)
add("build/apk/resources.arsc", "resources.arsc", True)          # stored, as the platform maps it
add("build/apk/res/drawable/icon.png", "res/drawable/icon.png", True)
z.close()
EOF
echo "== sign (v2) and verify"
javac -cp "$T/apksig-2.3.0.jar" -d build tools/Sign.java
[ -f tools/frognet.p12 ] || keytool -genkeypair -keystore tools/frognet.p12 -storetype PKCS12 -storepass frognet -keypass frognet \
    -alias frognet -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=FrogNet Communicator, O=Fawcett Innovations LLC, L=Burien, ST=WA, C=US"
java --add-exports java.base/sun.security.x509=ALL-UNNAMED --add-exports java.base/sun.security.pkcs=ALL-UNNAMED --add-exports java.base/sun.security.util=ALL-UNNAMED -cp "$T/apksig-2.3.0.jar:build" Sign tools/frognet.p12 frognet frognet build/unsigned.apk build/FrogNetCommunicator.apk
ls -la build/FrogNetCommunicator.apk
sha256sum build/FrogNetCommunicator.apk
