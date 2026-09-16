#!/bin/sh
# runme.sh -- the whole campaign, one command per node.
#
#   sh runme.sh 10.250.250.1 34
#
# Two arguments: THIS machine's mesh address, and the run id to start at.
# The four runs use mesh<N> .. mesh<N+3>. Give the SAME number on all four
# nodes, and one you have not used before -- rendezvous rows are keyed by
# run id and nothing reclaims them, so reusing one lets a rank read a dead
# process's address out of the store. netbench1 refuses that rather than
# letting it happen, which is what a hardcoded set of ids hit on its second
# use.
#
# The only argument is THIS machine's mesh address. Everything else --
# which rank you are, which runs you take part in, the run ids -- is
# derived from it, so the four nodes cannot disagree about the plan.
#
# It stops before each run and waits for <return>. Hit it on all four
# machines at roughly the same moment: the ranks then start together, which
# is what keeps launch skew out of the smallest size. A 116-second barrier
# in an earlier campaign was four terminals started a minute apart, nothing
# else.
#
# Six runs, in order:
#   N+0  world 4  psychedelic/shard   with New York
#   N+1  world 4  psychedelic/stack   with New York
#   N+2  world 4  gloo                with New York
#   N+3  world 3  psychedelic/shard   local only
#   N+4  world 3  psychedelic/stack   local only
#   N+5  world 3  gloo                local only
#
# Both reduce patterns at both world sizes, plus gloo at both.
#
# SHARD moves ring's byte count, 2(W-1)n/W, in TWO phases. STACK moves
# (W-1)n in ONE. On links of similar speed the byte saving wins and shard is
# the better pattern -- measured at world 3, where shard beat stack by a
# third.
#
# Add a link with a two-second read and that inverts: shard pays the slow
# link TWICE per collective and stack pays it once. At world 4 with New York,
# shard measured 1.53x SLOWER than gloo while its phase timings showed each
# phase was essentially one New York read. Stack was not in this list to
# compare against, which is why it is now.
#
# The comparison that matters is a difference of differences: what world 4
# costs each arm against what world 3 costs it. One arm alone cannot answer
# it, and neither can one world size.
set -e

ADDR="$1"
BASE="$2"
if [ -z "$ADDR" ] || [ -z "$BASE" ]; then
    echo "usage: sh runme.sh <this machine's mesh address> <run id number>"
    echo
    echo "  e.g.  sh runme.sh 10.250.250.1 34"
    echo
    echo "  Uses mesh34..mesh39. Same number on all four nodes,"
    echo "  and one never used before -- run ids are not reused."
    exit 2
fi
case "$BASE" in
    ""|*[!0-9]*) echo "run id must be a number, got '$BASE'"; exit 2 ;;
esac

# The fleet. rank 0 is also the gloo master and hosts the store.
MASTER=10.250.250.100
STORE=databasehost.frognet:80
OUT=/tmp/nb3
ITERS=15
TIMEOUT=600
NB="$(cd "$(dirname "$0")" && pwd)/netbench1.py"
SHOWERR="$(cd "$(dirname "$0")" && pwd)/showerr.py"
GATE="$(cd "$(dirname "$0")" && pwd)/gate.py"
TREE="${FROGNET_TREE:-/opt/frognet_semantic}"
# [ONE_PLACE_SETS_THEM_ALL_V1] Every tunable, in one place, passed on the
# command line to every rank. Change one here and it changes identically on
# all four nodes, because they all run this same file. A knob left in one
# node's shell is how a fleet ends up running two configurations under one
# set of shas -- FROGNET_PSY_PEER_WAIT_S=180 on a single box, against 30 on
# the other three, and nothing in the output saying they disagreed.
PEER_WAIT=300
SERVER_WAIT=300
SOCKET_TIMEOUT=60
SOCKET_MARGIN=60
IDLE_REAP=600
VERIFY=1
POOL=1
MANUAL="${FROGNET_MANUAL_GATE:-0}"

