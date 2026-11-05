// [RT_EOC_APP_V1] RangerTrak EOC: the shared module (../shared: the Android shell, the core, the RAM library, the page)
// with its own manifest; its manifest says ROLE = eoc, so it opens as the EOC only (its user and password).
//   ./gradlew :eoc:assembleDebug
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.eoc"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.eoc"
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
