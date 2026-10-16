#!/bin/bash
# build_static_clients.sh -- [STATIC_CLIENTS_V1] the x86-64 member programs as fully static binaries in build/static
# (the node and droplet bundles' /opt/fdf/bin), the same list and flags as build_arm64.sh's. Until 2026-10-10 these
# were compiled by hand; make_deliverables.sh and the netns tests read build/static. Run after tools/build.sh.
set -eu; cd "$(dirname "$0")/.."; P=build/platform; L=/usr/lib/x86_64-linux-gnu; mkdir -p build/static
CXX="g++ -O2 -pthread -D_GNU_SOURCE"
$CXX -std=c++17 -c $P/ribbit_cpp/frogram.cpp -o build/static/frogram.o
for t in fdf_client:fdf-client fdf_participants:fdf-participants ram_tool:ram fdf_events:fdf-events fdf_attention:fdf-attention fdf_where:fdf-where; do
  $CXX -std=c++20 -I$P/ribbit_cpp -Isrc src/${t%%:*}.cpp build/static/frogram.o $L/liblz4.a $L/libcrypto.a -lresolv -static -o build/static/${t##*:} 2>&1 \
    | grep -v "statically linked applications requires at runtime\|in function" || true
done
rm -f build/static/frogram.o
for b in fdf-client fdf-participants ram fdf-events fdf-attention fdf-where; do
  file build/static/$b | grep -q "statically linked" || { echo "build_static_clients: build/static/$b is not static" >&2; exit 1; }; done
echo "BUILD OK: build/static/{fdf-client,fdf-participants,ram,fdf-events,fdf-attention,fdf-where} (static x86-64)"
