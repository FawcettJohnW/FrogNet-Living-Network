#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# Ribbit-LISP qualification on a real machine.
# Builds everything, runs every gate the checkpoints report, and writes each stage's raw output to results/<stamp>/.
#
# Environment (all optional):
#   RIBBIT_RAM_HOST / RIBBIT_RAM_PORT  FNW1 shared-memory endpoint (default 127.0.0.1:8788)
#   RAM_SERVER        path to the LISP region's RAM host this script may start and restart (default ./ribbit_cpp/lisper-ram,
#                     a prebuilt x86-64 binary; on other architectures point this at your own FrogNet RAM server)
#   RIBBIT_RAM_EXTERNAL=1   use an already-running server at the endpoint; the script will not start, stop or restart it.
#                     Stages then share one memory, so earlier stages' cells remain (the tests clean up after themselves).
#   LISPERS_ROOT      path to an unpacked lispers.net tree (the directory containing lisp/lisp.py) for the byte oracle;
#                     the oracle stage is skipped if unset. Needs: pip install future
#   STAGES            space-separated subset of: build local fnw1 independent oracle handler boundary ryw failloud service stress lockfree perf bytes
#   RUNS              repeat count for the repeated independent-process tests (default 3)
set -u
cd "$(dirname "$0")/.."
ROOT=$(pwd)
# The Ribbit platform (the memory, FNW1, the RAM host) is built from ribbit/cpp, two levels up; RIBBIT points elsewhere.
RIBBIT=${RIBBIT:-$(cd "$ROOT/../../cpp" 2>/dev/null && pwd)}
[ -f "$RIBBIT/include/ram_host.hpp" ] || { echo "the Ribbit platform is not at ${RIBBIT:-../../cpp} (set RIBBIT to ribbit/cpp)"; exit 2; }
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$ROOT/results/$STAMP"; mkdir -p "$OUT"
export RIBBIT_RAM_HOST=${RIBBIT_RAM_HOST:-127.0.0.1}
export RIBBIT_RAM_PORT=${RIBBIT_RAM_PORT:-8788}
RAM_SERVER=${RAM_SERVER:-./ribbit_cpp/lisper-ram}
STAGES=${STAGES:-"build local fnw1 independent oracle handler client boundary ryw failloud service stress lockfree perf bytes"}
# (the platform's own stages -- semwire semcodec semtpl fnwp dataplane memory -- are ribbit/cpp/qualify.sh's)
# [STANDALONE_V1] A machine that is not a FrogNet node has neither tree. The package carries both references:
#   third_party/lispers.net/ Dino's lisp/lisp.py (Apache 2.0, LICENSE and NOTICE his) -- the byte oracle
# Either can still be pointed elsewhere (e.g. a node's own /opt/frognet_semantic) by setting the variable.
LISPERS_ROOT=${LISPERS_ROOT:-$(pwd)/third_party/lispers.net}
RUNS=${RUNS:-3}
PY=$(command -v python3 || command -v python)
SUMMARY="$OUT/SUMMARY.txt"
note(){ echo "$*" | tee -a "$SUMMARY"; }
has(){ case " $STAGES " in *" $1 "*) return 0;; *) return 1;; esac; }
CAPS=$(grep -o -- "--capabilities [^ ]*" run_ribbit_local.sh | cut -d' ' -f2)

fresh_server(){
  local i
  # every participant from an earlier stage is stopped first: stray participants corrupt later results
  pkill -x ribbit-lisp 2>/dev/null
  if [ "${RIBBIT_RAM_EXTERNAL:-0}" = "1" ]; then return 0; fi
  pkill -x "$(basename "$RAM_SERVER")" 2>/dev/null; sleep 0.3
  [ -x "$RAM_SERVER" ] || { note "RAM server $RAM_SERVER not executable; set RAM_SERVER or RIBBIT_RAM_EXTERNAL=1"; exit 2; }
  setsid nohup "$RAM_SERVER" --listen "0.0.0.0:$RIBBIT_RAM_PORT" > "$OUT/ram-server-$1.log" 2>&1 < /dev/null &
  for i in $(seq 1 50); do
    (exec 3<>/dev/tcp/$RIBBIT_RAM_HOST/$RIBBIT_RAM_PORT) 2>/dev/null && return 0; sleep 0.1
  done
  note "RAM server did not open $RIBBIT_RAM_HOST:$RIBBIT_RAM_PORT"; exit 2
}

