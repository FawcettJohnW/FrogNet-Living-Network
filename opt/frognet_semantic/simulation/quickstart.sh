#!/bin/bash
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
# quickstart.sh -- [QUICKSTART_V1] see CroakOS work on one Linux machine, in about a minute after the first build.
#
#   bash opt/frognet_semantic/simulation/quickstart.sh          (from the checkout's root, or from anywhere)
#
# It builds the CroakOS binaries the first time (g++, liblz4-dev, libssl-dev), then runs real CroakOS processes, one per
# simulated machine, and says in plain words what happened: machines at three sites becoming one network that reaches
# between sites only through the region hub; then a network losing a link, losing an exit to the Internet, getting it
# back, machines moving, leaving and rejoining, and one machine's link going dark and coming back. After every one of
# those, every path between every pair of machines is checked. The full gate is run_all.py; SIM_STATUS.md says what
# each tier covers.
set -e -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; FS="$(dirname "$HERE")"; ROOT="$(cd "$FS/../.." && pwd)"
RINAB="$FS/../fdf/src/fdf-source/rinab"; BIN="$RINAB/build/static"
if ! [ -x "$BIN/fdf-ram" ] || ! [ -x "$BIN/fdf-client" ] || ! [ -x "$BIN/fdf-events" ]; then
    echo "== Building the CroakOS binaries once (a few minutes) =="
    (cd "$RINAB" && bash tools/build.sh && bash tools/build_static_host.sh && bash tools/build_static_clients.sh)
fi
OUT="$(mktemp -d /tmp/croakos-quickstart-XXXXXX)"
export PYTHONPATH="$FS" FROGNET_PROXY_ROOT="$FS" FROGNET_BIN_ROOT="$ROOT/usr/local/bin" PYTHONUTF8=1
echo
echo "== 1. Three sites become one network =="
echo "   Site A is a small LAN, site B a chain of three machines, site C a star. Each site has a gateway with an"
echo "   Internet link. CroakOS joins all of them into one network; between sites, traffic goes through the region hub."
python3 "$HERE/s_topo_tier.py" --bin "$BIN" --links lan --out "$OUT" --work "$OUT" --only topo_asym_three_sites \
    | awk '/\[(PASS|FAIL)\]/ && !n {print; n=1}'
python3 - "$OUT" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1] + "/verify-topo_asym_three_sites-cold.tsv"), delimiter="\t"))
ok = sum(r["verdict"].startswith("ok") for r in rows)
hub = [r for r in rows if "hub" in r["path"].split(">")]
print(f"   {ok} of {len(rows)} machine-to-network paths work. {len(hub)} of them cross between sites, all through the hub, for example:")
for r in hub[:3]:
    print("     " + r["path"].replace(">", " -> "))
PY
echo
echo "== 2. A network that changes under you =="
echo "   Eleven machines, two exits to the Internet (one fast, one slow). Then: a link goes down, the fast exit is"
echo "   lost and comes back, a machine moves, machines leave and rejoin, and one machine's link goes dark. After each"
echo "   change every path is checked against the one the network should now use."
python3 "$HERE/s_topo_tier.py" --bin "$BIN" --links lan --out "$OUT" --work "$OUT" --only mesh \
    | grep -E '^\s+\[(PASS|FAIL)\]|^\s+\.\. |^S TOPO'
echo
echo "Every path, step by step: $OUT/verify-*.tsv   The whole gate: python3 simulation/run_all.py (SIM_STATUS.md)"
