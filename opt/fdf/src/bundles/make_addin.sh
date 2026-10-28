#!/bin/bash
# make_addin.sh -- [ADDIN_V1] one add-in as its own bundle, fdf-addin-<name>-linux-<arch|any>-<build>.tgz:
#   fdf-addin-<name>-<build>/install                    looks first (a core here? what it needs? this processor?), installs
#   fdf-addin-<name>-<build>/opt/fdf/addins/<name>/     ADDIN (name, build, arch, on-loss, about), start, its programs
#   fdf-addin-<name>-<build>/opt/fdf/addins/lib/        addin.sh (control RAM, own tuple, on-loss) -- the same in every add-in
# The add-ins:
#   comms   the Communicator's servers: bin/comms-ram, bin/comms-media (rinab tools/build_comms_static.sh <arch>), per arch
#   kart    FrogNet Kart: the kart service, game hosts and the game (kart/*.py) and libkartram.so (kart/build_kartram.sh
#           [--arm64]), per arch; its RAMs are the core's rina-host
#   dc      DisasterComm: its pages (opt/fdf/www/dc) and their Apache config -- any arch
# usage: make_addin.sh comms|kart|dc OUTDIR [x86_64|aarch64]
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
  kart)  L=$R/kart/$([ "$ARCH" = aarch64 ] && echo arm64/)libkartram.so
         [ -f "$L" ] || { echo "make_addin: no $L (kart/build_kartram.sh$([ "$ARCH" = aarch64 ] && echo ' --arm64'))" >&2; exit 1; }
         cp "$S/addins/kart/kart-play" "$S/addins/kart/kart-find" $A/$N/
         for f in kart_service.py kart_host.py kart_server.py kartmem.py ribbit.py kartdemo.py DESIGN.md README.md; do cp "$R/kart/$f" $A/$N/; done
         cp "$L" $A/$N/libkartram.so ;;
  dc)    ARCH=any; F=$(cd "$S/../fdf-source/fdf" && pwd); mkdir -p $P/opt/fdf/www $P/etc/apache2/conf-available
         cp -a "$F/www/dc" $P/opt/fdf/www/dc; cp "$S/addins/dc/fdf-dc.conf" $P/etc/apache2/conf-available/ ;;
esac
{ grep -v '^arch=' "$S/addins/$N/ADDIN"; echo "arch=$ARCH"; echo "built=$(date -u +%Y-%m-%dT%H:%MZ)"; } > $A/$N/ADDIN
chmod 755 $P/install $A/$N/start; find $A -name __pycache__ -prune -exec rm -rf {} +
OUT=$O/fdf-addin-$N-linux-$ARCH-$B.tgz
tar -C $T -czf "$OUT" "fdf-addin-$N-$B"; ls -la "$OUT"; cat $A/$N/ADDIN
