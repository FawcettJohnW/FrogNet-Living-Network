#!/bin/sh
# repeat.sh -- the same three arms, N times, back to back.
#
#   sh repeat.sh <this machine's mesh address> <run id number> [reps] [world]
#
#   sh repeat.sh 10.250.250.100 200 3 3      -> world 3, 3 reps, ids 200..208
#   sh repeat.sh 10.250.250.100 300 3 4      -> world 4, 3 reps, ids 300..308
#
# WHY THIS EXISTS
# A single campaign measured gloo at 448 ns/el on the same link where an
# earlier session measured 12460, and the ratio everything was resting on
# turned out to be a property of the control, not the treatment. One run of
# anything on this mesh is a sample of the network at that moment.
#
# WHY THE ARMS ROTATE
# Arm order rotates every repetition. If the network degrades over the ten
# minutes a campaign takes, whichever arm always runs last always looks
# worst -- and that is indistinguishable from it BEING worst.
#
# WHY THE ARMS ARE INTERLEAVED, NOT BATCHED
# gloo, shard and stack alternate rather than running three golos then three
# shards. Conditions drift; interleaving spreads that drift across all three
# arms instead of concentrating it in one.
set -e

ADDR="$1"; BASE="$2"; REPS="${3:-3}"; WORLD="${4:-4}"
if [ -z "$ADDR" ] || [ -z "$BASE" ]; then
    echo "usage: sh repeat.sh <mesh address> <run id number> [reps] [world]"
    echo
    echo "  sh repeat.sh 10.250.250.100 200 3 3    world 3, 3 reps"
    echo "  sh repeat.sh 10.250.250.100 300 3 4    world 4, 3 reps"
    echo
    echo "  Same number and same reps/world on every node."
    exit 2
fi
case "$BASE" in ""|*[!0-9]*) echo "run id must be a number"; exit 2 ;; esac

MASTER=10.250.250.100
STORE=databasehost.frognet:80
OUT=/tmp/nb3
ITERS=15
TIMEOUT=600
HERE="$(cd "$(dirname "$0")" && pwd)"
NB="$HERE/netbench1.py"
GATE="$HERE/gate.py"
SHOWERR="$HERE/showerr.py"
TREE="${FROGNET_TREE:-/opt/frognet_semantic}"

PEER_WAIT=300
SERVER_WAIT=300
SOCKET_TIMEOUT=60
SOCKET_MARGIN=60
IDLE_REAP=600
VERIFY=1
POOL=1

case "$ADDR" in
    10.250.250.100) RANK=0; NAME=AI-Host   ;;
    10.250.250.1)   RANK=1; NAME=Seattle5  ;;
    10.170.170.1)   RANK=2; NAME=Seattle7  ;;
    10.199.199.1)   RANK=3; NAME=BAMacBook ;;
    *) echo "unknown address $ADDR"; exit 2 ;;
esac

STALE=$(ps -eo pid,args \
        | grep -E "python[0-9.]*[[:space:]]+[^[:space:]]*netbench1\.py" \
        | grep -- "--rank" | grep -v "[[:space:]]--version" || true)
if [ -n "$STALE" ]; then
    echo "netbench1 already running here:"; echo "$STALE" | sed 's/^/  /'
    echo "  pkill -f netbench1.py"; exit 2
fi
# [HOLD_THE_LOCK_FOR_THE_WHOLE_CAMPAIGN_V1]
#
# netbench1 takes the runMerge lock around each rank invocation and drops it
# when that arm ends. Across nine arms and half an hour that leaves a gap
# between every arm, and a merge starting in one of them either re-plumbs
# routes while the next arm is rendezvousing, or holds the lock so the next
# arm waits its 120 s and fails.
#
# Take it once, here, for the campaign. FROGNET_MERGE_LOCK_HELD tells the
# ranks it is already held so they inherit it rather than queueing behind
# their own parent.
LOCKFILE=/var/run/runMerge.lock
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCKFILE" || { echo "cannot open $LOCKFILE"; exit 2; }
    if ! flock -n 9; then
        echo "runMerge lock is held by another process -- a merge is running."
        echo "Wait for it rather than measuring across it."
        exit 2
    fi
    echo "runMerge lock held for the whole campaign"
    FROGNET_MERGE_LOCK_HELD=1
    export FROGNET_MERGE_LOCK_HELD
