#!/bin/bash
# make_scale_test.sh OUTDIR -- [SCALE_PACKAGE_V1] one tarball, x86-64, that unpacks to /opt/fdf/experiments/scale:
#   scale-series                    the runner (experiments/scale/scale-series)
#   rinab/build/static/             the static binaries the bed and fdf-hub run, every one this build's tag
#   rinab/tools/{netns-scale-test.sh,fdf-hub}
#   fdf/{bin/fdf-facts,lib/python,templates}
#   frognet_semantic/               the tunnel daemon's tree (no docs, ribbit, simulation, tests)
#   bin/wireguard-go                the WireGuard the bed uses (libc only)
#   BUILD                           the build tag and the commit
# On the machine:  cd / && tar xvzf - < /tmp/scale-test.tgz ; /opt/fdf/experiments/scale/scale-series
set -eu; H=$(cd "$(dirname "$0")/.." && pwd); TOP=$(cd $H/.. && pwd); FN=$(cd $H/../../../../frognet_semantic && pwd)
OUT=${1:?usage: make_scale_test.sh OUTDIR}; mkdir -p $OUT
TAG=$(grep -o 'fdf-[0-9-]*-r[0-9]*' $H/src/fdf_version.hpp)
BINS="rina-host fdf-client fdf-events fdf-where fdf-participants fdf-attention ram"    # what the bed and fdf-hub run
for b in $(for x in $BINS; do echo $H/build/static/$x; done); do [ -x $b ] || { echo "make_scale_test: $b missing"; exit 1; }; t=$(strings $b | grep -o 'fdf-[0-9-]*-r[0-9]*' | sort -u | tr '\n' ' ')
  [ -z "$t" ] || strings $b | grep -qx "$TAG" || strings $b | grep -q "$TAG" || { echo "make_scale_test: $b carries $t, not $TAG: rebuild first"; exit 1; }; done   # older tags appear as message text
S=$(mktemp -d); R=$S/opt/fdf/experiments/scale; mkdir -p $R/rinab/build/static $R/rinab/tools $R/fdf/bin $R/fdf/lib $R/fdf/templates $R/bin
cp $H/experiments/scale/scale-series $R/
for x in $BINS; do cp $H/build/static/$x $R/rinab/build/static/; done
cp $H/tools/netns-scale-test.sh $H/tools/fdf-hub $R/rinab/tools/
cp $TOP/fdf/bin/fdf-facts $R/fdf/bin/; cp -a $TOP/fdf/lib/python $R/fdf/lib/; cp -a $TOP/fdf/templates/. $R/fdf/templates/
tar -C $FN --exclude=./docs --exclude=./ribbit --exclude=./simulation --exclude=./tests --exclude=./codex_fullsuite \
    --exclude=./oracles --exclude=./frogsim --exclude='__pycache__' -cf - . | tar -C $R -xf - --one-top-level=frognet_semantic
cp "$(command -v wireguard-go)" $R/bin/
{ echo "$TAG"; git -C $H log -1 --format='commit %h %cI'; } > $R/BUILD
find $R -name __pycache__ -prune -exec rm -rf {} +
tar -C $S -czf $OUT/scale-test.tgz opt; rm -rf $S
echo "$OUT/scale-test.tgz  $(du -h $OUT/scale-test.tgz | cut -f1)  sha256 $(sha256sum $OUT/scale-test.tgz | cut -c1-16)  $TAG"
