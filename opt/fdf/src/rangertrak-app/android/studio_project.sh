#!/bin/bash
# studio_project.sh OUTDIR -- [RT_ANDROID_STUDIO_PROJECT_V1] RangerTrak-Android-Studio-<build>.zip: this Gradle project with
# everything it compiles inside it, to open and run in Android Studio anywhere -- the shared module gets its own copies
# of the core (shared/src/core), the RAM library (shared/src/ram) and the page (shared/src/main/assets/ui) in place of the paths into
# the repository. The apps (app, team, public), the Gradle wrapper and settings are the repository's own.
set -eu
D=$(cd "$(dirname "$0")" && pwd); APP=$(dirname "$D"); OUT=${1:?usage: studio_project.sh OUTDIR}; mkdir -p "$OUT"
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $APP/core/src/net/frognet/rangertrak/AppCore.java)
N=RangerTrak-Android-Studio-$BUILD; T=$(mktemp -d); P=$T/$N; mkdir -p $P
(cd $D && tar cf - --exclude=build --exclude=.gradle --exclude=local.properties --exclude=build_apk.sh --exclude=studio_project.sh --exclude=studio_update.sh --exclude=teamram --exclude=typecheck.sh .) | (cd $P && tar xf -)
cp -r $APP/core/src $P/shared/src/core; cp -r $APP/../frognet-ram-java/src $P/shared/src/ram
mkdir -p $P/shared/src/main/assets; cp -r $APP/ui $P/shared/src/main/assets/ui     # the page: plain assets, no copy step
# [RT_IS_A_MEMBER_V1] the FrogNet member and its Communicator, from frognet-android, as the repository's shared module compiles them
FN=$APP/../frognet-android; mkdir -p $P/shared/src/fdf
cp -r $FN/member/src/main/kotlin $P/shared/src/fdf/member; cp -r $FN/app/src/main/kotlin $P/shared/src/fdf/app
cp -r $FN/app/src/main/java $P/shared/src/fdf/comms
# fdf-tun beside [TEAM_HOST_V1] libteamram.so (already in shared/src/main/jniLibs: built here by teamram/build.sh)
mkdir -p $P/shared/src/main/jniLibs; cp -r $FN/app/src/main/jniLibs/. $P/shared/src/main/jniLibs/
cat > $P/shared/build.gradle.kts <<'KTS'
// [RT_ANDROID_SHARED_V1] what the three RangerTrak apps (app, team, public) share, once: the Android shell
// (src/main/java: MainActivity -- the WebView, the GPS, the no-GPS address), the app core (src/core), the RAM library
// (src/ram) and the page (src/main/assets/ui). Each app is its manifest: its id, its name, its view.
// [RT_IS_A_MEMBER_V1] as the kart app: the FrogNet member is compiled in (src/fdf/member: the protocol; src/fdf/app:
// MemberService, BridgeVpn, Identity, ...; src/main/jniLibs: fdf-tun and libteamram.so, the team RAM on a lead's phone; and
// the WireGuard crypto), and its Communicator
// (src/fdf/comms), which Call opens in this app. (Exported from the repository by android/studio_project.sh, where these
// are compiled from frognet-android and the app's own directories.)
plugins { id("com.android.library"); kotlin("android") }
android {
    namespace = "net.frognet.rangertrak.shared"
    compileSdk = 34
    defaultConfig { minSdk = 29 }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    sourceSets["main"].java.srcDirs("src/main/java", "src/core", "src/ram", "src/fdf/comms")
    sourceSets["main"].kotlin.srcDirs("src/fdf/member", "src/fdf/app")
}
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531"); implementation("com.journeyapps:zxing-android-embedded:4.3.0") }
KTS
printf '/build\n*/build\n.gradle\n.idea\nlocal.properties\n*.iml\n' > $P/.gitignore
cat > $P/README.txt <<TXT
RangerTrak for Android ($BUILD) -- an Android Studio project
Open this folder in Android Studio (File > Open). Gradle syncs (it fetches Gradle 8.9 and the Android plugin 8.5.2;
JDK 17 or newer, Android SDK 34). Run configurations: team (RangerTrak Team), public (RangerTrak Public), app (every view).
Command line: ./gradlew :team:assembleDebug :public:assembleDebug
  -> team/build/outputs/apk/debug/team-debug.apk, public/build/outputs/apk/debug/public-debug.apk
Modules: shared (the Android shell, the core in shared/src/core, the RAM library in shared/src/ram, the page in
shared/src/main/assets/ui), and one module per app (its manifest: id, name, view).
On the phone: join the node's Wi-Fi; Node is filled from the gateway. The build is on the first screen; page errors
show on the status line and in Logcat (tag RangerTrak).
TXT
(cd $T && zip -qr "$OUT/$N.zip" $N); ls -la "$OUT/$N.zip"; rm -rf $T
