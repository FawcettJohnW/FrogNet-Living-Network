#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# run_suite.sh REDIS_TREE   run Redis's own test suite against Ribbit and tally the result
#
# Install the wrapper first (bench/install_wrapper.sh REDIS_TREE). Settings:
#   IO_THREADS  Ribbit event threads            (default 1)
#   CLIENTS     test files run in parallel      (default 4)
#   FILES       test files, space separated     (default: the data-structure and core files below)
#   OUT         log directory                   (default ./suite-<date>)
#
# Writes OUT/suite.log (the full runtest output) and OUT/summary.txt
# (ok / err totals, every [err] grouped by test file, any timeout or exception).
T="$1"
[ -n "$T" ] && [ -x "$T/runtest" ] || { echo "usage: $0 REDIS_TREE" >&2; exit 1; }
head -c 2 "$T/src/redis-server" | grep -q '#!' || { echo "$T/src/redis-server is stock Redis; run bench/install_wrapper.sh first" >&2; exit 1; }
IO_THREADS=${IO_THREADS:-1}; CLIENTS=${CLIENTS:-4}
FILES=${FILES:-"unit/type/string unit/type/incr unit/type/hash unit/type/set unit/type/list unit/type/list-2 unit/type/list-3 unit/type/zset unit/type/stream unit/type/stream-cgroups unit/keyspace unit/expire unit/bitops unit/bitfield unit/geo unit/hyperloglog unit/multi unit/sort unit/scan unit/protocol unit/auth unit/quit unit/info-command unit/introspection unit/introspection-2"}
OUT=${OUT:-$PWD/suite-$(date -u +%Y%m%d-%H%M%S)}
mkdir -p "$OUT"
ARGS=(); for f in $FILES; do ARGS+=(--single "$f"); done
echo "Ribbit, io-threads $IO_THREADS, $CLIENTS files in parallel, $(echo $FILES | wc -w) files -> $OUT"
( cd "$T" && RIBBIT_ARGS="--io-threads $IO_THREADS ${RIBBIT_ARGS}" ./runtest "${ARGS[@]}" \
    --ignore-encoding --ignore-digest --durable --clients "$CLIENTS" --timeout 600 ) > "$OUT/suite.log" 2>&1
python3 - "$OUT" <<'PY'
import re, sys, collections
out = sys.argv[1]; log = open(out + '/suite.log', errors='replace').read().splitlines()
ok = sum(1 for l in log if l.startswith('[ok]'))
errs = [l for l in log if l.startswith('[err]')]
bad = [l for l in log if re.search(r'\[(exception|timeout)\]|Killing still running|I/O error', l)]
byfile = collections.OrderedDict()
for l in errs:
    m = re.search(r' in (tests/\S+\.tcl)', l); byfile.setdefault(m.group(1) if m else '?', []).append(l)
s = ['ok %d   err %d   timeouts/exceptions %d' % (ok, len(errs), len(bad)), '']
for f, ls in byfile.items():
    s.append('%s  (%d)' % (f, len(ls))); s += ['   ' + l for l in ls]; s.append('')
if bad: s += ['TIMEOUTS / EXCEPTIONS'] + ['   ' + l for l in bad]
open(out + '/summary.txt', 'w').write('\n'.join(s) + '\n'); print('\n'.join(s[:1]))
print('details: %s/summary.txt' % out)
PY
