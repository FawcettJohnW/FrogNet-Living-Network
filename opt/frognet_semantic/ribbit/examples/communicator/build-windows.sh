#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build-windows.sh -- the native Windows build of the Communicator's participant programs (comms-app, comms-feed).
# Run it in an MSYS2 "UCRT64" (or "MINGW64") shell. It installs the toolchain and libraries with pacman, builds, and
# assembles dist\ : the two .exe files with every DLL and Qt plugin they need, so dist\ runs on a Windows machine
# with no MSYS2 installed. The memory (comms-ram) and the media server (comms-media) run on the Linux side.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${MINGW_PREFIX:-}" ] || [ -z "${MINGW_PACKAGE_PREFIX:-}" ]; then
    echo "run this in an MSYS2 UCRT64 or MINGW64 shell (Start menu: 'MSYS2 UCRT64'), not the plain MSYS shell"
    exit 1
fi

P="$MINGW_PACKAGE_PREFIX"
# A fresh MSYS2 carries the installer's package list, which may not name everything below ("target not found").
# Bring MSYS2 up to date first. If this updates MSYS2's own core, pacman says to close the shell: reopen it and run
# this script again.
pacman -Syu --noconfirm
pacman -S --needed --noconfirm \
    "$P-gcc" "$P-cmake" "$P-ninja" "$P-pkgconf" \
    "$P-qt6-base" "$P-qt6-tools" \
    "$P-ffmpeg" "$P-portaudio" "$P-opus" "$P-libvpx" "$P-lz4" "$P-openssl"

cmake -G Ninja -B build-win -DCMAKE_BUILD_TYPE=Release
cmake --build build-win --target comms-app comms-feed

# dist\: the programs, Qt's DLLs and plugins (windeployqt), then every remaining DLL they load from this MSYS2
# environment (FFmpeg, PortAudio, Opus, libvpx, OpenSSL, the compiler runtime), found by asking ldd -- repeated until
# nothing new appears, because DLLs load DLLs.
rm -rf dist && mkdir -p dist
cp build-win/comms-app.exe build-win/comms-feed.exe dist/
WDQ=$(command -v windeployqt6 || command -v windeployqt-qt6 || command -v windeployqt || true)
[ -n "$WDQ" ] || { echo "windeployqt not found (it comes with $P-qt6-tools)"; exit 1; }
"$WDQ" --release --no-translations --no-system-d3d-compiler --no-opengl-sw dist/comms-app.exe
for pass in 1 2 3 4 5; do
    before=$(ls dist | wc -l)
    find dist -name '*.exe' -o -name '*.dll' | while read -r f; do
        ldd "$f" 2>/dev/null | awk '{print $3}' | grep -i "^$MINGW_PREFIX/" || true
    done | sort -u | while read -r d; do [ -f "dist/$(basename "$d")" ] || cp "$d" dist/; done
    [ "$(ls dist | wc -l)" -eq "$before" ] && break
done
mkdir -p dist/media && cp media/stock-720p.mp4 dist/media/
echo
echo "built: dist\\comms-app.exe dist\\comms-feed.exe ($(ls dist/*.dll | wc -l) DLLs alongside)"
echo "try:   dist/comms-app.exe --list-devices"
