#!/bin/sh
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# transportrun.sh -- PyTorch on the transport drivers: the same arms with only the transport under them changed.
# [THE_TENSORS_ARE_IN_THE_RAM_V1] [THE_RAM_SESSION_IS_A_DRIVERS_CONNECTION_V1]
#
#   sh transportrun.sh <mesh address> <run id number> [first arm]     same number on every node, one never used before
#
# Every node runs it. It first runs the simulator tier for this code on this node (simulation/collective_tier.py)
# and says what the one FrogTorch RAM listens with; then the arms, in step across the nodes (gate.py's barrier
# before each). Arm N is always run id tr<base+N>. One session, the control first:
#
#   arm  what                                                   the transport under FrogTorch's sessions with the RAM
#   0    ddpbench, stock DDP on gloo (the control)              gloo's own sockets, rank to rank; no setting reaches them
#   1    ddpbench, Psychedelic shard                            tcp
#   2    ddpbench, Psychedelic shard                            softhoma (databasehost.frognet softhoma)
#   3    meshfeed k=1, batch 64, 40 steps, --check              tcp
#   4    meshfeed k=1, batch 64, 40 steps, --check              softhoma
#   5    meshfeed k=1, batch 4096, 10 steps, --check            tcp
#   6    meshfeed k=1, batch 4096, 10 steps, --check            softhoma
#
# The word for an arm is given to that arm's processes alone (FROGNET_SEM_TRANSPORT); nothing on the node is changed
# between arms. The softhoma arms need the FrogTorch RAM to be listening with soft Homa: its node's
# /etc/frognet/semantic_transport has the line "databasehost.frognet softhoma" (every node has the same file, since
# the role moves) and frogtorch-ram was restarted after this code was installed. What it listens with is printed
# below before the first arm.
#
# Each rank writes its result of each arm into the RAM; at the end every node holds one read until all of them are
# there and prints the same report: whole-run seconds, held-out loss and param_sha as three matrices (ranks down,
# arms across), and the arms with their ratio against arm 0 of this session.
# Logs: /tmp/nb3/tr<id>.rank<R>.log
PY="${FROGNET_PY:-/usr/local/frognet-ai/venv/bin/python3}"
[ -x "$PY" ] || { echo "$PY is missing: run /usr/local/frognet-ai/install.sh first (it makes FrogTorch's venv)"; exit 1; }
ADDR="$1"; BASE="$2"; FIRST="${3:-0}"
if [ -z "$ADDR" ] || [ -z "$BASE" ]; then
    sed -n '9,36p' "$0"; exit 2
fi
case "$BASE" in ""|*[!0-9]*) echo "run id must be a number"; exit 2 ;; esac

# The nodes, in rank order (meshrun.sh's set). FROGNET_RUN_NODES names another set, the same on every node.
NODES="${FROGNET_RUN_NODES:-10.250.250.1=Seattle5 10.170.170.1=Seattle7 10.123.123.1=New-York-1 10.188.188.1=BAMacBook 10.251.251.1=Seattle3B}"
RANK=""; NAME=""; WORLD=0
for _n in $NODES; do
    [ "${_n%%=*}" = "$ADDR" ] && { RANK=$WORLD; NAME="${_n#*=}"; }
    WORLD=$((WORLD + 1))
done
[ -n "$RANK" ] || { echo "unknown address $ADDR (this run's nodes: $NODES)"; exit 2; }
HIDDEN="${FROGNET_DDP_HIDDEN:-1024}"
OUT="${FROGNET_RUN_OUT:-/tmp/nb3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TREE="${FROGNET_TREE:-/opt/frognet_semantic}"
MF="$HERE/meshfeed.py"; DB="$HERE/ddpbench.py"; GATE="$HERE/gate.py"; REP="$HERE/transport_report.py"
STORE=databasehost.frognet:80
GATE_WAIT="${FROGNET_GATE_WAIT:-3600}"
ARMS=7
W_TCP="tcp"
W_SOFT="auto,databasehost.frognet softhoma"
mkdir -p "$OUT"

echo "=============================================================="
echo " $NAME  --  rank $RANK at $ADDR   world $WORLD"
echo "=============================================================="
for _f in transportrun.sh transport_report.py meshfeed.py ddpbench.py gate.py; do
    [ -f "$HERE/$_f" ] && printf "  %-20s %s\n" "$_f" "$(sha256sum "$HERE/$_f" | cut -c1-12)" \
                       || printf "  %-20s MISSING\n" "$_f"
done
for _f in ram_plane.py frogtorch_ram.py frogtorch_ram_client.py frogtorch_ram_drivers.py psychedelic_backend.py ddp_hook.py; do
    printf "  %-28s %s\n" "$_f" "$(sha256sum "$TREE/agent_workload/tuplespace/$_f" 2>/dev/null | cut -c1-12)"
