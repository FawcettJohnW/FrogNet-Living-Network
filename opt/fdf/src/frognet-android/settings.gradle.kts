// FrogNet Android member. :member is the FD&F member protocol in plain Kotlin (no Android types): the same code runs
// in the app and on a JVM, where it is proven against a real rina-host. :member-cli is that JVM runner.
// :app (the Android app) is always included; -Pfdf.jvmOnly=true builds only the JVM modules (no Android SDK needed).
pluginManagement {
    repositories { mavenCentral(); gradlePluginPortal(); google() }
}
dependencyResolutionManagement {
    repositories { mavenCentral(); google() }
}
rootProject.name = "frognet-android"
include(":member", ":member-cli")
if (providers.gradleProperty("fdf.jvmOnly").orNull != "true") include(":app")
