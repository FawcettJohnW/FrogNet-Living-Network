// FrogNet Kart for Android [KART_ANDROID_V1] [KART_CALLS_IN_THE_GAME_V1] -- the same app build_apk.sh builds:
//   sources     src/ (the game) and frognet-android's net.frognet.communicator (the Communicator, compiled in unchanged)
//   manifest    AndroidManifest.xml (build_apk.sh writes its binary form from the same file)
//   version     versionName from Game.BUILD, versionCode from build_apk.sh's VERSION_CODE
//   signing     tools/frognet-kart.p12 for debug and release, so a build from here installs over build_apk.sh's and back
// AGP and Gradle are frognet-android's (8.5.2, 8.9). Plain Java: no Kotlin, no AndroidX, no resources.
plugins { id("com.android.application") version "8.5.2" }

val comms = file("../../../../frognet-android/app/src/main/java")
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
        targetSdk = 33
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
        getByName("debug") { signingConfig = signingConfigs.getByName("frognet") }
        getByName("release") { isMinifyEnabled = false; signingConfig = signingConfigs.getByName("frognet") }
    }
    sourceSets {
        getByName("main") {
            manifest.srcFile("AndroidManifest.xml")
            java.setSrcDirs(listOf("src", comms.path))
            res.setSrcDirs(emptyList<String>()); assets.setSrcDirs(emptyList<String>())
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    lint { abortOnError = false }
}
