#!/bin/bash
# package_windows.sh OUTDIR COMMS_DIST -- [ONE_BUNDLE_V1] (John 2026-11-10: "the comms-app is part of the bundle thrown down,
# but also such that there is only one on the device"; he chose one bundle for every app) OpenEOC-windows-x64-<build>.zip:
# one folder, one Java runtime, one lib, one Communicator, and a launcher per app:
#   OpenEOC-CommandCenter.cmd   the Command Center only (--lock=eoc)                     [RT_EOC_APP_V1][COMMAND_CENTER_V1]
#   OpenEOC-EOCAdmin.cmd        the Command Center's paperwork only (--lock=eocadmin)     [FEMA_EOC_V1]
#   OpenEOC.cmd                 every view, asked first (Team, Hospital, Ambulance, ...)  [RT_APP_DESKTOP_V1]
#   OpenEOC-Admin.cmd           the admin tool (ui-admin)                                 [ADMIN_APP_V1]
# Each app keeps its own settings (%USERPROFILE%\.openeoc), loads its own layers (--layers) and calls with the one
# Communicator, comms\comms-app.exe ([ONE_COMMUNICATOR_V1]). COMMS_DIST: the Communicator's Windows dist folder
# (build-windows.ps1's dist\: comms-app.exe with its DLLs and Qt plugins), copied whole into comms\. No COMMS_DIST, or
# one without comms-app.exe: nothing is made.
# The runtime: OpenJDK 21 for Windows x64 from pypi's jdk4py wheel (as the FD&F Windows bridge gets its runtime: no JDK
# download site is reachable from the build machine). JavaFX 21.0.5's Windows jars from Maven Central, on the class
# path. Two pure-Java JDK modules JavaFX's web view needs and that runtime lacks (jdk.jsobject, jdk.xml.dom) are made
# into jars from this machine's JDK 21. Needs: JDK 21, pip, curl, zip. Nothing is installed on Windows: unzip and run.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:?usage: package_windows.sh OUTDIR COMMS_DIST}; CD=${2:?usage: package_windows.sh OUTDIR COMMS_DIST (the Communicator Windows dist folder)}
[ -f "$CD/comms-app.exe" ] || { echo "package_windows.sh: $CD has no comms-app.exe: give the Communicator's Windows dist folder (build-windows.ps1's dist\\)"; exit 1; }
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
BUILD=$(sed -n 's/.*BUILD = "\(openeoc-app-[^"]*\)".*/\1/p' $D/core/src/net/frognet/openeoc/AppCore.java)
ABUILD=$(sed -n 's/.*BUILD = "\(openeoc-admin-[^"]*\)".*/\1/p' $D/core/src/net/frognet/openeoc/admin/AdminCore.java)
CC=help,field,eoc,medical,ics,fema; EA=help,medical,ics,fema,paper          # [RT_LAYERS_V1] the Command Center's, EOCAdmin's layers
NAME=OpenEOC; T=$(mktemp -d); R=$T/$NAME; mkdir -p $R/lib $T/fx
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
cp $D/build/*.jar $R/lib/
cp -r $D/ui $R/ui; cp -r $D/ui-admin $R/ui-admin; cp $D/brand/openeoc.ico $R/   # [OPENEOC_BRAND_V1] the icon, for a shortcut to a .cmd
mkdir -p $R/comms && cp -r "$CD"/. $R/comms/                                      # [ONE_COMMUNICATOR_V1] the one Communicator
J='start "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*"'
printf "@echo off\r\nrem OpenEOC Command Center, powered by the FrogNet Living Network (%s)\r\n$J net.frognet.openeoc.desktop.Launcher --ui=\"%%~dp0ui\" --lock=eoc --layers=%s %%*\r\n" "$BUILD" "$CC" > $R/OpenEOC-CommandCenter.cmd
printf "@echo off\r\nrem OpenEOC EOCAdmin, powered by the FrogNet Living Network (%s)\r\n$J net.frognet.openeoc.desktop.Launcher --ui=\"%%~dp0ui\" --lock=eocadmin --layers=%s %%*\r\n" "$BUILD" "$EA" > $R/OpenEOC-EOCAdmin.cmd
printf "@echo off\r\nrem OpenEOC, powered by the FrogNet Living Network (%s)\r\n$J net.frognet.openeoc.desktop.Launcher --ui=\"%%~dp0ui\" %%*\r\n" "$BUILD" > $R/OpenEOC.cmd
printf "@echo off\r\nrem OpenEOC Admin, powered by the FrogNet Living Network (%s)\r\n$J net.frognet.openeoc.desktop.AdminLauncher --ui=\"%%~dp0ui-admin\" %%*\r\n" "$ABUILD" > $R/OpenEOC-Admin.cmd
cat > $R/README.txt <<TXT
OpenEOC for Windows ($BUILD; admin $ABUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\OpenEOC) and double-click the app's .cmd. Nothing is installed. Every app here shares this
folder's runtime and its one Communicator (comms\comms-app.exe), which Call opens.
  OpenEOC-CommandCenter.cmd  the Command Center: a Command Center user and password (a user the admin gave the role eoc)
  OpenEOC-EOCAdmin.cmd       the Command Center's paperwork: the ICS forms (201-221), the situation report, the Incident
                             Action Plan, the after-action report; printed on this computer's printer
  OpenEOC.cmd                every view, asked first: a Team, a Hospital, an Ambulance, ..., Public
  OpenEOC-Admin.cmd          the admin tool: Node = any member with OpenEOC's door; App = openeoc; the admin user and
                             password set with  fdf-app config openeoc ... --admin USER:PASSWORD (checked here, never sent)
Connect: this computer on a FrogNet node's Wi-Fi; Node = the node's address (filled in from this computer's gateway).
Call needs the Communicator's servers on the node (comms-ram on 8800, comms-media on 8994: fdf-comms.sh).
Settings each app keeps: %USERPROFILE%\.openeoc (the admin tool: admin.json, never the password).
The map's background comes from OpenStreetMap over the Internet; without it the map is plain and everything is on it.
TXT
(cd $T && zip -qr "$OUT/$NAME-windows-x64-$BUILD.zip" $NAME); ls -la "$OUT/$NAME-windows-x64-$BUILD.zip"; rm -rf $T
