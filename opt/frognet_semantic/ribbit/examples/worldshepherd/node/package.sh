#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# package.sh ARCH -- [WORLDSHEPHERD_ON_A_NODE_V1] dist/worldshepherd-node-<build>-ARCH.tgz: everything one FrogNet node
# of that machine type needs for WorldShepherd, as paths from / (cd / && tar xvzf - < /tmp/<file>). ARCH: aarch64 or
# x86_64. Run by `make tarballs` (aarch64 first), after `make node`. SARA=DIR names the Sara_pro checkout whose
# worldshepherd_sara package and tests go in (John, 2026-11-25: "You can put his code in my tarball. It's not going
# anywhere except onto my machines"): it is in the tarball only, never in this tree.
set -eu
A=${1:?package.sh aarch64|x86_64}; H=$(cd "$(dirname "$0")/.." && pwd); TREE=$(cd "$H/../../../../.." && pwd)
: "${SARA:?package.sh: SARA=DIR, a checkout of Sara_pro at worldshepherd/connectors-distributed-claim-contract-v1-20260913}"
[ -f "$SARA/worldshepherd_sara/connector_ticket_shared.py" ] || { echo "package.sh: $SARA is not that checkout" >&2; exit 1; }
B="$(cut -d' ' -f2 "$H/VERSION")-$(sed -n 's/^BUILD_TAG = "fdf-app-\(.*\)".*/\1/p' "$TREE/opt/fdf/src/fdf-source/fdf/bin/fdf-app")"
REV=i
B="$B$REV"
S=$(mktemp -d); E=$S/opt/frognet_semantic/ribbit/examples/worldshepherd
mkdir -p "$S/opt/fdf/bin" "$E/lib" "$E/node" "$E/harness" "$E/Sara_pro" "$H/dist"
install -m 755 "$TREE/opt/fdf/src/fdf-source/fdf/bin/fdf-app" "$H/node/$A/worldshepherd-ram" "$S/opt/fdf/bin/"
install -m 644 "$H/node/$A/libkartram.so" "$TREE/opt/fdf/src/fdf-source/rinab/kart/ribbit.py" "$E/lib/"
install -m 644 "$H"/{ribbit_claim_store.py,ticket_sql.py,sara_ui.py,VERSION,README.md,DESIGN.md,ACCEPTANCE.md,RESULTS.md} "$E/"
install -m 644 "$H"/harness/{race.py,on_the_memory.py,run_his_tests.py,test_one_session_many_threads.py,acceptance_sara.py} "$E/harness/"
install -m 755 "$H/node/worldshepherd" "$E/node/"; install -m 644 "$H/node/NODE.md" "$E/node/"
mkdir -p "$E/Sara_pro/data"; git -C "$SARA" ls-files data | (cd "$SARA" && xargs -d "\n" cp --parents -t "$E/Sara_pro/")   # the data files his tests read, as committed
cp -r "$SARA/worldshepherd_sara" "$SARA/tests" "$E/Sara_pro/"; find "$E/Sara_pro" -name __pycache__ -prune -exec rm -rf {} +
# [A_NODE_HAS_NO_INTERNET_V1] (Seattle7, 2026-11-25: `apt-get install python3-fastapi` could not resolve deb.debian.org.)
# What SARA's package and its tests import, carried in the tarball: pure Python, the same for every machine type, at
# the versions Debian 12 has (its Python is 3.11). Nothing is fetched on a node. Only the node command's PYTHONPATH
# names this directory, so nothing else on the node sees it.
PYDEPS="fastapi==0.92.0 starlette==0.25.0 pydantic==1.10.21 anyio==3.6.2 sniffio==1.3.1 idna==3.10 typing_extensions==4.12.2 pytest==7.4.4 pluggy==1.5.0 iniconfig==2.0.0 packaging==24.2"
WH="$H/build/pydeps-wheels"; mkdir -p "$WH" "$E/pydeps"
if [ "$(cat "$WH/.pins" 2>/dev/null)" != "$PYDEPS" ]; then
    rm -f "$WH"/*.whl
    python3 -m pip download -q --no-deps --dest "$WH" --only-binary=:all: --platform any --implementation py --abi none --python-version 3.11 $PYDEPS
    echo "$PYDEPS" > "$WH/.pins"
fi
for w in "$WH"/*.whl; do case "$w" in *-py3-none-any.whl|*-py2.py3-none-any.whl) python3 -m zipfile -e "$w" "$E/pydeps/" ;; *) echo "package.sh: $w is not pure Python" >&2; exit 1 ;; esac; done
echo "$PYDEPS" | tr ' ' '\n' > "$E/pydeps/VERSIONS.txt"
echo "Sara_pro $(git -C "$SARA" rev-parse HEAD 2>/dev/null || echo unknown), branch worldshepherd/connectors-distributed-claim-contract-v1-20260913 (Brandon Ray Davis, WorldShepherd R&D)" > "$E/Sara_pro/SOURCE.txt"
echo "worldshepherd-node $B $A; FrogNet $(git -C "$TREE" rev-parse --short HEAD)$([ -z "$(git -C "$TREE" status --porcelain --untracked-files=no)" ] || echo +uncommitted); built $(date -u +%Y-%m-%dT%H:%MZ)" > "$E/node/BUILD"
O="$H/dist/worldshepherd-node-$B-$A.tgz"
tar -C "$S" --owner=0 --group=0 -czf "$O" opt
rm -rf "$S"
echo "TARBALL OK: $O ($(du -h "$O" | cut -f1), $(tar tzf "$O" | grep -vc '/$') files) for $A nodes"
