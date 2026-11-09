// [RT_EOC_APP_V1] OpenEOC EOC: the shared module (../shared: the Android shell, the core, the RAM library, the page)
// with its own manifest; its manifest says ROLE = eoc, so it opens as the EOC only (its user and password).
//   ./gradlew :eoc:assembleDebug
plugins { id("com.android.application") }
// [RT_LAYERS_V1] this app is the base (shared) and these layers: their pages (ui/layers/<name>.js, copied into this
// app's own assets: no other app carries them) and their cores (the same names); the manifest says them (LAYERS)
val rtLayers = listOf("help", "field", "eoc", "medical", "ics", "fema")
val genLayers = layout.buildDirectory.dir("generated/openeoc-layers")   // openeoc-layers-copy
val copyLayers = tasks.register<Copy>("copyLayers") { from(file("../../ui/layers")) { include(rtLayers.map { "$it.js" }) }; into(genLayers.map { it.dir("ui/layers") }) }   // openeoc-layers-copy
android {
    namespace = "net.frognet.openeoc.eoc"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.openeoc.eoc"
        minSdk = 29
        targetSdk = 34
        versionCode = 35
        versionName = "openeoc-app-2026-11-09-r60"
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
