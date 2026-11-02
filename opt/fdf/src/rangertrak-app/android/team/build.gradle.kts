// [RT_TEAM_APP_V1] RangerTrak Team: the Team app on its own -- the same page (../../ui), core (../../core), RAM library
// (../../../frognet-ram-java) and Android shell (../app/src/main/java) as RangerTrak, one copy of each; its manifest
// says ROLE = team, so it opens as a team member: the map centred on you (the phone's GPS, or the street address on a
// device without one), your team around you, chat and Call with anyone, the mission.
//   ./gradlew :team:assembleDebug     (or, with no Gradle: ./build_apk.sh OUTDIR team)
plugins { id("com.android.application") }
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/rt-assets")
val copyUi = tasks.register<Copy>("copyUi") { from(ui); into(genAssets.map { it.dir("ui") }) }
android {
    namespace = "net.frognet.rangertrak.team"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.team"
        minSdk = 26
        targetSdk = 34
        versionCode = 8
        versionName = "rt-app-2026-11-01-r8"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
    sourceSets["main"].java.srcDirs("../app/src/main/java", "../../core/src", "../../../frognet-ram-java/src")
    sourceSets["main"].assets.srcDir(genAssets)
    buildTypes { release { isMinifyEnabled = false } }
}
tasks.named("preBuild") { dependsOn(copyUi) }
