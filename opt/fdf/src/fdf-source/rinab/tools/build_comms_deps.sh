#!/bin/bash
# build_comms_deps.sh arm64|x86_64 -- [COMMS_ON_A_NODE_V1] the static media libraries comms-media links, built once into
# build/comms-deps/<arch> from their GitHub sources: opus 1.4, libvpx 1.13.1, PortAudio 19.7.0 (no host APIs: the server
# opens no device), FFmpeg 6.1.1 with only what comms-media uses: the libvpx VP8 encoder, the vp8 and h264 decoders
# and their parsers, swscale (--disable-everything otherwise: the binary stays small enough for one node tarball). arm64 is cross-compiled
# (aarch64-linux-gnu-*; libvpx needs nasm for x86_64). Then tools/build_comms_static.sh.
set -eu; A=${1:?arm64|x86_64}; H=$(cd "$(dirname "$0")/.." && pwd); S=$H/build/comms-deps/src; P=$H/build/comms-deps/$A; mkdir -p "$S" "$P"; cd "$S"
for r in "FFmpeg/FFmpeg n6.1.1" "webmproject/libvpx v1.13.1" "xiph/opus v1.4" "PortAudio/portaudio v19.7.0"; do
  set -- $r; d=$(basename $1); [ -d $d ] || git clone -q --depth 1 -b $2 https://github.com/$1.git $d; done
if [ $A = arm64 ]; then CM="-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc"; CR=aarch64-linux-gnu-
else CM="-DCMAKE_POSITION_INDEPENDENT_CODE=ON"; CR=""; fi
echo "== opus"
(cd opus && rm -rf b-$A && cmake -S . -B b-$A $CM -DCMAKE_INSTALL_PREFIX=$P -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
   -DOPUS_BUILD_PROGRAMS=OFF -DOPUS_BUILD_TESTING=OFF > /dev/null && cmake --build b-$A -j"$(nproc)" > /dev/null && cmake --install b-$A > /dev/null)
echo "== portaudio (no host APIs)"
(cd portaudio && rm -rf b-$A && cmake -S . -B b-$A $CM -DCMAKE_INSTALL_PREFIX=$P -DCMAKE_BUILD_TYPE=Release -DPA_BUILD_SHARED=OFF \
   -DPA_BUILD_STATIC=ON -DPA_USE_ALSA=OFF -DPA_USE_JACK=OFF -DPA_USE_OSS=OFF > /dev/null && cmake --build b-$A -j"$(nproc)" > /dev/null && cmake --install b-$A > /dev/null)
echo "== libvpx"
rm -rf vpx-$A && cp -r libvpx vpx-$A && (cd vpx-$A && make distclean > /dev/null 2>&1 || true)
(cd vpx-$A && if [ $A = arm64 ]; then T="--target=arm64-linux-gcc"; else T="--target=x86_64-linux-gcc --as=nasm"; fi
 CROSS=$CR ./configure $T --prefix=$P --disable-examples --disable-tools --disable-docs --disable-unit-tests --enable-pic \
   --disable-shared --enable-static > /dev/null && CROSS=$CR make -j"$(nproc)" > /dev/null && make install > /dev/null)
echo "== FFmpeg"
rm -rf ff-$A && cp -r FFmpeg ff-$A && (cd ff-$A && make distclean > /dev/null 2>&1 || true)   # a source tree another arch was built in
(cd ff-$A && X=""; [ $A = arm64 ] && X="--enable-cross-compile --cross-prefix=$CR --arch=aarch64 --target-os=linux"
 PKG_CONFIG_PATH=$P/lib/pkgconfig PKG_CONFIG_LIBDIR=$P/lib/pkgconfig ./configure --prefix=$P $X --pkg-config=pkg-config \
   --pkg-config-flags=--static --disable-programs --disable-doc --disable-shared --enable-static --enable-pic --disable-debug \
   --disable-network --disable-autodetect --disable-everything --enable-libvpx --enable-encoder=libvpx_vp8 \
   --enable-decoder=vp8,h264 --enable-parser=vp8,h264 --enable-swscale \
   --extra-cflags="-I$P/include" --extra-ldflags="-L$P/lib" > /dev/null \
 && make -j"$(nproc)" > /dev/null && make install > /dev/null)
ls "$P/lib"
