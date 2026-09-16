#!/bin/sh
# install.sh -- put every file where it goes, on this node, and prove it.
#
#   sh install.sh                       install into /opt/frognet_semantic
#   sh install.sh --tree /some/path     elsewhere
#   sh install.sh --check               verify only, change nothing
#   sh install.sh --record              re-stamp the manifest from what is
#                                       on disk, without copying anything.
#                                       Use after replacing a file by hand.
#   sh install.sh --with-api            also install web/api.php (db host only)
#
# What it installs, and where:
#
#   tree/tuplespace/*.py -> $TREE/agent_workload/tuplespace/
#   tree/core/*.py       -> $TREE/core/
#   bench/*              -> $BENCH  (default /usr/local/frognet-ai)
#   web/api.php          -> /var/www/html/  ONLY with --with-api
#
# Everything it replaces is backed up under $TREE/.frognet-ai-backup-<stamp>/.
#
# It then writes $TREE/.frognet-ai-version: the build id and the sha256 of
# every file it installed. netbench1.py reads that file at the start of every
# run and refuses to measure a tree that does not match it -- which is the
# check that would have caught three separate wasted campaigns where one node
# was running different code from the others.
set -e

TREE=/opt/frognet_semantic
BENCH=/usr/local/frognet-ai
CHECK=0
RECORD=0
WITH_API=0
HERE="$(cd "$(dirname "$0")" && pwd)"
# [STAND_SOMEWHERE_THAT_EXISTS_V1] Python puts the working directory first
# on sys.path. If the shell is sitting in a directory that has been deleted,
# every import below fails with FileNotFoundError and torch fails halfway
# through as a "partially initialized module ... circular import" -- which
# reads as a broken tree and a broken torch, and is neither.
if ! pwd >/dev/null 2>&1; then
    echo "the current directory no longer exists -- cd somewhere real first"
    exit 1
fi
cd "$HERE"

while [ $# -gt 0 ]; do
    case "$1" in
        --tree) TREE="$2"; shift 2 ;;
        --bench) BENCH="$2"; shift 2 ;;
        --check) CHECK=1; shift ;;
        --record) RECORD=1; shift ;;
        --with-api) WITH_API=1; shift ;;
        *) echo "unknown: $1"; exit 2 ;;
    esac
done

BUILD="$(cat "$HERE/BUILD" 2>/dev/null || echo unknown)"
VERSION_FILE="$TREE/.frognet-ai-version"

say()  { echo "  $*"; }
fail() { echo "  FAIL  $*"; RC=1; }
RC=0

echo "frognet-ai $BUILD"
echo "tree:  $TREE"
echo "bench: $BENCH"
echo

# ---- what must already be here -----------------------------------------
# This package carries the files that changed. It does not carry the tree.
[ -d "$TREE" ] || { echo "no tree at $TREE -- deploy FrogNet there first"; exit 1; }
MISSING=""
for f in agent_workload/tuplespace/finite.py \
         agent_workload/tuplespace/torch_store.py \
         agent_workload/aiconnect/service.py \
         core/hosts_only.py core/stat_schema.py; do
    [ -f "$TREE/$f" ] || MISSING="$MISSING $f"
done
if [ -n "$MISSING" ]; then
    echo "$TREE is missing files this package does NOT ship:$MISSING"
    echo "Those come from the FrogNet tree itself. Copy the whole tree from a"
    echo "node that works, then run this again."
    exit 1
fi

# ---- record mode --------------------------------------------------------
# A tree updated by hand -- one file dropped in rather than a whole install
# -- differs from its recorded build for an ordinary reason. This re-stamps
# the record so the difference stops being reported, WITHOUT copying
# anything, so it can never quietly install something.
if [ "$RECORD" = "1" ]; then
    [ -f "$VERSION_FILE" ] || { echo "nothing recorded yet at $VERSION_FILE"
                                echo "run a normal install first"; exit 1; }
    {
        echo "$BUILD (re-recorded $(date -u +%Y-%m-%dT%H:%M:%SZ))"
        sed '1d' "$VERSION_FILE" | while read -r _sha path; do
            [ -f "$path" ] || continue
            echo "$(sha256sum "$path" | cut -d' ' -f1) $path"
        done
    } > "$VERSION_FILE.new"
    mv "$VERSION_FILE.new" "$VERSION_FILE"
    echo "re-recorded $(( $(wc -l < "$VERSION_FILE") - 1 )) file(s) as they are on disk"
    exit 0
