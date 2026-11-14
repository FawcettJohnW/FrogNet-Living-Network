#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ft-transport.sh -- [FROGTASTIC_SHM_TRANSPORT_V1] the firmware's calls through FrogTastic-RAM give what they gave
# when the firmware linked mt_ribbit itself.
#
# ft_probe.cpp is built twice: ft-probe-direct links libmtribbit.a; ft-probe-shim links only the firmware's shim
# (front/src/mesh/ribbit/frogtastic_shim.cpp), which starts bin/frogtastic-ram for its node as the firmware does. Each
# run: a fresh meshtastic-ram, node b (always direct) and node a (direct, then through the bridge). Node a's and node b's output
# must be identical between the runs. Prints the diff and ends with "ft-transport PASS" or "ft-transport FAIL".
set -e -o pipefail
cd "$(dirname "$0")/.."
CXX=${CXX:-g++}
OUT=${OUT:-/tmp/ft-transport}
rm -rf "$OUT" && mkdir -p "$OUT"
[ -x bin/frogtastic-ram ] && [ -x bin/meshtastic-ram ] && [ -f lib/libmtribbit.a ] || tools/build-ribbit.sh

$CXX -std=c++20 -O2 -pthread -Wall -Wextra -Iribbit harness/ft_probe.cpp lib/libmtribbit.a -o "$OUT/ft-probe-direct" \
  -llz4 -lcrypto
# the firmware's side: C++17 like the firmware, nothing of the platform
$CXX -std=c++17 -O2 -pthread -Wall -Wextra -DARCH_PORTDUINO -Iribbit front/src/mesh/ribbit/frogtastic_shim.cpp \
  harness/ft_probe.cpp -o "$OUT/ft-probe-shim" -lrt
if nm -C "$OUT/ft-probe-shim" | grep -q 'ribbit::'; then
  echo "ft-transport FAIL: ft-probe-shim contains Ribbit platform code"
  exit 1
fi
echo "ft-probe-shim links no Ribbit platform code"

export FROGTASTIC_RAM_BIN=$PWD/bin/frogtastic-ram
unset FROGTASTIC_SHM
run() {
  local mode=$1 port=$2
  bin/meshtastic-ram --listen 127.0.0.1:$port > "$OUT/$mode-ram.log" 2>&1 & local ram=$!
  sleep 1
  "$OUT/ft-probe-direct" b 127.0.0.1:$port > "$OUT/$mode-b.out" 2> "$OUT/$mode-b.err" & local pb=$!
  "$OUT/ft-probe-$mode" a 127.0.0.1:$port > "$OUT/$mode-a.out" 2> "$OUT/$mode-a.err" || true
  wait $pb || true
  sleep 0.5
  # node a's FrogTastic-RAM left with it
  if pgrep -f "frogtastic-ram --shm /frogtastic-ram-0000000a" > /dev/null; then
    echo "ft-transport FAIL: node a's frogtastic-ram outlived its firmware"
    pkill -f "frogtastic-ram --shm /frogtastic-ram-0000000a" || true
    leftover=1
  fi
  kill $ram 2>/dev/null || true
  wait 2>/dev/null || true
}
leftover=0
run direct 18911
run shim 18912

echo "--- node a, direct:"
cat "$OUT/direct-a.out"
ok=1
for n in a b; do
  if ! diff "$OUT/direct-$n.out" "$OUT/shim-$n.out"; then
    echo "node $n differs between direct and shim (above)"
    ok=0
  fi
done
echo "--- node a through FrogTastic-RAM, its log (shim and bridge):"
grep "FROGTASTIC" "$OUT/shim-a.err"
[ $leftover = 0 ] || ok=0
[ $ok = 1 ] && echo "ft-transport PASS (node a and node b identical through FrogTastic-RAM)" || { echo "ft-transport FAIL"; exit 1; }
