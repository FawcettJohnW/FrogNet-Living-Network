// [RT_APP_ANDROID_V1] RangerTrak, every view (EOC, team, hospital, ambulance, public): the shared module (../shared: the
// Android shell, the same core and page as the desktop, the RAM library; one copy of each, no forks) with its manifest.
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.android"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak"
        minSdk = 26
        targetSdk = 34
        versionCode = 11
        versionName = "rt-app-2026-11-02-r11"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // [RT_ANDROID_SHARED_V1] the shell, the core, the RAM library, the page
