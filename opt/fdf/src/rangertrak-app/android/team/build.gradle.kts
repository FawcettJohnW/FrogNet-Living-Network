// [RT_TEAM_APP_V1] RangerTrak Team: the Team app on its own -- the shared module (../shared: the Android shell, the
// core, the RAM library, the page) with its own manifest; its manifest
// says ROLE = team, so it opens as a team member: the map centred on you (the phone's GPS, or the street address on a
// device without one), your team around you, chat and Call with anyone, the mission.
//   ./gradlew :team:assembleDebug     (or, with no Gradle: ./build_apk.sh OUTDIR team)
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.team"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.team"
        minSdk = 29
        targetSdk = 34
        versionCode = 17
        versionName = "rt-app-2026-11-02-r17"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }   // [RT_IS_A_MEMBER_V1] fdf-tun is RUN from nativeLibraryDir: extracted at install
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // [RT_ANDROID_SHARED_V1] the shell, the core, the RAM library, the page
