#!/usr/bin/env bash
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
: "${JAVA_HOME:?Set JAVA_HOME to JDK8}"
cpp="$here/../../cpp";stock="$here/../mqtt/stock";out="$here/build"
mkdir -p "$out/classes"
flags=(-std=c++20 -O2 -fPIC -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I"$cpp/include" -I"$JAVA_HOME/include" -I"$JAVA_HOME/include/linux")
objects=()
for name in frogram ram_host ribbit_space lf_ebr ribbit_pp; do
 g++ "${flags[@]}" -c "$cpp/src/$name.cpp" -o "$out/$name.o"
 objects+=("$out/$name.o")
done
g++ "${flags[@]}" -shared "$here/native/ribbit_jni.cpp" "${objects[@]}" -llz4 -lcrypto -o "$out/libribbit_senz.so"
g++ "${flags[@]}" "$here/native/test_host.cpp" "${objects[@]}" -llz4 -lcrypto -o "$out/test-host"
g++ "${flags[@]}" "$here/native/region_host.cpp" "${objects[@]}" -llz4 -lcrypto -o "$out/region-host"
# Split statements to keep compiler diagnostics useful without suppressing warnings.
g++ "${flags[@]}" "$here/native/probe.cpp" "${objects[@]}" -llz4 -lcrypto -o "$out/probe"
g++ "${flags[@]}" -I"$stock/include" "$here/native/device.cpp" "${objects[@]}" "$stock/lib/libmosquitto.so.1" -llz4 -lcrypto -o "$out/test-device"
"$JAVA_HOME/bin/javac" -source 8 -target 8 -d "$out/classes" "$here/java/com/fawcett/ribbit/Ribbit.java" "$here/tests/BindingTest.java"
