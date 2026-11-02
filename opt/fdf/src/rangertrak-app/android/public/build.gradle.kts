// [RT_PUBLIC_APP_V1] RangerTrak Public: the Public app on its own -- the same page (../../ui), core (../../core), RAM library
// (../../../frognet-ram-java) and Android shell (../app/src/main/java) as RangerTrak, one copy of each; its manifest
// says ROLE = public: it opens on the public's home (I need help, I'm safe, find someone, report to the EOC, hospitals
// and services, the survival library, FEMA, the map). The phone's position fills its forms; it is not published.
//   ./gradlew :public:assembleDebug     (or, with no Gradle: ./build_apk.sh OUTDIR public)
plugins { id("com.android.application") }
val ui = file("../../ui")
val genAssets = layout.buildDirectory.dir("generated/rt-assets")
val copyUi = tasks.register<Copy>("copyUi") { from(ui); into(genAssets.map { it.dir("ui") }) }
android {
    namespace = "net.frognet.rangertrak.pub"
    compileSdk = 34
    defaultConfig {
        applicationId = "net.frognet.rangertrak.pub"
        minSdk = 26
        targetSdk = 34
        versionCode = 10
        versionName = "rt-app-2026-11-02-r10"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_1_8; targetCompatibility = JavaVersion.VERSION_1_8 }
    sourceSets["main"].java.srcDirs("../app/src/main/java", "../../core/src", "../../../frognet-ram-java/src")
    sourceSets["main"].assets.srcDir(genAssets)
    buildTypes { release { isMinifyEnabled = false } }
}
tasks.named("preBuild") { dependsOn(copyUi) }