fi

# ---- verify mode --------------------------------------------------------
if [ "$CHECK" = "1" ]; then
    [ -f "$VERSION_FILE" ] || { echo "not installed: no $VERSION_FILE"; exit 1; }
    echo "installed build: $(head -1 "$VERSION_FILE")"
    BAD=0
    sed '1d' "$VERSION_FILE" | while read -r sha path; do
        [ -n "$sha" ] || continue
        got=$(sha256sum "$path" 2>/dev/null | cut -d' ' -f1)
        if [ "$got" != "$sha" ]; then
            echo "  DIFFERS  $path"
            echo "           installed $got"
            echo "           expected  $sha"
            BAD=1
        fi
    done
    # subshell: recheck outside it
    if sed '1d' "$VERSION_FILE" | while read -r sha path; do
           got=$(sha256sum "$path" 2>/dev/null | cut -d' ' -f1)
           [ "$got" = "$sha" ] || exit 1
       done; then
        echo "  all installed files match the recorded build"
        exit 0
    else
        echo "  tree does NOT match its recorded build"
        exit 1
    fi
fi

# ---- install ------------------------------------------------------------
STAMP=$(date +%Y%m%d-%H%M%S)
BK="$TREE/.frognet-ai-backup-$STAMP"

put() {   # src, dst
    src="$1"; dst="$2"
    if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then
        mkdir -p "$(dirname "$BK/${dst#/}")"
        cp -p "$dst" "$BK/${dst#/}"
        say "REPLACE $dst"
    elif [ -f "$dst" ]; then
        say "same    $dst"
        return 0
    else
        say "NEW     $dst"
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
}

for f in "$HERE"/tree/tuplespace/*.py; do
    put "$f" "$TREE/agent_workload/tuplespace/$(basename "$f")"
done
for f in "$HERE"/tree/core/*.py; do
    put "$f" "$TREE/core/$(basename "$f")"
done

mkdir -p "$BENCH"
for f in "$HERE"/bench/*; do
    put "$f" "$BENCH/$(basename "$f")"
done

if [ "$WITH_API" = "1" ]; then
    if [ -d /var/www/html ]; then
        put "$HERE/web/api.php" /var/www/html/api.php
    else
        fail "--with-api given but /var/www/html does not exist"
    fi
fi

# ---- record what was installed -----------------------------------------
{
    echo "$BUILD"
    for f in "$HERE"/tree/tuplespace/*.py; do
        d="$TREE/agent_workload/tuplespace/$(basename "$f")"
        echo "$(sha256sum "$d" | cut -d' ' -f1) $d"
    done
    for f in "$HERE"/tree/core/*.py; do
        d="$TREE/core/$(basename "$f")"
        echo "$(sha256sum "$d" | cut -d' ' -f1) $d"
    done
} > "$VERSION_FILE"

echo
say "recorded $(( $(wc -l < "$VERSION_FILE") - 1 )) file(s) in $VERSION_FILE"
[ -d "$BK" ] && say "replaced files backed up under $BK"

# ---- prove it works -----------------------------------------------------
echo
echo "verifying:"
PYP=""
[ -d /etc/frognet_bundles/communicator ] && PYP=/etc/frognet_bundles/communicator
if PYTHONPATH="${PYP:+$PYP:}$TREE" python3 - <<'PY'
import sys
bad = []
for m in ("core.frognet_tuples",
          "agent_workload.tuplespace.tensor_plane",
          "agent_workload.tuplespace.list_store",
          "agent_workload.tuplespace.store_server",
          "agent_workload.tuplespace.torch_backend",
          "agent_workload.tuplespace.psychedelic_backend"):
    try:
        __import__(m)
    except Exception as e:
        bad.append("%s: %s: %s" % (m, type(e).__name__, e))
for b in bad:
    print("  FAIL  import %s" % b)
sys.exit(1 if bad else 0)
PY
then
    say "every module imports"
else
    fail "the installed tree does not import"
fi

if python3 -c "import torch, torch.distributed as d; assert d.is_gloo_available()" 2>/dev/null; then
    say "torch $(python3 -c 'import torch;print(torch.__version__)') with gloo"
else
    fail "torch missing or built without gloo -- there is no control arm"
fi

echo
if [ "$RC" = "0" ]; then
    echo "READY.  Run a benchmark with:"
    echo "  cd $BENCH && python3 netbench1.py --version"
else
    echo "NOT READY -- see FAIL above"
fi
exit $RC
