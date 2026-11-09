// [ADMIN_PHONE_APP_V1] OpenEOC Admin on the phone: the admin app's own page (../../ui-admin, as assets) and its core
// (AdminCore, in the shared module's core) in a WebView -- the same page and core as the Windows admin app. On the node's
// LAN (the Admin's trust): it reaches the node's door by its address, nothing else.
//   ./gradlew :admin:assembleDebug
plugins { id("com.android.application") }
val uiAdmin = file("../../ui-admin")
val genAssets = layout.buildDirectory.dir("generated/admin-assets")
val copyUiAdmin = tasks.register<Copy>("copyUiAdmin") { from(uiAdmin); into(genAssets.map { it.dir("ui-admin") }) }
android {
    namespace = "net.frognet.openeoc.admin.android"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.openeoc.admin"
        minSdk = 29
        targetSdk = 34
        versionCode = 11
        versionName = "openeoc-admin-2026-11-09-r18"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    sourceSets["main"].assets.srcDir(genAssets)
    buildTypes { release { isMinifyEnabled = false } }
}
dependencies { implementation(project(":shared")) }   // AdminCore and the RAM library's Json
tasks.named("preBuild") { dependsOn(copyUiAdmin) }
tasks.matching { it.name.endsWith("Assets") && it.name != "copyUiAdmin" }.configureEach { dependsOn(copyUiAdmin) }