PKG_VERSION=$(sed -n 's/^#define RIBBIT_LISP_VERSION "\(.*\)"/\1/p' ribbit_cpp/version.hpp)
banner(){ note ""; note "################################################################################"; for l in "$@"; do note "##  $l"; done; note "################################################################################"; note ""; }
banner "RIBBIT-LISP $PKG_VERSION" "package: $ROOT" "qualification $STAMP on $(uname -srm), $(nproc) cores" "RAM endpoint: $RIBBIT_RAM_HOST:$RIBBIT_RAM_PORT$([ "${RIBBIT_RAM_EXTERNAL:-0}" = 1 ] && echo '  (external server)' || echo '  (fresh local server per stage)')"

if has build; then
  note "== build"
  CXX=${CXX:-g++}
  # [ENGINES_IN_THE_SESSION_V1] the RAM server every later stage starts is built from the same tree as the client:
  # a prebuilt binary from before the engines went into the socket loop cannot speak to this session.
  # [BUILD_PROGRESS_V1] one line per program as it finishes, with its time, so a slow machine is visibly working.
  # [BUILD_PARALLEL_V1] every core: frogram.cpp is compiled ONCE (it was compiled into six programs), then the programs
  # build side by side, JOBS at a time (default: the core count). Each program's compiler output goes to its own log,
  # appended to build.txt at the end; any failure fails the stage and names the program.
  JOBS=${JOBS:-$(nproc 2>/dev/null || echo 2)}
  BLOG="$OUT/build.d"; mkdir -p "$BLOG"; : > "$OUT/build.txt"
  # Like make: a program whose output is newer than every C++ source and header in the package (and than frogram.o)
  # is not compiled again ("up to date"); REBUILD=1 compiles everything.
  # The compiler's output goes to the terminal as it happens (|& tee), and into build.txt as well -- never only to a file.
  # Compiler output goes to the terminal AS IT HAPPENS (each line prefixed with what is being built, since four
  # jobs run at once), and is kept in build.d/ as well. Nothing is hidden in a file.
  # GCC 12 (Raspberry Pi OS bookworm, aarch64) reports -Wfree-nonheap-object inside libstdc++'s new_allocator.h for
  # tools/semtpl_driver.cpp, which frees no non-heap object -- a GCC 12 false positive at -O2 (GCC 12 on x86-64 and GCC
  # 13 do not report it). The -Werror builds keep every other warning an error; this one diagnostic is off.
  WERR="-Wall -Wextra -Werror -Wno-free-nonheap-object"
  # That diagnostic is on by default in GCC -- not only with -Wall -- so it is off in EVERY build, not only the -Werror ones.
  NOFP="-Wno-free-nonheap-object"
  SRC_HASH=$(find ribbit_cpp tools client "$RIBBIT/include" "$RIBBIT/src" "$RIBBIT/tools" -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.h' \) 2>/dev/null | LC_ALL=C sort | xargs cat | sha256sum | cut -c1-32)
  build_one(){ local out=$1; shift; local t0=$(date +%s) name=$(basename "$out") log="$BLOG/$(basename "$out").txt"
    # like make: a program newer than every source and header of the package is up to date and is not compiled again
    # [BUILD_BY_CONTENT_V1] up to date only if built from exactly these sources -- this package's AND the Ribbit
    # platform's (ribbit/cpp). Content, not timestamps: files copied or unpacked keep the time they were written, which
    # can be older than programs already built here, and a platform change must rebuild every program that links it.
    if [ -z "${REBUILD:-}" ] && [ -e "$out" ] && [ "$(cat "$out.srchash" 2>/dev/null)" = "$SRC_HASH" ]; then
      printf '  up to date %-29s\n' "$out"; return 0; fi
    $CXX "$@" -o "$out" 2>&1 | sed -u "s|^|  $name: |" | tee "$log"; local rc=${PIPESTATUS[0]}
    if [ "$rc" = 0 ]; then echo "$SRC_HASH" > "$out.srchash"; printf '  built %-34s %4ss\n' "$out" "$(( $(date +%s)-t0 ))"
    else printf '  FAILED %-33s (compiler errors above)\n' "$out"; return 1; fi; }
  # The libraries everything links: if a header is missing, say which package provides it -- before compiling
  # [AUTO_INSTALL_BUILD_DEPS_V1] missing lz4 / OpenSSL headers are installed, not reported
  source "$(dirname "$(readlink -f "$0")")/../../../cpp/ensure_build_deps.sh"; ribbit_ensure_build_deps
  echo "  $JOBS parallel jobs"
  t_all=$(date +%s)
  build_one ribbit_cpp/frogram.o -std=c++17 $NOFP -O2 -pthread -I"$RIBBIT/include" -c "$RIBBIT/src/frogram.cpp" || { cat "$BLOG"/*.txt >> "$OUT/build.txt"; note "build FAIL"; exit 1; }
  FO=ribbit_cpp/frogram.o
  pids=()
  run(){ while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n 2>/dev/null || true; done; build_one "$@" & pids+=($!); }
  run ribbit_cpp/lisper-ram -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" ribbit_cpp/lisper_ram.cpp "$RIBBIT/src/ram_host.cpp" $FO -llz4 -lcrypto
  run ribbit_cpp/ribbit-lisp -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" ribbit_cpp/ribbit_lisp.cpp $FO -llz4 -lcrypto
  run tools/lisp-handler-driver -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" tools/lisp_handler_driver.cpp $FO -lcrypto -llz4
  run tools/check-ms-secret -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" tools/check_ms_secret.cpp $FO -llz4 -lcrypto
  run tools/check-site-secret -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" tools/check_site_secret.cpp $FO -llz4 -lcrypto
  run tools/lisp-boundary -std=c++17 $NOFP -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" tools/lisp_boundary.cpp $FO -lcrypto -llz4
  run tools/tuple-write -std=c++17 -O2 -pthread $WERR -Iribbit_cpp -I"$RIBBIT/include" "$RIBBIT/tools/tuple_write.cpp" $FO -llz4 -lcrypto
  # [VENDOR_CLIENT_V1] the LISP vendor's client library: lisper::Client, its C interface client/lisper.h
  mkdir -p build
  run build/liblisper.so -std=c++17 $NOFP -O2 -pthread -shared -fPIC $WERR -Iribbit_cpp -I"$RIBBIT/include" -Iclient client/lisper_c.cpp client/lisper_client.cpp "$RIBBIT/src/ram_client.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto
  run build/test-lisper-client -std=c++17 $NOFP -O2 -pthread $WERR -Iribbit_cpp -I"$RIBBIT/include" -Iclient client/tests/test_lisper_client.cpp client/lisper_client.cpp "$RIBBIT/src/ram_client.cpp" $FO -llz4 -lcrypto
  run tools/lisp-service -std=c++17 -O2 -pthread $WERR -Iribbit_cpp -I"$RIBBIT/include" tools/lisp_service.cpp $FO -lcrypto -llz4
  bfail=0; for pid in "${pids[@]}"; do wait "$pid" || bfail=1; done
  cat "$BLOG"/*.txt >> "$OUT/build.txt"
  echo "  build took $(( $(date +%s)-t_all ))s"
  [ $bfail = 0 ] && note "build PASS" || { note "build FAIL"; exit 1; }
  # [VERSION_BANNER_V1] what was built, and -- for an external server -- what the RAM host says it is
  CV=$(./ribbit_cpp/ribbit-lisp --version); SV=$(./ribbit_cpp/lisper-ram --version)
  if [ "${RIBBIT_RAM_EXTERNAL:-0}" = "1" ]; then
    HV=$(echo '{"operation":"version","args":{}}' | timeout 20 ./ribbit_cpp/ribbit-lisp --ram "$RIBBIT_RAM_HOST" "$RIBBIT_RAM_PORT" 2>&1 | $PY -c 'import sys,json
l=sys.stdin.readline()
try:
    r=json.loads(l); print(json.loads(r["result"])["ram_host"] if r.get("ok") else "(unreachable: %s)" % r.get("error"))
except Exception as e: print("(no answer: %s)" % (l.strip() or e))')
    [ "$HV" = "$PKG_VERSION" ] && M="matches" || M="DOES NOT MATCH THIS PACKAGE ($PKG_VERSION)"
    banner "client built here: $CV" "RAM host $RIBBIT_RAM_HOST:$RIBBIT_RAM_PORT says: $HV" "RAM host version $M"
  else
    banner "client built here: $CV" "RAM server built here: $SV (started fresh for each stage)"
  fi
fi
if false; then :
fi

if has local; then
  note "== local conformance"
  LISP_ALLOW_MUTATION=1 timeout 600 $PY run_conformance.py --adapter command --command './ribbit_cpp/ribbit-lisp' \
    --capabilities "$CAPS" > "$OUT/local.txt" 2>&1
  # the suite's verdict line (OK / FAILED), not the last line: [PER_OPERATION_TIMING_V1] prints its table after it
  note "local: $(grep -E '^(OK|FAILED)' "$OUT/local.txt" | tail -1)  (per-operation timing: local.txt)"
fi

if has fnw1; then
  note "== clean FNW1 conformance"
  fresh_server fnw1
  LISP_ALLOW_MUTATION=1 timeout 600 $PY run_conformance.py --adapter command \
    --command "./ribbit_cpp/ribbit-lisp --ram $RIBBIT_RAM_HOST $RIBBIT_RAM_PORT" --capabilities "$CAPS" > "$OUT/fnw1.txt" 2>&1
  # the suite's verdict line (OK / FAILED), not the last line: [PER_OPERATION_TIMING_V1] prints its table after it
  note "fnw1: $(grep -E '^(OK|FAILED)' "$OUT/fnw1.txt" | tail -1)  (per-operation timing: fnw1.txt)"
fi

if has independent; then
  note "== independent-process tests (each on a fresh server)"
  for t in test_held_resolver test_held_ddt test_held_map_cache test_held_database_mapping test_held_etr_request \
           test_etr_registrar test_etr_registrar_notify test_native_registration test_native_liveness test_native_regovernance; do
    pass=0
    for i in $(seq 1 $RUNS); do
      fresh_server "$t-$i"
      timeout 180 $PY tools/$t.py > "$OUT/$t-$i.txt" 2>&1 && grep -q "^PASS" "$OUT/$t-$i.txt" && pass=$((pass+1))
    done
    note "$t: $pass/$RUNS"
  done
  fresh_server cross
  timeout 60 $PY tools/cross_process_ram.py ./ribbit_cpp/ribbit-lisp "$RIBBIT_RAM_HOST" "$RIBBIT_RAM_PORT" > "$OUT/cross_process_ram.txt" 2>&1
  note "cross_process_ram: $(tr '\n' ' ' < "$OUT/cross_process_ram.txt")"
fi

if has oracle; then
  if [ -n "${LISPERS_ROOT:-}" ] && [ ! -f "$LISPERS_ROOT/lisp/lisp.py" ]; then
    note "== Dino byte oracle NOT RUN: $LISPERS_ROOT/lisp/lisp.py not found (LISPERS_ROOT must be the directory containing lisp/lisp.py)"
  elif [ -n "${LISPERS_ROOT:-}" ]; then
    note "== Dino byte oracle"
    timeout 120 $PY tools/test_control_wire_oracle.py "$LISPERS_ROOT" > "$OUT/oracle.txt" 2>&1
    note "oracle: $(grep -c '^PASS' "$OUT/oracle.txt") of 4 PASS"
    grep -q "No module named 'future'" "$OUT/oracle.txt" && note "oracle needs the Python 'future' module: pip install future"
  else note "== Dino byte oracle skipped (set LISPERS_ROOT)"; fi
fi

if has handler; then
  note "== LispHandler (C++ UnRESTHandler): election, advertise, codec, UDP boundary, site"
  fresh_server handler
  PYTHONDONTWRITEBYTECODE=1 timeout 300 $PY tools/test_lisp_handler.py > "$OUT/lisp-handler.txt" 2>&1
  grep -h '^FAIL\|^RESULT' "$OUT/lisp-handler.txt" | tee -a "$SUMMARY"
fi

if has stress; then
  note "== multi-record atomicity stress"
  for i in 1 2 3; do fresh_server "stress-$i"
    timeout 600 $PY tools/stress_multirecord_atomic.py ./ribbit_cpp/ribbit-lisp 600 > "$OUT/stress-$i.txt" 2>&1
    note "stress $i: $(tail -1 "$OUT/stress-$i.txt")"; done
fi

if has lockfree; then
  note "== runtime lock-freedom and reclamation"
  # every participant source: the engine moved to lisp_engine.hpp; ribbit_lisp.cpp alone would pass vacuously
  PART="ribbit_cpp/ribbit_lisp.cpp ribbit_cpp/lisp_engine.hpp ribbit_cpp/lisp_handler.hpp $RIBBIT/include/unrest_handler.hpp"
  n=$(cat $PART | grep -c 'std::mutex\|condition_variable\|lock_guard\|unique_lock\|atomic_load\|atomic_store\|shared_ptr')
  r=$(cat $PART | grep -c 'ram->remove\|Memory::remove\|\.remove(')
  note "source gates: participant locks/atomic shared_ptr $n, Memory::remove $r"
fi

if has perf; then
  note "== performance"
  for b in "bench_etr_request.py 20000" "bench_etr_registrar.py 200" "bench_notify_roundtrip.py 500" "bench_native_registration.py 100" "bench_native_regovernance.py 50"; do
    fresh_server "perf-${b%% *}"
    timeout 600 $PY tools/$b > "$OUT/perf-${b%% *}.txt" 2>&1
    grep -h "median" "$OUT/perf-${b%% *}.txt" | tee -a "$SUMMARY"
  done
fi

if has client; then
  note "== [VENDOR_CLIENT_V1] the LISP client library against lisper-ram: from C++, and from Python through ctypes"
  fresh_server client
  ./build/test-lisper-client "$RIBBIT_RAM_PORT" > "$OUT/client-cpp.txt" 2>&1; grep -E "^(PASS|FAIL|RESULT)" "$OUT/client-cpp.txt" | tee -a "$SUMMARY" > /dev/null; note "$(tail -1 "$OUT/client-cpp.txt")"
  $PY client/tests/test_lisper.py "$RIBBIT_RAM_PORT" > "$OUT/client-python.txt" 2>&1; grep -E "^(PASS|FAIL|RESULT)" "$OUT/client-python.txt" | tee -a "$SUMMARY" > /dev/null; note "$(tail -1 "$OUT/client-python.txt")"
fi

if has boundary; then
  note "== [BOUNDARY_NEVER_WAITS_V1] the Map-Server's UDP front: Map-Requests never wait behind Map-Registers (22 ms wire)"
  $RAM_SERVER --listen 127.0.0.1:8936 > "$OUT/ram-server-boundary.log" 2>&1 & BS=$!
  $PY "$RIBBIT/tests/shaped_relay.py" --listen 127.0.0.1:8937 --to 127.0.0.1:8936 --rate 4000000 --delay-ms 11 > "$OUT/boundary-relay.txt" 2>&1 & BR=$!
  sleep 0.8
  timeout 300 $PY tools/test_boundary_hol.py tools/lisp-boundary 127.0.0.1 8937 24342 > "$OUT/boundary.txt" 2> "$OUT/boundary-stderr.txt"
  tee -a "$SUMMARY" < "$OUT/boundary.txt"
  kill $BS $BR 2>/dev/null; wait $BS $BR 2>/dev/null
fi
if has ryw; then
  note "== [READ_YOUR_OWN_WRITE_V1] put() completes when this participant observes it: put -> get, delete -> get"
  fresh_server ryw
  timeout 300 $PY tools/test_read_your_write.py "$RIBBIT_RAM_HOST" "$RIBBIT_RAM_PORT" 40 > "$OUT/ryw.txt" 2>&1
  note "$(tail -1 "$OUT/ryw.txt")"
fi
if has failloud; then
  note "== [FAIL_ONCE_LOUDLY_V1] the RAM host goes away: one answer naming it, exit 3, no abort"
  timeout 120 $PY tools/test_fail_loudly.py "$RAM_SERVER" 8939 > "$OUT/failloud.txt" 2>&1
  note "$(tail -1 "$OUT/failloud.txt")"
fi
if has service; then
  note "== [LISP_SERVICE_V1] two machines from one package: capability tuples, discovery, native registration, heartbeat"
  timeout 180 $PY tools/test_lisp_service.py "$RAM_SERVER" 8940 14342 > "$OUT/service.txt" 2>&1
  grep -E "^(PASS|FAIL)" "$OUT/service.txt" | tee -a "$SUMMARY" > /dev/null; note "$(tail -1 "$OUT/service.txt")"
fi
if has bytes; then
  note "== bytes on the wire (FNW1; BLDC is not in this tree)"
  for m in measure_registration_bytes.py measure_native_change_bytes.py measure_liveness_bytes.py; do
    fresh_server "bytes-$m"; timeout 300 $PY tools/$m > "$OUT/$m.txt" 2>&1; tee -a "$SUMMARY" < "$OUT/$m.txt"
  done
fi

pkill -x ribbit-lisp 2>/dev/null
[ "${RIBBIT_RAM_EXTERNAL:-0}" = "1" ] || pkill -x "$(basename "$RAM_SERVER")" 2>/dev/null
note "results: $OUT"
