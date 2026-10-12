#!/bin/bash
# build_arm64.sh -- [FDF_ARM64_V1] static aarch64 builds of the member-side programs (fdf-client, fdf-participants, ram)
# and rina-host (glibc malloc; jemalloc is not cross-built), for the Pi nodes (FrogNetHost). Run after tools/build.sh.
# Needs: g++-aarch64-linux-gnu (apt); lz4 and openssl sources, cross-built once into $ARM (done here if missing).
# Output: build/arm64/{fdf-client,fdf-participants,ram,fdf-events,rina-host}
set -eu; cd "$(dirname "$0")/.."; ARM=${ARM:-$PWD/build/arm-deps}; P=build/platform; mkdir -p build/arm64 "$ARM"
if [ ! -f "$ARM/lz4/lib/liblz4.a" ]; then git clone -q --depth 1 https://github.com/lz4/lz4.git "$ARM/lz4"; CC=aarch64-linux-gnu-gcc AR=aarch64-linux-gnu-ar make -s -C "$ARM/lz4/lib" liblz4.a; fi
if [ ! -f "$ARM/openssl/libcrypto.a" ]; then git clone -q --depth 1 -b openssl-3.0.13 https://github.com/openssl/openssl.git "$ARM/openssl"; (cd "$ARM/openssl" && ./Configure linux-aarch64 no-shared no-tests --cross-compile-prefix=aarch64-linux-gnu- >/dev/null && make -s -j8 build_libs); fi
I="-I$ARM/lz4/lib -I$ARM/openssl/include"; L="$ARM/lz4/lib/liblz4.a $ARM/openssl/libcrypto.a -lresolv -static"
CXX="aarch64-linux-gnu-g++ -O2 -pthread -D_GNU_SOURCE $I"
$CXX -std=c++17 -c $P/ribbit_cpp/frogram.cpp -o build/arm64/frogram.o
for t in fdf_client:fdf-client fdf_participants:fdf-participants ram_tool:ram fdf_events:fdf-events fdf_attention:fdf-attention fdf_where:fdf-where; do
  $CXX -std=c++20 -I$P/ribbit_cpp -Isrc src/${t%%:*}.cpp build/arm64/frogram.o $L -o build/arm64/${t##*:}; done
$CXX -std=c++20 -Isrc -Isrc/door -I$P/ribbit_cpp -c src/rina_region.cpp -o build/arm64/rina_region.o
$CXX -std=c++20 -Isrc -Isrc/door -I$P/ribbit_cpp -c src/ctl_host.cpp -o build/arm64/ctl_host.o
$CXX -std=c++20 -I$P/ribbit_cpp -I$P/ramsrv -Isrc/door -c build/ram_server_rina.cpp -o build/arm64/ram_server_rina.o
$CXX -static -o build/arm64/rina-host build/arm64/ram_server_rina.o build/arm64/rina_region.o build/arm64/ctl_host.o build/arm64/frogram.o $L -lpthread -ldl
file build/arm64/fdf-client build/arm64/rina-host | grep -q "ARM aarch64" && echo "BUILD OK: build/arm64/{fdf-client,fdf-participants,ram,fdf-events,rina-host} (static aarch64)"
