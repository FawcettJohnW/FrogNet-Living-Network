#!/bin/bash
# build_frr.sh -- [FRR_STATIC_V1] FRR 9.1's isisd and zebra, statically linked, for x86_64 and aarch64, for the IS-IS
# arm of the Day comparison (day-claims). Built from source with its three dependencies (pcre2 10.42, json-c 0.17,
# libyang 2.1.148) built static per architecture. No vtysh, no watchfrr, no other daemons: the bed writes each
# node's isisd/zebra config files and starts the two daemons itself. Output: build/frr/<arch>/{isisd,zebra}.
# Sources are cloned once into build/frr-src. Needs: cmake, autoconf, automake, libtool, bison, flex, python3,
# gcc/g++ and aarch64-linux-gnu-gcc/g++.
set -eu; cd "$(dirname "$0")/.."; R=$PWD; SRC=$R/build/frr-src; OUT=$R/build/frr; mkdir -p "$SRC" "$OUT"
J=$(nproc)
clone() { [ -d "$SRC/$1" ] || git clone -q --depth 1 -b "$2" "$3" "$SRC/$1"; }
clone pcre2   pcre2-10.42             https://github.com/PCRE2Project/pcre2.git
clone json-c  json-c-0.17-20230812    https://github.com/json-c/json-c.git
clone libyang v2.1.148                https://github.com/CESNET/libyang.git
clone frr     stable/9.1              https://github.com/FRRouting/frr.git
clone protobuf-c v1.4.1              https://github.com/protobuf-c/protobuf-c.git
# libxcrypt from its release tarball: its git tree needs a newer libtool than the build machine's to bootstrap
[ -d "$SRC/libxcrypt" ] || { curl -sSfL https://github.com/besser82/libxcrypt/releases/download/v4.4.36/libxcrypt-4.4.36.tar.xz \
  | tar -C "$SRC" -xJf - && mv "$SRC/libxcrypt-4.4.36" "$SRC/libxcrypt"; }
deps() {   # $1 arch, $2 CC, $3 cmake system processor
  local A=$1 CC=$2 P=$R/build/frr-deps/$1 B=$R/build/frr-obj/$1
  [ -f "$P/lib/libyang.a" ] && return 0
  mkdir -p "$P" "$B"
  local TC=(-DCMAKE_C_COMPILER=$CC -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=$3 -DCMAKE_INSTALL_PREFIX=$P
            -DCMAKE_FIND_ROOT_PATH=$P -DCMAKE_PREFIX_PATH=$P -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_BUILD_TYPE=Release)
  cmake -S "$SRC/pcre2" -B "$B/pcre2" "${TC[@]}" -DBUILD_SHARED_LIBS=OFF -DPCRE2_BUILD_PCRE2GREP=OFF -DPCRE2_BUILD_TESTS=OFF > "$B/pcre2.log"
  cmake --build "$B/pcre2" -j$J > /dev/null && cmake --install "$B/pcre2" > /dev/null
  cmake -S "$SRC/json-c" -B "$B/json-c" "${TC[@]}" -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DDISABLE_EXTRA_LIBS=ON > "$B/json-c.log"
  cmake --build "$B/json-c" -j$J > /dev/null && cmake --install "$B/json-c" > /dev/null
  cmake -S "$SRC/libyang" -B "$B/libyang" "${TC[@]}" -DBUILD_SHARED_LIBS=OFF -DENABLE_STATIC=ON -DENABLE_LYD_PRIV=ON -DENABLE_TESTS=OFF \
        -DENABLE_VALGRIND_TESTS=OFF -DPCRE2_INCLUDE_DIR=$P/include -DPCRE2_LIBRARY=$P/lib/libpcre2-8.a > "$B/libyang.log"
  cmake --build "$B/libyang" -j$J > "$B/libyang-build.log" 2>&1 && cmake --install "$B/libyang" > /dev/null
  [ -f "$P/lib/libyang.a" ] || { echo "build_frr: libyang.a not built for $A (see $B/libyang-build.log)" >&2; exit 1; }
}
pbc() {    # $1 arch, $2 host triplet ("" native): libprotobuf-c only (FRR 9.1 always links it; protoc-c is the build host's)
  local A=$1 P=$R/build/frr-deps/$1 B=$R/build/frr-obj/$1/protobuf-c
  [ -f "$P/lib/libprotobuf-c.a" ] && return 0
  [ -x "$SRC/protobuf-c/configure" ] || (cd "$SRC/protobuf-c" && ./autogen.sh > /dev/null 2>&1)
  mkdir -p "$B" && (cd "$B" && CFLAGS="-O2 -fPIC" "$SRC/protobuf-c/configure" ${2:+--host=$2} --prefix=$P --disable-protoc --enable-static --disable-shared \
     > configure.log 2>&1 && make -j$J > make.log 2>&1 && make install > install.log 2>&1) \
     || { echo "build_frr: protobuf-c failed for $A: $B" >&2; exit 1; }
}
deps x86_64  gcc                 x86_64
deps aarch64 aarch64-linux-gnu-gcc aarch64
pbc x86_64 ""
pbc aarch64 aarch64-linux-gnu
xcrypt() { # $1 arch, $2 host: libcrypt for the cross build (the build machine's own serves x86_64); FRR links crypt()
  local A=$1 P=$R/build/frr-deps/$1 B=$R/build/frr-obj/$1/libxcrypt
  [ -f "$P/lib/libcrypt.a" ] && return 0
  mkdir -p "$B" && (cd "$B" && CFLAGS="-O2 -fPIC" "$SRC/libxcrypt/configure" --host=$2 --prefix=$P --enable-static --disable-shared \
     --disable-werror > configure.log 2>&1 && make -j$J > make.log 2>&1 && make install > install.log 2>&1) \
     || { echo "build_frr: libxcrypt failed for $A: $B" >&2; exit 1; }
}
xcrypt aarch64 aarch64-linux-gnu
[ -x "$SRC/frr/configure" ] || (cd "$SRC/frr" && ./bootstrap.sh > /dev/null)
# [FRR_NO_INTERP_V1] libfrr carries an .interp section so libfrr.so can be executed; linked into a static daemon it
# leaves a PT_INTERP that sends the kernel to ld.so, which crashes. Kept out of these builds only.
grep -q FRR_NO_INTERP "$SRC/frr/lib/libfrr.c" || sed -i 's/^#ifdef INTERP$/#if defined(INTERP) \&\& !defined(FRR_NO_INTERP)/' "$SRC/frr/lib/libfrr.c"
grep -q FRR_NO_INTERP "$SRC/frr/lib/libfrr.c" || { echo "build_frr: could not patch lib/libfrr.c" >&2; exit 1; }
# -DVTYSH with --disable-vtysh: 9.1's lib/vty.c uses VTYSH_* unconditionally ([FRR_VTYSH_DEFINE_V1]); no vtysh binary, no readline
OFF="--disable-babeld --disable-bfdd --disable-bgpd --disable-eigrpd --disable-fabricd --disable-ldpd --disable-mgmtd
     --disable-nhrpd --disable-ospf6d --disable-ospfd --disable-ospfapi --disable-ospfclient --disable-pathd --disable-pbrd
     --disable-pim6d --disable-pimd --disable-ripd --disable-ripngd --disable-staticd --disable-vrrpd --disable-vtysh
     --disable-watchfrr --disable-doc --disable-capabilities --disable-irdp --disable-rtadv --disable-bgp-bmp
     --disable-bgp-vnc --disable-backtrace --disable-version-build-config --disable-protobuf --disable-fpm --disable-zeromq
     --disable-grpc --disable-scripting"
