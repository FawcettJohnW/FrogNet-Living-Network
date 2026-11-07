#!/bin/bash
# build.sh -- [RT_APP_CORE_V1] build/frognet-ram.jar, build/rangertrak-core.jar (Java 8: Android and any JVM) and
# build/rangertrak-desktop.jar (JavaFX 21, from Maven Central into build/fx)
set -eu; D=$(cd "$(dirname "$0")" && pwd); B=$D/build; mkdir -p $B/fx
"$D/../frognet-ram-java/build.sh" >/dev/null; cp "$D/../frognet-ram-java/build/frognet-ram.jar" $B/
rm -rf $B/core $B/desktop; javac --release 8 -Xlint:-options -cp $B/frognet-ram.jar -d $B/core $(find $D/core/src -name '*.java')
jar cf $B/rangertrak-core.jar -C $B/core .
for m in base graphics controls web media; do f=javafx-$m-21.0.5-linux.jar; [ -s $B/fx/$f ] || curl -sf --retry 6 --retry-all-errors --retry-delay 5 -o $B/fx/$f https://repo1.maven.org/maven2/org/openjfx/javafx-$m/21.0.5/$f; done
javac --release 21 -cp "$B/frognet-ram.jar:$B/rangertrak-core.jar:$(ls $B/fx/*.jar | tr '\n' ':')" -d $B/desktop $(find $D/desktop/src -name '*.java')
jar cf $B/rangertrak-desktop.jar -C $B/desktop .
ls -la $B/*.jar
