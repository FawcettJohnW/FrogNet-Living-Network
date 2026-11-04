// RangerTrak for Android [RT_APP_ANDROID_V1]: the app's page (../ui) and core (../core, on ../../frognet-ram-java) in a
// WebView, with the phone's GPS. Built with Android Studio or ./gradlew :app:assembleDebug (Android SDK + Google's Maven).
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { google(); mavenCentral() } }
rootProject.name = "rangertrak-android"
include(":shared")     // [RT_ANDROID_SHARED_V1] the code the three apps share
include(":app")
include(":team")       // [RT_TEAM_APP_V1] RangerTrak Team
include(":public")     // [RT_PUBLIC_APP_V1] RangerTrak Public
include(":base")
// [RT_SUITE_APPS_V1] the suite's unit apps, one per unit; [ADMIN_PHONE_APP_V1] the admin app on the phone
include(":ambulance", ":hospital", ":water", ":power", ":shelter", ":phone")
include(":admin")       // [BASE_APP_V1] Disaster Area, the device's hidden base: the FrogNet membership the apps share
