#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# qualify.sh -- the Ribbit platform's own qualification: builds the platform (the client library, the RAM host base
# class, ribbit-ram, tuple-write) and its tests, runs every platform gate, and writes each stage's output to
# results/<stamp>/. The vendors' examples qualify themselves (examples/*/tools/qualify.sh).
#
# Environment (all optional):
#   FROGNET_SEMANTIC_ROOT  the FrogNet semantic engine's Python, for the S1/S2/S3 oracles (default tests/reference)
#   STAGES            space-separated subset of: build semwire semcodec semtpl fnwp dataplane memory lockfree ramrows perf
#   JOBS              parallel compiles (default: this machine's cores)
set -u
cd "$(dirname "$0")"
ROOT=$(pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$ROOT/results/$STAMP"; mkdir -p "$OUT" bin
STAGES=${STAGES:-"build semwire semcodec semtpl fnwp dataplane memory lockfree ramrows perf"}
FROGNET_SEMANTIC_ROOT=${FROGNET_SEMANTIC_ROOT:-$ROOT/tests/reference}
PY=$(command -v python3 || command -v python)
SUMMARY="$OUT/SUMMARY.txt"
note(){ echo "$*" | tee -a "$SUMMARY"; }
has(){ case " $STAGES " in *" $1 "*) return 0;; *) return 1;; esac; }
CXX=${CXX:-g++}
JOBS=${JOBS:-$(nproc)}
# GCC 12 (Raspberry Pi OS bookworm, aarch64) reports -Wfree-nonheap-object inside libstdc++'s new_allocator.h for
# tests/semtpl_driver.cpp, which frees no non-heap object -- a GCC 12 false positive at -O2 (GCC 12 on x86-64 and GCC 13
# do not report it). Same fix as examples/lispers.net/tools/qualify.sh: every other warning stays an error; this one
# diagnostic is off in every build, -Werror or not (it is on by default in GCC, not only with -Wall).
WERR="-Wall -Wextra -Werror -Wno-free-nonheap-object"
NOFP="-Wno-free-nonheap-object"
note ""; note "################################################################################"
note "##  RIBBIT PLATFORM -- $(sed -n 's/^#define RIBBIT_PLATFORM_VERSION "\(.*\)"/\1/p' src/ram_host.cpp)"
note "##  qualification $STAMP on $(uname -srm), $(nproc) cores"
note "################################################################################"

