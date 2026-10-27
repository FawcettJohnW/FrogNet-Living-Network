#!/bin/bash
# run-e1.sh [SEED ...] -- [DAY_E1_SELECT_V1] Experiment E1 against a fresh build/rina-host per seed. Needs tools/build.sh
# first. Raw measurements, the host log and the build tag go to experiments/day-claims/runs/e1-<UTC time>/.
# Exit 0 only if every seed passes. EVENTS (default 300) is the number of serial changes per seed.
set -u
cd "$(dirname "$0")/../.."
SEEDS=("$@"); [ ${#SEEDS[@]} -eq 0 ] && SEEDS=(1 2 3)
EVENTS=${EVENTS:-300}; PORT=${PORT:-52295}; API=/FawcettInnovations.RINA.ram_interface.php
OUT=experiments/day-claims/runs/e1-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
F="-std=c++20 -O2 -Wall -Wextra -Wno-unused-parameter -pthread -D_GNU_SOURCE -Ibuild/platform/ribbit_cpp -Isrc"
g++ $F experiments/day-claims/e1_select.cpp build/obj/frogram.o -llz4 -lcrypto -lresolv -o build/bin/e1-select || { echo "e1_select does not build"; exit 1; }
grep -o 'fdf-[0-9-]*-r[0-9]*' src/fdf_version.hpp | head -1 > "$OUT/BUILD"
bad=0
for s in "${SEEDS[@]}"; do
    RINA_REGION_FILE=$OUT/region-$s.perm build/rina-host --listen 127.0.0.1:$PORT --api $API > "$OUT/host-$s.log" 2>&1 & H=$!
    for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.05; done
    echo "== seed $s"
    build/bin/e1-select 127.0.0.1:$PORT "s$s-$$" "$s" "$EVENTS" "$OUT/raw-$s.jsonl" |& tee "$OUT/out-$s.txt"; [ ${PIPESTATUS[0]} = 0 ] || bad=$((bad + 1))
    kill $H; wait $H 2>/dev/null
done
echo "evidence: $OUT"
[ $bad = 0 ] && echo "RESULT PASS (${#SEEDS[@]} seeds)" || { echo "RESULT FAIL ($bad of ${#SEEDS[@]} seeds)"; exit 1; }
