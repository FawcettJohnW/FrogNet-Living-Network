#!/bin/sh
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# meshrun.sh -- the extended meshfeed campaign: the data comes to the compute, against DDP's gradient exchange.
#
#   sh meshrun.sh <mesh address> <run id number> [first arm]        same number on every node, one never used before
#
# Every node runs it; each arm forms its own group in the tuple space (meshfeed's rendezvous; gate.py before the
# ddpbench controls), so the arms run in step across the nodes. Arm N is always run id <base+N>.
#
#   arm  what                                                               shows
#   0    meshfeed k=1, batch 64, 40 steps, --check                          ddpbench's workload; oracle comparison
#   1    meshfeed k=1, batch 1024, 200 steps, --check                       70 KB per node per step, read from disk
#   2    meshfeed k=1, batch 1024, 200 steps, --feed-hz 5                   data that arrives over time
#   3    meshfeed k=1, batch 4096, 10 steps, --check                        compute heavy: one machine
#   4    meshfeed k=2, batch 4096, 10 steps                                 compute set of 2, psychedelic all_reduce
#   5    meshfeed k=3, batch 4096, 10 steps                                 compute set of 3
#   6    ddpbench gloo, batch 4096, 10 steps                                control for 3-5
#   7    ddpbench psychedelic shard, batch 4096, 10 steps                   control for 3-5
#
# Logs in /tmp/nb3/mf<id>.rank<R>.log; the summary at the end is every arm's result line.
set -e
ADDR="$1"; BASE="$2"; FIRST="${3:-0}"
if [ -z "$ADDR" ] || [ -z "$BASE" ]; then
    sed -n '10,26p' "$0"; exit 2
fi
case "$BASE" in ""|*[!0-9]*) echo "run id must be a number"; exit 2 ;; esac

# the five nodes of the 2026-11-12/13 campaigns, in their hookrun ranks
case "$ADDR" in
    10.250.250.1)   RANK=0; NAME=Seattle5   ;;
    10.170.170.1)   RANK=1; NAME=Seattle7   ;;
    10.123.123.1)   RANK=2; NAME=New-York-1 ;;
    10.188.188.1)   RANK=3; NAME=BAMacBook  ;;
    10.251.251.1)   RANK=4; NAME=Seattle3B  ;;
    *) echo "unknown address $ADDR (meshrun's set: Seattle5, Seattle7, New-York-1, BAMacBook, Seattle3B)"; exit 2 ;;
esac
WORLD=5
HIDDEN="${FROGNET_DDP_HIDDEN:-1024}"
OUT=/tmp/nb3
HERE="$(cd "$(dirname "$0")" && pwd)"
TREE="${FROGNET_TREE:-/opt/frognet_semantic}"
MF="$HERE/meshfeed.py"; DB="$HERE/ddpbench.py"; GATE="$HERE/gate.py"
STORE=databasehost.frognet:80
mkdir -p "$OUT"

echo "=============================================================="
echo " $NAME  --  rank $RANK at $ADDR   world $WORLD"
echo "=============================================================="
for _f in meshrun.sh meshfeed.py ddpbench.py gate.py; do
    [ -f "$HERE/$_f" ] && printf "  %-14s %s\n" "$_f" "$(sha256sum "$HERE/$_f" | cut -c1-12)" \
                       || printf "  %-14s MISSING\n" "$_f"
done
echo "  run ids: mf$BASE .. mf$((BASE + 7))   logs: $OUT/mf<id>.rank$RANK.log"
echo

mf() {   # arm, label, meshfeed arguments...
    _N="$1"; _LABEL="$2"; shift 2
    _ID="mf$((BASE + _N))"
    echo ">>> $_ID  $_LABEL"
    if [ "$_N" -lt "$FIRST" ]; then echo "    skipped"; return 0; fi
    python3 "$MF" --rank "$RANK" --world "$WORLD" --runid "$_ID" --addr "$ADDR" --tree "$TREE" \
        --hidden "$HIDDEN" "$@" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
        grep -E "^    \[|meshfeed    |Traceback|Error|refusing" || true
    echo
}

db() {   # arm, label, backend, ddpbench arguments...
    _N="$1"; _LABEL="$2"; _BE="$3"; shift 3
    _ID="mf$((BASE + _N))"
    echo ">>> $_ID  $_LABEL"
    if [ "$_N" -lt "$FIRST" ]; then echo "    skipped"; return 0; fi
    python3 "$GATE" --role barrier --runid "$_ID" --arm 0 --rank "$RANK" --world "$WORLD" \
        --wait 3600 --tree "$TREE" || exit 2
    _RED=""; [ "$_BE" = psychedelic ] && _RED="--reduce shard"
    python3 "$DB" --rank "$RANK" --world "$WORLD" --backend "$_BE" $_RED --bucket-view 0 \
        --addr "$ADDR" --store "$STORE" --runid "$_ID" --tree "$TREE" --hidden "$HIDDEN" \
        --timeout 1800 --peer-wait 600 --out "$OUT" "$@" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
        grep -E "^    \[|ms/step|FAILED" || true
    echo
}

mf 0 "meshfeed k=1, batch 64 -- ddpbench's workload, oracle check" --check
mf 1 "meshfeed k=1, batch 1024, 200 steps -- read from disk, oracle check" --batch 1024 --steps 200 --warmup 8 --check
mf 2 "meshfeed k=1, batch 1024, 200 steps -- data arriving at 5 Hz" --batch 1024 --steps 200 --warmup 8 --feed-hz 5
mf 3 "meshfeed k=1, batch 4096, 10 steps -- one machine, oracle check" --batch 4096 --steps 10 --warmup 2 --check
mf 4 "meshfeed k=2, batch 4096, 10 steps -- compute set of 2" --batch 4096 --steps 10 --warmup 2 --k 2
mf 5 "meshfeed k=3, batch 4096, 10 steps -- compute set of 3" --batch 4096 --steps 10 --warmup 2 --k 3
db 6 "ddpbench gloo, batch 4096, 10 steps -- control for 3-5" gloo --batch 4096 --steps 10 --warmup 2
db 7 "ddpbench psychedelic shard, batch 4096, 10 steps -- control for 3-5" psychedelic --batch 4096 --steps 10 --warmup 2

echo "=============================================================="
echo " done on $NAME. Results:"
for _n in 0 1 2 3 4 5 6 7; do
    _f="$OUT/mf$((BASE + _n)).rank$RANK.log"
    [ -f "$_f" ] && printf "  mf%-6s %s\n" "$((BASE + _n))" \
        "$(grep -hE "meshfeed    |ms/step \(min\)" "$_f" | tail -1 | sed 's/^ *//')"
done
echo "=============================================================="
