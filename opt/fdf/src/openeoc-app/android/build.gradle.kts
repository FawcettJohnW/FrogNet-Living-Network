// [ONE_PLUGIN_CLASSLOADER_V1] the Android plugin and the Kotlin plugin on the root classpath, as frognet-android and the kart
// app have them (one classloader; modules apply them without versions). Kotlin: the FrogNet member compiled into
// shared ([RT_IS_A_MEMBER_V1]).
buildscript {
    repositories { google(); mavenCentral() }
    dependencies {
        classpath("com.android.tools.build:gradle:8.5.2")
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21")
    }
}
