// [RT_SUITE_APPS_V1] RangerTrak Phone: the shared module (../shared: the Android shell, the core, the RAM library, the page)
// with its own manifest; its manifest says ROLE = phone, so it opens as a Phone unit only (it does not do the other units' work).
//   ./gradlew :phone:assembleDebug
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.phone"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.phone"
        minSdk = 29
        targetSdk = 34
        versionCode = 25
        versionName = "rt-app-2026-11-04-r25"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }
