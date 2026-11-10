// [FEMA_EOC_V1] OpenEOC EOCAdmin: the EOC's paperwork (ICS forms filled from the boards, the IAP, the after-action report)
// -- the shared module with its own manifest; ROLE = eocadmin: it opens as EOCAdmin only (an EOC user and password).
//   ./gradlew :eocadmin:assembleDebug
plugins { id("com.android.application") }
// [RT_LAYERS_V1] this app is the base (shared) and these layers: their pages (ui/layers/<name>.js, copied into this
// app's own assets: no other app carries them) and their cores (the same names); the manifest says them (LAYERS)
val rtLayers = listOf("help", "medical", "ics", "fema", "paper")
val genLayers = layout.buildDirectory.dir("generated/openeoc-layers")   // openeoc-layers-copy
val copyLayers = tasks.register<Copy>("copyLayers") { from(file("../../ui/layers")) { include(rtLayers.map { "$it.js" }) }; into(genLayers.map { it.dir("ui/layers") }) }   // openeoc-layers-copy
android {
    namespace = "net.frognet.openeoc.eocadmin"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.openeoc.eocadmin"
        minSdk = 29
        targetSdk = 34
        versionCode = 52
        versionName = "openeoc-app-2026-11-10-r77"
        manifestPlaceholders["rtLayers"] = rtLayers.joinToString(",")
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { jniLibs { useLegacyPackaging = true } }
    sourceSets["main"].assets.srcDir(genLayers)   // openeoc-layers-copy
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }
tasks.named("preBuild") { dependsOn(copyLayers) }   // openeoc-layers-copy
tasks.matching { it.name.endsWith("Assets") && it.name != "copyLayers" }.configureEach { dependsOn(copyLayers) }   // openeoc-layers-copy
