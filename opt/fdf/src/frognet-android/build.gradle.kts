// [ONE_PLUGIN_CLASSLOADER_V1] The Kotlin plugin and the Android Gradle plugin are on the ROOT buildscript classpath,
// so every module sees both from one classloader. (Applied per module, KGP was loaded where AGP was not, and Android
// Studio's sync failed: "Unable to load class 'com.android.build.gradle.api.BaseVariant'".) Modules apply them without
// versions. -Pfdf.jvmOnly=true leaves AGP and Google's repository out: the JVM runner builds without them.
buildscript {
    val jvmOnlyHere = providers.gradleProperty("fdf.jvmOnly").orNull == "true"
    repositories {
        mavenCentral(); gradlePluginPortal()
        if (!jvmOnlyHere) google()
    }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21")
        if (!jvmOnlyHere) classpath("com.android.tools.build:gradle:8.5.2")
    }
}