case "$ADDR" in
    10.250.250.100) RANK=0; NAME=AI-Host   ;;
    10.250.250.1)   RANK=1; NAME=Seattle5  ;;
    10.170.170.1)   RANK=2; NAME=Seattle7  ;;
    10.199.199.1)   RANK=3; NAME=BAMacBook ;;
    *) echo "I do not know which rank $ADDR is."
       echo "Known: 10.250.250.100=0  10.250.250.1=1  10.170.170.1=2  10.199.199.1=3"
       exit 2 ;;
esac

[ -f "$NB" ] || { echo "netbench1.py not next to this script ($NB)"; exit 2; }
# [A_ZOMBIE_RANK_IS_STILL_A_PEER_V1]
#
# A netbench1 process left over from an earlier run is not idle. It still
# holds its plane socket on the port it advertised, and it may still hold
# the c10d TCPStore that ranks agree the group token through -- so a new run
# can read the OLD group token, build state names under it, and dial a plane
# that died two campaigns ago. The symptom is a connection that answers and
# then closes, and a group token that is somehow the same as last time.
#
# It costs an hour to work that out from the inside. It costs one ps to
# refuse.
# Match a RUNNING RANK, not any command line that mentions the file: an
# editor, a cp, or this very shell pipeline will contain the string and are
# not stale ranks. A rank is python running netbench1.py with a --rank.
STALE=$(ps -eo pid,args \
        | grep -E "python[0-9.]*[[:space:]]+[^[:space:]]*netbench1\.py" \
        | grep -- "--rank" | grep -v "[[:space:]]--version" || true)
if [ -n "$STALE" ]; then
    echo "There are netbench1 processes already running on this node:"
    echo "$STALE" | sed 's/^/  /'
    echo
    echo "They still hold plane sockets and possibly the rendezvous store,"
    echo "so this run would rendezvous against them. Kill them first:"
    echo "  pkill -f netbench1.py"
    exit 2
fi

# [HOLD_THE_LOCK_FOR_THE_WHOLE_CAMPAIGN_V1] netbench1 takes the runMerge
# lock per arm and drops it when that arm ends, leaving a gap between every
# arm that a merge can start in -- re-plumbing routes mid-campaign, or
# holding the lock so the next arm waits its 120 s and fails. Take it once,
# for the campaign; the ranks inherit it.
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
    echo "NOTE: flock not found; the lock will be taken per arm instead."
fi

mkdir -p "$OUT"

echo "=============================================================="
echo " $NAME  --  rank $RANK at $ADDR"
echo "=============================================================="
# [EVERY_FILE_SAYS_WHAT_IT_IS_V1]
#
# netbench1 --version prints the shas of the TREE modules. It never printed
# the harness's own files, so a node with a new runme.sh and an old
# netbench1.py looked identical to one with all four current -- until an
# unrecognized-argument error, three arms in.
#
# Print them here, where they can be compared across nodes at a glance,
# the same way the tree modules already are.
echo "harness:"
for _f in runme.sh netbench1.py gate.py showerr.py plot_proof.py \
          proof_collect.py; do
    _p="$(cd "$(dirname "$0")" && pwd)/$_f"
    if [ -f "$_p" ]; then
        printf "  %-18s %s\n" "$_f" \
            "$(sha256sum "$_p" | cut -c1-12)"
    else
        printf "  %-18s MISSING\n" "$_f"
    fi
done
echo

python3 "$NB" --version --peer-wait "$PEER_WAIT" \
    --server-wait "$SERVER_WAIT" --socket-timeout "$SOCKET_TIMEOUT" \
    --socket-margin "$SOCKET_MARGIN" --idle-reap "$IDLE_REAP" \
    --verify "$VERIFY" --pool "$POOL" | sed 's/^/  /'
echo
echo "Compare the shas above with the other three nodes BEFORE starting."
echo "A fleet running different code measures two things and reports one."
echo
echo "run ids: mesh$BASE mesh$(( BASE + 1 )) mesh$(( BASE + 2 )) \
mesh$(( BASE + 3 )) mesh$(( BASE + 4 )) mesh$(( BASE + 5 ))"
echo "         the same four on every node, and never reused."
echo

