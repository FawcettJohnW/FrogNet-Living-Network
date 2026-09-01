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
"""Reversi / Othello -- 2 players. Place a disc so it flanks one or more of the
opponent's discs in a straight line; those are flipped. If you have no legal move you
pass; if neither side can move the game ends and the most discs wins."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from game_base import GameCodex

N = 8
DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


class ReversiCodex(GameCodex):
    NAME = "reversi"
    MIN_PLAYERS = 2
    MAX_PLAYERS = 2

    def initial_setup(self, seats: List[str]) -> Dict[str, Any]:
        g = [[-1] * N for _ in range(N)]
        g[3][3], g[4][4] = 1, 1          # light = seat 1
        g[3][4], g[4][3] = 0, 0          # dark  = seat 0 moves first
        return {"grid": g}

    def _idx(self, state, who) -> int:
        return state["seats"].index(who)

    def _flips(self, grid, r, c, idx) -> List[Tuple[int, int]]:
        if grid[r][c] != -1:
            return []
        opp = 1 - idx
        won: List[Tuple[int, int]] = []
        for dr, dc in DIRS:
            line, rr, cc = [], r + dr, c + dc
            while 0 <= rr < N and 0 <= cc < N and grid[rr][cc] == opp:
                line.append((rr, cc)); rr += dr; cc += dc
            if line and 0 <= rr < N and 0 <= cc < N and grid[rr][cc] == idx:
                won.extend(line)
        return won

    def _moves(self, grid, idx) -> List[Tuple[int, int]]:
        return [(r, c) for r in range(N) for c in range(N) if self._flips(grid, r, c, idx)]

    def legal_actions(self, state, who) -> List[Dict[str, Any]]:
        if state["turn"] != who:
            return []
        idx = self._idx(state, who)
        return [{"r": r, "c": c} for (r, c) in self._moves(state["board"]["grid"], idx)]

    def apply_action(self, state, who, action) -> bool:
        grid = state["board"]["grid"]
        idx = self._idx(state, who)
        r, c = int(action.get("r", -1)), int(action.get("c", -1))
        flips = self._flips(grid, r, c, idx)
        if not flips:
            return False
        grid[r][c] = idx
        for (rr, cc) in flips:
            grid[rr][cc] = idx
        self._advance(state, idx)
        return True

    def _advance(self, state, idx) -> None:
        grid = state["board"]["grid"]
        opp = 1 - idx
        if self._moves(grid, opp):
            state["turn"] = state["seats"][opp]
        elif self._moves(grid, idx):
            state["turn"] = state["seats"][idx]          # opponent passes
        else:
            d = sum(row.count(0) for row in grid)
            l = sum(row.count(1) for row in grid)
            state["phase"] = "over"
            state["winner"] = (state["seats"][0] if d > l
                               else state["seats"][1] if l > d else None)
            state["board"]["score"] = {state["seats"][0]: d, state["seats"][1]: l}
