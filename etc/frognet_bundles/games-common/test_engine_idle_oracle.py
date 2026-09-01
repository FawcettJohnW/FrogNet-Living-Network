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
"""Oracle - the resident boardgame engine idles on an empty table.

bg_engine.py runs governor_step at --hz 5 in a `while True`. With nobody seated
that is a permanent poll storm against databasehost.frognet (the field symptom:
thousands of SensorType=boardgame* reads for a table no one is at). The
[ENGINE_IDLE_WHEN_EMPTY_V1] gate drops to a slow heartbeat until a User object is
seated.

This oracle is deliberately DEPLOYMENT-AWARE, because the real failure was not the
gate logic (that was written) but a stale DEPLOYED copy: the systemd unit launches
/etc/frognet_bundles/boardgame/bg_engine.py, and the fix had only landed in the
opt/ staging copy. So this checks BOTH:

  1) every bg_engine.py in the tree carries the gate (no copy can diverge), and
  2) the engine, run --once against an EMPTY table, runs governor_step ZERO times.

FAIL on old (governor runs on an empty table); PASS on the gated engine.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# tree root is .../etc/frognet_bundles/games-common -> up 3
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEPLOYED = os.path.join(ROOT, "etc", "frognet_bundles", "boardgame")


def _find_all_engines():
    hits = []
    for dirpath, _dirs, files in os.walk(ROOT):
        if "bg_engine.py" in files:
            hits.append(os.path.join(dirpath, "bg_engine.py"))
    return hits


def _run_empty_table(bundle_dir):
    """Import the engine from bundle_dir and run main() --once against an empty
    table with governor_step counted. Returns the call count."""
    import importlib
    for m in ("bg_engine", "bg_governor", "bg_space"):
        sys.modules.pop(m, None)
    sys.path.insert(0, bundle_dir)
    try:
        import bg_governor as G
        import bg_engine
        calls = {"gov": 0}
        G._seat_users = lambda space, gid: []          # empty table
        def _fake_gov(space, gid, rng):
            calls["gov"] += 1; return {}
        G.governor_step = _fake_gov
        class _FakeSpace:
            def __init__(self, *a, **k): pass
        bg_engine.TupleSpace = _FakeSpace
        bg_engine.HttpSpace = _FakeSpace
        argv = sys.argv
        sys.argv = ["bg_engine.py", "--gid", "t1", "--dbhost", "127.0.0.1", "--once"]
        try:
            bg_engine.main()
        finally:
            sys.argv = argv
        return calls["gov"]
    finally:
        if bundle_dir in sys.path:
            sys.path.remove(bundle_dir)


def main():
    checks = []

    engines = _find_all_engines()
    ungated = [e for e in engines
               if "ENGINE_IDLE_WHEN_EMPTY_V1" not in open(e, encoding="utf-8").read()]
    checks.append((f"all {len(engines)} bg_engine.py copies carry the idle gate",
                   not ungated))

    calls = _run_empty_table(DEPLOYED)
    checks.append(("deployed engine runs governor_step 0x on an empty table (--once)",
                   calls == 0))

    print("=== ENGINE IDLE ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if ungated:
        for e in ungated:
            print(f"      UNGATED: {e}")
    if all(ok for _, ok in checks):
        print("ALL ENGINE-IDLE ORACLE CHECKPOINTS PASS")
        return 0
    print(f"ENGINE-IDLE ORACLE FAILED (deployed governor calls on empty table = {calls})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
