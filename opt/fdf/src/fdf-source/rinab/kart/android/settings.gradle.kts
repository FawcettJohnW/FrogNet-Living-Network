// FrogNet Kart for Android, for Android Studio [KART_ANDROID_V1]: open this directory (kart/android) as the project.
// One module, the app, at the root. The Communicator's code is not copied here: build.gradle.kts compiles it from
// frognet-android (../../../../frognet-android/app/src/main/java), as build_apk.sh does.
pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositories { google(); mavenCentral() }
}
rootProject.name = "frognet-kart"