done
if [ -f /etc/frognet/semantic_transport ]; then
    echo "  /etc/frognet/semantic_transport on this node: $(tr '\n' ';' < /etc/frognet/semantic_transport)"
else
    echo "  /etc/frognet/semantic_transport on this node: not there (auto)"
fi
echo "  run ids: tr$BASE .. tr$((BASE + ARMS - 1))   logs: $OUT/tr<id>.rank$RANK.log"
echo
if [ "$FIRST" -le 0 ]; then
    echo ">>> the simulator tier for this code, on this node (simulation/collective_tier.py)"
    PYTHONPATH="$TREE" "$PY" "$TREE/simulation/collective_tier.py" 2>&1 | tee "$OUT/tr$BASE.tier.rank$RANK.log" | \
        grep -E "^\s+\[FAIL\]|^ALL COLLECTIVE|^COLLECTIVE TIER" | sed 's/^/    /'
    echo
fi
"$PY" "$REP" ram --tree "$TREE" || echo "  (the FrogTorch RAM did not answer; the arms below will say why)"
echo

now() { date +%s.%N; }

arm() {   # arm, kind (ddp|mesh), word, label, then the program's own arguments
    _N="$1"; _KIND="$2"; _WORD="$3"; _LABEL="$4"; shift 4
    _ID="tr$((BASE + _N))"
    case "$_WORD" in none) _SAY="gloo's own";; tcp) _SAY="tcp";; *) _SAY="softhoma";; esac
    echo ">>> $_ID  arm $_N  $_LABEL   [$_SAY]"
    if [ "$_N" -lt "$FIRST" ]; then echo "    skipped"; return 0; fi
    "$PY" "$GATE" --role barrier --runid "$_ID" --arm 0 --rank "$RANK" --world "$WORLD" \
        --wait "$GATE_WAIT" --tree "$TREE" || exit 2
    _T0=$(now)
    if [ "$_KIND" = ddp ]; then
        _BE="$1"; shift
        _RED=""; [ "$_BE" = psychedelic ] && _RED="--reduce shard"
        if [ "$_WORD" = none ]; then
            "$PY" "$DB" --rank "$RANK" --world "$WORLD" --backend "$_BE" $_RED --bucket-view 0 \
                --addr "$ADDR" --store "$STORE" --runid "$_ID" --tree "$TREE" --hidden "$HIDDEN" \
                --timeout 1800 --peer-wait 600 --out "$OUT" "$@" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
                grep -E "^    \[|ms/step|FAILED|sessions with the RAM"
        else
            FROGNET_SEM_TRANSPORT="$_WORD" "$PY" "$DB" --rank "$RANK" --world "$WORLD" --backend "$_BE" $_RED --bucket-view 0 \
                --addr "$ADDR" --store "$STORE" --runid "$_ID" --tree "$TREE" --hidden "$HIDDEN" \
                --timeout 1800 --peer-wait 600 --out "$OUT" "$@" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
                grep -E "^    \[|ms/step|FAILED|sessions with the RAM"
        fi
    else
        FROGNET_SEM_TRANSPORT="$_WORD" "$PY" "$MF" --rank "$RANK" --world "$WORLD" --runid "$_ID" --addr "$ADDR" \
            --tree "$TREE" --hidden "$HIDDEN" "$@" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
            grep -E "^    \[|meshfeed    |Traceback|Error|sessions with the RAM"
    fi
    _T1=$(now)
    _WHOLE=$(awk "BEGIN { printf \"%.2f\", $_T1 - $_T0 }")
    "$PY" "$REP" put --base "$BASE" --arm "$_N" --rank "$RANK" --world "$WORLD" --label "$_LABEL" --word "$_SAY" \
        --kind "$_KIND" --whole-s "$_WHOLE" --out "$OUT" --runid "$_ID" --tree "$TREE"
    echo
}

arm 0 ddp  none      "ddpbench gloo (control)"          gloo        --steps 40 --warmup 8
arm 1 ddp  "$W_TCP"  "ddpbench psychedelic shard"       psychedelic --steps 40 --warmup 8
arm 2 ddp  "$W_SOFT" "ddpbench psychedelic shard"       psychedelic --steps 40 --warmup 8
arm 3 mesh "$W_TCP"  "meshfeed k=1 batch 64"            --check
arm 4 mesh "$W_SOFT" "meshfeed k=1 batch 64"            --check
arm 5 mesh "$W_TCP"  "meshfeed k=1 batch 4096"          --batch 4096 --steps 10 --warmup 2 --check
arm 6 mesh "$W_SOFT" "meshfeed k=1 batch 4096"          --batch 4096 --steps 10 --warmup 2 --check

echo "=============================================================="
echo " done on $NAME. Every rank's results, read from the RAM:"
"$PY" "$REP" matrix --base "$BASE" --arms "$ARMS" --world "$WORLD" --wait "${FROGNET_REPORT_WAIT:-600}" --tree "$TREE"
echo "=============================================================="
