#!/bin/bash
# build_kartram.sh -- [KART_ON_THE_SESSION_V1] the kart game's memory library from this tree's patched session client
# (run tools/build.sh first). Linux: kart/libkartram.so (lz4 and libstdc++ linked in; needs glibc 2.34+ and libcrypto.so.3: Ubuntu 22.04 or later). With --windows also
# kart/kartram.dll (64-bit, static, no other DLLs), the way tools/build_windows_client.sh builds fdf-client.exe:
# needs g++-mingw-w64-x86-64-posix and github.com for lz4. Compiler output goes to this terminal.
set -eu; H=$(cd "$(dirname "$0")/.." && pwd); cd "$H"; P=build/platform/ribbit_cpp
[ -f $P/frogram.cpp ] || { echo "build_kartram: run tools/build.sh first ($P/frogram.cpp is not there)" >&2; exit 1; }
LZ=$(ls /usr/lib/*/liblz4.a 2>/dev/null | head -1)
[ -n "$LZ" ] || { echo "build_kartram: liblz4.a not found (apt install liblz4-dev)" >&2; exit 1; }
g++ -std=c++17 -O2 -fPIC -shared -pthread -fvisibility=hidden -I$P kart/kartram.cpp kart/kartram_glibc.cpp $P/frogram.cpp "$LZ" -lcrypto -static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL -Wl,--version-script=kart/kartram.map -Wl,--no-undefined -o kart/libkartram.so
echo "BUILD OK: kart/libkartram.so"
if [ "${2:-${1:-}}" = --arm64 ] || [ "${1:-}" = --arm64 ]; then   # [KART_ARM64_V1] kart/arm64/libkartram.so for the Pi nodes, all static inside
    ARM=${ARM:-$H/build/arm-deps}; [ -f "$ARM/openssl/libcrypto.a" ] || { echo "build_kartram: run tools/build_arm64.sh first ($ARM)" >&2; exit 1; }
    mkdir -p kart/arm64
    aarch64-linux-gnu-g++ -std=c++17 -O2 -fPIC -shared -pthread -fvisibility=hidden -I$P -I$ARM/lz4/lib -I$ARM/openssl/include \
        kart/kartram.cpp kart/kartram_glibc.cpp $P/frogram.cpp $ARM/lz4/lib/liblz4.a $ARM/openssl/libcrypto.a \
        -static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL -Wl,--version-script=kart/kartram.map -Wl,--no-undefined -o kart/arm64/libkartram.so
    echo "BUILD OK: kart/arm64/libkartram.so"
fi
if [ "${1:-}" = --windows ] || [ "${2:-}" = --windows ]; then
    W=build/win; mkdir -p $W
    CXX=x86_64-w64-mingw32-g++-posix; CC=x86_64-w64-mingw32-gcc-posix; AR=x86_64-w64-mingw32-ar
    [ -d $W/lz4 ] || git clone -q --depth 1 https://github.com/lz4/lz4.git $W/lz4
    [ -f $W/liblz4.a ] || { for f in lz4 lz4hc lz4frame xxhash; do $CC -O2 -c $W/lz4/lib/$f.c -o $W/$f.o; done; $AR rcs $W/liblz4.a $W/lz4.o $W/lz4hc.o $W/lz4frame.o $W/xxhash.o; }
    $CXX -std=c++17 -O2 -shared -D_WIN32_WINNT=0x0A00 -Isrc/win -I$W/lz4/lib -I$P kart/kartram.cpp $P/frogram.cpp $W/liblz4.a \
        -static -static-libgcc -static-libstdc++ -lbcrypt -lws2_32 -liphlpapi -lsynchronization -o kart/kartram.dll
    echo "BUILD OK: kart/kartram.dll"
fi
