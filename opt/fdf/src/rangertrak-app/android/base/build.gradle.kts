// [BASE_APP_V1] Disaster Area, the device's hidden base (John 2026-11-03: "the base disaster area app is hidden from the
// user" ... "shared between all of the various apps and ram regions a given machine needs"): the FrogNet membership,
// one per device, which every level app (RangerTrak Team, Public, the kart ...) asks to join, change or leave. It is
// frognet-android's app, compiled here from frognet-android (its manifest, its Kotlin, the member protocol, its
// Communicator and fdf-tun), not copied, so the one Android Studio tree builds every APK.
//   ./gradlew :base:assembleDebug
plugins { id("com.android.application"); kotlin("android") }
val fn = file("../../../frognet-android")
if (!File(fn, "app/src/main/kotlin/com/fawcettinnovations/fdf/android/JoinActivity.kt").isFile)
    throw GradleException("no FrogNet base source at $fn (frognet-android, beside rangertrak-app in opt/fdf/src)")
android {
    namespace = "com.fawcettinnovations.fdf.android"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.fawcettinnovations.fdf.android"
        minSdk = 29
        targetSdk = 34
        versionCode = 20
        versionName = "fdf-base-2026-11-03-r1"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildTypes { release { isMinifyEnabled = false } }
    packaging { jniLibs { useLegacyPackaging = true } }   // fdf-tun is RUN from nativeLibraryDir: extracted at install
    sourceSets["main"].manifest.srcFile(File(fn, "app/src/main/AndroidManifest.xml"))
    sourceSets["main"].java.srcDirs(File(fn, "app/src/main/java").path)
    sourceSets["main"].kotlin.srcDirs(File(fn, "member/src/main/kotlin").path, File(fn, "app/src/main/kotlin").path)
    sourceSets["main"].jniLibs.srcDirs(File(fn, "app/src/main/jniLibs").path)
}
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531") }
