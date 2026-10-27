#!/bin/bash
# make_addin.sh -- [ADDIN_V1] one add-in as its own bundle, fdf-addin-<name>-linux-<arch|any>-<build>.tgz:
#   fdf-addin-<name>-<build>/install                    looks first (a core here? what it needs? this processor?), installs
#   fdf-addin-<name>-<build>/opt/fdf/addins/<name>/     ADDIN (name, build, arch, on-loss, about), start, its programs
#   fdf-addin-<name>-<build>/opt/fdf/addins/lib/        addin.sh (control RAM, own tuple, on-loss) -- the same in every add-in
# The add-ins:
#   comms   the Communicator's servers: bin/comms-ram, bin/comms-media (rinab tools/build_comms_static.sh <arch>), per arch
#   kart    the kart race: its RAM is the core's rina-host; kart-play + game/ (kartdemo.py, ribbit.py; pygame) -- any arch
# usage: make_addin.sh comms|kart OUTDIR [x86_64|aarch64]
set -eu
S=$(cd "$(dirname "$0")" && pwd); R=$(cd "$S/../fdf-source/rinab" && pwd)
N=${1:?usage: make_addin.sh comms|kart OUTDIR [arch]}; O=$(mkdir -p "${2:?usage: make_addin.sh NAME OUTDIR [arch]}" && cd "$2" && pwd)
ARCH=${3:-$(uname -m)}; T=$(mktemp -d); trap 'rm -rf $T' EXIT
[ -f "$S/addins/$N/ADDIN" ] || { echo "make_addin: no add-in $N in $S/addins" >&2; exit 2; }
B=$(sed -n 's/^build=//p' "$S/addins/$N/ADDIN")
P=$T/fdf-addin-$N-$B; A=$P/opt/fdf/addins; mkdir -p $A/$N
cp -a "$S/addins/lib" $A/lib; cp "$S/addins/$N/start" $A/$N/; cp "$S/addins/install" $P/install
case "$N" in
  comms) C=$R/build/comms-$([ "$ARCH" = aarch64 ] && echo arm64 || echo x86_64)
         for b in comms-ram comms-media; do [ -x "$C/$b" ] || { echo "make_addin: no $C/$b (rinab tools/build_comms_static.sh)" >&2; exit 1; }; done
         mkdir -p $A/$N/bin; cp $C/comms-ram $C/comms-media $A/$N/bin/ ;;
  kart)  ARCH=any; cp "$S/addins/kart/kart-play" $A/$N/; mkdir -p $A/$N/game; cp "$R/kart/kartdemo.py" "$R/kart/ribbit.py" $A/$N/game/ ;;
esac
{ grep -v '^arch=' "$S/addins/$N/ADDIN"; echo "arch=$ARCH"; echo "built=$(date -u +%Y-%m-%dT%H:%MZ)"; } > $A/$N/ADDIN
chmod 755 $P/install $A/$N/start
OUT=$O/fdf-addin-$N-linux-$ARCH-$B.tgz
tar -C $T -czf "$OUT" "fdf-addin-$N-$B"; ls -la "$OUT"; cat $A/$N/ADDIN
