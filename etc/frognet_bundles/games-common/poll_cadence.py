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
"""poll_cadence.py - adaptive board poll interval, driven by the game tuple itself.

An idle backgammon table polled the shared `state` tuple every 1.2s whether or not
anyone was playing, so an empty lobby produced the same round-trip storm to the
databasehost as an active game. But the state tuple already carries everything
needed to know nobody is playing: `phase` and `seats` (presence is inside it too).

Rule: poll FAST only when a game is actually live or someone is seated; otherwise
back off to a slow watch interval - fast enough to notice the next join within a
few seconds, but not hammering an empty table.

  active(st)  -> True if phase=="play" OR any seat is filled.
                 (presence role=="player" implies a seat, so seats covers it; no
                 client-side clock needed, which keeps this skew-proof.)
  next_interval(st, fast_ms, idle_ms) -> fast_ms if active else idle_ms.
                 A non-dict / read-miss state is treated as idle: never hammer on
                 a board we couldn't read.

Tunables (read by the boards, not here): FROGNET_BOARD_POLL_MS (default 1200),
FROGNET_BOARD_IDLE_POLL_MS (default 8000).
"""
from __future__ import annotations


def active(st) -> bool:
    if not isinstance(st, dict):
        return False
    phase = st.get("phase")
    if phase == "play":
        return True
    # a lobby with someone seated is forming a game (e.g. waiting for an opponent);
    # an empty lobby or a finished (gameover) table has nothing to poll fast for.
    if phase == "lobby" and st.get("seats"):
        return True
    return False


def next_interval(st, fast_ms: int, idle_ms: int) -> int:
    return fast_ms if active(st) else idle_ms
