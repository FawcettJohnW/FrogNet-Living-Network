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
sim/elector_check.py - proves the post-merge databasehost elector's two gates and its
change/HUP idempotency, using the REAL elect_once decision from frognet_dbhost_elector.py.
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_ROOT))    # .../work
sys.path.insert(0, os.path.join(_WORK, "usr", "local", "bin"))

from frognet_dbhost_elector import elect_once, is_control, within_window, control_ip

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

# hosts block: control = 10.250.250.1 (highest .1); an off-mesh DB box exists at 10.77.77.50
LINES = ["10.120.120.1 FrogNetHost.x", "10.250.250.1 FrogNetHost.x",
         "10.160.160.1 FrogNetHost.x", "10.250.250.1 databasehost.frognet",
         "10.250.250.1 databasehost_control.frognet"]
# the election re-points databasehost.frognet to the most-capable candidate (off-mesh winner)
def apply_pick(ls, dbhost):
    return [l for l in ls if "databasehost.frognet" not in l] + ["10.77.77.50 databasehost.frognet"]
def apply_same(ls, dbhost):
    return list(ls)
NOW = 100000

def run():
    probs = []
    if control_ip(LINES) != "10.250.250.1":
        probs.append(f"control should be highest .1, got {control_ip(LINES)}")
    check("[CTL] control = highest .1 gateway (10.250.250.1)", probs)

    # GATE 1 - a non-control node never elects, even fresh in-window
    probs = []
    _nl, ch = elect_once(LINES, "10.120.120.1", NOW, NOW - 30, apply_pick)
    if ch:
        probs.append("a NON-control node ran the election - only control may")
    check("[GATE1] non-control node is a no-op (single writer = control)", probs)

    # GATE 2 - control but the post-merge window expired -> no-op
    probs = []
    _nl, ch = elect_once(LINES, "10.250.250.1", NOW, NOW - 9999, apply_pick)
    if ch:
        probs.append("control elected OUTSIDE the 10-min window - window gate failed")
    check("[GATE2] control outside the post-merge window is a no-op", probs)

    # control + in-window -> elects, repoints databasehost.frognet to the winner
    probs = []
    nl, ch = elect_once(LINES, "10.250.250.1", NOW, NOW - 30, apply_pick)
    if not ch or "10.77.77.50 databasehost.frognet" not in nl:
        probs.append(f"control in-window did not re-elect databasehost: changed={ch}")
    if "10.250.250.1 databasehost_control.frognet" not in nl:
        probs.append("control line must be preserved across a data re-election")
    check("[ELECT] control in-window re-elects databasehost.frognet from candidates", probs)

    # idempotent - a settled list yields no change -> no write -> no HUP
    probs = []
    _nl2, ch2 = elect_once(nl, "10.250.250.1", NOW, NOW - 30, apply_same)
    if ch2:
        probs.append("settled list still reported a change - not idempotent (would HUP needlessly)")
    check("[IDEMPOTENT] settled candidate list -> no change -> no write/HUP", probs)

def main():
    print("=== post-merge databasehost elector: control gate + window gate + idempotency ===")
    run()
    print("\n" + ("ALL ELECTOR CHECKS PASS" if not FAILS
                  else f"ELECTOR CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
