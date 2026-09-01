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
"""Connect Four -- 2 players. Drop a disc into a column; first to four in a row
(horizontal, vertical, or diagonal) wins. Rules only; the base does the rest."""
from __future__ import annotations
from typing import Any, Dict, List
from game_base import GameCodex

ROWS, COLS, NEED = 6, 7, 4


class ConnectFourCodex(GameCodex):
    NAME = "connectfour"
    MIN_PLAYERS = 2
    MAX_PLAYERS = 2

    def initial_setup(self, seats: List[str]) -> Dict[str, Any]:
        # grid[r][c] = seat index (0/1) or -1 empty; row 0 is the BOTTOM.
        return {"grid": [[-1] * COLS for _ in range(ROWS)]}

    def _seat_idx(self, state, who) -> int:
        return state["seats"].index(who)

    def legal_actions(self, state, who) -> List[Dict[str, Any]]:
        if state["turn"] != who:
            return []
        grid = state["board"]["grid"]
        return [{"col": c} for c in range(COLS) if grid[ROWS - 1][c] == -1]

    def apply_action(self, state, who, action) -> bool:
        grid = state["board"]["grid"]
        c = int(action.get("col", -1))
        if not (0 <= c < COLS) or grid[ROWS - 1][c] != -1:
            return False
        idx = self._seat_idx(state, who)
        r = next(rr for rr in range(ROWS) if grid[rr][c] == -1)
        grid[r][c] = idx
        if self._wins(grid, r, c, idx):
            state["winner"] = who
            state["phase"] = "over"
        elif all(grid[ROWS - 1][cc] != -1 for cc in range(COLS)):
            state["winner"] = None
            state["phase"] = "over"            # full board, draw
        else:
            nxt = (state["seats"].index(who) + 1) % len(state["seats"])
            state["turn"] = state["seats"][nxt]
        return True

    def _wins(self, grid, r, c, idx) -> bool:
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            run = 1
            for s in (1, -1):
                rr, cc = r + dr * s, c + dc * s
                while 0 <= rr < ROWS and 0 <= cc < COLS and grid[rr][cc] == idx:
                    run += 1
                    rr += dr * s
                    cc += dc * s
            if run >= NEED:
                return True
        return False
