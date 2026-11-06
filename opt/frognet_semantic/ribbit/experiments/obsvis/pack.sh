#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# pack.sh [OUTDIR] -- the harness as one tarball, obsvis_<build>.tgz: this directory (without results or build) and
# the Ribbit sources it builds from (ribbit/cpp include+src, ribbit/defaults/api, ribbit/examples/chat), under
# obsvis_<build>/ribbit/... so build.sh finds them where it looks.
set -eu
cd "$(dirname "$0")"
B=$(sed -n 's/^BUILD = "\(obsvis-[^"]*\)"/\1/p' run.py | sed 's/^obsvis-//')
N=obsvis_$B; O=${1:-.}; T=$(mktemp -d)
R=../..
mkdir -p $T/$N/ribbit/cpp $T/$N/ribbit/defaults $T/$N/ribbit/examples $T/$N/ribbit/experiments
cp -r $R/cpp/include $R/cpp/src $R/cpp/ensure_build_deps.sh $T/$N/ribbit/cpp/
rsync -a --exclude build $R/defaults/api $T/$N/ribbit/defaults/
rsync -a --exclude build $R/examples/chat $T/$N/ribbit/examples/
rsync -a --exclude results --exclude results-smoke --exclude build --exclude __pycache__ --exclude '*.tgz' ./ $T/$N/ribbit/experiments/obsvis/
tar czf $O/$N.tgz -C $T $N && rm -rf $T
echo "$O/$N.tgz"
