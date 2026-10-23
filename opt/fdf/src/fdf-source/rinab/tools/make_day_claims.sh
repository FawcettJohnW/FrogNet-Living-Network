#!/bin/bash
# make_day_claims.sh OUTDIR -- [DAY_CLAIMS_DIST_V1] ONE tarball for every machine, x86_64 and aarch64 alike: the
# distributed Day-claims experiments, self-contained under /opt/fdf/experiments/day-claims (its own static rina-host and
# e2-dist per architecture, the operator script, the scenarios with their references). It installs nothing else and
# changes no FD&F or FrogNet file. Tarred from / (paths absolute-from-root), files only, owner root.
# Needs: tools/build.sh, tools/build_static_host.sh, tools/build_arm64.sh, then this (it builds the static e2-dist pair).
set -eu
H=$(cd "$(dirname "$0")/.." && pwd); OUT=${1:?usage: make_day_claims.sh OUTDIR}; mkdir -p "$OUT"; cd "$H"
TAG=$(sed -n 's/^#define FDF_BUILD_TAG "\(.*\)"/\1/p' src/fdf_version.hpp); NOW=$(date -u +%Y-%m-%dT%H:%MZ)
P=build/platform; L=/usr/lib/x86_64-linux-gnu; ARM=build/arm-deps
g++ -std=c++20 -O2 -pthread -D_GNU_SOURCE -I$P/ribbit_cpp -Isrc -static experiments/day-claims/e2_dist.cpp build/obj/frogram.o \
    $L/liblz4.a $L/libcrypto.a -lresolv -o build/static/e2-dist 2>&1 | grep -v "statically linked applications\|in function" || true
aarch64-linux-gnu-g++ -std=c++20 -O2 -pthread -D_GNU_SOURCE -I$ARM/lz4/lib -I$ARM/openssl/include -I$P/ribbit_cpp -Isrc -static \
    experiments/day-claims/e2_dist.cpp build/arm64/frogram.o $ARM/lz4/lib/liblz4.a $ARM/openssl/libcrypto.a -lresolv -o build/arm64/e2-dist 2>&1 \
    | grep -v "statically linked applications\|in function" || true
SCEN=$(ls -d experiments/day-claims/runs/e2-2*/scenarios.jsonl | tail -1)
for b in build/bin/rina-host-static build/static/e2-dist build/arm64/rina-host build/arm64/e2-dist; do
  [ -x "$b" ] || { echo "make_day_claims: $b missing" >&2; exit 1; }
  strings "$b" | grep -q "$TAG" || { echo "make_day_claims: $b is not $TAG" >&2; exit 1; }
  strings "$b" | grep -q "DAF_MODEL_V1" || [ "${b##*/}" = e2-dist ] || { echo "make_day_claims: $b has no DAF model" >&2; exit 1; }; done
file build/bin/rina-host-static build/static/e2-dist | grep -c "x86-64.*statically linked" | grep -q 2 || { echo "make_day_claims: x86 binaries not static x86-64" >&2; exit 1; }
file build/arm64/rina-host build/arm64/e2-dist | grep -c "ARM aarch64.*statically linked" | grep -q 2 || { echo "make_day_claims: arm binaries not static aarch64" >&2; exit 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT; D=$W/opt/fdf/experiments/day-claims
mkdir -p "$D/bin/x86_64" "$D/bin/aarch64" "$D/scenarios"
cp build/bin/rina-host-static "$D/bin/x86_64/rina-host"; cp build/static/e2-dist "$D/bin/x86_64/e2-dist"
cp build/arm64/rina-host "$D/bin/aarch64/rina-host"; cp build/arm64/e2-dist "$D/bin/aarch64/e2-dist"
cp experiments/day-claims/dist/day-claims "$D/day-claims"; cp "$SCEN" "$D/scenarios/e2.jsonl"
cp experiments/day-claims/EXPERIMENTS.md experiments/day-claims/RESULTS-E1.md experiments/day-claims/RESULTS-E2.md "$D/"
chmod 755 "$D/day-claims" "$D"/bin/*/*
printf 'package=day-claims.tgz\nbuild=%s\nbuilt=%s\nfor=every machine (x86_64 and aarch64): the distributed Day-claims experiments\nscenarios=%s\n' "$TAG" "$NOW" "$(sha256sum "$SCEN" | cut -c1-16)" > "$D/BUILD"
(cd "$W" && find . \( -type f -o -type l \) | sed 's|^\./||' | sort | tar --owner=0 --group=0 --no-recursion -czf - -T -) > "$OUT/day-claims.tgz"
printf '%s  %s bytes  sha256 %s  build %s\n' day-claims.tgz "$(stat -c %s "$OUT/day-claims.tgz")" "$(sha256sum "$OUT/day-claims.tgz" | cut -c1-16)" "$TAG"
