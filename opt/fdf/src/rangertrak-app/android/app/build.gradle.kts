// [RT_APP_ANDROID_V1] the same page and core as the desktop: the page is copied into the APK's assets at build time,
// the core and the RAM library are compiled in from their own directories (one copy of each, no forks).
plugins { id("com.android.application") }
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/rt-assets")
val copyUi = tasks.register<Copy>("copyUi") { from(ui); into(genAssets.map { it.dir("ui") }) }
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
    sourceSets["main"].java.srcDirs("src/main/java", "../../core/src", "../../../frognet-ram-java/src")
    sourceSets["main"].assets.srcDir(genAssets)
    buildTypes { release { isMinifyEnabled = false } }
}
tasks.named("preBuild") { dependsOn(copyUi) }
