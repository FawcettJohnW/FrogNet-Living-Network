#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# step1-mesh-e2e.sh BINARY -- [MT_PACKETS_ON_THE_RAM_V1] oracle for Phase 1 step 1.
# meshtastic-mcp's own scripts/ci_device_mesh_e2e.py, unmodified: two nodes, a broadcast token must reach the DUT over
# its TCP client API. UDP multicast to the Meshtastic groups is blackholed for the run, so the only path between the
# nodes is the shared RAM (bin/meshtastic-ram). Prints the e2e verdict line, the memory's view, and the nodes'
# [MT_RIBBIT] counts; every log is kept under results/step1-<stamp>/.
set -u
BIN=${1:?usage: step1-mesh-e2e.sh /path/to/meshtasticd}
EX=$(cd "$(dirname "$0")/.." && pwd)
MCP=${MCP:-/home/claude/mesh/mcp}
OUT=$EX/results/step1-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"
PORT=${RAM_PORT:-18800}
echo "== $(cat "$EX/VERSION")"; echo "== binary $BIN ($("$BIN" --version 2>/dev/null | head -1))"; echo "== results $OUT"
"$EX/bin/meshtastic-ram" --listen 127.0.0.1:$PORT > "$OUT/meshtastic-ram.log" 2>&1 & RAM=$!
for _ in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
for g in 224.0.0.69 239.0.0.69; do ip route add blackhole $g/32 2>/dev/null; done
echo "== multicast blackholed: $(ip route show type blackhole | tr '\n' ' ')"
cleanup(){ for g in 224.0.0.69 239.0.0.69; do ip route del blackhole $g/32 2>/dev/null; done; kill $RAM 2>/dev/null; wait $RAM 2>/dev/null; }
trap cleanup EXIT
cd "$MCP"
MESHTASTIC_RIBBIT=127.0.0.1:$PORT timeout 300 .venv/bin/python scripts/ci_device_mesh_e2e.py --binary "$BIN" \
    --workdir "$OUT/lab" --count 2 2>&1 | tee "$OUT/e2e.log"
RC=${PIPESTATUS[0]}
echo "== e2e rc=$RC"
for l in "$OUT"/lab/*.log; do
  printf '== %s: writes %s reads %s udp-sends %s start: %s\n' "$(basename "$l")" "$(grep -c '\[MT_RIBBIT\] write' "$l")" \
    "$(grep -c '\[MT_RIBBIT\] read id' "$l")" "$(grep -c 'Broadcasting packet over UDP' "$l")" \
    "$(grep -m1 -o '\[MT_RIBBIT\] mt-ribbit[^\"]*' "$l" | head -1)"
done
exit $RC
