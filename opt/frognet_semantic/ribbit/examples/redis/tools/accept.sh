#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# accept.sh -- acceptance for the Redis region: stock Redis 7.2.11 is stood up beside it and every test runs on BOTH.
# Part 1 is parity (does it do what Redis does), part 2 is what the region does that stock Redis cannot. Stock's
# result in part 2 is measured on every run, never written down from belief. Raw output per stage and arm goes to
# results/accept-<stamp>/; SUMMARY.txt collects the PASS/FAIL/RESULT/MEASURE lines. Compiler output goes to the terminal.
#
#   REDIS_SRC   a built redis-7.2.11 tree (make). Its src/redis-server is moved to src/redis-server.stock once, and
#               src/redis-server becomes a wrapper that runs whichever arm $ACCEPT_SERVER names, so Redis's suite
#               starts stock or the region from the same tree with the same tests.
#   STAGES      subset of: build metrics suite concurrency bench endpoints loss hostloss scale inet   (default: all but inet)
#   SUITE       Redis suite files (default: the ten qualify.sh runs)
#   K           endpoints for endpoints/scale (default: cores, at least 2)
#   RAM_ENDPOINT  host:port of a deployed Redis RAM host -- the inet stage only
set -u -o pipefail
cd "$(dirname "$0")/.."; ROOT=$PWD
command -v tclsh >/dev/null || { echo "accept FAIL: Redis's suite needs tclsh (apt-get install tcl)" >&2; exit 1; }
REDIS_SRC=${REDIS_SRC:?REDIS_SRC must name a built redis-7.2.11 tree}; REDIS_SRC=$(cd "$REDIS_SRC" && pwd)
# the suite puts unix sockets under REDIS_SRC/tests/tmp; a path near the kernel's 108-byte limit stops stock Redis from
# starting while the region (which opens no unix socket) runs on -- an unequal test. Refuse such a tree up front.
[ ${#REDIS_SRC} -le 40 ] || { echo "accept FAIL: REDIS_SRC path is ${#REDIS_SRC} characters; the suite's unix sockets need it 40 or less (e.g. /srv/redis-7.2.11)" >&2; exit 1; }
STAGES=${STAGES:-"build metrics suite concurrency bench endpoints loss hostloss scale"}
SUITE=${SUITE:-"unit/type/incr unit/type/string unit/type/hash unit/type/set unit/type/list unit/type/zset unit/keyspace unit/type/stream unit/type/stream-cgroups unit/multi"}
K=${K:-$(nproc)}; [ "$K" -lt 2 ] && K=2
OUT=$ROOT/results/accept-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"; SUMMARY=$OUT/SUMMARY.txt
has() { [[ " $STAGES " == *" $1 "* ]]; }
note() { echo "$*" | tee -a "$SUMMARY"; }
pick() { grep -aE "^(PASS|FAIL|RESULT|MEASURE)" "$1" | sed "s/^/  /" | tee -a "$SUMMARY"; }
STOCK=$REDIS_SRC/src/redis-server.stock
note "## Redis region acceptance $(basename $OUT) on $(uname -srm), $(nproc) cores"
note "## stock: $( { [ -x $STOCK ] && $STOCK --version; } || $REDIS_SRC/src/redis-server --version)"

if has build; then
  note "== build"
  if bash tools/build.sh |& tee "$OUT/build.txt"; then note "  $(tail -1 "$OUT/build.txt")"; else note "  RESULT FAIL build"; exit 1; fi
fi

# the wrapper: one tree, two arms
if file "$REDIS_SRC/src/redis-server" | grep -q ELF; then mv "$REDIS_SRC/src/redis-server" "$STOCK"; fi
cat > "$REDIS_SRC/src/redis-server" <<EOF
#!/bin/sh
# accept.sh: runs the arm ACCEPT_SERVER names -- stock ($STOCK) or ribbit ($ROOT/build/ribbit-redis-front)
case "\${ACCEPT_SERVER:-stock}" in
  stock)  exec "$STOCK" "\$@" ;;
  ribbit) exec "$ROOT/build/ribbit-redis-front" "\$@" \${RIBBIT_ARGS:-} ;;
  *) echo "ACCEPT_SERVER must be stock or ribbit" >&2; exit 2 ;;
esac
EOF
chmod +x "$REDIS_SRC/src/redis-server"
CLI=$REDIS_SRC/src/redis-cli

if has metrics; then
  note "== metrics (lines of code and cyclomatic complexity, stock src/ vs the region's src/)"
  python3 tests/accept_metrics.py "$REDIS_SRC" "$ROOT" > "$OUT/metrics.txt" 2>&1; cat "$OUT/metrics.txt" | tee -a "$SUMMARY"
fi