# [THE_PRIMARY_SAYS_GO_THROUGH_THE_STORE_V1]
#
# Rank 0 publishes "this arm is starting" and everyone else blocks on a read
# of that cell. No keypresses, nothing that leaves a run waiting on a human,
# and the followers start within a store round trip of each other instead of
# within however fast four keyboards can be hit -- a campaign spent 116
# seconds in its first barrier for no reason but launch skew.
#
# Same shape as the collectives: publish a fact, read what is there. The
# primary does not need to know who is listening, and a follower that
# arrives late still finds the cell.
#
# FROGNET_MANUAL_GATE=1 keeps the keypress, for when the store itself is
# what is being debugged and cannot be relied on to carry the signal.
wait_for_go() {
    GID="$1"; LABEL="$2"
    echo
    echo ">>> $LABEL"
    if [ "$MANUAL" = "1" ]; then
        echo ">>> press <return> on all four nodes together"
        read _ignored
        return 0
    fi
    if [ "$RANK" = "0" ]; then
        # A moment for the followers to be waiting before the gate opens.
        # Not required -- a late reader still finds the cell -- but it keeps
        # the start times tight.
        sleep 2
        python3 "$GATE" --role primary --runid "$GID" --arm 0 \
            --store "$STORE" --tree "$TREE" || exit 2
    else
        echo ">>> waiting for rank 0 to open this arm"
        python3 "$GATE" --role follower --runid "$GID" --arm 0 \
            --store "$STORE" --tree "$TREE" || exit 2
    fi
}

do_run() {   # id, world, backend, reduce, label
    ID="$1"; W="$2"; BE="$3"; RED="$4"; LABEL="$5"
    if [ "$RANK" -ge "$W" ]; then
        echo "--- $ID ($LABEL): world $W, rank $RANK sits this one out."
        return 0
    fi
    wait_for_go "$ID" "$ID  $LABEL"
    echo "--- $ID: world $W, $BE${RED:+/$RED} ..."
    if python3 "$NB" --rank "$RANK" --world "$W" --backend "$BE" \
        ${RED:+--reduce $RED} --runid "$ID" --addr "$ADDR" \
        --master "$MASTER" --store "$STORE" \
        --profile slope --iters "$ITERS" --timeout "$TIMEOUT" --diag \
        --peer-wait "$PEER_WAIT" --server-wait "$SERVER_WAIT" \
        --socket-timeout "$SOCKET_TIMEOUT" --socket-margin "$SOCKET_MARGIN" \
        --idle-reap "$IDLE_REAP" --verify "$VERIFY" --pool "$POOL" \
        --out "$OUT" > "$OUT/$ID.rank$RANK.log" 2>&1
    then
        echo "    ok"
    else
        echo "    FAILED -- $OUT/$ID.rank$RANK.log"
        # The reason lives in the result JSON, not at the end of the log:
        # the log's last lines are the banner and the lock release, which is
        # what an earlier version of this printed, and it told nobody
        # anything.
        python3 "$SHOWERR" "$OUT" "$RANK" "$BE" "$RED" "$ID" | sed 's/^/      /'
    fi
}

do_run "mesh$BASE"           4 psychedelic shard "world 4, WITH New York"
do_run "mesh$(( BASE + 1 ))" 4 psychedelic stack "world 4, WITH New York"
do_run "mesh$(( BASE + 2 ))" 4 gloo        ""    "world 4, WITH New York, control"
do_run "mesh$(( BASE + 3 ))" 3 psychedelic shard "world 3, local only"
do_run "mesh$(( BASE + 4 ))" 3 psychedelic stack "world 3, local only"
do_run "mesh$(( BASE + 5 ))" 3 gloo        ""    "world 3, local only, control"

echo
echo "=============================================================="
echo " done on $NAME."
echo
echo " Copy $OUT from all four nodes onto one machine, then:"
echo "   python3 proof_collect.py $OUT"
echo "   python3 plot_proof.py    $OUT plots/"
echo "=============================================================="
