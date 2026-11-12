import java.io.File
import java.util.zip.ZipFile
// OpenEOC for Android [RT_APP_ANDROID_V1]: the app's page (../ui) and core (../core, on ../../frognet-ram-java) in a
// WebView, with the phone's GPS. Built with Android Studio or ./gradlew :app:assembleDebug (Android SDK + Google's Maven).
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
// [UPDATE_IN_SETTINGS_V1] (found 2026-11-10: "Could not read script ...rt-update.settings.gradle.kts as it does not
// exist" -- the project's settings depended on a second file arriving with it) the update applier is here, in the
// settings themselves: nothing else for a build to need first
// rt-update.settings.gradle.kts -- [RT_ANDROID_UPDATE_APPLIES_ITSELF_V1] (John 2026-11-05: "we are not going to do this
// for every change"). Twice an update zip was unzipped and the phone still ran the old build: where the files landed
// (Windows' Extract All puts them in a new folder named after the zip) is not where Android Studio builds from, and
// nothing said so. Now every build of this project (Run, Make, Sync: settings are read first, every time):
//   1 says which build it is building and from where: "OpenEOC: building openeoc-app-... in <this folder>"
//   2 looks for update zips as delivered (OpenEOC-Android-Studio-update-openeoc-app-*.zip, "(1)" copies too) in this
//     folder, the folder above it and the user's Downloads; reads each one's build from its own AppCore.java; and
//     applies the newest one newer than this project, here, before anything is compiled -- its files over this tree,
//     its REMOVED.txt deleted -- and says so. A zip made over a newer project than this one (BASE.txt) stops the build
//     and says why. Nothing to unzip by hand: download it, press Run.
// The repository's own Android build (no shared/src/core here) builds from the repository: nothing is applied there.

val rtRoot: File = settingsDir
val rtCore = File(rtRoot, "shared/src/core/net/frognet/openeoc/AppCore.java")
val rtBuildRe = Regex("""openeoc-app-(\d{4}-\d{2}-\d{2})-r(\d+)""")
fun rtKey(b: String): Pair<String, Int>? = rtBuildRe.find(b)?.let { Pair(it.groupValues[1], it.groupValues[2].toInt()) }
fun rtNewer(a: String, b: String): Boolean {          // a newer than b
    val x = rtKey(a) ?: return false; val y = rtKey(b) ?: return true
    return if (x.first != y.first) x.first > y.first else x.second > y.second
}
// [RT_ANDROID_STABLE_ROOT_V1] an update's entries are under OpenEOC-Android-Studio/ (r51 on), or bare (r42..r50)
val rtRootDir = "OpenEOC-Android-Studio/"
fun rtRel(name: String): String = if (name.startsWith(rtRootDir)) name.substring(rtRootDir.length) else name
fun rtEntry(zf: ZipFile, rel: String) = zf.getEntry(rtRootDir + rel) ?: zf.getEntry(rel)
fun rtBuildOf(text: String): String = Regex("""BUILD = "(openeoc-app-[^"]+)"""").find(text)?.groupValues?.get(1) ?: ""

if (rtCore.isFile) {
    val here = rtBuildOf(rtCore.readText())
    val dirs = listOfNotNull(rtRoot, rtRoot.parentFile, File(System.getProperty("user.home"), "Downloads")).filter { it.isDirectory }.distinct()
    val zips = dirs.flatMap { d -> (d.listFiles() ?: arrayOf()).filter { it.isFile && it.name.startsWith("OpenEOC-Android-Studio-update-openeoc-app-") && it.name.endsWith(".zip") } }
    var best: File? = null; var bestBuild = ""; var bestBase = ""
    for (z in zips) {
        try {
            ZipFile(z).use { zf ->
                val core = rtEntry(zf, "shared/src/core/net/frognet/openeoc/AppCore.java")
                val b = if (core != null) rtBuildOf(zf.getInputStream(core).reader().readText()) else (rtBuildRe.find(z.name)?.value ?: "")
                val base = rtEntry(zf, "BASE.txt")?.let { zf.getInputStream(it).reader().readText().trim() } ?: ""
                if (b.isNotEmpty() && rtNewer(b, here) && (best == null || rtNewer(b, bestBuild))) { best = z; bestBuild = b; bestBase = base }
            }
        } catch (e: Exception) { println("OpenEOC: ${z.path} is not a readable update zip (${e.message}): not applied") }
    }
    val z = best
    if (z != null) {
        if (bestBase.isNotEmpty() && rtNewer(bestBase, here))
            throw GradleException("OpenEOC: ${z.name} ($bestBuild) is made over the project at $bestBase; this project ($rtRoot) is $here, older: open the full project $bestBase (or later), then build")
        var n = 0
        ZipFile(z).use { zf ->
            for (e in zf.entries()) {
                val rel = rtRel(e.name)
                if (e.isDirectory || rel.isEmpty() || rel == "README.txt" || rel == "BASE.txt" || rel == "REMOVED.txt") continue
                val out = File(rtRoot, rel).canonicalFile
                if (!out.path.startsWith(rtRoot.canonicalPath + File.separator)) continue      // nothing outside this project
                out.parentFile.mkdirs(); zf.getInputStream(e).use { i -> out.outputStream().use { o -> i.copyTo(o) } }; n++
            }
            rtEntry(zf, "REMOVED.txt")?.let { r ->
                for (p in zf.getInputStream(r).reader().readLines().map { it.trim() }.filter { it.isNotEmpty() }) {
                    val f = File(rtRoot, p).canonicalFile
                    if (f.path.startsWith(rtRoot.canonicalPath + File.separator) && f.delete()) n++
                }
            }
        }
        println("OpenEOC: applied ${z.path} ($bestBuild over $here): $n file(s) in $rtRoot")
    }
    println("OpenEOC: building ${rtBuildOf(rtCore.readText()).ifEmpty { "(no build in AppCore.java)" }} in $rtRoot")
}

dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "openeoc-android"
include(":shared")     // [RT_ANDROID_SHARED_V1] the code the three apps share
include(":app")
include(":team")       // [RT_TEAM_APP_V1] OpenEOC Team
include(":public")     // [RT_PUBLIC_APP_V1] OpenEOC Public
// [RT_SUITE_APPS_V1] the suite's unit apps, one per unit; [ADMIN_PHONE_APP_V1] the admin app on the phone
include(":eoc", ":ambulance", ":hospital", ":water", ":power", ":shelter", ":telecom")   // [RT_EOC_APP_V1] eoc: the EOC app
include(":admin")      // [ADMIN_PHONE_APP_V1] OpenEOC Admin on the phone
include(":eocadmin")   // [FEMA_EOC_V1] OpenEOC EOCAdmin: the EOC's paperwork
