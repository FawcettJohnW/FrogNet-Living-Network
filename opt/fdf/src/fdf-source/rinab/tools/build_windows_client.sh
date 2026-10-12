#!/bin/bash
# build_windows_client.sh -- [FDF_WINDOWS_CLIENT_V1] cross-builds fdf-client.exe (64-bit Windows, static, no DLLs) from
# this tree's patched session client (run tools/build.sh first: it applies [FUTEX_PORTABLE_V1] and [WINDOWS_PORTABLE_V1]).
# Needs: g++-mingw-w64-x86-64-posix (apt), and network access to github.com for lz4. Output: build/win/fdf-client.exe
set -eu; H=$(cd "$(dirname "$0")/.." && pwd); cd "$H"; W=build/win; mkdir -p $W
CXX=x86_64-w64-mingw32-g++-posix; CC=x86_64-w64-mingw32-gcc-posix; AR=x86_64-w64-mingw32-ar
[ -d $W/lz4 ] || git clone -q --depth 1 https://github.com/lz4/lz4.git $W/lz4
for f in lz4 lz4hc lz4frame xxhash; do $CC -O2 -c $W/lz4/lib/$f.c -o $W/$f.o; done; $AR rcs $W/liblz4.a $W/lz4.o $W/lz4hc.o $W/lz4frame.o $W/xxhash.o
F="-std=c++17 -O2 -D_WIN32_WINNT=0x0A00 -Isrc/win -I$W/lz4/lib -Ibuild/platform/ribbit_cpp"
$CXX $F -c build/platform/ribbit_cpp/frogram.cpp -o $W/frogram.o
$CXX $F src/fdf_client.cpp $W/frogram.o $W/liblz4.a -static -lbcrypt -lws2_32 -liphlpapi -lsynchronization -o $W/fdf-client.exe
echo "BUILD OK: $W/fdf-client.exe"