else
    echo "NOTE: flock not found; the lock will be taken per arm instead,"
    echo "      which leaves a gap between arms a merge can start in."
fi

mkdir -p "$OUT"

echo "=============================================================="
echo " $NAME  --  rank $RANK at $ADDR   world $WORLD, $REPS reps"
echo "=============================================================="
echo "harness:"
for _f in repeat.sh netbench1.py gate.py showerr.py; do
    _p="$HERE/$_f"
    [ -f "$_p" ] && printf "  %-16s %s\n" "$_f" \
        "$(sha256sum "$_p" | cut -c1-12)" || printf "  %-16s MISSING\n" "$_f"
done
python3 "$NB" --version --peer-wait "$PEER_WAIT" \
    --server-wait "$SERVER_WAIT" --socket-timeout "$SOCKET_TIMEOUT" \
    --socket-margin "$SOCKET_MARGIN" --idle-reap "$IDLE_REAP" \
    --verify "$VERIFY" --pool "$POOL" 2>/dev/null | sed 's/^/  /'
echo
LAST=$(( BASE + REPS * 3 - 1 ))
echo "run ids: mesh$BASE .. mesh$LAST   (3 arms x $REPS reps)"
echo

if [ "$RANK" -ge "$WORLD" ]; then
    echo "world $WORLD -- rank $RANK is not in this group. Nothing to do."
    exit 0
fi

gate() {
    if [ "$RANK" = "0" ]; then
        sleep 2
        python3 "$GATE" --role primary --runid "$1" --arm 0 \
            --store "$STORE" --tree "$TREE" || exit 2
    else
        python3 "$GATE" --role follower --runid "$1" --arm 0 \
            --store "$STORE" --tree "$TREE" || exit 2
    fi
}

do_arm() {   # id, backend, reduce, label
    ID="$1"; BE="$2"; RED="$3"; LABEL="$4"
    echo
    echo ">>> $ID  $LABEL"
    gate "$ID"
    printf "    running ... "
    if python3 "$NB" --rank "$RANK" --world "$WORLD" --backend "$BE" \
        ${RED:+--reduce $RED} --runid "$ID" --addr "$ADDR" \
        --master "$MASTER" --store "$STORE" \
        --profile slope --iters "$ITERS" --timeout "$TIMEOUT" --diag \
        --peer-wait "$PEER_WAIT" --server-wait "$SERVER_WAIT" \
        --socket-timeout "$SOCKET_TIMEOUT" --socket-margin "$SOCKET_MARGIN" \
        --idle-reap "$IDLE_REAP" --verify "$VERIFY" --pool "$POOL" \
        --out "$OUT" > "$OUT/$ID.rank$RANK.log" 2>&1
    then
        echo "ok"
    else
        echo "FAILED"
        python3 "$SHOWERR" "$OUT" "$RANK" "$BE" "$RED" "$ID" | sed 's/^/      /'
    fi
}

REP=1
ID=$BASE
while [ "$REP" -le "$REPS" ]; do
    # Rotate, so no arm is always first (warm-up) or always last (drift).
    case $(( REP % 3 )) in
      1) ORDER="gloo: psychedelic:shard psychedelic:stack" ;;
      2) ORDER="psychedelic:shard psychedelic:stack gloo:" ;;
      *) ORDER="psychedelic:stack gloo: psychedelic:shard" ;;
    esac
    for A in $ORDER; do
        BE=$(echo "$A" | cut -d: -f1)
        RED=$(echo "$A" | cut -d: -f2)
        do_arm "mesh$ID" "$BE" "$RED" "rep $REP/$REPS, world $WORLD, $BE${RED:+/$RED}"
        ID=$(( ID + 1 ))
    done
    REP=$(( REP + 1 ))
done

echo
echo "=============================================================="
echo " done on $NAME."
echo "   python3 fit.py $OUT mesh$BASE mesh$LAST"
echo "=============================================================="