if has build; then
  note "== build"
  BLOG="$OUT/build.d"; mkdir -p "$BLOG"
  build_one(){
    local out=$1; shift; local name; name=$(basename "$out"); local log="$BLOG/$name.txt"; local t0; t0=$(date +%s)
    $CXX "$@" -o "$out" 2>&1 | sed -u "s|^|  $name: |" | tee "$log"; local rc=${PIPESTATUS[0]}
    if [ "$rc" = 0 ]; then printf '  built %-34s %4ss\n' "$out" "$(( $(date +%s)-t0 ))"
    else printf '  FAILED %-33s (compiler errors above)\n' "$out"; return 1; fi; }
  # [AUTO_INSTALL_BUILD_DEPS_V1] missing lz4 / OpenSSL headers are installed, not reported
  source "$(dirname "$(readlink -f "$0")")/ensure_build_deps.sh"; ribbit_ensure_build_deps
  echo "  $JOBS parallel jobs"; t_all=$(date +%s)
  build_one bin/frogram.o -std=c++17 $NOFP -O2 -pthread -Iinclude -c src/frogram.cpp || { note "build FAIL"; exit 1; }
  FO=bin/frogram.o
  pids=()
  run(){ while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n 2>/dev/null || true; done; build_one "$@" & pids+=($!); }
  I="-Iinclude -Itests"
  run bin/ram_client.o -std=c++17 $NOFP -O2 -pthread $WERR $I -c src/ram_client.cpp
  run bin/ribbit-ram -std=c++17 $NOFP -O2 -pthread $WERR $I tools/ribbit_ram.cpp src/ram_host.cpp $FO -llz4 -lcrypto
  run bin/tuple-write -std=c++17 -O2 -pthread $WERR $I tools/tuple_write.cpp $FO -llz4 -lcrypto
  run bin/semwire-driver -std=c++17 -O2 $WERR $I tests/semwire_driver.cpp
  run bin/bench-semwire -std=c++17 $NOFP -O2 $I tests/bench_semwire.cpp
  run bin/semcodec-driver -std=c++17 -O2 $WERR $I tests/semcodec_driver.cpp -llz4
  run bin/bench-semcodec -std=c++17 $NOFP -O2 $I tests/bench_semcodec.cpp -llz4
  run bin/semtpl-driver -std=c++17 -O2 $WERR $I tests/semtpl_driver.cpp -llz4 -lcrypto
  run bin/bench-semtpl -std=c++17 $NOFP -O2 $I tests/bench_semtpl.cpp -llz4 -lcrypto
  run bin/ramrows-driver -std=c++17 $NOFP -O2 $I tests/ramrows_driver.cpp $FO -llz4 -lcrypto
  run bin/test-lockfree-snapshot -std=c++17 $NOFP -O2 -pthread $I tests/test_lockfree_snapshot.cpp
  run bin/test-ebr-stress -std=c++17 $NOFP -O2 -pthread $I tests/test_ebr_stress.cpp
  run bin/test-ram-memory -std=c++17 -O2 -pthread $WERR $I tests/test_ram_memory.cpp
  run bin/test-write-window -std=c++17 -O2 -pthread $WERR $I tests/test_write_window.cpp
  run bin/test-row-scan-bound -std=c++17 -O2 -pthread $WERR $I tests/test_row_scan_bound.cpp
  run bin/test-fnwp-e2e -std=c++17 -O2 -pthread $WERR $I tests/test_fnwp_e2e.cpp src/frogram.cpp -llz4 -lcrypto
  run bin/test-session-tcp -std=c++17 -O2 -pthread $WERR $I tests/test_session_tcp.cpp src/frogram.cpp -llz4 -lcrypto
  run bin/test-fanin-data -std=c++17 -O2 -pthread $WERR $I tests/test_fanin_data.cpp src/frogram.cpp -llz4 -lcrypto
  bfail=0; for pid in "${pids[@]}"; do wait "$pid" || bfail=1; done
  cat "$BLOG"/*.txt >> "$OUT/build.txt"
  echo "  build took $(( $(date +%s)-t_all ))s"
  [ $bfail = 0 ] && note "build PASS" || { note "build FAIL"; exit 1; }
fi

oracle(){   # name, python module that must exist under FROGNET_SEMANTIC_ROOT, oracle script, mutants script, timeout
  if [ ! -f "$FROGNET_SEMANTIC_ROOT/$2" ]; then note "== $1 oracle NOT RUN: $FROGNET_SEMANTIC_ROOT/$2 not found"; return; fi
  note "== $1 oracle (C++ against the FrogNet Python reference, $2)"
  PYTHONDONTWRITEBYTECODE=1 timeout 900 $PY "tests/$3" "$FROGNET_SEMANTIC_ROOT" > "$OUT/$1.txt" 2>&1
  grep -h '^PASS\|^FAIL\|^RESULT' "$OUT/$1.txt" | tee -a "$SUMMARY"
  grep -q "No module named 'lz4'" "$OUT/$1.txt" && note "$1 oracle needs the Python 'lz4' module: pip install lz4"
  PYTHONDONTWRITEBYTECODE=1 timeout "$5" "tests/$4" "$FROGNET_SEMANTIC_ROOT" > "$OUT/$1-mutants.txt" 2>&1
  note "$1 $(tail -1 "$OUT/$1-mutants.txt")"
}
has semwire  && oracle semwire  core/semcache_wire.py test_semwire_oracle.py  semwire_mutants.sh  1800
has semcodec && oracle semcodec core/codec.py         test_semcodec_oracle.py semcodec_mutants.sh 3600
has semtpl   && oracle semtpl   core/json_handler.py  test_semtpl_oracle.py   semtpl_mutants.sh   3600

if has fnwp; then
  note "== FNWP engines end to end on the RAM wire (ClientEngine <-> ServerEngine over the host's api())"
  for s in 1 2 3; do
    # [RAM_INTERFACE_IS_THE_VENDORS_V1] the endpoint is the vendor's: FrogNet's default and a vendor-named one
    for ep in /ram.php /lisper-api; do
      tag=$(echo "$ep" | tr -c 'A-Za-z0-9.\n' '_')
      timeout 600 ./bin/test-fnwp-e2e $s "$ep" > "$OUT/fnwp-e2e-seed$s$tag.txt" 2>&1
      grep -E "^endpoint|^reads|^storm|^RESULT" "$OUT/fnwp-e2e-seed$s$tag.txt" | tee -a "$SUMMARY"
    done
  done
fi

if has dataplane; then
  note "== [ENGINES_IN_THE_SESSION_V1] + [HIGH_SPEED_DATA_SOCKET_V1]: frogram::Session <-> the RAM host over real TCP, then fan-in over a shaped wire"
  for ep in /ram.php /lisper-api; do
    tag=$(echo "$ep" | tr -c 'A-Za-z0-9.\n' '_')
    timeout 600 ./bin/test-session-tcp 8931 "$ep" > "$OUT/session-tcp$tag.txt" 2>&1; grep -E "^RESULT|^FAIL" "$OUT/session-tcp$tag.txt" | tee -a "$SUMMARY"
  done
  $PY tests/shaped_relay.py --listen 127.0.0.1:8933 --to 127.0.0.1:8932 --rate 4000000 --delay-ms 10 > "$OUT/relay.txt" 2>&1 & RPID=$!
  # [RELAY_READY_BEFORE_FANIN_V1] Wait until the relay is LISTENING, not a fixed half second: on a Raspberry Pi the
  # Python relay was not up in 0.5 s, the test's connect to 8933 was refused, and it aborted with frogram::Unreachable
  # (20261001-102146). Checked with ss, not by connecting -- a probe connection would itself be relayed to 8932, where
  # nothing listens until the test starts. A relay that exits, or is not listening within 30 s, stops the stage with
  # its log; the test never runs against a relay that is not there.
  relay_up=0
  for _i in $(seq 1 300); do
    kill -0 "$RPID" 2>/dev/null || break
    if ss -ltnH 'sport = :8933' 2>/dev/null | grep -q .; then relay_up=1; break; fi
    sleep 0.1
  done
  if [ "$relay_up" != 1 ]; then
    note "RESULT FAIL fan-in: the shaped relay on 127.0.0.1:8933 is not listening (relay log: $OUT/relay.txt)"
    sed 's/^/  relay: /' "$OUT/relay.txt" | tail -n 20
  else
  timeout 900 ./bin/test-fanin-data 8932 8933 /lisper-api 2000000 > "$OUT/fanin.txt" 2>&1
  grep -E "^RESULT|^FAIL|interleave|slowest" "$OUT/fanin.txt" | tee -a "$SUMMARY"
  fi
  kill $RPID 2>/dev/null; wait $RPID 2>/dev/null
fi

if has memory; then
  note "== [ROW_LOCKS_V1] the RAM host's memory: row locks only, triggers on envelopes and on the space"
  timeout 300 ./bin/test-ram-memory include/ram_memory.hpp > "$OUT/memory.txt" 2>&1
  grep -a -E "^RAM-MEMORY" "$OUT/memory.txt" | while read -r l; do note "$l"; done
  # The write window and the row scan race writers against readers; one run proves little (the row-scan miss showed on
  # a Pi 5 about 2 runs in 20). Each is run repeatedly and the report says how many runs passed, with the first failure.
  repeat_race(){   # label, runs, command...
    local label=$1 runs=$2; shift 2; local ok=0 i first=""
    for i in $(seq 1 "$runs"); do
      if timeout 300 "$@" > "$OUT/race-run.txt" 2>&1; then ok=$((ok+1))
      else [ -z "$first" ] && first=$(grep -a -m1 "^FAIL" "$OUT/race-run.txt"); fi
      cat "$OUT/race-run.txt" >> "$OUT/memory.txt"
    done
    rm -f "$OUT/race-run.txt"
    if [ "$ok" = "$runs" ]; then note "$label PASS: $ok of $runs runs"
    else note "$label FAIL: $ok of $runs runs passed -- first failure: ${first:-see $OUT/memory.txt}"; fi; }
  repeat_race WRITE-WINDOW 20 ./bin/test-write-window
  repeat_race ROW-SCAN-BOUND 5 ./bin/test-row-scan-bound
fi

if has lockfree; then
  note "== runtime lock-freedom and reclamation"
  note "$(./bin/test-lockfree-snapshot 2>&1 | tail -1)"
  note "$(timeout 300 ./bin/test-ebr-stress 2>&1 | tail -1)"
fi

if has ramrows; then
  note "== the RAM-answer handler (ramrows.hpp) against ribbit-ram's real answers"
  ./bin/ribbit-ram --listen 127.0.0.1:8788 > "$OUT/ribbit-ram-ramrows.log" 2>&1 & RR=$!; sleep 0.5
  timeout 300 $PY tests/test_ramrows.py 8788 > "$OUT/ramrows.txt" 2>&1; note "$(tail -1 "$OUT/ramrows.txt")"
  kill $RR 2>/dev/null; wait $RR 2>/dev/null
fi

if has perf; then
  note "== performance: the semantic wire and codec, C++ and the Python reference"
  note "-- semwire S1 encode/parse, C++"; ./bin/bench-semwire > "$OUT/perf-semwire.txt" 2>&1; tee -a "$SUMMARY" < "$OUT/perf-semwire.txt"
  note "-- semcodec S2 codec, C++"; ./bin/bench-semcodec > "$OUT/perf-semcodec.txt" 2>&1; tee -a "$SUMMARY" < "$OUT/perf-semcodec.txt"
  if [ -f "$FROGNET_SEMANTIC_ROOT/core/codec.py" ]; then
    PYTHONDONTWRITEBYTECODE=1 $PY tests/bench_semwire_python.py "$FROGNET_SEMANTIC_ROOT" 2>&1 | grep -v '^\[codec\]' > "$OUT/perf-semwire-python.txt"
    note "-- semwire S1, Python reference"; tee -a "$SUMMARY" < "$OUT/perf-semwire-python.txt"
    PYTHONDONTWRITEBYTECODE=1 $PY tests/bench_semcodec_python.py "$FROGNET_SEMANTIC_ROOT" 2>&1 | grep -v '^\[codec\]' > "$OUT/perf-semcodec-python.txt"
    note "-- semcodec S2, Python reference"; tee -a "$SUMMARY" < "$OUT/perf-semcodec-python.txt"
  fi
fi
note "results: $OUT"