frr() {    # $1 arch, $2 host triplet ("" native), $3 extra configure args
  local A=$1 P=$R/build/frr-deps/$1 B=$R/build/frr-obj/$1/frr
  mkdir -p "$B" "$OUT/$A"
  (cd "$B" && PKG_CONFIG_PATH=$P/lib/pkgconfig PKG_CONFIG_LIBDIR=$P/lib/pkgconfig \
     PYTHON=python3.12 CPPFLAGS="-I$P/include -DVTYSH -DFRR_NO_INTERP" LDFLAGS="-L$P/lib" LIBS="-lprotobuf-c -lyang -ljson-c -lpcre2-8 -lcrypt -lpthread -lm" \
     "$SRC/frr/configure" ${2:+--host=$2} --prefix=/opt/fdf/experiments/day-claims/isis --sysconfdir=/nonexistent --with-yangmodelsdir=/opt/fdf/experiments/day-claims/isis/yang \
       --localstatedir=/nonexistent --enable-static --enable-static-bin --enable-shared --enable-user=root --enable-group=root \
       $OFF $3 > "$B/configure.log" 2>&1) || { echo "build_frr: configure failed for $A: $B/configure.log" >&2; tail -20 "$B/configure.log" >&2; exit 1; }
  make -C "$B" -j$J > "$B/make.log" 2>&1 || { echo "build_frr: make failed for $A: $B/make.log" >&2; grep -m5 -i "error" "$B/make.log" >&2; exit 1; }
  # [FRR_ALL_STATIC_V1] --enable-static-bin leaves glibc dynamic; relink the two daemons with libtool's -all-static, no
  # PIE and without FRR's -export-dynamic (with -static it leaves a PT_INTERP and the binary crashes in ld.so), so
  # the same files run on Debian bookworm (glibc 2.36) and Ubuntu 24.04 alike
  rm -f "$B/zebra/zebra" "$B/isisd/isisd"
  make -C "$B" zebra/zebra isisd/isisd AM_LDFLAGS= LDFLAGS="-L$P/lib -all-static -no-pie" > "$B/relink.log" 2>&1 || { echo "build_frr: static relink failed for $A: $B/relink.log" >&2; exit 1; }
  for d in zebra isisd; do cp "$B/$d/$d" "$OUT/$A/$d"; ${2:+$2-}strip "$OUT/$A/$d"; done
  mkdir -p "$OUT/yang" && find "$SRC/frr/yang" -name "*.yang" -exec cp {} "$OUT/yang/" \;   # read at start from --with-yangmodelsdir
  file "$OUT/$A"/* | sed "s|$R/||"
}
frr x86_64 "" ""
[ "${1:-}" = x86 ] && exit 0
# cross: clippy (FRR's build-time code generator) runs on the build machine
frr aarch64 aarch64-linux-gnu "--with-clippy=$R/build/frr-obj/x86_64/frr/lib/clippy"
