#!/bin/sh
# hookrun.sh -- the DDP variations, on every node, gated through the store.
#
#   sh hookrun.sh <this machine's mesh address> <run id number>
#
#   sh hookrun.sh 10.250.250.1 800     -> ddp800 .. ddp809
#
# WHAT THIS MEASURES, AND WHY IT IS NOT netbench1
#
# netbench1 measures collectives. A collective owes the exact sum over every
# rank, and inside that contract the best available is to move the same
# bytes more cheaply -- which is where the collectives work has landed, at
# parity with gloo.
#
# The comm hook is PyTorch's own boundary where that contract stops. It is
# handed a bucket and owes a Future[Tensor]; what happens in between is the
# implementer's. That is where "which bytes are worth incorporating at all"
# becomes a question you are allowed to ask, and it is the only place the
# architecture can win rather than match.
#
# THE ARMS
#
#   gloo                         the control. No hook; PyTorch's own path.
#   psychedelic + no hook        our all_reduce under DDP.
#   psychedelic + faithful       the hook, computing the same sum. This is
#                                the one that isolates hook OVERHEAD: it
#                                answers exactly what all_reduce answers,
#                                so any difference is the boundary itself.
#   psychedelic + fresh_only     incorporates what peers have published
#                                rather than waiting for all of it. Less
#                                work done, not the same work done faster.
#   psychedelic + every_k        reduces every k steps.
#
# Each runs with gradient_as_bucket_view off and on. The flag removes
# PyTorch's own grad<->bucket copies -- two full passes over every
# parameter per step -- and it helps every backend equally, so it is a
# variable to hold rather than a result.
#
# CORRECTNESS IS PART OF THE MEASUREMENT
#
# fresh_only and every_k change what is incorporated, so they MAY train
# differently -- that is the point, and the held-out loss is how you find
# out whether it mattered. gloo, no-hook and faithful must all agree: they
# compute the same sum, and if their held-out losses differ, something is
# wrong that speed does not excuse.
set -e

ADDR="$1"; BASE="$2"; FIRST="${3:-0}"
if [ -z "$ADDR" ] || [ -z "$BASE" ]; then
    echo "usage: sh hookrun.sh <mesh address> <run id number> [first arm]"
    echo
    echo "  sh hookrun.sh 10.250.250.1 800       all ten arms"
    echo "  sh hookrun.sh 10.250.250.1 800 1     skip arm 0"
    echo
    echo "  Arms, in order:"
    echo "    0  gloo               bucket_view=0"
    echo "    1  psychedelic/shard  bucket_view=0"
    echo "    2  + faithful         bucket_view=0"
    echo "    3  + fresh_only       bucket_view=0"
    echo "    4  + every_k          bucket_view=0"
    echo "    5  gloo               bucket_view=1"
    echo "    6  psychedelic/shard  bucket_view=1"
    echo "    7  + faithful         bucket_view=1"
    echo "    8  + fresh_only       bucket_view=1"
    echo "    9  + every_k          bucket_view=1"
    echo
    echo "  Run ids stay tied to the arm: arm N is always ddp<base+N>, so"
    echo "  skipping does not renumber anything and results from different"
    echo "  invocations still line up."
    echo
    echo "  Same number on every node, and one never used before."
    exit 2
fi
case "$BASE" in ""|*[!0-9]*) echo "run id must be a number"; exit 2 ;; esac

MASTER=10.250.250.100
STORE=databasehost.frognet:80
OUT=/tmp/nb3
WORLD=4
STEPS=40
WARMUP=8
# ~17 MB of gradients at 1024. Below about 512 the grad<->bucket copies are
# too small to measure. Override per node if a machine cannot run the model:
#   FROGNET_DDP_HIDDEN=256 sh hookrun.sh ...
# It must be the SAME on every node -- the model has to match.
HIDDEN="${FROGNET_DDP_HIDDEN:-1024}"
TIMEOUT=600
PEER_WAIT=300
HERE="$(cd "$(dirname "$0")" && pwd)"
DB="$HERE/ddpbench.py"
GATE="$HERE/gate.py"
TREE="${FROGNET_TREE:-/opt/frognet_semantic}"
# How long to wait at a barrier for every rank to arrive. Generous, because
# the previous arm may still be finishing on a slow node; but finite,
# because a rank that will never come should be reported, not waited for.
GATE_WAIT="${FROGNET_GATE_WAIT:-3600}"

case "$ADDR" in
    10.250.250.100) RANK=0; NAME=AI-Host   ;;
    10.250.250.1)   RANK=1; NAME=Seattle5  ;;
    10.170.170.1)   RANK=2; NAME=Seattle7  ;;
    10.123.123.1)   RANK=3; NAME=HappyDog ;;
    *) echo "unknown address $ADDR"; exit 2 ;;
esac

STALE=$(ps -eo pid,args \
        | grep -E "python[0-9.]*[[:space:]]+[^[:space:]]*ddpbench\.py" \
        | grep -- "--rank" || true)
if [ -n "$STALE" ]; then
    echo "ddpbench already running here:"; echo "$STALE" | sed 's/^/  /'
    echo "  pkill -f ddpbench.py"; exit 2
fi
mkdir -p "$OUT"

