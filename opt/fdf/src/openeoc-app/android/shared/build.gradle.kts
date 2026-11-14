// [RT_ANDROID_SHARED_V1] what the three OpenEOC apps (app, team, public) share, once: the Android shell
// (src/main/java: MainActivity -- the WebView, the GPS, the no-GPS address), the app core (../../core/src) and the RAM
// library (../../../frognet-ram-java/src), compiled in from their own directories, and the page (../../ui), copied into
// the assets at build time. Each app is its manifest: its id, its name, its view.
// [APP_IS_SELF_CONTAINED_V1] (John 2026-11-04: "All apps are fully self contained entities": nothing else is installed)
// the FrogNet member is compiled in, so opening the app joins FrogNet and brings up the tunnel the RAMs (10.x) are reached
// through -- frognet-android's member/ (the protocol), app/src/main/kotlin (MemberService, BridgeVpn, Identity, ...), its
// jniLibs (fdf-tun) and the WireGuard crypto it uses; and its Communicator (app/src/main/java, net.frognet.communicator),
// which Call opens in this app. One copy of each: compiled from frognet-android, not copied. (It replaces [BASE_APP_V1].)
plugins { id("com.android.library"); kotlin("android") }
val fn = file("../../../frognet-android")
if (!File(fn, "member/src/main/kotlin/com/fawcettinnovations/fdf/member/Member.kt").isFile)
    throw GradleException("no FrogNet member source at $fn (frognet-android, beside openeoc-app in opt/fdf/src)")
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/openeoc-assets")
// [RT_LAYERS_V1] the base page only: each app copies its own layers (ui/layers) into its own assets
val copyUi = tasks.register<Copy>("copyUi") { from(ui) { exclude("layers/**") }; into(genAssets.map { it.dir("ui") }) }
android {
    namespace = "net.frognet.openeoc.shared"
    compileSdk = 34
    defaultConfig { minSdk = 29 }                    // the member's and the Communicator's (as frognet-android)
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    sourceSets["main"].java.srcDirs("src/main/java", "../../core/src", "../../../frognet-ram-java/src", File(fn, "app/src/main/java").path)
    sourceSets["main"].kotlin.srcDirs(File(fn, "member/src/main/kotlin").path, File(fn, "app/src/main/kotlin").path)
    // fdf-tun (frognet-android's) and [TEAM_HOST_V1] libteamram.so, the team's RAM on the lead's phone (teamram/build.sh)
    sourceSets["main"].jniLibs.srcDirs(File(fn, "app/src/main/jniLibs").path, "src/main/jniLibs")
    sourceSets["main"].assets.srcDir(genAssets)
}
// [TEAM_HOST_V1] zxing-android-embedded: Scan team reads the lead's hotspot QR code (Maven Central)
dependencies { implementation("com.wireguard.android:tunnel:1.0.20250531"); implementation("com.journeyapps:zxing-android-embedded:4.3.0"); implementation("androidx.core:core:1.12.0") }   // zxing 4.x calls androidx ContextCompat but its POM does not say so
tasks.named("preBuild") { dependsOn(copyUi) }
// the tasks that read the assets say they need the copy (Gradle 8 refuses a task that reads another's output undeclared)
tasks.matching { it.name.endsWith("Assets") && it.name != "copyUi" }.configureEach { dependsOn(copyUi) }
