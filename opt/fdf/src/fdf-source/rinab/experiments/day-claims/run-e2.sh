#!/bin/bash
# run-e2.sh [PER_KIND] [CHANGES] -- [DAY_E2_TREE_V1] Experiment E2 against a fresh build/rina-host. Needs tools/build.sh
# first. Regenerates the scenarios from e2_ref.py (deterministic), then runs every participant against them. Scenarios,
# raw measurements, the host log and the build tag go to experiments/day-claims/runs/e2-<UTC time>/.
set -u
cd "$(dirname "$0")/../.."
PER=${1:-20}; CH=${2:-5}; PORT=${PORT:-52395}; API=/FawcettInnovations.RINA.ram_interface.php
OUT=experiments/day-claims/runs/e2-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
python3 -I experiments/day-claims/e2_ref.py selfcheck 25 > "$OUT/ref-selfcheck.txt" || { cat "$OUT/ref-selfcheck.txt"; echo "reference self-check failed"; exit 1; }
tail -1 "$OUT/ref-selfcheck.txt"
python3 -I experiments/day-claims/e2_ref.py scenarios "$OUT/scenarios.jsonl" "$PER" "$CH" || exit 1
F="-std=c++20 -O2 -Wall -Wextra -Wno-unused-parameter -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc"
g++ $F experiments/day-claims/e2_tree.cpp build/obj/frogram.o -llz4 -lcrypto -lresolv -o build/bin/e2-tree || { echo "e2_tree does not build"; exit 1; }
grep -o 'fdf-[0-9-]*-r[0-9]*' src/fdf_version.hpp | head -1 > "$OUT/BUILD"
RINA_REGION_FILE=$OUT/region.perm build/rina-host --listen 127.0.0.1:$PORT --api $API > "$OUT/host.log" 2>&1 & H=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.05; done
build/bin/e2-tree 127.0.0.1:$PORT "$$" "$OUT/scenarios.jsonl" "$OUT/raw.jsonl" |& tee "$OUT/out.txt"; rc=${PIPESTATUS[0]}
kill $H; wait $H 2>/dev/null
echo "evidence: $OUT"; exit $rc
