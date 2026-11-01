#!/bin/bash
# package_windows.sh OUTDIR -- [RT_APP_DESKTOP_V1] RangerTrak-windows-x64-<build>.zip: the app, its page, JavaFX for
# Windows and a Java runtime, in one folder; RangerTrak.cmd starts it. Nothing is installed: unzip and run.
# The runtime: OpenJDK 21 for Windows x64 from pypi's jdk4py wheel (as the FD&F Windows bridge gets its runtime: no JDK
# download site is reachable from the build machine). JavaFX 21.0.5's Windows jars from Maven Central, on the class
# path. Two pure-Java JDK modules JavaFX's web view needs and that runtime lacks (jdk.jsobject, jdk.xml.dom) are made
# into jars from this machine's JDK 21. Needs: JDK 21, pip, curl, zip.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:?usage: package_windows.sh OUTDIR}; mkdir -p "$OUT"
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $D/core/src/net/frognet/rangertrak/AppCore.java)
T=$(mktemp -d); R=$T/RangerTrak; mkdir -p $R/lib $T/fx
"$D/build.sh" >/dev/null
mkdir -p $D/build/fx-win      # kept between builds; Maven Central answers 429 when asked too fast, so each fetch retries
for m in base graphics controls web media; do f=$D/build/fx-win/javafx-$m-21.0.5-win.jar
  [ -s $f ] || curl -sf --retry 6 --retry-all-errors --retry-delay 5 -o $f https://repo1.maven.org/maven2/org/openjfx/javafx-$m/21.0.5/javafx-$m-21.0.5-win.jar; done
cp $D/build/fx-win/*.jar $R/lib/
pip download "jdk4py>=21,<22" --platform win_amd64 --only-binary=:all: --no-deps -d $T/whl -q
(cd $T && mkdir w && cd w && unzip -q ../whl/jdk4py-*.whl) && mv $T/w/jdk4py/java-runtime $R/runtime
JH=$(dirname "$(dirname "$(readlink -f "$(which java)")")")
jimage extract --dir $T/x --include "regex:/(jdk.jsobject|jdk.xml.dom)/.*" "$JH/lib/modules"
for m in jdk.jsobject jdk.xml.dom; do rm -f $T/x/$m/module-info.class; jar cf $R/lib/$m.jar -C $T/x/$m .; done
cp $D/build/*.jar $R/lib/; cp -r $D/ui $R/ui
printf '@echo off\r\nrem RangerTrak, powered by the FrogNet Living Network (%s)\r\nstart "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*" net.frognet.rangertrak.desktop.Launcher --ui="%%~dp0ui" %%*\r\n' "$BUILD" > $R/RangerTrak.cmd
cat > $R/README.txt <<TXT
RangerTrak for Windows ($BUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\RangerTrak) and double-click RangerTrak.cmd. Nothing is installed.
Connect: this computer on a FrogNet node's Wi-Fi; Node = the node's address (filled in from this computer's gateway);
the view: EOC (its user and password), a Team, or Public. Settings this computer keeps: %USERPROFILE%\.rangertrak
The map's background comes from OpenStreetMap over the Internet; without it the map is plain and everything is on it.
TXT
(cd $T && zip -qr "$OUT/RangerTrak-windows-x64-$BUILD.zip" RangerTrak); ls -la "$OUT/RangerTrak-windows-x64-$BUILD.zip"; rm -rf $T
