#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# big.sh <workload> <outstanding> <seconds> [profile]: one cp_node client and server on soft Homa; prints his last line.
H=${SOFT:-/home/claude/src/frognet/opt/frognet_semantic/ribbit/examples/homa}; HOMA=/home/claude/src/homa; L=/home/claude/logs/fast
export SOFTHOMA_RAM=127.0.0.1:9131
pkill -f "softhoma-ram 127.0.0.1:9131" 2>/dev/null; pkill -f "first-port 4300" 2>/dev/null; sleep 0.3
$H/build/softhoma-ram 127.0.0.1:9131 >$L/ram.log 2>&1 & RAM=$!; sleep 0.5
env LD_PRELOAD=$H/build/libsofthoma.so $HOMA/util/cp_node server --protocol homa --first-port 4300 --port-threads 2 >$L/srv.log 2>&1 & S=$!; sleep 0.7
env LD_PRELOAD=$H/build/libsofthoma.so timeout $3 $HOMA/util/cp_node client --protocol homa --workload $1 --client-max $2 --ports 1 --first-port 4300 --first-server 1 --server-nodes 1 >$L/cli.log 2>&1 & C=$!
if [ -n "$4" ]; then sleep 3; CP=$(pgrep -f "cp_node client.*4300" | tail -1); SP=$(pgrep -f "cp_node server.*4300" | tail -1)
  for who in ram:$RAM srv:$SP cli:$CP; do n=${who%%:*}; p=${who##*:}; : >$L/stk-$n.txt
    for i in $(seq 1 25); do gdb -p $p -batch -ex "thread apply all bt 12" 2>/dev/null >>$L/stk-$n.txt; sleep 0.05; done; done; fi
sleep $(( $3 - 1 )); for who in ram:$RAM srv:$(pgrep -f "cp_node server.*4300" | tail -1) cli:$(pgrep -f "cp_node client.*4300" | tail -1); do awk -v n=${who%%:*} '{printf "  cpu %s user %.2fs sys %.2fs\n", n, $14/100, $15/100}' /proc/${who##*:}/stat; done
for who in srv:$(pgrep -f "cp_node server.*4300" | tail -1) cli:$(pgrep -f "cp_node client.*4300" | tail -1); do for t in /proc/${who##*:}/task/*; do awk -v n=${who%%:*} -v t=${t##*/} '($14+$15)>20{printf "    %s thread %s %s user %.2fs sys %.2fs\n", n, t, $2, $14/100, $15/100}' $t/stat; done; done
[ -n "$5" ] && gdb -p $(pgrep -f "cp_node server.*4300" | tail -1) -batch -ex "thread apply all bt 14" >$L/srv-bt.txt 2>/dev/null
wait $C; kill $S $RAM 2>/dev/null; wait 2>/dev/null
grep "clients:" $L/cli.log | tail -3 | head -1
