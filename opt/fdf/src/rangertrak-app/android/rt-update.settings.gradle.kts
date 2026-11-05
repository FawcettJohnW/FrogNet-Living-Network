// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// rt-update.settings.gradle.kts -- [RT_ANDROID_UPDATE_APPLIES_ITSELF_V1] (John 2026-11-05: "we are not going to do this
// for every change"). Twice an update zip was unzipped and the phone still ran the old build: where the files landed
// (Windows' Extract All puts them in a new folder named after the zip) is not where Android Studio builds from, and
// nothing said so. Now every build of this project (Run, Make, Sync: settings are read first, every time):
//   1 says which build it is building and from where: "RangerTrak: building rt-app-... in <this folder>"
//   2 looks for update zips as delivered (RangerTrak-Android-Studio-update-rt-app-*.zip, "(1)" copies too) in this
//     folder, the folder above it and the user's Downloads; reads each one's build from its own AppCore.java; and
//     applies the newest one newer than this project, here, before anything is compiled -- its files over this tree,
//     its REMOVED.txt deleted -- and says so. A zip made over a newer project than this one (BASE.txt) stops the build
//     and says why. Nothing to unzip by hand: download it, press Run.
// The repository's own Android build (no shared/src/core here) builds from the repository: nothing is applied there.
import java.io.File
import java.util.zip.ZipFile

val rtRoot: File = settingsDir
val rtCore = File(rtRoot, "shared/src/core/net/frognet/rangertrak/AppCore.java")
val rtBuildRe = Regex("""rt-app-(\d{4}-\d{2}-\d{2})-r(\d+)""")
fun rtKey(b: String): Pair<String, Int>? = rtBuildRe.find(b)?.let { Pair(it.groupValues[1], it.groupValues[2].toInt()) }
fun rtNewer(a: String, b: String): Boolean {          // a newer than b
    val x = rtKey(a) ?: return false; val y = rtKey(b) ?: return true
    return if (x.first != y.first) x.first > y.first else x.second > y.second
}
fun rtBuildOf(text: String): String = Regex("""BUILD = "(rt-app-[^"]+)"""").find(text)?.groupValues?.get(1) ?: ""

if (rtCore.isFile) {
    val here = rtBuildOf(rtCore.readText())
    val dirs = listOfNotNull(rtRoot, rtRoot.parentFile, File(System.getProperty("user.home"), "Downloads")).filter { it.isDirectory }.distinct()
    val zips = dirs.flatMap { d -> (d.listFiles() ?: arrayOf()).filter { it.isFile && it.name.startsWith("RangerTrak-Android-Studio-update-rt-app-") && it.name.endsWith(".zip") } }
    var best: File? = null; var bestBuild = ""; var bestBase = ""
    for (z in zips) {
        try {
            ZipFile(z).use { zf ->
                val core = zf.getEntry("shared/src/core/net/frognet/rangertrak/AppCore.java")
                val b = if (core != null) rtBuildOf(zf.getInputStream(core).reader().readText()) else (rtBuildRe.find(z.name)?.value ?: "")
                val base = zf.getEntry("BASE.txt")?.let { zf.getInputStream(it).reader().readText().trim() } ?: ""
                if (b.isNotEmpty() && rtNewer(b, here) && (best == null || rtNewer(b, bestBuild))) { best = z; bestBuild = b; bestBase = base }
            }
        } catch (e: Exception) { println("RangerTrak: ${z.path} is not a readable update zip (${e.message}): not applied") }
    }
    val z = best
    if (z != null) {
        if (bestBase.isNotEmpty() && rtNewer(bestBase, here))
            throw GradleException("RangerTrak: ${z.name} ($bestBuild) is made over the project at $bestBase; this project ($rtRoot) is $here, older: open the full project $bestBase (or later), then build")
        var n = 0
        ZipFile(z).use { zf ->
            for (e in zf.entries()) {
                if (e.isDirectory || e.name == "README.txt" || e.name == "BASE.txt" || e.name == "REMOVED.txt") continue
                val out = File(rtRoot, e.name).canonicalFile
                if (!out.path.startsWith(rtRoot.canonicalPath + File.separator)) continue      // nothing outside this project
                out.parentFile.mkdirs(); zf.getInputStream(e).use { i -> out.outputStream().use { o -> i.copyTo(o) } }; n++
            }
            zf.getEntry("REMOVED.txt")?.let { r ->
                for (p in zf.getInputStream(r).reader().readLines().map { it.trim() }.filter { it.isNotEmpty() }) {
                    val f = File(rtRoot, p).canonicalFile
                    if (f.path.startsWith(rtRoot.canonicalPath + File.separator) && f.delete()) n++
                }
            }
        }
        println("RangerTrak: applied ${z.path} ($bestBuild over $here): $n file(s) in $rtRoot")
    }
    println("RangerTrak: building ${rtBuildOf(rtCore.readText()).ifEmpty { "(no build in AppCore.java)" }} in $rtRoot")
}
