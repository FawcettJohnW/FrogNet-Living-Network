// FrogNet Kart for Android [KART_ANDROID_V1] [KART_CALLS_IN_THE_GAME_V1] -- the same app build_apk.sh builds:
//   sources     src/ (the game) and frognet-android's net.frognet.communicator (the Communicator, compiled in unchanged)
//   manifest    AndroidManifest.xml (build_apk.sh writes its binary form from the same file)
//   version     versionName from Game.BUILD, versionCode from build_apk.sh's VERSION_CODE
//   signing     debug: Studio's debug key; release: tools/frognet-kart.p12, build_apk.sh's key
//   [KART_IS_A_MEMBER_V1] the FrogNet member, compiled in, so this app is self-contained: frognet-android's member/
//               (the protocol) and app/src/main/kotlin (MemberService, BridgeVpn, ...), its jniLibs (fdf-tun), and the
//               WireGuard crypto it uses (com.wireguard.android:tunnel, as frognet-android)
// AGP, Kotlin and Gradle are frognet-android's (8.5.2, 2.0.21, 8.9). No AndroidX, no resources.
plugins { id("com.android.application") version "8.5.2"; id("org.jetbrains.kotlin.android") version "2.0.21" }

val fn = file("../../../../frognet-android")
val comms = File(fn, "app/src/main/java")
if (!File(comms, "net/frognet/communicator/core/Room.java").isFile)
    throw GradleException("no Communicator source at $comms (frognet-android, beside fdf-source in opt/fdf/src)")
val buildName = Regex("BUILD = \"kart-android-([^\"]+)\"").find(file("src/net/frognet/kart/core/Game.java").readText())?.groupValues?.get(1)
    ?: throw GradleException("no BUILD in Game.java")
val code = Regex("VERSION_CODE=(\\d+)").find(file("build_apk.sh").readText())?.groupValues?.get(1)?.toInt()
    ?: throw GradleException("no VERSION_CODE in build_apk.sh")

layout.buildDirectory.set(file("build/gradle"))          // build_apk.sh has build/ itself

android {
    namespace = "net.frognet.kart"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.kart"
        minSdk = 29                                       // the Communicator's codecs (as frognet-android)
        targetSdk = 34                                    // the member's foreground service type (specialUse)
        versionCode = code
        versionName = buildName
    }
    signingConfigs {
        create("frognet") {
            storeFile = file("tools/frognet-kart.p12"); storeType = "pkcs12"
            storePassword = "frognet"; keyAlias = "frognet-kart"; keyPassword = "frognet"
        }
    }
    buildTypes {
        // debug: Android Studio's own debug key, as frognet-android's builds (which install). release: the kart key, the
        // one build_apk.sh signs with. A debug build and a sideloaded APK do not install over each other: uninstall first.
        getByName("release") { isMinifyEnabled = false; signingConfig = signingConfigs.getByName("frognet") }
    }
    sourceSets {
        getByName("main") {
            manifest.srcFile("AndroidManifest.xml")
            java.setSrcDirs(listOf("src", comms.path))
            kotlin.setSrcDirs(listOf(File(fn, "member/src/main/kotlin").path, File(fn, "app/src/main/kotlin").path))
            jniLibs.setSrcDirs(listOf(File(fn, "app/src/main/jniLibs").path))
            res.setSrcDirs(emptyList<String>()); assets.setSrcDirs(emptyList<String>())
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { jniLibs { useLegacyPackaging = true } }   // fdf-tun is RUN from nativeLibraryDir: extracted at install
    lint { abortOnError = false }
}
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531") }
