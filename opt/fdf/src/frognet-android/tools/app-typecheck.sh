#!/bin/bash
# app-typecheck.sh -- the Android app's sources (app/ Kotlin + Java, and :member) compiled against the Android 14
# framework (robolectric's android-all, Maven Central) and wireguard-android's tunnel classes, on a machine with no
# Android SDK and no Google repository. It proves the app type-checks; it builds no APK (tools/make_apk.sh does, with
# the SDK). Needs gradle, JDK 17+, curl, unzip. usage: tools/app-typecheck.sh [WORKDIR]
set -eu
A=$(cd "$(dirname "$0")/.." && pwd); W=${1:-$(mktemp -d)}; mkdir -p "$W/libs"
WG=1.0.20250531
[ -s "$W/libs/wg-tunnel.jar" ] || { curl -fsS -o "$W/tunnel.aar" "https://repo1.maven.org/maven2/com/wireguard/android/tunnel/$WG/tunnel-$WG.aar"
                                   (cd "$W" && unzip -o -q tunnel.aar classes.jar && mv classes.jar libs/wg-tunnel.jar); }
echo 'rootProject.name = "fdf-app-typecheck"' > "$W/settings.gradle.kts"
cat > "$W/build.gradle.kts" <<EOG
buildscript { repositories { mavenCentral(); gradlePluginPortal() }; dependencies { classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21") } }
apply(plugin = "org.jetbrains.kotlin.jvm")
repositories { mavenCentral() }
configure<SourceSetContainer> { named("main") {
    java.srcDirs("$A/app/src/main/java")
    (this as ExtensionAware).extensions.getByName<org.gradle.api.file.SourceDirectorySet>("kotlin").srcDirs("$A/member/src/main/kotlin", "$A/app/src/main/kotlin") } }
configure<JavaPluginExtension> { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
dependencies { "compileOnly"("org.robolectric:android-all:14-robolectric-10818077"); "compileOnly"(files("libs/wg-tunnel.jar")) }
EOG
(cd "$W" && gradle -q compileKotlin compileJava 2>&1 | grep -v '^w:\|JAVA_TOOL\|^Note:') || true
N=$(find "$W/build/classes" -name '*.class' -newer "$W/build.gradle.kts" 2>/dev/null | wc -l)
[ -f "$W/build/classes/kotlin/main/com/fawcettinnovations/fdf/android/MemberService.class" ] && [ "$N" -gt 0 ] \
  && echo "app-typecheck: PASS ($N classes against android-all 14 + wireguard tunnel $WG)" || { echo "app-typecheck: FAIL (above)"; exit 1; }
