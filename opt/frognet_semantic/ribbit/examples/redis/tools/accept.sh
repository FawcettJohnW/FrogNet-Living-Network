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
#   STAGES      subset of: build metrics suite concurrency bench clients warm endpoints loss hostloss scale warmscale inet   (default: all but inet)
#   SUITE       Redis suite files (default: the ten qualify.sh runs)
#   K           endpoints for endpoints/scale (default: cores, at least 2)
#   RAM_ENDPOINT  host:port of a deployed memory (ribbit-redis-host) -- the inet stage only
set -u -o pipefail
cd "$(dirname "$0")/.."; ROOT=$PWD
command -v tclsh >/dev/null || { echo "accept FAIL: Redis's suite needs tclsh (apt-get install tcl)" >&2; exit 1; }
REDIS_SRC=${REDIS_SRC:?REDIS_SRC must name a built redis-7.2.11 tree}; REDIS_SRC=$(cd "$REDIS_SRC" && pwd)
# the suite puts unix sockets under REDIS_SRC/tests/tmp; a path near the kernel's 108-byte limit stops stock Redis from
# starting while the region (which opens no unix socket) runs on -- an unequal test. Refuse such a tree up front.
[ ${#REDIS_SRC} -le 40 ] || { echo "accept FAIL: REDIS_SRC path is ${#REDIS_SRC} characters; the suite's unix sockets need it 40 or less (e.g. /srv/redis-7.2.11)" >&2; exit 1; }
STAGES=${STAGES:-"build metrics suite concurrency bench clients endpoints loss hostloss scale"}
SUITE=${SUITE:-"unit/type/incr unit/type/string unit/type/hash unit/type/set unit/type/list unit/type/zset unit/keyspace unit/type/stream unit/type/stream-cgroups unit/multi"}
K=${K:-$(nproc)}; [ "$K" -lt 2 ] && K=2
OUT=$ROOT/results/accept-$(date +%Y%m%d-%H%M%S); mkdir -p "$OUT"; SUMMARY=$OUT/SUMMARY.txt
has() { [[ " $STAGES " == *" $1 "* ]]; }
note() { echo "$*" | tee -a "$SUMMARY"; }
pick() { grep -aE "^(PASS|FAIL|RESULT|MEASURE)" "$1" | sed "s/^/  /" | tee -a "$SUMMARY"; }
STOCK=$REDIS_SRC/src/redis-server.stock
note "## Redis region acceptance $(basename $OUT) on $(uname -srm), $(nproc) cores"
note "## stock: $( { [ -x $STOCK ] && $STOCK --version; } || $REDIS_SRC/src/redis-server --version)"
note "## region build: $(cat "$ROOT/VERSION" 2>/dev/null || git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

if has build; then
  note "== build"
  if bash tools/build.sh |& tee "$OUT/build.txt"; then note "  $(tail -1 "$OUT/build.txt")"; else note "  RESULT FAIL build"; exit 1; fi
fi

# the wrapper: one tree, two arms
if file "$REDIS_SRC/src/redis-server" | grep -q ELF; then mv "$REDIS_SRC/src/redis-server" "$STOCK"; fi
cat > "$REDIS_SRC/src/redis-server" <<EOF
#!/bin/sh
# accept.sh: runs the arm ACCEPT_SERVER names -- stock ($STOCK) or ribbit ($ROOT/build/ribbit-redis-front, a front on a memory of its own)
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
  if python3 tests/accept_metrics.py "$REDIS_SRC" "$ROOT" > "$OUT/metrics.txt" 2>&1; then cat "$OUT/metrics.txt" | tee -a "$SUMMARY"
  else note "  RESULT FAIL metrics: $(tail -1 "$OUT/metrics.txt")"; fi
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
    # tests by name AND occurrence: some tests run twice under one name (once per encoding); the second run is "<name> (run 2)",
    # so a test that fails in one encoding only is still a gap (counting names once hid one on the Pi 5)
    nth() { awk '{c[$0]++; print (c[$0] > 1 ? $0 " (run " c[$0] ")" : $0)}' | sort; }
    okn() { grep -a '^\[ok\]' "$1" | sed 's/^\[ok\]: //; s/ ([0-9]* ms)$//' | nth; }
    errn() { grep -a '^\[err\]' "$1" | sed 's/^\[err\]: //; s/ in tests\/.*$//' | nth; }
    gaps=$(comm -23 <(okn "$s") <(okn "$r"))
    red=$(comm -12 <(printf '%s\n' "$gaps" | sed '/^$/d' | sort) <(errn "$r"))
    notrun=$(comm -23 <(printf '%s\n' "$gaps" | sed '/^$/d' | sort) <(errn "$r"))
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

# [REPEATED_MEASURE_V1] A single benchmark run swings +-25% on a shared machine (six runs of one command on a fresh
# stock server: 602k..980k SET/s), so every throughput figure is REPEAT runs, stock and region interleaved, reported as
# median [min..max] with the CPU steal ticks the runs saw.
REPEAT=${REPEAT:-5}
measure_k() {   # $1 K, $2 tag -> MEASURE lines appended to $OUT/$2-measures.txt
  for r in $(seq 1 $REPEAT); do for arm in stock ribbit; do
    s1=$(awk '/^cpu /{print $9}' /proc/stat)
    python3 tests/accept_beyond.py $arm scale $((9880 + 10 * r)) "$REDIS_SRC" build $1 > "$OUT/$2-$arm-$1-$r.txt" 2>&1
    s2=$(awk '/^cpu /{print $9}' /proc/stat)
    grep -a "^MEASURE" "$OUT/$2-$arm-$1-$r.txt" | sed "s/\$/ steal=$((s2 - s1))/" >> "$OUT/$2-measures.txt"
    grep -aq "^RESULT PASS" "$OUT/$2-$arm-$1-$r.txt" || note "  RESULT FAIL $2 $arm K=$1 run $r: $(tail -1 "$OUT/$2-$arm-$1-$r.txt")"
  done; done
}
report() {      # $1 tag
  python3 - "$OUT/$1-measures.txt" <<'PY' | tee -a "$SUMMARY"
import re, statistics, sys
rows = {}
for l in open(sys.argv[1]):
    m = re.match(r"MEASURE (\S+) (\S+) K=(\d+) total (\d+) req/s.* steal=(\d+)", l)
    if m: rows.setdefault((int(m.group(3)), m.group(2), m.group(1)), []).append((int(m.group(4)), int(m.group(5))))
print("  %-4s %-4s %28s %28s %7s %6s" % ("K", "cmd", "stock median [min..max]", "region median [min..max]", "ratio", "steal"))
for k, cmd in sorted({(k, c) for k, c, _ in rows}):
    s, r = rows.get((k, cmd, "stock"), []), rows.get((k, cmd, "ribbit"), [])
    if not s or not r: continue
    f = lambda v: "%9.0f [%7.0f..%7.0f]" % (statistics.median(x for x, _ in v), min(x for x, _ in v), max(x for x, _ in v))
    print("  %-4d %-4s %28s %28s %7.2f %6d" % (k, cmd, f(s), f(r), statistics.median(x for x, _ in r) / statistics.median(x for x, _ in s), sum(t for _, t in s + r)))
PY
}
if has bench; then
  note "== bench: redis-benchmark -c 50 -P 16 -r 100000, one endpoint each, $REPEAT runs interleaved (req/s)"
  measure_k 1 bench; report bench
fi

# [MANY_CLIENTS_V1] John 2026-10-14: "we can't change the client, but we can add a test that runs a hundred of them at
# the same time". Unpipelined clients (-P 1), as most real clients are: one at a time, then a hundred at once.
if has clients; then
  note "== clients: redis-benchmark -P 1 (each client one command at a time), one endpoint, $REPEAT runs interleaved (req/s)"
  for c in 1 100; do
    note "  $c client(s) at once:"
    BENCH_C=$c BENCH_P=1 BENCH_N=$([ $c = 1 ] && echo 20000 || echo 200000) measure_k 1 clients$c; report clients$c
  done
fi

# [WARM_READS_V1] a server that has been running has been read: the same reads again, after one unmeasured pass, on
# both arms (stock gains nothing from it; a front answers from what it has read). GET only: a SET is never local.
if has warm; then
  note "== warm: redis-benchmark GET after one unmeasured pass of the same run, one endpoint, $REPEAT runs interleaved (req/s)"
  note "  50 clients, -P 16:"
  BENCH_WARM=1 BENCH_C=50 BENCH_P=16 BENCH_N=400000 BENCH_TESTS=get measure_k 1 warm16; report warm16
  note "  100 clients, -P 1:"
  BENCH_WARM=1 BENCH_C=100 BENCH_P=1 BENCH_N=200000 BENCH_TESTS=get measure_k 1 warm1; report warm1
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
  note "== scale: K endpoints at once, requests/s summed, $REPEAT runs interleaved (stock SET can only go to its primary)"
  for k in $(seq 1 $K); do measure_k $k scale; done; report scale
fi
if has warmscale; then
  note "== warmscale: K endpoints at once, GET after one unmeasured pass, requests/s summed, $REPEAT runs interleaved"
  for k in $(seq 1 $K); do BENCH_WARM=1 BENCH_TESTS=get measure_k $k warmscale; done; report warmscale
fi
if has inet; then
  note "== inet: the two-front checks with both fronts here and the memory deployed elsewhere"
  if [ -z "${RAM_ENDPOINT:-}" ]; then note "  RESULT FAIL inet: RAM_ENDPOINT is not set"
  else MEMORY=$RAM_ENDPOINT python3 tests/run_two_fronts.py build 9900 > "$OUT/inet.txt" 2>&1; pick "$OUT/inet.txt"; fi
fi
note "## results in $OUT"