note "# PART 1 -- parity: the same tests on stock and on the region"
if has suite; then
  note "== suite: Redis 7.2.11's own tests, each file on both arms; a test green on stock and red on the region is a parity gap"
  for f in $SUITE; do
    for arm in stock ribbit; do
      ( cd "$REDIS_SRC" && ACCEPT_SERVER=$arm ./runtest --single $f --ignore-encoding --ignore-digest --durable --clients 1 --timeout 600 ) 2>&1 \
        | sed 's/\x1b\[[0-9;]*m//g' > "$OUT/suite-$arm-$(basename $f).txt"
    done
    s=$OUT/suite-stock-$(basename $f).txt; r=$OUT/suite-ribbit-$(basename $f).txt
    for x in $s $r; do   # a file that ran no test is a failure of the run, never a zero-gap result
      grep -aqE '^\[(ok|err)\]' $x || note "  RESULT FAIL suite $f: $(basename $x .txt) ran no test: $(tail -1 $x)"
    done
    # names, one per test (a few tests run under the same name twice -- once per encoding -- and count once here)
    okn() { grep -a '^\[ok\]' "$1" | sed 's/^\[ok\]: //; s/ ([0-9]* ms)$//' | sort -u; }
    errn() { grep -a '^\[err\]' "$1" | sed 's/^\[err\]: //; s/ in tests\/.*$//' | sort -u; }
    gaps=$(comm -23 <(okn "$s") <(okn "$r"))
    red=$(comm -12 <(printf '%s\n' "$gaps" | sed '/^$/d' | sort -u) <(errn "$r"))
    notrun=$(comm -23 <(printf '%s\n' "$gaps" | sed '/^$/d' | sort -u) <(errn "$r"))
    note "  $f  stock ok $(okn $s | wc -l) err $(errn $s | wc -l)   region ok $(okn $r | wc -l) err $(errn $r | wc -l)   green on stock only: $(printf '%s' "$gaps" | grep -c .) (red on the region $(printf '%s' "$red" | grep -c .), not run on the region $(printf '%s' "$notrun" | grep -c .))"
    [ -n "$red" ] && printf '%s\n' "$red" | sed 's/^/      stock-only, red on the region: /' >> "$SUMMARY"
    [ -n "$notrun" ] && printf '%s\n' "$notrun" | sed 's/^/      stock-only, not run on the region (the suite skipped it): /' >> "$SUMMARY"
  done
fi

if has concurrency; then
  note "== concurrency: every read-modify-write family hammered from several connections, Redis's guarantee checked"
  P=9800; mkdir -p /tmp/accept-stock-$P; $STOCK --port $P --save "" --appendonly no --bind 127.0.0.1 --dir /tmp/accept-stock-$P > /dev/null 2>&1 & S=$!
  for i in $(seq 1 100); do $CLI -p $P ping >/dev/null 2>&1 && break; sleep 0.1; done
  ENDPOINTS=$P python3 tests/run_concurrency.py build $P > "$OUT/concurrency-stock.txt" 2>&1; kill $S; wait $S 2>/dev/null
  note "  stock:"; grep -a '^RESULT' "$OUT/concurrency-stock.txt" | sed 's/^/    /' | tee -a "$SUMMARY"
  python3 tests/run_concurrency.py build 9810 > "$OUT/concurrency-ribbit.txt" 2>&1
  note "  region:"; grep -a '^RESULT' "$OUT/concurrency-ribbit.txt" | sed 's/^/    /' | tee -a "$SUMMARY"
  grep -a '^FAIL' "$OUT/concurrency-stock.txt" "$OUT/concurrency-ribbit.txt" | sed 's/^/    /' | tee -a "$SUMMARY"
fi

if has bench; then
  note "== bench: redis-benchmark, one endpoint each, same command lines (req/s; region/stock)"
  for arm in stock ribbit; do python3 tests/accept_beyond.py $arm scale 9820 "$REDIS_SRC" build 1 > "$OUT/bench-$arm.txt" 2>&1; done
  for t in SET GET; do
    s=$(grep -a "MEASURE stock $t" "$OUT/bench-stock.txt" | awk '{print $6}'); r=$(grep -a "MEASURE ribbit $t" "$OUT/bench-ribbit.txt" | awk '{print $6}')
    note "  $t  stock $s  region $r  ratio $(python3 -c "print('%.2f' % ($r/$s))" 2>/dev/null)"
  done
fi

note "# PART 2 -- beside stock as an operator would set it up: the same test on every arm, stock's result measured"
note "#   stock: persistence off (the region has none) | stock-aof: AOF on | stock-sentinel: AOF + Sentinel failover | ribbit: the region"
port=9840
for t in endpoints loss hostloss; do
  if has $t; then
    note "== $t"
    for arm in stock stock-aof stock-sentinel ribbit; do
      note "  $arm:"; python3 tests/accept_beyond.py $arm $t $port "$REDIS_SRC" build $([ $t = endpoints ] && echo $K || echo 2) > "$OUT/$t-$arm.txt" 2>&1
      grep -aE "^(PASS|FAIL|RESULT|MEASURE)" "$OUT/$t-$arm.txt" | sed "s/^/    /" | tee -a "$SUMMARY"
      grep -aqE "^RESULT" "$OUT/$t-$arm.txt" || note "    RESULT FAIL $arm $t: did not finish: $(tail -1 "$OUT/$t-$arm.txt")"
      port=$((port + 10))
    done
  fi
done
if has scale; then
  note "== scale: K endpoints at once, requests/s summed (stock SET can only go to its primary)"
  for k in $(seq 1 $K); do for arm in stock ribbit; do
    python3 tests/accept_beyond.py $arm scale 9880 "$REDIS_SRC" build $k > "$OUT/scale-$arm-$k.txt" 2>&1
    grep -a "^MEASURE" "$OUT/scale-$arm-$k.txt" | sed "s/^/  /" | tee -a "$SUMMARY"
  done; done
fi
if has inet; then
  note "== inet: two fronts here, one keyspace on the deployed RAM host"
  if [ -z "${RAM_ENDPOINT:-}" ]; then note "  RESULT FAIL inet: RAM_ENDPOINT is not set"
  else RAM_ENDPOINT=$RAM_ENDPOINT python3 tests/run_two_fronts.py build "$CLI" 9900 > "$OUT/inet.txt" 2>&1; pick "$OUT/inet.txt"; fi
fi
note "## results in $OUT"
