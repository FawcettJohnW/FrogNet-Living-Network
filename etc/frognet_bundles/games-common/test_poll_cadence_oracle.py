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
"""Oracle - adaptive board poll cadence.

The idle backgammon table polled the shared tuple every 1.2s with nobody
playing. This proves the poll interval is now driven by the tuple's own
phase/seats: an unseated lobby backs off, while a live or seated table keeps
polling fast.

Fail-on-old / pass-on-new is expressed by reads-per-minute:
  - OLD (fixed 1200ms): an idle table = 50 reads/min regardless of state.
  - NEW (adaptive): an idle lobby = 60000/IDLE reads/min (7.5 at 8s), while a
    live/seated table stays at 50/min. An idle table still at ~50/min would fail.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from poll_cadence import active, next_interval

FAST = 1200
IDLE = 8000


def rpm(interval_ms):
    return 60000.0 / interval_ms


def run():
    fails = []

    empty_lobby = {"phase": "lobby", "seats": [], "presence": {}}
    seated_lobby = {"phase": "lobby", "seats": ["alice"], "presence": {}}   # waiting for opp
    in_play = {"phase": "play", "seats": ["alice", "bob"], "turn": "alice"}
    gameover = {"phase": "gameover", "seats": ["alice", "bob"], "winner": "alice"}
    watch_empty = {"phase": "lobby", "seats": [],
                   "presence": {"carol": {"role": "watcher", "ts": 0}}}
    read_miss = None
    garbage = "<<unparseable>>"

    # ---- idle cases must back off ----
    for label, st in (("empty lobby", empty_lobby),
                      ("watched empty lobby", watch_empty),
                      ("gameover", gameover),
                      ("read miss", read_miss),
                      ("garbage", garbage)):
        iv = next_interval(st, FAST, IDLE)
        if iv != IDLE:
            fails.append(f"[{label}] expected IDLE {IDLE}ms, got {iv}ms ({rpm(iv):.0f}/min)")

    # ---- active cases must stay fast ----
    for label, st in (("seated lobby (waiting opp)", seated_lobby),
                      ("in play", in_play)):
        iv = next_interval(st, FAST, IDLE)
        if iv != FAST:
            fails.append(f"[{label}] expected FAST {FAST}ms, got {iv}ms - responsiveness lost")

    # ---- the storm comparison: old fixed vs new adaptive on an idle table ----
    old_idle_rpm = rpm(FAST)                       # old code polled fast regardless
    new_idle_rpm = rpm(next_interval(empty_lobby, FAST, IDLE))
    if not (new_idle_rpm < old_idle_rpm / 5):
        fails.append(f"idle table not backed off enough: old={old_idle_rpm:.0f}/min "
                     f"new={new_idle_rpm:.0f}/min")

    # ---- a join must snap it back to fast on the very next decision ----
    if next_interval(empty_lobby, FAST, IDLE) != IDLE:
        fails.append("pre-join not idle")
    if next_interval(seated_lobby, FAST, IDLE) != FAST:
        fails.append("post-join did not snap back to fast")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1

    print(f"PASS: idle lobby {old_idle_rpm:.0f}/min -> {new_idle_rpm:.0f}/min "
          f"(backed off); seated/in-play stay {rpm(FAST):.0f}/min; read-miss treated idle; "
          f"join snaps back to fast")
    return 0


if __name__ == "__main__":
    sys.exit(run())
