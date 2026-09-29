#!/bin/bash
# Redis region on the RAM host -- qualification. Each stage's raw output goes to results/<stamp>/<stage>.txt; SUMMARY.txt
# collects the RESULT lines. Compiler output goes to the terminal as it happens.
#   REDIS_SRC     a built redis-7.2.11 tree (its tests/ and src/redis-cli, src/redis-benchmark), required for suite stages
#   STAGES        subset of: build oracles suite inet deploy     (default: all)
#   SUITE         suite files for the suite and inet stages (default: the ones this package has matched the control on)
#   RAM_ENDPOINT  host:port of the Redis RAM host on the deployment (streamingfrog) -- required by the deploy stage;
#                 there is no local substitute. RIBBIT_REDIS_ADMIN is NOT needed for deploy (nothing is configured).
set -u -o pipefail
cd "$(dirname "$0")/.."; ROOT=$PWD
STAGES=${STAGES:-"build oracles suite inet deploy"}; RAM_ENDPOINT=${RAM_ENDPOINT:-}; REDIS_SRC=${REDIS_SRC:-}
SUITE=${SUITE:-"unit/type/incr unit/type/string unit/type/hash unit/type/set unit/type/list unit/type/zset unit/keyspace unit/type/stream unit/type/stream-cgroups unit/multi"}
OUT=results/$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"; SUMMARY=$OUT/SUMMARY.txt
has() { [[ " $STAGES " == *" $1 "* ]]; }
note() { echo "$*" | tee -a "$SUMMARY"; }
note "## Redis region on the RAM host, qualification $(basename $OUT) on $(uname -srm), $(nproc) cores"
if has build; then note "== build"; if bash tools/build.sh |& tee "$OUT/build.txt"; then note "$(tail -1 "$OUT/build.txt")"; else note "RESULT FAIL build"; exit 1; fi; fi
CLI=${REDIS_SRC:+$REDIS_SRC/src/redis-cli}
stage() { local name=$1; shift; note "== $name"; "$@" > "$OUT/$name.txt" 2>&1; grep -aE "^(FAIL|RESULT)" "$OUT/$name.txt" | sed "s/^/  /" | tee -a "$SUMMARY"; }
if has oracles; then
  [ -n "$CLI" ] || { note "RESULT FAIL oracles: REDIS_SRC not set (redis-cli needed)"; }
  [ -n "$CLI" ] && stage two-fronts python3 tests/run_two_fronts.py build "$CLI" 9611
  [ -n "$CLI" ] && stage trust python3 tests/run_trust.py build "$CLI" 9631
  [ -n "$CLI" ] && stage reconnect python3 tests/run_reconnect.py build "$CLI" 9651
  stage same-miss python3 tests/run_same_miss.py build/ribbit-redis-front 9671
fi
suite() {   # $1 name, $2 the program Redis's suite starts as src/redis-server
  [ -n "$REDIS_SRC" ] || { note "== $1"; note "  RESULT FAIL $1: REDIS_SRC not set"; return; }
  for f in $SUITE; do
    note "== $1 $f"
    ( cd "$REDIS_SRC" && RIBBIT_REDIS="$2" RIBBIT_BUILD="$ROOT/build" ./runtest --single $f --ignore-encoding --ignore-digest --durable --clients 1 --timeout 600 ) 2>&1 \
      | sed 's/\x1b\[[0-9;]*m//g' > "$OUT/$1-$(basename $f).txt"
    note "  ok $(grep -ac '^\[ok\]' "$OUT/$1-$(basename $f).txt") err $(grep -ac '^\[err\]' "$OUT/$1-$(basename $f).txt") $(grep -aoE 'TIMEOUT|exception' "$OUT/$1-$(basename $f).txt" | head -1)"
  done
}
if has suite; then suite suite "$ROOT/build/ribbit-redis-front"; fi
if has inet; then SUITE=${INET_SUITE:-"unit/type/incr unit/type/string"} suite inet "$ROOT/tests/internet_wrapper.py"; fi
if has deploy; then
  note "== deploy (two fronts here, connected up to the deployment's RAM host)"
  if [ -z "$RAM_ENDPOINT" ]; then note "  RESULT FAIL deploy: RAM_ENDPOINT is not set (the deployment's Redis RAM host, e.g. streamingfrog:PORT); no local substitute"
  elif [ -z "$CLI" ]; then note "  RESULT FAIL deploy: REDIS_SRC not set"
  else
    RAM_ENDPOINT=$RAM_ENDPOINT python3 tests/run_two_fronts.py build "$CLI" 9691 > "$OUT/deploy-two-fronts.txt" 2>&1
    grep -aE "^(FAIL|RESULT)" "$OUT/deploy-two-fronts.txt" | sed "s/^/  /" | tee -a "$SUMMARY"
    FROGNET_BLOB_ROOT=/tmp/ribbit-deploy-blobs RIBBIT_RAM=$RAM_ENDPOINT build/ribbit-redis-front --port 9699 --save "" > "$OUT/deploy-front.log" 2>&1 & F=$!
    for i in $(seq 1 100); do $CLI -p 9699 ping >/dev/null 2>&1 && break; sleep 0.1; done
    { $REDIS_SRC/src/redis-cli -p 9699 --latency --raw -i 1 2>/dev/null & L=$!; sleep 5; kill $L; } > "$OUT/deploy-latency.txt" 2>&1
    note "  latency to the deployment (min max avg samples ms): $(head -1 "$OUT/deploy-latency.txt")"
    for spec in "-c 1 -P 1 -n 500" "-c 50 -P 1 -n 5000" "-c 50 -P 16 -n 100000"; do
      $REDIS_SRC/src/redis-benchmark -p 9699 -q $spec -t set,get -r 100000 2>&1 | tr '\r' '\n' | grep "requests per" | sed "s/^/  [$spec] /" | tee -a "$SUMMARY"
    done
    kill $F
  fi
fi
note "## results in $OUT"
