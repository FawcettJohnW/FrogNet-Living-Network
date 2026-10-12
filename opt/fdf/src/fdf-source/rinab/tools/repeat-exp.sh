#!/bin/bash
# repeat-exp.sh RLITE_ROOT EXPERIMENT N -- run one experiment N times in one run.sh run (after one build) and print each
# run's RESULT line, to tell a reproducible effect from a one-off. Example: tools/repeat-exp.sh /rlite experiments/exp4-readdress.sh 8
U=${1:?RLITE_ROOT}; X=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); N=${3:-5}
H=$(cd "$(dirname "$0")/.." && pwd); C=$(mktemp -d /tmp/repeat-exp.XXXX); T=""
for i in $(seq $N); do cp "$X" "$C/$(basename "$X" .sh)-r$i.sh"; T="$T $C/$(basename "$X" .sh)-r$i.sh"; done   # one log per run
TESTS="$T" "$H/run.sh" "$U" 1 $N
d=$(ls -td "$H"/runs/* | head -1)
echo "== $(basename "$X") x$N in $d"; awk 'NR>1{print $1, $3, $4" s"}' "$d/suite.tsv"; for i in $(seq $N); do echo "r$i: $(grep -h RESULT "$d/$(basename "$X" .sh)-r$i.log")"; done; rm -rf "$C"
