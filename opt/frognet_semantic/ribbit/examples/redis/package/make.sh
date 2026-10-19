#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# make.sh <redis-7.2.11 git checkout> <out.tgz> -- the acceptance package: one tarball for every machine type (it builds
# where it runs), untarred at / into /opt/redis-ribbit-accept: stock Redis's source, this region (the tree's layout kept,
# so its build finds ribbit/cpp), run.sh and README.txt from here, and VERSION (this tree's commit).
set -eu
RSRC=$1; OUT=$2; HERE=$(cd "$(dirname "$0")" && pwd); TREE=$(cd "$HERE/../../../../../.." && pwd)
W=$(mktemp -d); P=$W/opt/redis-ribbit-accept; mkdir -p $P/ribbit/examples $P/ribbit/cpp
git -C "$RSRC" archive --prefix=redis-7.2.11/ HEAD | tar -x -C $P
git -C "$TREE" archive HEAD opt/frognet_semantic/ribbit/examples/redis | tar -x -C $W
mv $W/opt/frognet_semantic/ribbit/examples/redis $P/ribbit/examples/redis
git -C "$TREE" archive HEAD opt/frognet_semantic/ribbit/cpp/ensure_build_deps.sh opt/frognet_semantic/ribbit/cpp/include opt/frognet_semantic/ribbit/cpp/src | tar -x -C $W   # [ONE_ORDER_V1] the box links ribbit/cpp
cp -r $W/opt/frognet_semantic/ribbit/cpp/. $P/ribbit/cpp/
rm -rf $W/opt/frognet_semantic; cp "$HERE/run.sh" "$HERE/README.txt" $P/; rm -rf $P/ribbit/examples/redis/package
echo "$(git -C "$TREE" rev-parse --short HEAD) $(date -u +%Y-%m-%dT%H:%MZ)" > $P/ribbit/examples/redis/VERSION
tar czf "$OUT" -C $W opt; rm -rf $W; echo "built $OUT"
