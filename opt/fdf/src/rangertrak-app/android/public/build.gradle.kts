// [RT_PUBLIC_APP_V1] RangerTrak Public: the Public app on its own -- the shared module (../shared: the Android shell, the
// core, the RAM library, the page) with its own manifest; its manifest
// says ROLE = public: it opens on the public's home (I need help, I'm safe, find someone, report to the EOC, hospitals
// and services, the survival library, FEMA, the map). The phone's position fills its forms; it is not published.
//   ./gradlew :public:assembleDebug     (or, with no Gradle: ./build_apk.sh OUTDIR public)
plugins { id("com.android.application") }
android {
    namespace = "net.frognet.rangertrak.pub"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.pub"
        minSdk = 26
        targetSdk = 34
        versionCode = 14
        versionName = "rt-app-2026-11-02-r14"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // [RT_ANDROID_SHARED_V1] the shell, the core, the RAM library, the page
