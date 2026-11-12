#!/bin/bash
# rt-oracle.sh: RepeaterTastic interop oracle against stock meshtasticd, host mode, fresh state.
# Same sequence as tests/interop/README.md "golden vectors", minus docker.
set -u
export MESHTASTICD_BIN=${MESHTASTICD_BIN:?}
PY=/home/claude/mesh/venv-mpy/bin/python
RT=/home/claude/mesh/rt/tests/interop
OUT=/home/claude/mesh/p0/rt-vectors
T=/home/claude/mesh/tools
$T/rt-host.sh down --wipe >/dev/null 2>&1
rm -rf $OUT; mkdir -p $OUT
$T/rt-host.sh up 2 || exit 10
cd $RT
$PY setup_nodes.py 127.0.0.1:4410 127.0.0.1:4411 --lat 51.5 --lon -0.12 || { echo "SETUP FAILED"; $T/rt-host.sh down; exit 11; }
sleep ${RT_SETTLE:-70}
$PY setup_nodes.py 127.0.0.1:4410 127.0.0.1:4411 --show-nodes
$PY capture_vectors.py 127.0.0.1:4410 127.0.0.1:4411 --out $OUT --image "native-stock-$(basename $MESHTASTICD_BIN)"
CRC=$?
echo "=== capture rc=$CRC"
# verify_vectors.py checks vectors/*/*.json beside itself; run it on a copy pointed at OUT
mkdir -p $OUT/.verify && cp verify_vectors.py $OUT/.verify/ && ln -sfn $OUT $OUT/.verify/vectors
$PY $OUT/.verify/verify_vectors.py
VRC=$?
echo "=== verify rc=$VRC"
$T/rt-host.sh down
[ $CRC -eq 0 ] && [ $VRC -eq 0 ]
