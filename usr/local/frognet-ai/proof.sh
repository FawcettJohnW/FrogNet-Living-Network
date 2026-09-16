#!/bin/sh
# proof.sh -- run the same plan on every node, no orchestration.
#
#   sh proof.sh --rank 0 --addr 10.250.250.100 --master 10.250.250.100 \
#               --store databasehost.frognet:80 --tag p1
#
# Same --tag and --master on every node; --rank and --addr differ. Start
# them within a few seconds of each other.
#
# WHY THERE IS NO ORCHESTRATOR
# Every node walks the identical list of arms in the identical order, and
# the run id for each arm is derived from --tag, the arm and the repetition.
# So all ranks agree on which arm they are in without anyone telling them --
# the same way they agree on collective sequence numbers. A node that falls
# behind blocks the arm it is in and catches up; a node that is missing
# fails that arm and the rest still run.
#
# WHAT IT PROVES, AND WHY EACH PIECE IS THERE
#   world sweep 2,3,4   the byte model predicts stack degrades as W/2 and
#                       shard does not. One world size cannot show that;
#                       three can. This is the claim that decides whether
#                       the advantage survives a bigger fleet.
#   gloo every time     the denominator has to be measured in the same
#                       session as the numerator, on the same links, in the
#                       same order. A baseline from yesterday is not a
#                       control.
#   stack AND shard     separates the two mechanisms. If stack also beats
#                       gloo, the win is concurrent reads. If only shard
#                       does, the byte pattern is carrying it.
#   3 repetitions       one run of anything here has been wrong often
#                       enough to make this non-negotiable.
#   arm order rotated   whichever arm runs first pays the warm-up.
set -e

RANK=""; ADDR=""; MASTER=""; STORE=""; TAG=""
REPS=3; ITERS=9; TIMEOUT=300; OUT=/tmp/proof; NB=./netbench1.py
WORLDS="4 3 2"

while [ $# -gt 0 ]; do
    case "$1" in
        --rank) RANK="$2"; shift 2 ;;
        --addr) ADDR="$2"; shift 2 ;;
        --master) MASTER="$2"; shift 2 ;;
        --store) STORE="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --reps) REPS="$2"; shift 2 ;;
        --iters) ITERS="$2"; shift 2 ;;
        --worlds) WORLDS="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --netbench1) NB="$2"; shift 2 ;;
        *) echo "unknown: $1"; exit 2 ;;
    esac
done
for v in RANK ADDR MASTER STORE TAG; do
    eval "x=\$$v"
    [ -n "$x" ] || { echo "--$(echo $v | tr A-Z a-z) is required"; exit 2; }
done
[ -f "$NB" ] || { echo "netbench1.py not found at $NB (use --netbench1)"; exit 2; }

mkdir -p "$OUT"
echo "proof: rank $RANK at $ADDR, tag $TAG, worlds [$WORLDS], $REPS reps"
python3 "$NB" --version | sed 's/^/  /'
echo

run_arm() {          # world, backend, reduce, rep
    W="$1"; BE="$2"; RED="$3"; REP="$4"
    if [ "$RANK" -ge "$W" ]; then
        echo "  w$W $BE${RED:+/$RED} rep$REP -- rank $RANK not in this group, skipping"
        return 0
    fi
    ID="${TAG}.w${W}.${BE}${RED:+-$RED}.r${REP}"
    D="$OUT/$ID"
    mkdir -p "$D"
    printf "  w%s %-18s rep%s ... " "$W" "$BE${RED:+/$RED}" "$REP"
    if python3 "$NB" --rank "$RANK" --world "$W" --backend "$BE" \
        ${RED:+--reduce $RED} --runid "$ID" --addr "$ADDR" \
        --master "$MASTER" --store "$STORE" \
        --profile slope --iters "$ITERS" --timeout "$TIMEOUT" \
        --out "$D" >"$D/rank$RANK.log" 2>&1
    then
        echo "ok"
    else
        echo "FAILED (see $D/rank$RANK.log)"
    fi
}

REP=1
while [ "$REP" -le "$REPS" ]; do
    for W in $WORLDS; do
        # Rotate which arm goes first, so warm-up is not always paid by the
        # same one.
        case $(( (REP + W) % 3 )) in
          0) ORDER="gloo: psychedelic:stack psychedelic:shard" ;;
          1) ORDER="psychedelic:stack psychedelic:shard gloo:" ;;
          *) ORDER="psychedelic:shard gloo: psychedelic:stack" ;;
        esac
        for A in $ORDER; do
            BE=$(echo "$A" | cut -d: -f1)
            RED=$(echo "$A" | cut -d: -f2)
            run_arm "$W" "$BE" "$RED" "$REP"
        done
    done
    REP=$(( REP + 1 ))
done

echo
echo "done. Copy $OUT from every node to one machine, then:"
echo "  python3 proof_collect.py $OUT"
