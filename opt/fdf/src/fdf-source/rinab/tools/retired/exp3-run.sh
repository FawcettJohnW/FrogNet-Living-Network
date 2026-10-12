#!/bin/bash
# exp3-run.sh -- [EXP3_TOPOLOGY_RIG_V1] FD&F experiment 3: all 20 of FrogNet's discovery topologies through topo-rig.py,
# one JSON line each to evidence/exp3/mode-b.jsonl (appended as each finishes). Run as root, on its own.
H=$(cd "$(dirname "$0")/.." && pwd); OUT=$H/evidence/exp3/mode-b.jsonl; mkdir -p "$(dirname "$OUT")"; : > "$OUT"
for i in $(seq 0 19); do timeout 400 python3 "$H/tools/topo-rig.py" "$H/experiments/exp3-topologies.json" $i >> "$OUT" 2>> "$H/evidence/exp3/mode-b.err"; done
echo "done" >> "$H/evidence/exp3/mode-b.err"
