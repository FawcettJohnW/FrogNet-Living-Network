// [RT_APP_ANDROID_V1] OpenEOC, every view (EOC, team, hospital, ambulance, public): the shared module (../shared: the
// Android shell, the same core and page as the desktop, the RAM library; one copy of each, no forks) with its manifest.
plugins { id("com.android.application") }
// [RT_LAYERS_V1] this app is the base (shared) and these layers: their pages (ui/layers/<name>.js, copied into this
// app's own assets: no other app carries them) and their cores (the same names); the manifest says them (LAYERS)
val rtLayers = listOf("help", "field", "eoc", "team", "medical", "units", "public", "ics", "fema", "paper")
val genLayers = layout.buildDirectory.dir("generated/openeoc-layers")   // openeoc-layers-copy
val copyLayers = tasks.register<Copy>("copyLayers") { from(file("../../ui/layers")) { include(rtLayers.map { "$it.js" }) }; into(genLayers.map { it.dir("ui/layers") }) }   // openeoc-layers-copy
android {
    namespace = "net.frognet.openeoc.android"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.openeoc"
        minSdk = 29
        targetSdk = 34
        versionCode = 49
        versionName = "openeoc-app-2026-11-10-r74"
        manifestPlaceholders["rtLayers"] = rtLayers.joinToString(",")
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }   // fdf-tun and [TEAM_HOST_V1] libteamram.so are RUN from nativeLibraryDir: extracted at install
    sourceSets["main"].assets.srcDir(genLayers)   // openeoc-layers-copy
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // [RT_ANDROID_SHARED_V1] the shell, the core, the RAM library, the page
tasks.named("preBuild") { dependsOn(copyLayers) }   // openeoc-layers-copy
tasks.matching { it.name.endsWith("Assets") && it.name != "copyLayers" }.configureEach { dependsOn(copyLayers) }   // openeoc-layers-copy
