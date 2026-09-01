#!/usr/bin/env bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# =============================================================================
# full_tar.bash - snapshot this node's FrogNet SOURCE into a tarball.
#
# [ONE_MANIFEST_V1] Reads /usr/local/lib/frognet_world_manifest.sh, the same
# list frognet_build_release.sh and frognet_make_build_tar.sh use.
#
# WHY THE EXCLUSIONS ARE BY PATH AND NOT BY EXTENSION
#
# The first version of this script had a long --exclude list by extension, and
# it was there for a real reason: without any exclusions this tree is 675 MB.
# But excluding by extension cut the wrong things:
#
#   --exclude='*.tgz'   took the BROKER, which ships as a bundle. The tarball
#                       had an empty broker directory, so a clone could build a
#                       LAN pond and never cross the internet.
#   --exclude='*.png'   took the web root's assets. The shipped var/www/html
#     '*.svg' '*.ico'   had zero images in it.
#
# The bulk was never those. It is a handful of large VENDORED and GENERATED
# directories, and demo media. Those are named by path below and cut exactly,
# which leaves the source complete and the tarball small.
#
# Everything cut here is either somebody else's repository (with a fetch script
# beside it), output regenerable from source in the tree, or media that is not
# source at all. Nothing cut is needed to build or run a node.
#
#   full_tar.bash [--output DIR] [--tag TAG] [--max-mb N] [--report]
#
#   --report    print what each manifest path costs and exit. Run this first
#               if the tarball is unexpectedly large: it names the offender
#               instead of leaving you to guess at extensions.
#   --max-mb N  fail if the result exceeds N megabytes (default 450, which
#               leaves headroom under a 500 MB transfer limit).
# =============================================================================
set -eu

OUTPUT="${HOME}"
TAG=""
MAX_MB=450
REPORT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --output)   OUTPUT="$2"; shift 2 ;;
        --output=*) OUTPUT="${1#*=}"; shift ;;
        --tag)      TAG="_$2"; shift 2 ;;
        --tag=*)    TAG="_${1#*=}"; shift ;;
        --max-mb)   MAX_MB="$2"; shift 2 ;;
        --max-mb=*) MAX_MB="${1#*=}"; shift ;;
        --report)   REPORT=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

_MANIFEST="/usr/local/lib/frognet_world_manifest.sh"
[ -r "$_MANIFEST" ] || { echo "ERROR: manifest not readable: $_MANIFEST" >&2; exit 1; }
# shellcheck source=/dev/null
. "$_MANIFEST"

frognet_check_manifest / || exit 1

# ---------------------------------------------------------------------------
# NOT SOURCE. Cut by path, so that what is cut is a decision rather than a
# side effect of a filename.
#
# Each of these is regenerable, vendored, or media. If you add to this list,
# add the reason -- a future reader has to be able to tell "this is somebody
# else's repo" from "this is the broker and we lost it".
# ---------------------------------------------------------------------------
NOT_SOURCE=(
    # Third-party conformance corpora. fetch_corpora.sh clones all three.
    'opt/frognet_semantic/codex_fullsuite/corpora'
    # The whole games tree. It is 58 MB on a real node -- ddnet is a C++ game
    # checkout, and the rest is assets, not FrogNet. Cut at the PARENT: scoping
    # this to games/ddnet left the parent to arrive from a live box, which is
    # how a source tarball ends up carrying a game.
    #
    # The FrogNet apps that happen to be games are NOT here. backgammon,
    # hearts, liarsdice, the boardgame engine and games-common are bundles that
    # demonstrate the shared-memory programming model, they are a few hundred
    # KB each, and they are the point.
    'etc/frognet_bundles/games'
    # Build output, regenerable from CMakeLists.txt + src/ + include/.
    'usr/local/bin/frognet_monitor_cpp/build'
    'usr/local/bin/frognet_monitor_cpp/linux'
    # Runtime state, not source.
    'opt/frognet_semantic/blob_cache'
    'opt/frognet_semantic/logs'
    'var/www/html/media'
    # Python environments and dependency trees.
    'opt/frognet_semantic/venv'
    'usr/local/lib/python3.11/site-packages'
)

# Demo captures and recordings. Large, and not source in any sense. Images are
# NOT here on purpose: the site needs them and they are small.
MEDIA_EXT=( 'mp4' 'webm' 'mov' 'mkv' 'avi' 'wav' 'mp3' 'flac' 'iso' 'img' )

# ---- report mode ----------------------------------------------------------
if [ "$REPORT" = "1" ]; then
    echo "manifest paths by size on this host:"
    for _p in "${FROGNET_WORLD_PATHS[@]}"; do
        [ -e "/$_p" ] || continue
        printf '  %10s  %s\n' "$(du -sh "/$_p" 2>/dev/null | cut -f1)" "$_p"
    done | sort -hr
    echo
    echo "cut as not-source:"
    for _p in "${NOT_SOURCE[@]}"; do
        [ -e "/$_p" ] || continue
        printf '  %10s  %s\n' "$(du -sh "/$_p" 2>/dev/null | cut -f1)" "$_p"
    done | sort -hr
    exit 0
