// [RT_ANDROID_SHARED_V1] what the three RangerTrak apps (app, team, public) share, once: the Android shell
// (src/main/java: MainActivity -- the WebView, the GPS, the no-GPS address), the app core (../../core/src) and the RAM
// library (../../../frognet-ram-java/src), compiled in from their own directories, and the page (../../ui), copied into
// the assets at build time. Each app is its manifest: its id, its name, its view.
// [BASE_APP_V1] the FrogNet membership is NOT compiled in (it was, [RT_IS_A_MEMBER_V1]: Team and Public on one phone were
// two memberships): it is the device's hidden base app's (frognet-android's app, "Disaster Area"), which this app asks to
// join, change or leave (Base.java). frognet-android's Communicator (app/src/main/java, net.frognet.communicator), which
// Call opens in this app, is compiled in from frognet-android, not copied.
plugins { id("com.android.library") }
val fn = file("../../../frognet-android")
if (!File(fn, "app/src/main/java/net/frognet/communicator/CommunicatorActivity.java").isFile)
    throw GradleException("no FrogNet Communicator source at $fn (frognet-android, beside rangertrak-app in opt/fdf/src)")
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/rt-assets")
val copyUi = tasks.register<Copy>("copyUi") { from(ui); into(genAssets.map { it.dir("ui") }) }
android {
    namespace = "net.frognet.rangertrak.shared"
    compileSdk = 34
    defaultConfig { minSdk = 29 }                    // the Communicator's (as frognet-android)
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    sourceSets["main"].java.srcDirs("src/main/java", "../../core/src", "../../../frognet-ram-java/src", File(fn, "app/src/main/java").path)
    // [TEAM_HOST_V1] libteamram.so, the team's RAM on the lead's phone (teamram/build.sh); fdf-tun is the base's
    sourceSets["main"].jniLibs.srcDirs("src/main/jniLibs")
    sourceSets["main"].assets.srcDir(genAssets)
}
// [TEAM_HOST_V1] zxing-android-embedded: Scan team reads the lead's hotspot QR code (Maven Central)
dependencies { implementation("com.journeyapps:zxing-android-embedded:4.3.0"); implementation("androidx.core:core:1.12.0") }   // zxing 4.x calls androidx ContextCompat but its POM does not say so
tasks.named("preBuild") { dependsOn(copyUi) }
// the tasks that read the assets say they need the copy (Gradle 8 refuses a task that reads another's output undeclared)
tasks.matching { it.name.endsWith("Assets") && it.name != "copyUi" }.configureEach { dependsOn(copyUi) }
