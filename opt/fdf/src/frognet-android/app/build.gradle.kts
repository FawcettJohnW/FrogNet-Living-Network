// The Android app: an FD&F leaf. The member protocol is :member (pure Kotlin, proven on a JVM against a real rina-host);
// the tunnels are fdf-tun (tun/, wireguard-go + the 1:1 rewrite) on one VpnService TUN; wireguard-android is kept for its keys.
plugins {
    id("com.android.application")
    kotlin("android")
}
android {
    namespace = "com.fawcettinnovations.fdf.android"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.fawcettinnovations.fdf.android"
        minSdk = 29                       // Android 10: the Communicator's camera/codec paths (it was minSdk 29 on its own)
        targetSdk = 34
        versionCode = 16
        versionName = "fdf-kt-2026-10-23-a16"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildTypes { release { isMinifyEnabled = false } }
    // [BRIDGE_V1] fdf-tun ships as jniLibs/<abi>/libfdftun.so (a static linux executable, built by tun/build.sh) and is
    // RUN from nativeLibraryDir: extracted at install, which is what legacy packaging does
    packaging { jniLibs { useLegacyPackaging = true } }
}
dependencies {
    implementation(project(":member"))
    implementation("com.wireguard.android:tunnel:1.0.20250531")
}