fi

OUT="${OUTPUT}/frognet-source${TAG}-$(date +%Y%m%d).tgz"

_EX=()
for _n in '*/__pycache__' '*.pyc' '*.pyo' '*/venv' '*/.venv' '*/site-packages' \
          '*/node_modules' '*/.git' '*.log' \
          '*.bak' '*.bak.*' '*.orig' '*.old' '*~' '*.rej' '*.swp'; do
    _EX+=( "--exclude=$_n" )
done
for _n in "${FROGNET_NEVER_SHIP[@]}"; do _EX+=( "--exclude=$_n" ); done
for _n in "${NOT_SOURCE[@]}";        do _EX+=( "--exclude=$_n" ); done
for _e in "${MEDIA_EXT[@]}";         do _EX+=( "--exclude=*.$_e" ); done

# [SHIP_THE_CODE_TOKENIZE_THE_SECRET_V1] config.php and DB_CONFIG.json are code
# with one secret line each. Excluding them left a checkout with no config.php at
# all, so a --from-repo node had nothing for phase C1b to inject into and api.php
# could not reach the database. Ship them, with the password replaced by the
# placeholder C1b already substitutes -- and that C1b DIES on if any survives.
#
# Staged in a temp tree and handed to tar with a second -C, so the live node's
# real files are excluded and the tokenized ones take their place at the same
# member paths. No post-processing of the archive.
_TOKENIZE=( 'var/www/html/config.php' 'opt/frognet_semantic/DB_CONFIG.json' )
_STAGE="$(mktemp -d /tmp/.frognet_tokenize.XXXXXX)"
trap 'rm -rf "$_STAGE"' EXIT
for _t in "${_TOKENIZE[@]}"; do
    [ -f "/$_t" ] || { echo "ERROR: $_t is in the world but not on this host" >&2; exit 1; }
    mkdir -p "$_STAGE/$(dirname "$_t")"
    sed -E "s/(define\('DB_PASS', *')[^']*(')/\1__FROGNET_DB_PASS__\2/; s/(\"password\" *: *\")[^\"]*(\")/\1__FROGNET_DB_PASS__\2/" \
        "/$_t" > "$_STAGE/$_t"
    grep -q '__FROGNET_DB_PASS__' "$_STAGE/$_t" || {
        echo "ERROR: tokenizing $_t produced no placeholder - the password field did not match. Refusing to ship it with a live credential." >&2
        exit 1; }
    _EX+=( "--exclude=$_t" )
done

# Build uncompressed, append the tokenized copies, then compress. A second -C in
# one tar call does NOT work here: --exclude matches the member NAME, so the same
# pattern that drops the live file drops the staged one behind it. Appending is a
# separate pass, so the exclude cannot reach it.
_TAR="${OUT%.tgz}.tar"
tar -cf "$_TAR" "${_EX[@]}" -C / "${FROGNET_WORLD_PATHS[@]}"
tar -rf "$_TAR" -C "$_STAGE" "${_TOKENIZE[@]}"

# [LICENSE_TRAVELS_WITH_THE_WORK_V1] COPYING and LICENSE also go at the ARCHIVE
# ROOT, because that is where GitHub, SPDX scanners and anyone who unpacks the
# tarball will look for them. They ship inside the world too (see
# usr/local/share/frognet in the manifest), so an installed node has them; these
# are the copies a reader of the repository sees first.
for _l in LICENSE COPYRIGHT README.md CLAIMS.md AI_READ_FIRST.md; do
    [ -f "/usr/local/share/frognet/$_l" ] || { echo "ERROR: /usr/local/share/frognet/$_l missing - refusing to publish a GPL work with no license text" >&2; exit 1; }
    cp "/usr/local/share/frognet/$_l" "$_STAGE/$_l"
done
tar -rf "$_TAR" -C "$_STAGE" LICENSE COPYRIGHT README.md CLAIMS.md AI_READ_FIRST.md
gzip -c "$_TAR" > "$OUT"
rm -f "$_TAR"

_BYTES="$(stat -c %s "$OUT")"
_MB=$(( _BYTES / 1048576 ))
echo "wrote $OUT (${_MB} MB)"

echo "contents:"
tar tzf "$OUT" > /tmp/.ft_list.$$
for _p in "${FROGNET_WORLD_PATHS[@]}"; do
    printf '  %-44s %6s entries\n' "$_p" "$(grep -c "^${_p}" /tmp/.ft_list.$$ || true)"
done
rm -f /tmp/.ft_list.$$

# [NO_FALLBACK_V1] Over the cap is a failure, not a warning. A tarball too
# large to hand to anyone is not a delivered tarball, and finding that out at
# upload time wastes the trip.
if [ "$_MB" -gt "$MAX_MB" ]; then
    echo >&2
    echo "ERROR: ${_MB} MB exceeds --max-mb ${MAX_MB}." >&2
    echo "       Run: full_tar.bash --report" >&2
    echo "       to see which manifest path is carrying the weight, then add it" >&2
    echo "       to NOT_SOURCE in this script WITH A REASON, or cut it here." >&2
    exit 1
fi
