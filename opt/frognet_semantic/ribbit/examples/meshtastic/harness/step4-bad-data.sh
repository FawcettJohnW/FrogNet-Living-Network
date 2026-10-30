#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# step4-bad-data.sh BINARY [COUNT] -- Phase 1 step 4: harness/bad_data.py against COUNT nodes whose only path is the
# shared RAM (Meshtastic multicast blackholed). Logs and results.json under results/step4-<stamp>/.
set -u
BIN=${1:?usage: step4-bad-data.sh /path/to/meshtasticd [count]}; N=${2:-4}
EX=$(cd "$(dirname "$0")/.." && pwd)
MCP=${MCP:-/home/claude/mesh/mcp}
OUT=$EX/results/step4-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"
PORT=${RAM_PORT:-18830}
echo "== $(cat "$EX/VERSION")"; echo "== binary $BIN ($("$BIN" --version 2>/dev/null | head -1)), $N nodes"; echo "== results $OUT"
if [ "${STOCK:-0}" = 1 ]; then
  # the same scenarios against stock: nodes on UDP multicast, no memory; S7 (malformed cells) does not apply
  echo "== STOCK mode: UDP multicast, no shared RAM"; RAMARG=""; ENVSET=""
else
  "$EX/bin/meshtastic-ram" --listen 127.0.0.1:$PORT > "$OUT/meshtastic-ram.log" 2>&1 & RAM=$!
  for _ in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.1; done
  for g in 224.0.0.69 239.0.0.69; do ip route add blackhole $g/32 2>/dev/null; done
  cleanup(){ for g in 224.0.0.69 239.0.0.69; do ip route del blackhole $g/32 2>/dev/null; done; kill $RAM 2>/dev/null; wait $RAM 2>/dev/null; }
  trap cleanup EXIT
  RAMARG=127.0.0.1:$PORT; ENVSET="MESHTASTIC_RIBBIT=127.0.0.1:$PORT"
fi
env $ENVSET timeout 900 "$MCP/.venv/bin/python" "$EX/harness/bad_data.py" --binary "$BIN" \
    --workdir "$OUT/lab" --count "$N" --probe "$EX/bin/mtr-probe" --ram "$RAMARG" 2>&1 | tee "$OUT/bad_data.log"
RC=${PIPESTATUS[0]}
for l in "$OUT"/lab/*.log; do
  printf '== %s: writes %s reads %s facts-applied %s gone %s not-written(relay) %s undecodable %s\n' "$(basename "$l")" \
    "$(grep -c '\[MT_RIBBIT\] write' "$l")" "$(grep -c '\[MT_RIBBIT\] read id' "$l")" "$(grep -c '\[MT_RIBBIT\] applied' "$l")" \
    "$(grep -c 'gone: node removed' "$l")" "$(grep -c 'not written' "$l")" "$(grep -c 'does not decode\|unreadable' "$l")"
done
exit $RC