# Hold the merge lock for the whole campaign, not per arm: a merge starting
# between arms re-plumbs routes underneath the next one.
LOCKFILE=/var/run/runMerge.lock
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCKFILE" || { echo "cannot open $LOCKFILE"; exit 2; }
    if ! flock -n 9; then
        echo "runMerge lock held by another process -- a merge is running."
        exit 2
    fi
    echo "runMerge lock held for the whole campaign"
    FROGNET_MERGE_LOCK_HELD=1; export FROGNET_MERGE_LOCK_HELD
fi

echo "=============================================================="
echo " $NAME  --  rank $RANK at $ADDR   world $WORLD"
echo "=============================================================="
echo "harness:"
for _f in hookrun.sh ddpbench.py gate.py; do
    _p="$HERE/$_f"
    [ -f "$_p" ] && printf "  %-16s %s\n" "$_f" \
        "$(sha256sum "$_p" | cut -c1-12)" || printf "  %-16s MISSING\n" "$_f"
done
echo "  model: hidden $HIDDEN, $STEPS steps, $WARMUP warmup"
echo

LAST=$(( BASE + 9 ))
echo "run ids: ddp$BASE .. ddp$LAST"
[ "$FIRST" -gt 0 ] && echo "         starting at arm $FIRST (ddp$(( BASE + FIRST ))); earlier arms skipped"
echo "logs:    $OUT/ddp<id>.rank$RANK.log   (tail -f one of these)"
echo

if [ "$RANK" -ge "$WORLD" ]; then
    echo "world $WORLD -- rank $RANK is not in it."; exit 0
fi

# [EVERYONE_ARRIVES_BEFORE_ANYONE_STARTS_V1] A barrier, not a signal.
#
# Arms here run over an hour, so ranks finish far apart, and a rank that is
# absent -- still finishing, or its session dropped -- used to be
# discovered by PyTorch's rendezvous timing out after 1800 seconds with
# "3/4 clients joined". Half an hour to learn something knowable at once.
#
# Every rank publishes its arrival and waits for all of them. If one is
# missing, the others are still here saying which.
gate() {
    python3 "$GATE" --role barrier --runid "$1" --arm 0 \
        --rank "$RANK" --world "$WORLD" --wait "$GATE_WAIT" \
        --store "$STORE" --tree "$TREE" || exit 2
}

# [DO_NOT_CLOBBER_THE_CALLER_S_COUNTER_V1] These were named ID/BE/HOOK,
# and `ID` is the loop counter in the caller -- sh has no locals, so the
# first call overwrote it with "ddp800" and the next increment died with
# "Illegal number: ddp800". Prefixed names, so the function cannot reach
# into the loop that calls it.
# [AN_ARM_KEEPS_ITS_NUMBER_V1] Skipping is a decision about what to RUN,
# not about what things are called. Arm N is ddp<base+N> whether or not
# earlier arms ran in this invocation, so a campaign assembled from several
# invocations still compares.
run_one() {   # arm index, id, backend, hook, bucket_view, label
    _N="$1"; _ID="$2"; _BE="$3"; _HOOK="$4"; _BV="$5"; _LABEL="$6"
    if [ "$_N" -lt "$FIRST" ]; then
        echo
        echo "--- $_ID  $_LABEL"
        echo "    skipped (arm $_N, starting at $FIRST)"
        return 0
    fi
    echo
    echo ">>> $_ID  $_LABEL"
    gate "$_ID"
    printf "    "
    _RED=""
    [ "$_BE" = psychedelic ] && _RED="--reduce shard"
    python3 "$DB" --rank "$RANK" --world "$WORLD" --backend "$_BE" \
        $_RED ${_HOOK:+--hook $_HOOK} --bucket-view "$_BV" \
        --addr "$ADDR" --master "$MASTER" --store "$STORE" --runid "$_ID" \
        --tree "$TREE" --steps "$STEPS" --warmup "$WARMUP" \
        --hidden "$HIDDEN" --timeout "$TIMEOUT" --peer-wait "$PEER_WAIT" \
        --out "$OUT" 2>&1 | tee "$OUT/$_ID.rank$RANK.log" | \
        grep -E "^    \[|ms/step|FAILED"
}

N=0
ID=$BASE
for BV in 0 1; do
    run_one "$N" "ddp$ID"     gloo        ""          "$BV" \
        "gloo, bucket_view=$BV -- control"         ; N=$((N+1)); ID=$((ID+1))
    run_one "$N" "ddp$ID"     psychedelic ""          "$BV" \
        "psychedelic/shard, no hook, bucket_view=$BV" ; N=$((N+1)); ID=$((ID+1))
    run_one "$N" "ddp$ID"     psychedelic faithful    "$BV" \
        "hook: faithful -- same sum, isolates hook cost" ; N=$((N+1)); ID=$((ID+1))
    run_one "$N" "ddp$ID"     psychedelic fresh_only  "$BV" \
        "hook: fresh_only -- incorporates what is there" ; N=$((N+1)); ID=$((ID+1))
    run_one "$N" "ddp$ID"     psychedelic every_k     "$BV" \
        "hook: every_k -- reduces every k steps"   ; N=$((N+1)); ID=$((ID+1))
done

echo
echo "=============================================================="
echo " done on $NAME."
echo "   python3 hookfit.py $OUT ddp$BASE ddp$LAST"
echo "=============================================================="
