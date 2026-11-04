#!/bin/bash
# studio_project.sh OUTDIR -- [RT_ANDROID_STUDIO_PROJECT_V1] RangerTrak-Android-Studio-<build>.zip: this Gradle project with
# everything it compiles inside it, to open and run in Android Studio anywhere -- the shared module gets its own copies
# of the core (shared/src/core), the RAM library (shared/src/ram) and the page (shared/src/main/assets/ui) in place of the paths into
# the repository. The apps (app, team, public), the Gradle wrapper and settings are the repository's own.
set -eu
D=$(cd "$(dirname "$0")" && pwd); APP=$(dirname "$D"); OUT=${1:?usage: studio_project.sh OUTDIR}; mkdir -p "$OUT"
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $APP/core/src/net/frognet/rangertrak/AppCore.java)
N=RangerTrak-Android-Studio-$BUILD; T=$(mktemp -d); P=$T/$N; mkdir -p $P
(cd $D && tar cf - --exclude=build --exclude=./base --exclude=.gradle --exclude=local.properties --exclude=build_apk.sh --exclude=studio_project.sh --exclude=studio_update.sh --exclude=teamram --exclude=typecheck.sh .) | (cd $P && tar xf -)
cp -r $APP/core/src $P/shared/src/core; cp -r $APP/../frognet-ram-java/src $P/shared/src/ram
mkdir -p $P/shared/src/main/assets; cp -r $APP/ui $P/shared/src/main/assets/ui     # the page: plain assets, no copy step
# [BASE_APP_V1] the FrogNet membership is the base app's (module base, frognet-android's app); shared has the Communicator
FN=$APP/../frognet-android; mkdir -p $P/shared/src/fdf $P/base/src/main
cp -r $FN/app/src/main/java $P/shared/src/fdf/comms
cp $FN/app/src/main/AndroidManifest.xml $P/base/src/main/; cp -r $FN/app/src/main/java $P/base/src/main/java
cp -r $FN/app/src/main/kotlin $P/base/src/main/kotlin; cp -r $FN/member/src/main/kotlin $P/base/src/member
mkdir -p $P/base/src/main/jniLibs; cp -r $FN/app/src/main/jniLibs/. $P/base/src/main/jniLibs/   # fdf-tun
cat > $P/shared/build.gradle.kts <<'KTS'
// [RT_ANDROID_SHARED_V1] what the three RangerTrak apps (app, team, public) share, once: the Android shell
// (src/main/java: MainActivity -- the WebView, the GPS, the no-GPS address), the app core (src/core), the RAM library
// (src/ram), the page (src/main/assets/ui), libteamram.so (src/main/jniLibs: the team RAM on a lead's phone) and the
// Communicator (src/fdf/comms), which Call opens in this app. [BASE_APP_V1] the FrogNet membership is NOT here: it is
// the base app's (module base, Disaster Area), which this app asks to join (Base.java). (Exported from the repository
// by android/studio_project.sh, where these are compiled from frognet-android and the app's own directories.)
plugins { id("com.android.library") }
android {
    namespace = "net.frognet.rangertrak.shared"
    compileSdk = 34
    defaultConfig { minSdk = 29 }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    sourceSets["main"].java.srcDirs("src/main/java", "src/core", "src/ram", "src/fdf/comms")
}
dependencies { implementation("com.journeyapps:zxing-android-embedded:4.3.0"); implementation("androidx.core:core:1.12.0") }   // zxing 4.x calls androidx ContextCompat but its POM does not say so
KTS
cat > $P/base/build.gradle.kts <<'KTS'
// [BASE_APP_V1] Disaster Area, the device's hidden base: the FrogNet membership, one per device, which every level app
// (RangerTrak Team, Public, ...) asks to join, change or leave (src/main/kotlin: JoinActivity, MemberService, BridgeVpn,
// Identity ...; src/member: the protocol; src/main/jniLibs: fdf-tun; src/main/java: its Communicator). Install it with
// the apps; it has no icon. (Exported from the repository by android/studio_project.sh, from frognet-android.)
plugins { id("com.android.application"); kotlin("android") }
android {
    namespace = "com.fawcettinnovations.fdf.android"
    compileSdk = 34
    defaultConfig { applicationId = "com.fawcettinnovations.fdf.android"; minSdk = 29; targetSdk = 34; versionCode = 20; versionName = "fdf-base-2026-11-03-r1" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildTypes { release { isMinifyEnabled = false } }
    packaging { jniLibs { useLegacyPackaging = true } }
    sourceSets["main"].kotlin.srcDirs("src/main/kotlin", "src/member")
}
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531") }
KTS
# [ADMIN_PHONE_APP_V1] the admin app's page as the admin module's own assets (in the repository: ../../ui-admin, copied at build)
mkdir -p $P/admin/src/main/assets; cp -r $APP/ui-admin $P/admin/src/main/assets/ui-admin
cat > $P/admin/build.gradle.kts <<'KTS'
// [ADMIN_PHONE_APP_V1] RangerTrak Admin on the phone: the admin app's page (src/main/assets/ui-admin) and its core
// (AdminCore, in the shared module's src/core) in a WebView -- the same page and core as the Windows admin app.
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.admin.android"
    compileSdk = 34
    defaultConfig { applicationId = "net.frognet.rangertrak.admin"; minSdk = 29; targetSdk = 34; versionCode = 8; versionName = "rt-admin-2026-11-04-r8" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }
KTS
printf '/build\n*/build\n.gradle\n.idea\nlocal.properties\n*.iml\n' > $P/.gitignore
cat > $P/README.txt <<TXT
RangerTrak for Android ($BUILD) -- an Android Studio project
Open this folder in Android Studio (File > Open). Gradle syncs (it fetches Gradle 8.9 and the Android plugin 8.5.2;
JDK 17 or newer, Android SDK 34). Run configurations: team (RangerTrak Team), public (RangerTrak Public), the unit apps
ambulance, hospital, water, power, shelter, phone (RangerTrak <Unit>), admin (RangerTrak Admin), app (every view),
base (Disaster Area: the FrogNet membership every RangerTrak app uses; install it too -- it has no icon).
Command line: ./gradlew :base:assembleDebug :team:assembleDebug :public:assembleDebug :ambulance:assembleDebug ... :admin:assembleDebug
  -> base/build/outputs/apk/debug/base-debug.apk, team/build/outputs/apk/debug/team-debug.apk, public/...
Modules: shared (the Android shell, the core in shared/src/core, the RAM library in shared/src/ram, the page in
shared/src/main/assets/ui), and one module per app (its manifest: id, name, view).
On the phone: join the node's Wi-Fi; Node is filled from the gateway. The build is on the first screen; page errors
show on the status line and in Logcat (tag RangerTrak).
TXT
(cd $T && zip -qr "$OUT/$N.zip" $N); ls -la "$OUT/$N.zip"; rm -rf $T
