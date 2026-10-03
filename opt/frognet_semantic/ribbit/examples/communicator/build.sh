#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh [RIBBIT_ROOT] -- build the C++ Communicator. Self-contained: the Ribbit platform it needs is vendored in
# third_party/ribbit. Pass a FrogNet tree's ribbit directory (opt/frognet_semantic/ribbit) to build against that instead.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="${1:-$(pwd)/third_party/ribbit}"
[ -f "$ROOT/cpp/src/ram_host.cpp" ] || { echo "no Ribbit platform at $ROOT (pass the ribbit directory as the argument)"; exit 1; }
# pkg-config module -> the Debian/Ubuntu package that provides it
declare -A PKG=( [libavcodec]=libavcodec-dev [libavformat]=libavformat-dev [libavdevice]=libavdevice-dev
                 [libavutil]=libavutil-dev [libswscale]=libswscale-dev [vpx]=libvpx-dev [opus]=libopus-dev
                 [portaudio-2.0]=portaudio19-dev [Qt6Widgets]=qt6-base-dev [liblz4]=liblz4-dev [libcrypto]=libssl-dev
                 [gl]=libgl-dev )   # Qt's GUI needs the OpenGL headers; Ubuntu 22.04's qt6-base-dev does not pull them in
need=()
for t in cmake g++ pkg-config; do command -v "$t" >/dev/null || need+=("$t"); done
for m in "${!PKG[@]}"; do
    # Qt 6 on Ubuntu 22.04 ships no pkg-config file: look for its CMake package instead
    if [ "$m" = Qt6Widgets ] && ls /usr/lib/*/cmake/Qt6Widgets/Qt6WidgetsConfig.cmake /usr/lib/cmake/Qt6Widgets/Qt6WidgetsConfig.cmake >/dev/null 2>&1; then continue; fi
    command -v pkg-config >/dev/null && pkg-config --exists "$m" || need+=("${PKG[$m]}")
done
if [ ${#need[@]} -gt 0 ]; then
    echo "installing: ${need[*]}"
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    $SUDO apt-get update -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends "${need[@]}"
fi
# [BUILD_BY_CONTENT_V1] tar restores old modification times, so make can take a new source for older than the object a
# previous build left, and skip it. Every source's content hash is kept; a file whose content changed since the last
# build is named, its time refreshed, and the build runs -- make then recompiles exactly what changed.
SUMS=build/.source-hashes
if [ -f "$SUMS" ]; then
    changed=$( (find src tests tools third_party CMakeLists.txt -type f -print0 | sort -z | xargs -0 sha256sum) | { diff - "$SUMS" || true; } | awk '/^</ {print $3}')
    if [ -n "$changed" ]; then
        echo "sources changed since the last build -- recompiling them:"
        echo "$changed" | sed 's/^/  /'
        echo "$changed" | xargs touch
    fi
elif [ -d build ]; then
    echo "no record of the sources the existing build was made from -- recompiling everything"
    find src tests tools third_party CMakeLists.txt -type f -exec touch {} +
fi
cmake -B build -DCMAKE_BUILD_TYPE=Release -DRIBBIT_ROOT="$ROOT"
cmake --build build -j"$(nproc)"
(find src tests tools third_party CMakeLists.txt -type f -print0 | sort -z | xargs -0 sha256sum) > "$SUMS"
echo "built: build/comms-app build/comms-feed build/comms-media build/comms-ram"
