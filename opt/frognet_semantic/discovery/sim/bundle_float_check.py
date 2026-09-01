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
"""
sim/bundle_float_check.py - proves the bundle apps' vendored FloatWatch: a resolved-IP delta
on the table/codex host fires the re-sync exactly once (prime, then on change), and never on a
settled host. Uses the REAL FloatWatch shipped in the backgammon bundle (calendar ships a copy).
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_WORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))))
sys.path.insert(0, os.path.join(_WORK, "etc", "frognet_bundles",
                                "net.frognet.backgammon", "app"))
from host_float import FloatWatch, resolve_base_ip

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

def run():
    # simulate the client's watermark; on_float resets it (the app's re-sync)
    app = {"watermark": 17, "connected": False, "resyncs": 0}
    def on_float():
        app["watermark"] = -1; app["connected"] = True; app["resyncs"] += 1
    ip = ["10.111.11.1"]
    fw = FloatWatch(resolve_ip=lambda: ip[0], on_float=on_float)
    r1 = fw.tick()                         # prime
    r2 = fw.tick()                         # same host: no-op
    ip[0] = "10.250.250.1"                  # table host floats
    r3 = fw.tick()                         # delta: re-sync
    r4 = fw.tick()                         # settled: no-op

    probs = []
    if r1 or r2 or r4:
        probs.append(f"re-synced without a float: {(r1, r2, r4)}")
    if not r3 or app["resyncs"] != 1:
        probs.append(f"did not re-sync exactly once on the float (resyncs={app['resyncs']})")
    check("[FLOAT] table-host IP delta -> exactly one re-sync (prime, then on change)", probs)

    probs = []
    if app["watermark"] != -1:
        probs.append("watermark not dropped on float (new host's ts would read as a split)")
    if not app["connected"]:
        probs.append("prior 'disconnected' not cleared on float")
    check("[RESYNC] float drops stale watermark + clears disconnect (recovers from split)", probs)

    probs = []
    if resolve_base_ip("http://nonexistent.invalid:80") is not None:
        probs.append("resolve_base_ip should return None (not raise) on an unresolvable host")
    check("[RESOLVE] base parsing tolerates host:port and fails soft", probs)

def main():
    print("=== bundle app FloatWatch: re-sync on table-host float ===")
    run()
    print("\n" + ("ALL BUNDLE-FLOAT CHECKS PASS" if not FAILS
                  else f"BUNDLE-FLOAT CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
