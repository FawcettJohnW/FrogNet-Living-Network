// [RT_ANDROID_SHARED_V1] what the three RangerTrak apps (app, team, public) share, once: the Android shell
// (src/main/java: MainActivity -- the WebView, the GPS, the no-GPS address, Call into the Communicator), the app core
// (../../core/src) and the RAM library (../../../frognet-ram-java/src), compiled in from their own directories, and
// the page (../../ui), copied into the assets at build time. Each app is its manifest: its id, its name, its view.
plugins { id("com.android.library") }
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/rt-assets")
val copyUi = tasks.register<Copy>("copyUi") { from(ui); into(genAssets.map { it.dir("ui") }) }
android {
    namespace = "net.frognet.rangertrak.shared"
    compileSdk = 34
    defaultConfig { minSdk = 26 }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
    sourceSets["main"].java.srcDirs("src/main/java", "../../core/src", "../../../frognet-ram-java/src")
    sourceSets["main"].assets.srcDir(genAssets)
}
tasks.named("preBuild") { dependsOn(copyUi) }
// the tasks that read the assets say they need the copy (Gradle 8 refuses a task that reads another's output undeclared)
tasks.matching { it.name.endsWith("Assets") && it.name != "copyUi" }.configureEach { dependsOn(copyUi) }
