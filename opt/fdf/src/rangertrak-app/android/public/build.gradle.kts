// [RT_PUBLIC_APP_V1] RangerTrak Public: the Public app on its own -- the shared module (../shared: the Android shell, the
// core, the RAM library, the page) with its own manifest; its manifest
// says ROLE = public: it opens on the public's home (I need help, I'm safe, find someone, report to the EOC, hospitals
// and services, the survival library, FEMA, the map). The phone's position fills its forms; it is not published.
//   ./gradlew :public:assembleDebug     (or, with no Gradle: ./build_apk.sh OUTDIR public)
plugins { id("com.android.application") }
// [RT_LAYERS_V1] this app is the base (shared) and these layers: their pages (ui/layers/<name>.js, copied into this
// app's own assets: no other app carries them) and their cores (the same names); the manifest says them (LAYERS)
val rtLayers = listOf("help", "public")
val genLayers = layout.buildDirectory.dir("generated/rt-layers")   // rt-layers-copy
val copyLayers = tasks.register<Copy>("copyLayers") { from(file("../../ui/layers")) { include(rtLayers.map { "$it.js" }) }; into(genLayers.map { it.dir("ui/layers") }) }   // rt-layers-copy
android {
    namespace = "net.frognet.rangertrak.pub"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.pub"
        minSdk = 29
        targetSdk = 34
        versionCode = 29
        versionName = "rt-app-2026-11-04-r29"
        manifestPlaceholders["rtLayers"] = rtLayers.joinToString(",")
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }   // fdf-tun and [TEAM_HOST_V1] libteamram.so are RUN from nativeLibraryDir: extracted at install
    sourceSets["main"].assets.srcDir(genLayers)   // rt-layers-copy
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // [RT_ANDROID_SHARED_V1] the shell, the core, the RAM library, the page
tasks.named("preBuild") { dependsOn(copyLayers) }   // rt-layers-copy
tasks.matching { it.name.endsWith("Assets") && it.name != "copyLayers" }.configureEach { dependsOn(copyLayers) }   // rt-layers-copy
