#!/bin/bash
# studio_project.sh OUTDIR -- [RT_ANDROID_STUDIO_PROJECT_V1] OpenEOC-Android-Studio-<build>.zip: this Gradle project with
# everything it compiles inside it, to open and run in Android Studio anywhere -- the shared module gets its own copies
# of the core (shared/src/core), the RAM library (shared/src/ram) and the page (shared/src/main/assets/ui) in place of the paths into
# the repository. The apps (app, team, public), the Gradle wrapper and settings are the repository's own.
set -eu
D=$(cd "$(dirname "$0")" && pwd); APP=$(dirname "$D"); OUT=${1:?usage: studio_project.sh OUTDIR}; mkdir -p "$OUT"
BUILD=$(sed -n 's/.*BUILD = "\(openeoc-app-[^"]*\)".*/\1/p' $APP/core/src/net/frognet/openeoc/AppCore.java)
# [RT_ANDROID_STABLE_ROOT_V1] one root, every build (as the Windows zips: OpenEOC\, OpenEOC-EOC\): the project is
# OpenEOC-Android-Studio\ whatever build; the zip's name carries the build (unique downloads). An update zip has the
# same root, so unzipped next to the project it lands in it.
R=OpenEOC-Android-Studio; N=$R-$BUILD; T=$(mktemp -d); P=$T/$R; mkdir -p $P
(cd $D && tar cf - --exclude=build --exclude=.gradle --exclude=local.properties --exclude=build_apk.sh --exclude=studio_project.sh --exclude=studio_update.sh --exclude=teamram --exclude=typecheck.sh .) | (cd $P && tar xf -)
cp -r $APP/core/src $P/shared/src/core; cp -r $APP/../frognet-ram-java/src $P/shared/src/ram
mkdir -p $P/shared/src/main/assets; cp -r $APP/ui $P/shared/src/main/assets/ui     # the page: plain assets, no copy step
# [RT_LAYERS_V1] the base page in shared; each app's own layers in its own assets (as its build.gradle.kts lists them),
# its copy step gone (the lines marked openeoc-layers-copy)
rm -rf $P/shared/src/main/assets/ui/layers
for g in $P/*/build.gradle.kts; do
  m=$(dirname $g); L=$(sed -n 's/^val rtLayers = listOf(\(.*\))$/\1/p' $g | tr -d '" ' | tr ',' ' ')
  [ -n "$L" ] || continue
  mkdir -p $m/src/main/assets/ui/layers; for n in $L; do cp $APP/ui/layers/$n.js $m/src/main/assets/ui/layers/; done
  sed -i '/openeoc-layers-copy/d' $g
done
# [APP_IS_SELF_CONTAINED_V1] the FrogNet member and its Communicator, from frognet-android, as the repository's shared
# module compiles them: every app carries its own membership; nothing else is installed
FN=$APP/../frognet-android; mkdir -p $P/shared/src/fdf
cp -r $FN/member/src/main/kotlin $P/shared/src/fdf/member; cp -r $FN/app/src/main/kotlin $P/shared/src/fdf/app
cp -r $FN/app/src/main/java $P/shared/src/fdf/comms
# fdf-tun beside [TEAM_HOST_V1] libteamram.so (already in shared/src/main/jniLibs: built here by teamram/build.sh)
mkdir -p $P/shared/src/main/jniLibs; cp -r $FN/app/src/main/jniLibs/. $P/shared/src/main/jniLibs/
cat > $P/shared/build.gradle.kts <<'KTS'
// [RT_ANDROID_SHARED_V1] what the OpenEOC apps share, once: the Android shell (src/main/java: MainActivity -- the
// WebView, the GPS, the no-GPS address), the app core (src/core), the RAM library (src/ram) and the page
// (src/main/assets/ui). Each app is its manifest: its id, its name, its view.
// [APP_IS_SELF_CONTAINED_V1] every app is its own FrogNet member, nothing else installed: the member is compiled in
// (src/fdf/member: the protocol; src/fdf/app: MemberService, BridgeVpn, Identity, ...; src/main/jniLibs: fdf-tun and
// libteamram.so, the team RAM on a lead's phone; and the WireGuard crypto), and its Communicator (src/fdf/comms), which
// Call opens in this app. (Exported from the repository by android/studio_project.sh, where these are compiled from
// frognet-android and the app's own directories.)
plugins { id("com.android.library"); kotlin("android") }
android {
    namespace = "net.frognet.openeoc.shared"
    compileSdk = 34
    defaultConfig { minSdk = 29 }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    sourceSets["main"].java.srcDirs("src/main/java", "src/core", "src/ram", "src/fdf/comms")
    sourceSets["main"].kotlin.srcDirs("src/fdf/member", "src/fdf/app")
}
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531"); implementation("com.journeyapps:zxing-android-embedded:4.3.0"); implementation("androidx.core:core:1.12.0") }   // zxing 4.x calls androidx ContextCompat but its POM does not say so
KTS
# [ADMIN_PHONE_APP_V1] the admin app's page as the admin module's own assets (in the repository: ../../ui-admin, copied at build)
mkdir -p $P/admin/src/main/assets; cp -r $APP/ui-admin $P/admin/src/main/assets/ui-admin
cat > $P/admin/build.gradle.kts <<'KTS'
// [ADMIN_PHONE_APP_V1] OpenEOC Admin on the phone: the admin app's page (src/main/assets/ui-admin) and its core
// (AdminCore, in the shared module's src/core) in a WebView -- the same page and core as the Windows admin app.
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.openeoc.admin.android"
    compileSdk = 34
    defaultConfig { applicationId = "net.frognet.openeoc.admin"; minSdk = 29; targetSdk = 34; versionCode = 12; versionName = "openeoc-admin-2026-11-09-r19" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }
KTS
printf '/build\n*/build\n.gradle\n.idea\nlocal.properties\n*.iml\n' > $P/.gitignore
cat > $P/README.txt <<TXT
OpenEOC for Android ($BUILD) -- an Android Studio project
Open this folder in Android Studio (File > Open). Gradle syncs (it fetches Gradle 8.9 and the Android plugin 8.5.2;
JDK 17 or newer, Android SDK 34). Run configurations: eoc (OpenEOC EOC), team (OpenEOC Team), public (OpenEOC Public), the unit apps
app (OpenEOC: every view, asks which app -- the one to run), ambulance, hospital, water, power, shelter, telecom (OpenEOC <Unit>), admin (OpenEOC Admin).
Each APK is the whole app: its FrogNet membership is inside it; nothing else is installed.
Command line: ./gradlew :eoc:assembleDebug :team:assembleDebug :public:assembleDebug :ambulance:assembleDebug ... :admin:assembleDebug
  -> eoc/build/outputs/apk/debug/eoc-debug.apk, team/build/outputs/apk/debug/team-debug.apk, public/...
Modules: shared (the Android shell, the core in shared/src/core, the RAM library in shared/src/ram, the page in
shared/src/main/assets/ui), and one module per app (its manifest: id, name, view).
On the phone: join the node's Wi-Fi; Node is filled from the gateway. The build is on the first screen; page errors
show on the status line and in Logcat (tag OpenEOC).
TXT
(cd $T && zip -qr "$OUT/$N.zip" $R); ls -la "$OUT/$N.zip"; rm -rf $T
