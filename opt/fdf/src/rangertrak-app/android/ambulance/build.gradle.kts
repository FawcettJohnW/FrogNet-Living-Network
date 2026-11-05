// [RT_SUITE_APPS_V1] RangerTrak Ambulance: the shared module (../shared: the Android shell, the core, the RAM library, the page)
// with its own manifest; its manifest says ROLE = ambulance, so it opens as a Ambulance unit only (it does not do the other units' work).
//   ./gradlew :ambulance:assembleDebug
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.ambulance"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.ambulance"
        minSdk = 29
        targetSdk = 34
        versionCode = 27
        versionName = "rt-app-2026-11-04-r27"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }
