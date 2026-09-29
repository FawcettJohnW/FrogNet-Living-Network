#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- builds the Communicator example into build/, ready to run from there or to copy anywhere:
#   build/comms-ram        the Communicator's RAM host
#   build/libtuples.so     the default Ribbit API's client library (ribbit/defaults/api)
#   build/core/            frognet_tuples (the default API, the node's signatures) and the FrogNet modules the
#                          Communicator imports (hosts_only, the semantic codec and wire, the SOTF handler), copied
#                          from this tree's own opt/frognet_semantic/core (SEMANTIC)
#   build/*.py, assets/    the Communicator itself, unchanged
set -eu
cd "$(dirname "$0")"
RIBBIT=${RIBBIT:-$(cd ../../cpp && pwd)}
DEFAULTS=${DEFAULTS:-$(cd ../../defaults/api && pwd)}
SEMANTIC=${SEMANTIC:-$(cd ../../.. && pwd)}                 # opt/frognet_semantic: its core/ modules are copied, not duplicated
CXX=${CXX:-g++}
rm -rf build; mkdir -p build/core
F="-std=c++17 -O2 -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I$RIBBIT/include -I$DEFAULTS"
echo "== comms-ram";    $CXX $F comms_ram.cpp "$RIBBIT/src/ram_host.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/comms-ram
echo "== libtuples.so"; $CXX $F -shared -fPIC "$DEFAULTS/tuples_c.cpp" "$DEFAULTS/tuples.cpp" "$RIBBIT/src/ram_client.cpp" "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o build/libtuples.so
tar cf - --exclude=build --exclude=core --exclude=comms_ram.cpp --exclude=build.sh --exclude=test.sh --exclude=README.md . | (cd build && tar xf -)
for m in __init__ hosts_only sotf_handler semcache_wire codec unrest_handler frognet_diag; do cp "$SEMANTIC/core/$m.py" build/core/; done
cp "$DEFAULTS/frognet_tuples.py" build/core/
# `import frognet_tuples` and `from core import frognet_tuples` are the one module, as on a node
cat > build/frognet_tuples.py <<'PY'
"""frognet_tuples -- the same module as core.frognet_tuples (the default Ribbit API), under the name the Communicator imports."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import frognet_tuples as _m
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
sys.modules[__name__] = _m
PY
echo "built: build/comms-ram build/libtuples.so build/core and the Communicator"
