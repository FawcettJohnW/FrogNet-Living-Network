#!/bin/bash
# package_windows.sh OUTDIR [admin|eoc|eocadmin] -- [FEMA_EOC_V1] eocadmin: OpenEOC-EOCAdmin-windows-x64-<build>.zip, the EOC's
# paperwork (the same app, opening as EOCAdmin only: --lock=eocadmin); [RT_EOC_APP_V1] [COMMAND_CENTER_V1] eoc: OpenEOC-CommandCenter-windows-x64-<build>.zip, the Command Center (the
# same app, opening as the Command Center only: OpenEOC-CommandCenter.cmd passes --lock=eoc); [RT_APP_DESKTOP_V1] OpenEOC-windows-x64-<build>.zip: the app, its page, JavaFX for
# Windows and a Java runtime, in one folder; OpenEOC.cmd starts it. Nothing is installed: unzip and run.
# The runtime: OpenJDK 21 for Windows x64 from pypi's jdk4py wheel (as the FD&F Windows bridge gets its runtime: no JDK
# download site is reachable from the build machine). JavaFX 21.0.5's Windows jars from Maven Central, on the class
# path. Two pure-Java JDK modules JavaFX's web view needs and that runtime lacks (jdk.jsobject, jdk.xml.dom) are made
# into jars from this machine's JDK 21. Needs: JDK 21, pip, curl, zip.
# admin: [ADMIN_APP_V1] OpenEOC-Admin-windows-x64-<admin build>.zip instead, the standalone admin app (ui-admin,
# OpenEOC-Admin.cmd), the same runtime and jars.
set -eu
D=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:?usage: package_windows.sh OUTDIR}; mkdir -p "$OUT"
ADMIN=${2:-}
BUILD=$(sed -n 's/.*BUILD = "\(openeoc-app-[^"]*\)".*/\1/p' $D/core/src/net/frognet/openeoc/AppCore.java)
NAME=OpenEOC
LOCK=""; LAYERS=""; [ "$ADMIN" = eoc ] && { NAME=OpenEOC-CommandCenter; LOCK=eoc; LAYERS=help,field,eoc,medical,ics,fema; }   # [RT_LAYERS_V1] the EOC app's layers
[ "$ADMIN" = eocadmin ] && { NAME=OpenEOC-EOCAdmin; LOCK=eocadmin; LAYERS=help,medical,ics,fema,paper; }   # [FEMA_EOC_V1] EOCAdmin's
if [ "$ADMIN" = admin ]; then BUILD=$(sed -n 's/.*BUILD = "\(openeoc-admin-[^"]*\)".*/\1/p' $D/core/src/net/frognet/openeoc/admin/AdminCore.java); NAME=OpenEOC-Admin; fi
T=$(mktemp -d); R=$T/$NAME; mkdir -p $R/lib $T/fx
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
if [ "$ADMIN" = admin ]; then
cp -r $D/ui-admin $R/ui-admin; cp $D/brand/openeoc.ico $R/   # [OPENEOC_BRAND_V1] the icon, for a shortcut to the .cmd
printf '@echo off\r\nrem OpenEOC Admin, powered by the FrogNet Living Network (%s)\r\nstart "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*" net.frognet.openeoc.desktop.AdminLauncher --ui="%%~dp0ui-admin" %%*\r\n' "$BUILD" > $R/OpenEOC-Admin.cmd
cat > $R/README.txt <<TXT
OpenEOC Admin for Windows ($BUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\OpenEOC-Admin) and double-click OpenEOC-Admin.cmd. Nothing is installed.
Connect: Node = any member with OpenEOC's door; App = openeoc; the admin user and password set on the members
with  fdf-app config openeoc ... --admin USER:PASSWORD. The password is checked on this computer and never sent.
Teams and roles, users (passwords become verifiers here), login and partition behaviour; Apply sends the whole
configuration, signed with the admin key; every member checks it and says adopted or why not (Members).
It keeps the node, the app and the user name in %USERPROFILE%\.openeoc\admin.json; never the password.
TXT
(cd $T && zip -qr "$OUT/$NAME-windows-x64-$BUILD.zip" $NAME); ls -la "$OUT/$NAME-windows-x64-$BUILD.zip"; rm -rf $T; exit 0
fi
cp -r $D/ui $R/ui; cp $D/brand/openeoc.ico $R/   # [OPENEOC_BRAND_V1] the icon, for a shortcut to the .cmd
# [RT_LAYERS_V1] an app carries its own layers only (the client: every one)
if [ -n "$LAYERS" ]; then for f in $R/ui/layers/*.js; do n=$(basename $f .js); case ",$LAYERS," in *",$n,"*) ;; *) rm $f;; esac; done; fi
if [ "$LOCK" = eoc ]; then
printf '@echo off\r\nrem OpenEOC Command Center, powered by the FrogNet Living Network (%s)\r\nstart "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*" net.frognet.openeoc.desktop.Launcher --ui="%%~dp0ui" --lock=eoc --layers=%s %%*\r\n' "$BUILD" "$LAYERS" > $R/OpenEOC-CommandCenter.cmd
cat > $R/README.txt <<TXT
OpenEOC Command Center for Windows ($BUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\OpenEOC-CommandCenter) and double-click OpenEOC-CommandCenter.cmd. Nothing is installed.
Connect: this computer on a FrogNet node's Wi-Fi; Node = the node's address (filled in from this computer's gateway);
the Command Center's user and password (a user the admin gave the role eoc). It opens as the Command Center only: every
team, unit, request and report the Command Center's network can see. Settings this computer keeps: %USERPROFILE%\.openeoc
The map's background comes from OpenStreetMap over the Internet; without it the map is plain and everything is on it.
TXT
(cd $T && zip -qr "$OUT/$NAME-windows-x64-$BUILD.zip" $NAME); ls -la "$OUT/$NAME-windows-x64-$BUILD.zip"; rm -rf $T; exit 0
fi
if [ "$LOCK" = eocadmin ]; then
printf '@echo off\r\nrem OpenEOC EOCAdmin, powered by the FrogNet Living Network (%s)\r\nstart "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*" net.frognet.openeoc.desktop.Launcher --ui="%%~dp0ui" --lock=eocadmin --layers=%s %%*\r\n' "$BUILD" "$LAYERS" > $R/OpenEOC-EOCAdmin.cmd
cat > $R/README.txt <<TXT
OpenEOC EOCAdmin for Windows ($BUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\OpenEOC-EOCAdmin) and double-click OpenEOC-EOCAdmin.cmd. Nothing is installed.
The Command Center's paperwork: the ICS forms (201-221) and the situation report, each one filled in from its boards and the
organizations on the network; drafts and finals kept on the network for every EOCAdmin; the Incident Action Plan; the
after-action report and improvement plan; printed on this computer's printer.
Connect: this computer on a FrogNet node's Wi-Fi; Node = the node's address; a Command Center user and password (a user the admin
gave the role eoc). Settings this computer keeps: %USERPROFILE%\.openeoc
TXT
(cd $T && zip -qr "$OUT/$NAME-windows-x64-$BUILD.zip" $NAME); ls -la "$OUT/$NAME-windows-x64-$BUILD.zip"; rm -rf $T; exit 0
fi
printf '@echo off\r\nrem OpenEOC, powered by the FrogNet Living Network (%s)\r\nstart "" "%%~dp0runtime\\bin\\javaw.exe" -cp "%%~dp0lib\\*" net.frognet.openeoc.desktop.Launcher --ui="%%~dp0ui" %%*\r\n' "$BUILD" > $R/OpenEOC.cmd
cat > $R/README.txt <<TXT
OpenEOC for Windows ($BUILD), powered by the FrogNet Living Network
Unzip anywhere (e.g. C:\OpenEOC) and double-click OpenEOC.cmd. Nothing is installed.
Connect: this computer on a FrogNet node's Wi-Fi; Node = the node's address (filled in from this computer's gateway);
the view: Command Center (its user and password), a Team, or Public. Settings this computer keeps: %USERPROFILE%\.openeoc
The map's background comes from OpenStreetMap over the Internet; without it the map is plain and everything is on it.
TXT
(cd $T && zip -qr "$OUT/OpenEOC-windows-x64-$BUILD.zip" OpenEOC); ls -la "$OUT/OpenEOC-windows-x64-$BUILD.zip"; rm -rf $T
