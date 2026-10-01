#!/usr/bin/env python3
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
"""test_connect_retire_oracle - [CONNECT_TRIES_RETIRE_V2] under [ONE_STATE_V1].

A peer that will not establish a :9009 session in 3 tries (~30s) is taken out of the mix for this merge, so the
transport stops dialing it. History: V1 used mark(), which then spared .1/.2, so a DOWN .1 (Seattle2 10.120.120.1)
never left and re-dialed forever; V2 added a separate retire()/is_retired() set for it. [ONE_STATE_V1] (John's ruling,
2026-08-08: "it's either unreachable or it's not") removed the .1/.2 carve-out from mark() and deleted retire(): one
question, one set, disk-backed (the sentinel), cleared once per merge -- by runMerge.bash's flush_not_frognet stage
([FLUSH_HAS_A_CALLER_V1]; without that caller a mark lasted until the proxy restarted -- Seattle3, 2026-08-11).
This oracle imported retire/is_retired until 2026-09-25 and could not even load.
"""
import os, subprocess, sys, tempfile
_DIR = tempfile.mkdtemp()
os.environ["FROGNET_SENTINEL_DIR"] = _DIR          # isolate from the box sentinels
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import core.not_frognet as NF
from core.not_frognet import mark, is_marked, flush

FAILS = []
def check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILS.append(label)

_CONNECT_MAX_TRIES = 3
def _simulate_streak(outcomes):
    fails = 0
    for i, connected in enumerate(outcomes, 1):
        if connected:
            fails = 0
        else:
            fails += 1
            if fails >= _CONNECT_MAX_TRIES:
                return i
    return None

def _other_process_sees(ip):
    code = "import sys; sys.path.insert(0, %r); import core.not_frognet as N; print(N.is_marked(%r))" % (ROOT, ip)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ))
    return r.stdout.strip().endswith("True")

def main():
    print("=== CONNECT-RETIRE ORACLE (V2 under ONE_STATE_V1) ===")
    flush()
    check("a down .1 IS taken out of the mix (V1 left it churning to 16)", mark("10.120.120.1", reason="3 failed :9009 connects") and is_marked("10.120.120.1"))
    check("a .2 is not exempt either", mark("10.130.130.2") and is_marked("10.130.130.2"))
    check("a stray .20 client is marked", mark("10.250.250.20") and is_marked("10.250.250.20"))
    check("mark() refuses only an empty address", mark("") is False)
    check("retire()/is_retired() are gone: one set", not hasattr(NF, "retire") and not hasattr(NF, "is_retired"))
    check("disk-backed: the sentinel file holds the mark", os.path.exists(NF._sentinel()) and "10.120.120.1" in open(NF._sentinel()).read())
    check("another process sees the mark (the proxy is not the merge)", _other_process_sees("10.120.120.1"))
    flush()
    check("flush clears the mark (one fresh probe next merge)", not is_marked("10.120.120.1") and not is_marked("10.250.250.20"))
    check("flush removes the sentinel file", not os.path.exists(NF._sentinel()))
    check("after flush another process sees no mark", not _other_process_sees("10.120.120.1"))
    rm = open(os.path.join(ROOT, "..", "..", "usr", "local", "bin", "runMerge.bash")).read() if os.path.exists(os.path.join(ROOT, "..", "..", "usr", "local", "bin", "runMerge.bash")) else None
    if rm is not None:
        check("runMerge.bash flushes it every merge ([FLUSH_HAS_A_CALLER_V1])", "-m core.not_frognet flush" in rm)
    ts = open(os.path.join(ROOT, "proxy", "transport_semantic.py")).read()
    blk = ts[ts.index("if self._connect_fails == _CONNECT_MAX_TRIES:"):][:1400]
    check("the transport's 3-strike path marks the peer", "_nf_mark(self.host" in blk)
    check("the transport imports no retire()", "_nf_retire" not in "\n".join(l for l in ts.split("\n") if not l.lstrip().startswith("#")))
    check("never answers -> out on the 3rd", _simulate_streak([False, False, False]) == 3)
    check("connects on try 3 -> kept", _simulate_streak([False, False, True]) is None)
    check("success mid-streak resets", _simulate_streak([False, True, False, False]) is None)
    flush()
    print("\n" + ("ALL CONNECT-RETIRE CHECKS PASS" if not FAILS else f"CONNECT-RETIRE ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
