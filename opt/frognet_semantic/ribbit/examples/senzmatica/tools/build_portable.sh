#!/usr/bin/env bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# Build the node binaries for one machine type into OUT/opt/senz/{bin,lib}, portable to any Linux with
# glibc >= 2.17: compiled against a conda-forge sysroot, libstdc++/libgcc linked statically, every other
# library shipped in opt/senz/lib and found through RUNPATH $ORIGIN/../lib.
#
#   CXX       the cross/native g++ (conda-forge gxx_linux-64 or gxx_linux-aarch64, sysroot 2.17)
#   HOSTP     prefix holding that architecture's lz4, OpenSSL and cJSON headers and .so files
#   MOSQ      a Mosquitto tree built with the same toolchain (lib/libmosquitto.so.1; src/mosquitto optional)
#   JAVA_INC  optional: JDK include dir -> also builds libribbit_senz.so (the JNI binding)
#   OUT       output root
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
: "${CXX:?}" "${HOSTP:?}" "${MOSQ:?}" "${OUT:?}"
cpp="$here/../../cpp"; stock="$here/../mqtt/stock"
obj="$OUT/.obj"; bin="$OUT/opt/senz/bin"; lib="$OUT/opt/senz/lib"
mkdir -p "$obj" "$bin" "$lib"
flags=(-std=c++20 -O2 -fPIC -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I"$cpp/include" -I"$HOSTP/include")
link=(-static-libstdc++ -static-libgcc -L"$HOSTP/lib" -Wl,-rpath-link,"$HOSTP/lib" -Wl,-rpath,'$ORIGIN/../lib' -Wl,--enable-new-dtags)
objects=()
for name in frogram ram_host ribbit_space lf_ebr ribbit_pp; do
  "$CXX" "${flags[@]}" -c "$cpp/src/$name.cpp" -o "$obj/$name.o"; objects+=("$obj/$name.o")
done
"$CXX" "${flags[@]}" "$here/native/region_host.cpp" "${objects[@]}" "${link[@]}" -llz4 -lcrypto -lrt -o "$bin/region-host"
"$CXX" "${flags[@]}" "$here/native/probe.cpp" "${objects[@]}" "${link[@]}" -llz4 -lcrypto -lrt -o "$bin/probe"
"$CXX" "${flags[@]}" -I"$stock/include" "$here/native/device.cpp" "${objects[@]}" "${link[@]}" "$MOSQ/lib/libmosquitto.so.1" -llz4 -lcrypto -lrt -o "$bin/test-device"
if [ -n "${JAVA_INC:-}" ]; then
  "$CXX" "${flags[@]}" -I"$JAVA_INC" -I"$JAVA_INC/linux" -shared "$here/native/ribbit_jni.cpp" "${objects[@]}" "${link[@]}" -llz4 -lcrypto -lrt -o "$lib/libribbit_senz.so"
fi
copy() { local f; f="$(readlink -f "$1")"; cp "$f" "$lib/$(basename "$2")"; }
for so in liblz4.so.1 libcjson.so.1; do copy "$HOSTP/lib/$so" "$so"; done
for so in libcrypto.so libssl.so; do real="$(readlink -f "$HOSTP/lib/$so")"; cp "$real" "$lib/"; done
copy "$MOSQ/lib/libmosquitto.so.1" libmosquitto.so.1
if [ -x "$MOSQ/src/mosquitto" ]; then cp "$MOSQ/src/mosquitto" "$bin/mosquitto"; fi
rm -rf "$obj"
echo "built: $(ls "$bin" | tr '\n' ' ')| libs: $(ls "$lib" | tr '\n' ' ')"
