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
# run_baseline.sh - start the process(es) needed and run the baseline tests.
#
# Does three things and always cleans up after itself:
#   1. runs the unit + integration test suite (test_calendar_codex.py)
#   2. starts the dev mock codex server and smoke-tests all five verbs over HTTP
#   3. tears the server down (trap on EXIT) and reports a PASS/FAIL summary
#
# No box required. If your FrogNet tree is reachable, the integration test (I1,
# real codec) runs too; otherwise it reports the tree as unavailable and the rest
# still passes. No `pipefail` (prohibited in FrogNet bash).

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PORT="${MOCK_PORT:-8806}"
LOG="/tmp/fc_mock_${PORT}.log"
FAILS=0

note(){ echo "[baseline] $*"; }

# --- locate the FrogNet tree (for the real-codec integration test) --------
# Honors $FROGNET_SEMANTIC; else probes a few common locations. If found, the
# tree is put on PYTHONPATH so I1 runs green instead of reporting "unavailable".
find_tree(){
  local c
  for c in "${FROGNET_SEMANTIC:-}" \
           /opt/frognet_semantic \
           /tmp/ft/opt/frognet_semantic \
           "$HERE/../../../opt/frognet_semantic"; do
    if [ -n "$c" ] && [ -f "$c/core/codec.py" ]; then echo "$c"; return 0; fi
  done
  return 1
}
TREE="$(find_tree || true)"
if [ -n "$TREE" ]; then
  export PYTHONPATH="$TREE${PYTHONPATH:+:$PYTHONPATH}"
  note "FrogNet tree found: $TREE  (integration test I1 will run on the real codec)"
else
  note "FrogNet tree not found - I1 will report 'tree not importable'; U1-U5 still run."
fi

# --- 1. unit + integration suite ------------------------------------------
note "running test suite ..."
python3 tests/test_calendar_codex.py
if [ $? -ne 0 ]; then note "test suite FAILED"; FAILS=$((FAILS+1)); fi

# --- 2. start the mock codex and smoke-test the verbs ---------------------
note "starting mock codex on 127.0.0.1:${PORT} ..."
# the heredoc server binds the requested port and serves the real CalendarCodex
python3 - "$PORT" <<'BOOT' >"$LOG" 2>&1 &
import sys, os, threading
sys.path.insert(0, os.path.join("dev")); sys.path.insert(0, os.path.join("codex"))
import mock_codex_server as M
from http.server import ThreadingHTTPServer
port = int(sys.argv[1])
ThreadingHTTPServer(("127.0.0.1", port), M.H).serve_forever()
BOOT
MOCK_PID=$!
trap 'kill "$MOCK_PID" 2>/dev/null' EXIT

# wait for it to come up
up=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.3)
sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null; then up=1; break; fi
  sleep 0.3
done
if [ "$up" -ne 1 ]; then note "mock server did not start (see $LOG)"; FAILS=$((FAILS+1)); fi

# smoke-test all five verbs through HTTP
if [ "$up" -eq 1 ]; then
  note "smoke-testing verbs over HTTP ..."
  python3 - "$PORT" <<'PROBE'
import sys, json, urllib.request
B = "http://127.0.0.1:%s/unrest/family-calendar" % sys.argv[1]
def call(verb, body=None):
    req = urllib.request.Request(B + "/" + verb,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=3))
ok = True
ev = call("create", {"summary": "Sunday call", "start": "2026-06-14T10:00", "end": "2026-06-14T10:30"})
print("  create  ->", ev.get("summary"), "uid", ev.get("uid")); ok &= bool(ev.get("uid"))
el = call("list"); print("  list    ->", len(el["events"]), "event(s); ts is str:", isinstance(el["ts"], str))
ok &= (len(el["events"]) == 1 and isinstance(el["ts"], str))
up = call("update", {"uid": ev["uid"], "location": "Our place"}); print("  update  ->", up.get("location"))
ok &= (up.get("location") == "Our place")
pr = call("presence", {"who": "dad"}); print("  presence->", pr.get("who_is_here")); ok &= ("dad" in pr.get("who_is_here", []))
dl = call("delete", {"uid": ev["uid"]}); print("  delete  ->", dl); ok &= bool(dl.get("ok"))
el2 = call("list"); ok &= (len(el2["events"]) == 0)
print("  VERBS_OK" if ok else "  VERBS_FAIL")
sys.exit(0 if ok else 1)
PROBE
  if [ $? -ne 0 ]; then note "verb smoke-test FAILED (see $LOG)"; FAILS=$((FAILS+1)); fi
fi

# --- 3. summary (trap tears the server down on exit) ----------------------
echo
if [ "$FAILS" -eq 0 ]; then
  note "BASELINE OK - suite green and all five verbs serve correctly."
  exit 0
else
  note "BASELINE FAILED - $FAILS section(s) had failures."
  exit 1
fi
