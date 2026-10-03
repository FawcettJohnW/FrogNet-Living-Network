#!/bin/bash
# frognet_license_gaps.sh - name every file the license oracle is failing on.
#
# The oracle prints the first twelve and a count. When the count does not match
# what you expect, the count is useless and the LIST is what you need.
#
# Walks exactly what the oracle walks: the paths in FROGNET_WORLD_PATHS, using
# the same classifier the applier uses. No arguments.

set -u
MAN=/usr/local/lib/frognet_world_manifest.sh
[ -r "$MAN" ] || { echo "no manifest at $MAN" >&2; exit 1; }
# shellcheck source=/dev/null
. "$MAN"

exts='py|sh|bash|php|js|css|sql|cpp|h|hpp|cs|kt|html|service|timer|target|conf|cnf|yml|yaml|toml'
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

for p in "${FROGNET_WORLD_PATHS[@]}"; do
    [ -e "/$p" ] || continue
    find "/$p" -type f \
        -not -path '*/__pycache__/*' -not -path '*/venv/*' \
        -not -path '*/python3.11/*' 2>/dev/null
done | sort -u | while read -r f; do
    base="${f##*/}"
    case "$base" in COPYING|LICENSE|COPYRIGHT|README.md|.gitignore) continue ;; esac
    if [[ "$base" == *.* ]]; then
        ext="${base##*.}"
        [[ "$ext" =~ ^($exts)$ ]] || continue
    else
        head -c2 "$f" 2>/dev/null | grep -q '#!' || continue
    fi
    grep -q 'SPDX-License-Identifier: GPL-2.0-only' "$f" 2>/dev/null || echo "$f"
done > "$tmp"

echo "files with no GPL-2.0-only notice: $(wc -l < "$tmp")"
echo
echo "-- by directory --"
sed 's|/[^/]*$||' "$tmp" | sort | uniq -c | sort -rn
echo
echo "-- by extension --"
sed 's|.*\.||' "$tmp" | sort | uniq -c | sort -rn | head -12
echo
echo "-- full list --"
cat "$tmp"
