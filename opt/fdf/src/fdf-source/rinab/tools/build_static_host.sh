#!/bin/bash
# build_static_host.sh -- [STATIC_HOST_V1] fdf-ram, the RAM manager ([FDF_RAM_ON_PLATFORM_V1]), as ONE fully static
# binary (jemalloc, lz4, libcrypto, libstdc++, glibc all inside), so it runs on any x86-64 Linux regardless of its glibc
# (the 24.04-built dynamic one needs glibc 2.38 and fails on Ubuntu 22.04: StreamingFrog, "GLIBC_2.38 not found").
# Run after tools/build.sh. Needs libjemalloc-dev, liblz4-dev, libssl-dev (their .a files).
# Output: build/bin/fdf-ram-static, and build/static/fdf-ram (what the netns tests and the deliverables take)
set -eu; cd "$(dirname "$0")/.."; L=/usr/lib/x86_64-linux-gnu; F=build/fdfram
g++ -static -o build/bin/fdf-ram-static $F/fdf_ram.o $F/fdf_region.o $F/daf_region.o $F/ctl_host.o $F/p_ram_host.o $F/p_lf_ebr.o $F/p_frogram.o \
    $L/libjemalloc.a $L/liblz4.a $L/libcrypto.a -lresolv -lpthread -ldl 2>&1 | grep -v "statically linked applications requires at runtime\|in function" || true
mkdir -p build/static && cp build/bin/fdf-ram-static build/static/fdf-ram
file build/bin/fdf-ram-static | grep -q "statically linked" && echo "BUILD OK: build/bin/fdf-ram-static (static), and build/static/fdf-ram"
