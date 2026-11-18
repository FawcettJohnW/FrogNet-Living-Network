#!/bin/bash
# matrix.sh <transport setting> <links> <seconds budget>: runs the topologies not yet run for that setting, one after
# another, until the budget is used. One result file per topology: /home/claude/logs/final/<tag>/<topology>.txt
set -u
T="$1"; L="$2"; BUDGET="${3:-520}"
tag=$(echo "${T}__${L}" | tr -c 'a-zA-Z0-9_\n' '_')
out=/home/claude/logs/final/$tag; mkdir -p "$out"
cd /home/claude/src/frognet/opt/frognet_semantic
export FROGNET_TREE=/home/claude/src/frognet; . /home/claude/handoff/handoff/env.sh >/dev/null 2>&1
start=$(date +%s)
for f in simulation/baselines/topo_*.json; do
  k=$(basename "$f" .json)
  [ -s "$out/$k.txt" ] && grep -q "^RESULT" "$out/$k.txt" && continue
  [ $(( $(date +%s) - start )) -gt "$BUDGET" ] && { echo "budget used; more to run"; break; }
  timeout 900 $PY simulation/wire_between_machines_tier.py --transport "$T" --links "$L" --topology "$k" > "$out/$k.txt" 2>&1
  grep -q "^RESULT" "$out/$k.txt" || { echo "RESULT FAIL (did not finish)" >> "$out/$k.txt"; $PY -c "
import sys,tempfile; sys.argv=['x']; sys.path.insert(0,'simulation')
import wire_between_machines_tier as T
T.tear_down([T.Machine(i,'m','10.0.0.1',(),tempfile.gettempdir()) for i in range(12)])" >/dev/null 2>&1; }
  echo "$k $(tail -1 "$out/$k.txt") pass=$(grep -c '\[PASS\]' "$out/$k.txt") fail=$(grep -c '\[FAIL\]' "$out/$k.txt") $(( $(date +%s) - start ))s"
done
echo "done so far: $(grep -l '^RESULT' $out/*.txt 2>/dev/null | wc -l) of 20; passing: $(grep -l '^RESULT PASS' $out/*.txt 2>/dev/null | wc -l)"
