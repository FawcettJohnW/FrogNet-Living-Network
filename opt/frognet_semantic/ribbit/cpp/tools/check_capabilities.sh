#!/usr/bin/env bash
# Copyright (C) 2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
# Focused gate: current platform + existing API++ regression + capability negative controls.
set -eu
cd "$(dirname "$0")/.."
CXX=${CXX:-g++}
source ./ensure_build_deps.sh
ribbit_ensure_build_deps
OUT=${CAPABILITY_BUILD_DIR:-$PWD/bin/capabilities}
mkdir -p "$OUT"
flags=(-std=c++20 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I"$PWD/include")
common=()
for name in frogram ram_host ribbit_space lf_ebr; do
  "$CXX" "${flags[@]}" -c "src/$name.cpp" -o "$OUT/$name.o"
  common+=("$OUT/$name.o")
done
"$CXX" "${flags[@]}" -c src/ribbit_pp.cpp -o "$OUT/ribbit_pp.o"
"$CXX" "${flags[@]}" -c tests/test_capabilities.cpp -o "$OUT/test_capabilities.o"
"$CXX" "${flags[@]}" "$OUT/test_capabilities.o" "$OUT/ribbit_pp.o" "${common[@]}" -llz4 -lcrypto -o "$OUT/test-capabilities"
timeout 90 "$OUT/test-capabilities" > "$OUT/positive.txt" 2>&1
cat "$OUT/positive.txt"
grep -q '^RESULT PASS capability API' "$OUT/positive.txt"
"$CXX" "${flags[@]}" tests/test_ribbit_pp.cpp "$OUT/ribbit_pp.o" "${common[@]}" -llz4 -lcrypto -o "$OUT/test-ribbit-pp"
timeout 120 "$OUT/test-ribbit-pp" 8960 > "$OUT/regression.txt" 2>&1
cat "$OUT/regression.txt"
grep -q '^RESULT PASS API++' "$OUT/regression.txt"
for mutant in 1 2 3; do
  "$CXX" "${flags[@]}" -DCAPABILITY_BROKEN="$mutant" -c src/ribbit_pp.cpp -o "$OUT/mutant-$mutant.o"
  "$CXX" "${flags[@]}" "$OUT/test_capabilities.o" "$OUT/mutant-$mutant.o" "${common[@]}" -llz4 -lcrypto -o "$OUT/mutant-$mutant"
  if timeout 90 "$OUT/mutant-$mutant" > "$OUT/mutant-$mutant.txt" 2>&1; then
    echo "FAIL capability mutant $mutant survived"; exit 1
  fi
  # A crash or timeout is not accepted as a killed behavioral mutant.
  grep -q '^FAIL ' "$OUT/mutant-$mutant.txt"
  grep -q '^RESULT FAIL capability API' "$OUT/mutant-$mutant.txt"
  echo "PASS capability mutant $mutant detected"
done
for mutant in 1 2 3; do
  "$CXX" "${flags[@]}" -DTUPLE_HELPER_BROKEN="$mutant" -c src/ribbit_pp.cpp -o "$OUT/helper-mutant-$mutant.o"
  "$CXX" "${flags[@]}" "$OUT/test_capabilities.o" "$OUT/helper-mutant-$mutant.o" "${common[@]}" -llz4 -lcrypto -o "$OUT/helper-mutant-$mutant"
  if timeout 90 "$OUT/helper-mutant-$mutant" > "$OUT/helper-mutant-$mutant.txt" 2>&1; then
    echo "FAIL tuple helper mutant $mutant survived"; exit 1
  fi
  grep -q '^FAIL ' "$OUT/helper-mutant-$mutant.txt"
  grep -q '^RESULT FAIL capability API' "$OUT/helper-mutant-$mutant.txt"
  echo "PASS tuple helper mutant $mutant detected"
done
echo "RESULT PASS tuple/capability operations, API++ regression, and 6/6 mutation controls"
