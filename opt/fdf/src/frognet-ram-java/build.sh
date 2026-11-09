#!/bin/bash
# build.sh -- frognet-ram.jar (Java 8 class files: Android and any JVM)
set -eu; D=$(cd "$(dirname "$0")" && pwd); rm -rf $D/build; mkdir -p $D/build/c
javac --release 8 -Xlint:-options -d $D/build/c $(find $D/src -name '*.java')
jar cf $D/build/frognet-ram.jar -C $D/build/c .; ls -la $D/build/frognet-ram.jar
