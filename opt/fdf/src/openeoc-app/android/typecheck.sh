#!/bin/bash
# typecheck.sh -- the OpenEOC Android apps' sources as Android Studio compiles them (:shared: this app's Java, the
# core, the RAM library, and frognet-android's member, app Kotlin and Communicator Java: [APP_IS_SELF_CONTAINED_V1] each
# app is its own FrogNet member) compiled against the Android 14
# framework (robolectric's android-all, Maven Central) and wireguard-android's tunnel classes, on a machine with no
# Android SDK. It proves the sources type-check; Android Studio builds the APK. usage: typecheck.sh [WORKDIR]
set -eu
H=$(cd "$(dirname "$0")" && pwd); APP=$(cd "$H/.." && pwd); SRC=$(cd "$APP/.." && pwd); FN=$SRC/frognet-android
W=${1:-$(mktemp -d)}; mkdir -p "$W/libs"; WG=1.0.20250531
for t in 1 2 3 4 5; do [ -s "$W/libs/wg-tunnel.jar" ] && break
  curl -fsS -o "$W/tunnel.aar" "https://repo1.maven.org/maven2/com/wireguard/android/tunnel/$WG/tunnel-$WG.aar" \
    && (cd "$W" && unzip -o -q tunnel.aar classes.jar && mv classes.jar libs/wg-tunnel.jar) || sleep $((t * 10)); done
ZX=4.3.0   # [TEAM_HOST_V1] zxing-android-embedded (an aar: its classes) and zxing core, from Maven Central
for t in 1 2 3 4 5; do [ -s "$W/libs/zxing-embedded.jar" ] && break
  curl -fsS -o "$W/zx.aar" "https://repo1.maven.org/maven2/com/journeyapps/zxing-android-embedded/$ZX/zxing-android-embedded-$ZX.aar" \
    && (cd "$W" && unzip -o -q zx.aar classes.jar && mv classes.jar libs/zxing-embedded.jar) || sleep $((t * 10)); done
for t in 1 2 3 4 5; do [ -s "$W/libs/zxing-core.jar" ] && break
  curl -fsS -o "$W/libs/zxing-core.jar" "https://repo1.maven.org/maven2/com/google/zxing/core/3.4.1/core-3.4.1.jar" || { rm -f "$W/libs/zxing-core.jar"; sleep $((t * 10)); }; done
rm -rf "$W/build/classes"                       # a pass is this run's classes, never an earlier run's
echo 'rootProject.name = "openeoc-typecheck"' > "$W/settings.gradle.kts"
cat > "$W/build.gradle.kts" <<EOG
buildscript { repositories { mavenCentral(); gradlePluginPortal() }; dependencies { classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.0.21") } }
apply(plugin = "org.jetbrains.kotlin.jvm")
repositories { mavenCentral() }
configure<SourceSetContainer> { named("main") {
    java.srcDirs("$H/shared/src/main/java", "$APP/core/src", "$SRC/frognet-ram-java/src", "$FN/app/src/main/java")
    (this as ExtensionAware).extensions.getByName<org.gradle.api.file.SourceDirectorySet>("kotlin").srcDirs("$FN/member/src/main/kotlin", "$FN/app/src/main/kotlin") } }
configure<JavaPluginExtension> { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach { compilerOptions {
    jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    freeCompilerArgs.addAll("-Xnullability-annotations=@android.annotation:ignore", "-Xnullability-annotations=@libcore.util:ignore", "-Xnullability-annotations=@androidx.annotation:ignore") } }
dependencies { "compileOnly"("org.robolectric:android-all:14-robolectric-10818077"); "compileOnly"(files("libs/wg-tunnel.jar", "libs/zxing-embedded.jar", "libs/zxing-core.jar")) }
EOG
# a pass is gradle's own success, never class files that happen to be there ([TYPECHECK_IS_THE_COMPILERS_V1]: the r19
# AppCore static-context error passed with 124 classes from gradle's cache)
set +e; (cd "$W" && gradle -q --rerun-tasks compileKotlin compileJava 2>&1; echo "GRADLE_RC=$?") > "$W/gradle.out"; set -e
grep -v '^w:\|JAVA_TOOL\|^Note:\|^GRADLE_RC=' "$W/gradle.out" || true
grep -q '^GRADLE_RC=0$' "$W/gradle.out" || { echo "typecheck: FAIL (the compiler said so, above)"; exit 1; }
N=$(find "$W/build/classes" -name '*.class' -newer "$W/build.gradle.kts" 2>/dev/null | wc -l)
[ -f "$W/build/classes/kotlin/main/com/fawcettinnovations/fdf/android/MemberService.class" ] \
  && [ -f "$W/build/classes/java/main/net/frognet/openeoc/android/Member.class" ] \
  && [ -f "$W/build/classes/java/main/net/frognet/openeoc/android/MainActivity.class" ] \
  && [ ! -e "$W/build/classes/java/main/net/frognet/openeoc/android/Base.class" ] \
  && echo "typecheck: PASS ($N classes against android-all 14 + wireguard tunnel $WG)" || { echo "typecheck: FAIL (above)"; exit 1; }
