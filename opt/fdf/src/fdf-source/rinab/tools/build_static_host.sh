#!/bin/bash
# build_static_host.sh -- [STATIC_HOST_V1] rina-host as ONE fully static binary (jemalloc, lz4, libcrypto, libstdc++,
# glibc all inside), so the RAM server runs on any x86-64 Linux regardless of its glibc: the 24.04-built dynamic one
# needs glibc 2.38 and fails on Ubuntu 22.04 (StreamingFrog: "GLIBC_2.38 not found"). Run after tools/build.sh.
# Needs libjemalloc-dev, liblz4-dev, libssl-dev (their .a files). Output: build/bin/rina-host-static
set -eu; cd "$(dirname "$0")/.."; L=/usr/lib/x86_64-linux-gnu
g++ -static -o build/bin/rina-host-static build/obj/ram_server_rina.o build/obj/rina_region.o build/obj/daf_region.o build/obj/ctl_host.o build/obj/frogram.o \
    $L/libjemalloc.a $L/liblz4.a $L/libcrypto.a -lresolv -lpthread -ldl 2>&1 | grep -v "statically linked applications requires at runtime\|in function" || true
mkdir -p build/static && cp build/bin/rina-host-static build/static/rina-host   # the netns tests run build/static/rina-host
file build/bin/rina-host-static | grep -q "statically linked" && echo "BUILD OK: build/bin/rina-host-static (static), and build/static/rina-host"
