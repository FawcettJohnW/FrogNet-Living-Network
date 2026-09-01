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
"""test_connect_retire_oracle - [CONNECT_TRIES_RETIRE_V2].

A peer that will not establish a :9009 session in 3 tries (~30s) is retired for this merge
so the transport stops dialing it. V1 wrongly used mark() (the non-FrogNet cache), which
spares .1/.2 roles -- so a DOWN .1 node (e.g. Seattle2 10.120.120.1 in the field) never
retired and its strike counter climbed 3,4,5,...,16 while it re-dialed every cycle. V2 uses
a SEPARATE retire set: any address, any cause, same per-merge flush, and NOT the non-FrogNet
cache (so discovery never mistakes a down member for a stranger).
"""
import os, sys, tempfile
os.environ["FROGNET_SENTINEL_DIR"] = tempfile.mkdtemp()   # isolate from the box sentinels
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.not_frognet import mark, is_marked, retire, is_retired, flush

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

def main():
    print("=== CONNECT-RETIRE ORACLE (V2) ===")

    # the V1 bug: a down .1 must now actually retire, and NOT via the non-FrogNet cache
    flush()
    retire("10.120.120.1", reason="3 failed :9009 connects")
    check("down .1 node IS retired (V1 left it churning to 16)", is_retired("10.120.120.1"))
    check("down .1 is NOT in the non-FrogNet cache (discovery unaffected)",
          not is_marked("10.120.120.1"))

    # stray clients retire too
    retire("10.250.250.20")
    check("stray .20 client is retired", is_retired("10.250.250.20"))

    # the non-FrogNet invariant is intact: mark() still spares .1/.2, still marks strays
    check("mark() still REFUSES a .1 (invariant intact)", not mark("10.130.130.1"))
    check("mark() still marks a stray client", mark("10.130.130.99"))

    # per-merge lifecycle: flush clears the retire set -> one fresh probe next merge
    flush()
    check("flush clears retire (fresh probe next merge)", not is_retired("10.120.120.1"))
    check("flush also clears the non-FrogNet cache", not is_marked("10.130.130.99"))

    # 3-strike counter, any cause (timeouts count)
    check("never answers -> retire on the 3rd", _simulate_streak([False, False, False]) == 3)
    check("connects on try 3 -> NOT retired", _simulate_streak([False, False, True]) is None)
    check("success mid-streak resets", _simulate_streak([False, True, False, False]) is None)

    flush()
    print("\n" + ("ALL CONNECT-RETIRE V2 CHECKS PASS" if not FAILS
                  else f"CONNECT-RETIRE ORACLE FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
