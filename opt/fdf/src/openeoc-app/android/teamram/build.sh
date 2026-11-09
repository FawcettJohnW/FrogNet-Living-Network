#!/bin/bash
# build.sh -- [TEAM_RAM_V1] the team RAM (team_ram.cpp on the Ribbit platform) as one static executable per Android ABI,
# written where the app ships its native programs: shared/src/main/jniLibs/<abi>/libteamram.so (the name only makes
# Android install it; it is an executable, run from nativeLibraryDir like libfdftun.so).
# musl, not glibc: a static glibc pthread uses clone3 and rseq, which an app's seccomp filter may kill; musl uses
# neither. Toolchain: zig 0.13 (pip install ziglang==0.13.0), which carries musl and libc++ for every target.
# lz4 and libcrypto are cross-built once per target into $DEPS (sources: build_arm64.sh's clones, or fetched here).
#   build.sh [BUILD]        BUILD names the binary (--version); default: the app's versionName
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); APP=$(cd "$HERE/../.." && pwd); SRC=$(cd "$APP/.." && pwd)
PLAT=$(cd "$SRC/../../frognet_semantic/ribbit/cpp" && pwd)
BUILD=${1:-$(sed -n 's/.*versionName *= *"\(.*\)".*/\1/p' "$APP/android/team/build.gradle.kts" | head -1)}
DEPS=${DEPS:-$HERE/.deps}; JNI=$APP/android/shared/src/main/jniLibs
ZIG="python3 -m ziglang"; python3 -m ziglang version >/dev/null 2>&1 || { echo "build.sh: zig missing: pip install ziglang==0.13.0" >&2; exit 1; }
LZ4SRC=$SRC/fdf-source/rinab/build/arm-deps/lz4; SSLSRC=$SRC/fdf-source/rinab/build/arm-deps/openssl
[ -d "$LZ4SRC/lib" ] || { LZ4SRC=$DEPS/src/lz4; [ -d "$LZ4SRC" ] || git clone -q --depth 1 https://github.com/lz4/lz4.git "$LZ4SRC"; }
[ -f "$SSLSRC/Configure" ] || { SSLSRC=$DEPS/src/openssl; [ -d "$SSLSRC" ] || git clone -q --depth 1 -b openssl-3.0.13 https://github.com/openssl/openssl.git "$SSLSRC"; }
# abi  zig target  openssl target
for row in "arm64-v8a aarch64-linux-musl linux-aarch64" "armeabi-v7a arm-linux-musleabihf linux-armv4" "x86_64 x86_64-linux-musl linux-x86_64"; do
  set -- $row; ABI=$1; T=$2; OT=$3; D=$DEPS/$ABI; mkdir -p "$D" "$JNI/$ABI"
  CC="$ZIG cc -target $T"; CXX="$ZIG c++ -target $T"
  # a clean checkout of each source per target (git clone --local): build_arm64.sh's trees hold glibc aarch64 objects
  # that make would otherwise take as up to date
  if [ ! -f "$D/lz4/lib/liblz4.a" ]; then rm -rf "$D/lz4"; git clone -q --local "$LZ4SRC" "$D/lz4"
     make -s -C "$D/lz4/lib" CC="$CC" AR="$ZIG ar" liblz4.a; fi
  if [ ! -f "$D/openssl/libcrypto.a" ]; then rm -rf "$D/openssl"; git clone -q --local "$SSLSRC" "$D/openssl"
     (cd "$D/openssl" && CC="$CC" AR="$ZIG ar" RANLIB="$ZIG ranlib" ./Configure $OT no-shared no-tests no-asm no-async no-engine -static >/dev/null && make -s -j"$(nproc)" build_libs >/dev/null); fi
  O=$D/obj; mkdir -p "$O"
  F="-std=c++20 -O2 -pthread -Wno-free-nonheap-object -I$PLAT/include -I$D/lz4/lib -I$D/openssl/include"
  for f in ram_host lf_ebr frogram; do $CXX $F -c "$PLAT/src/$f.cpp" -o "$O/$f.o" & done
  $CXX $F -DTEAM_RAM_BUILD="\"$BUILD\"" -c "$HERE/team_ram.cpp" -o "$O/team_ram.o"; wait
  $CXX -static -s -o "$JNI/$ABI/libteamram.so" "$O"/team_ram.o "$O"/ram_host.o "$O"/lf_ebr.o "$O"/frogram.o "$D/lz4/lib/liblz4.a" "$D/openssl/libcrypto.a"
  echo "built $JNI/$ABI/libteamram.so ($T, $(stat -c %s "$JNI/$ABI/libteamram.so") bytes)"
done
echo "BUILD OK: libteamram.so $BUILD for arm64-v8a, armeabi-v7a, x86_64"
