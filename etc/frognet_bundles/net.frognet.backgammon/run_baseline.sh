#!/usr/bin/env bash
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
# run_baseline.sh — run the engine/codex tests, then smoke-test the verbs over HTTP.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
PORT="${MOCK_PORT:-8893}"; FAILS=0
note(){ echo "[baseline] $*"; }
for c in "${FROGNET_SEMANTIC:-}" /opt/frognet_semantic /tmp/ft/opt/frognet_semantic; do
  if [ -n "$c" ] && [ -f "$c/core/codec.py" ]; then export PYTHONPATH="$c${PYTHONPATH:+:$PYTHONPATH}"; note "tree: $c"; break; fi
done
note "running engine + codex tests ..."; python3 tests/test_backgammon_codex.py || FAILS=$((FAILS+1))
note "running convergence sim (doctrine served path: SAME/DIFF, real codec) ..."
python3 dev/convergence_sim.py | grep -E "RESULT|move on the wire|SAME|reconstruction" || true
python3 dev/convergence_sim.py >/dev/null 2>&1 || FAILS=$((FAILS+1))
note "starting dev server + smoke-testing verbs ..."
python3 - "$PORT" <<'PY' >/tmp/bg_base.log 2>&1 &
import sys,os,threading; sys.path.insert(0,"dev"); sys.path.insert(0,"codex")
import serve_web_test as W
from http.server import ThreadingHTTPServer
ThreadingHTTPServer(("127.0.0.1",int(sys.argv[1])),W.H).serve_forever()
PY
SRV=$!; trap 'kill "$SRV" 2>/dev/null' EXIT
for _ in 1 2 3 4 5 6 7 8; do python3 -c "import socket,sys;s=socket.socket();s.settimeout(.3);sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null && break; sleep .3; done
python3 - "$PORT" <<'PY'
import sys,json,urllib.request
B="http://127.0.0.1:%s/unrest/family-backgammon"%sys.argv[1]
def call(v,d=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(B+"/"+v+"?g=b1",
        json.dumps(d).encode() if d is not None else None,{"Content-Type":"application/json"}),timeout=3))
ok=True
st=call("new_game",{}); ok&=(st["turn"]=="w" and sum(abs(x) for x in st["points"])==30)
st=call("roll",{}); ok&=(st["phase"] in ("move","roll"))
if st.get("legal"): m=st["legal"][0]; st=call("move",{"from":m["from"],"die":m["die"]}); ok&=True
pr=call("presence",{"who":"dad"}); ok&=("dad" in pr["who_is_here"])
print("  VERBS_OK" if ok else "  VERBS_FAIL"); sys.exit(0 if ok else 1)
PY
[ $? -ne 0 ] && FAILS=$((FAILS+1))
echo; if [ "$FAILS" -eq 0 ]; then note "BASELINE OK"; exit 0; else note "BASELINE FAILED ($FAILS)"; exit 1; fi
