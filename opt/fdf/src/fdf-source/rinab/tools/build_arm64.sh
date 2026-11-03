#!/bin/bash
# build_arm64.sh -- [FDF_ARM64_V1] static aarch64 builds of the member-side programs (fdf-client, fdf-participants, ram)
# and fdf-ram, the RAM manager (glibc malloc; jemalloc is not cross-built), for the Pi nodes (FrogNetHost). Run after tools/build.sh.
# Needs: g++-aarch64-linux-gnu (apt); lz4 and openssl sources, cross-built once into $ARM (done here if missing).
# Output: build/arm64/{fdf-client,fdf-participants,ram,fdf-events,fdf-attention,fdf-where,fdf-ram}
set -eu; cd "$(dirname "$0")/.."; ARM=${ARM:-$PWD/build/arm-deps}; mkdir -p build/arm64 "$ARM"
if [ ! -f "$ARM/lz4/lib/liblz4.a" ]; then git clone -q --depth 1 https://github.com/lz4/lz4.git "$ARM/lz4"; CC=aarch64-linux-gnu-gcc AR=aarch64-linux-gnu-ar make -s -C "$ARM/lz4/lib" liblz4.a; fi
if [ ! -f "$ARM/openssl/libcrypto.a" ]; then git clone -q --depth 1 -b openssl-3.0.13 https://github.com/openssl/openssl.git "$ARM/openssl"; (cd "$ARM/openssl" && ./Configure linux-aarch64 no-shared no-tests --cross-compile-prefix=aarch64-linux-gnu- >/dev/null && make -s -j8 build_libs); fi
I="-I$ARM/lz4/lib -I$ARM/openssl/include"; L="$ARM/lz4/lib/liblz4.a $ARM/openssl/libcrypto.a -lresolv -static"
CXX="aarch64-linux-gnu-g++ -O2 -pthread -D_GNU_SOURCE $I"
PL=../../../../frognet_semantic/ribbit/cpp   # [FDF_RAM_ON_PLATFORM_V1] the clients on the platform, as fdf-ram is
$CXX -std=c++20 -I$PL/include -c $PL/src/frogram.cpp -o build/arm64/frogram.o
$CXX -std=c++20 -I$PL/include -c $PL/src/lf_ebr.cpp -o build/arm64/lf_ebr.o
for t in fdf_client:fdf-client fdf_participants:fdf-participants ram_tool:ram fdf_events:fdf-events fdf_attention:fdf-attention fdf_where:fdf-where; do
  $CXX -std=c++20 -I$PL/include -Isrc src/${t%%:*}.cpp build/arm64/frogram.o build/arm64/lf_ebr.o $L -o build/arm64/${t##*:}; done
# [FDF_RAM_ON_PLATFORM_V1] fdf-ram, the RAM manager, against the platform's headers (it replaces rina-host)
F=build/arm64/fdfram; mkdir -p $F
for x in rina_region ctl_host daf_region fdf_ram; do $CXX -std=c++20 -Isrc -Isrc/door -I$PL/include -c src/$x.cpp -o $F/$x.o; done
$CXX -std=c++20 -I$PL/include -c $PL/src/ram_host.cpp -o $F/p_ram_host.o; cp build/arm64/frogram.o $F/p_frogram.o; cp build/arm64/lf_ebr.o $F/p_lf_ebr.o
$CXX -static -o build/arm64/fdf-ram $F/fdf_ram.o $F/rina_region.o $F/daf_region.o $F/ctl_host.o $F/p_ram_host.o $F/p_lf_ebr.o $F/p_frogram.o $L -lpthread -ldl
rm -f build/arm64/rina-host build/arm64/ram_server_rina.o   # [FDF_RAM_ON_PLATFORM_V1] rina-host is gone
file build/arm64/fdf-client build/arm64/fdf-ram | grep -q "ARM aarch64" && echo "BUILD OK: build/arm64/{fdf-client,fdf-participants,ram,fdf-events,fdf-attention,fdf-where,fdf-ram} (static aarch64)"
