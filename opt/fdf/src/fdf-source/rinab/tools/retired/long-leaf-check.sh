#!/bin/bash
# long-leaf-check.sh -- [LONG_LEAF_ONE_PASS_V1] leaf_type as vendored vs as patched, on the same 300,016 strings x 3 paths.
set -e; cd "$(dirname "$0")/.."; D=$(mktemp -d /tmp/long-leaf-check.XXXXXX)
g++ -std=c++20 -O2 -Iplatform/ribbit-v0.63/ribbit_cpp tests/long_leaf_exact.cpp -o $D/vendored
g++ -std=c++20 -O2 -Ibuild/platform/ribbit_cpp tests/long_leaf_exact.cpp -o $D/patched
( time $D/vendored > $D/v.txt ) 2> $D/v.time; ( time $D/patched > $D/p.txt ) 2> $D/p.time
echo "answers: $(wc -l < $D/v.txt)  hosts: $(grep -c '^host$' $D/v.txt)  differing: $(cmp -l $D/v.txt $D/p.txt 2>/dev/null | wc -l)"
echo "vendored $(grep real $D/v.time)   patched $(grep real $D/p.time)"
cmp -s $D/v.txt $D/p.txt && echo "RESULT PASS" || { echo "RESULT FAIL"; diff $D/v.txt $D/p.txt | head; exit 1; }
