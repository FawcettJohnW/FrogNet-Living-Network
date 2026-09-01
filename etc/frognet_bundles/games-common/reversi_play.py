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
"""reversi_play.py -- graphical, click-a-cell Reversi/Othello over the FrogNet tuple board.

Same LocalGame tuple backend as game_app.py -- the board IS the shared tuple, written
current-state-only (no history). Click a legal cell to place a disc; the codex flips the
flanked discs by its own rules; the opponent's moves arrive by poll, exactly as
everywhere else in FrogNet. Dark (seat 0) moves first; legal cells show a faint ghost in
your colour, hover outlines the cell.

    python3 reversi_play.py --who alice --table den --dbhost databasehost.frognet
"""
from __future__ import annotations
import argparse, os, sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "games-common")):
    if p not in sys.path:
        sys.path.insert(0, p)

from game_tuple_client import LocalGame
try:
    from game_client import legal_for
except Exception:
    def legal_for(st, who): return st.get("legal") or []

N = 8
BG = "#0c1118"; FELT = "#13754a"; LINE = "#0c4a30"; INK = "#e8eef5"; DIM = "#8aa0b8"
DISCS = ["#161616", "#f3f3f3"]            # seat 0 = dark (moves first), seat 1 = light
EDGE = ["#000000", "#c9c9c9"]
NAME = {0: "Dark", 1: "Light"}


class Reversi(tk.Tk):
    CELL = 56; PAD = 18

    def __init__(self, lg, who):
        super().__init__()
        self.lg = lg; self.who = who
        self.state = None; self.hover = None
        self.title(f"Reversi \u00b7 {who}")
        self.configure(bg=BG)
        self.S = N * self.CELL + 2 * self.PAD

        self.status = tk.Label(self, text="connecting\u2026", bg=BG, fg=DIM,
                               font=("Helvetica", 13))
        self.status.pack(pady=(10, 2))
        self.canvas = tk.Canvas(self, width=self.S, height=self.S, bg=BG,
                                highlightthickness=0)
        self.canvas.pack(padx=14)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))

        bar = tk.Frame(self, bg=BG); bar.pack(fill="x", pady=8)
        tk.Button(bar, text="New game", command=self._new, bg=FELT, fg=INK, bd=0,
                  padx=12, pady=6).pack(side="right", padx=12)

        try:                                   # ensure a table exists and take a seat
            if self.lg.get() is None:
                self.lg.start(seats=[])
            self.lg.join("player")
        except Exception:
            pass
        self._refresh()
        self.after(800, self._poll)

    # ---- state helpers
    def _grid(self):
        g = ((self.state or {}).get("board") or {}).get("grid")
        return g if isinstance(g, list) and len(g) == N else [[-1] * N for _ in range(N)]

    def _my_idx(self):
        seats = (self.state or {}).get("seats") or []
        return seats.index(self.who) if self.who in seats else None

    def _my_turn(self):
        s = self.state or {}
        return s.get("phase") == "play" and s.get("turn") == self.who

    def _legal_cells(self):
        try:
            return {(a["r"], a["c"]) for a in (legal_for(self.state, self.who) or [])
                    if "r" in a and "c" in a}
        except Exception:
            return set()

    def _cell_at(self, x, y):
        c = (x - self.PAD) // self.CELL
        r = (y - self.PAD) // self.CELL
        if 0 <= r < N and 0 <= c < N:
            return int(r), int(c)
        return None

    # ---- interaction
    def _set_hover(self, cell):
        if cell != self.hover:
            self.hover = cell; self._draw()

    def _hover(self, e):
        self._set_hover(self._cell_at(e.x, e.y))

    def _click(self, e):
        if not self._my_turn():
            return
        cell = self._cell_at(e.x, e.y)
        if cell is not None and cell in self._legal_cells():
            r, c = cell
            try:
                self.state = self.lg.act({"r": r, "c": c})
                self._draw()
            except Exception as ex:
                self.status.config(text=f"move failed: {ex}")

    def _new(self):
        try:
            self.state = self.lg.start(seats=[]); self.lg.join("player")
            self._refresh()
        except Exception:
            pass

    # ---- poll / refresh
    def _poll(self):
        self._refresh()
        self.after(1000, self._poll)

    def _refresh(self):
        try:
            st = self.lg.get()
            if isinstance(st, dict):
                self.state = st
        except Exception:
            self.status.config(text="can't reach the table")
            return
        self._draw()

    # ---- draw
    def _draw(self):
        cv = self.canvas; cv.delete("all")
        s = self.state or {}
        grid = self._grid()
        legal = self._legal_cells() if self._my_turn() else set()
        mine = self._my_idx()

        cv.create_rectangle(self.PAD - 6, self.PAD - 6, self.S - self.PAD + 6,
                            self.S - self.PAD + 6, fill=FELT, outline="")
        for i in range(N + 1):
            o = self.PAD + i * self.CELL
            cv.create_line(self.PAD, o, self.S - self.PAD, o, fill=LINE)
            cv.create_line(o, self.PAD, o, self.S - self.PAD, fill=LINE)

        for r in range(N):
            for c in range(N):
                x0 = self.PAD + c * self.CELL
                y0 = self.PAD + r * self.CELL
                cx, cy = x0 + self.CELL / 2, y0 + self.CELL / 2
                v = grid[r][c]
                if v in (0, 1):
                    rad = self.CELL * 0.38
                    cv.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                                   fill=DISCS[v], outline=EDGE[v], width=2)
                elif (r, c) in legal and mine is not None:
                    rad = self.CELL * 0.14
                    cv.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                                   fill="", outline=DISCS[mine], width=2)

        if self._my_turn() and self.hover in legal:
            r, c = self.hover
            x0 = self.PAD + c * self.CELL; y0 = self.PAD + r * self.CELL
            cv.create_rectangle(x0 + 1, y0 + 1, x0 + self.CELL - 1, y0 + self.CELL - 1,
                                outline="#ffffff66", width=2)
            if mine is not None:
                cx, cy = x0 + self.CELL / 2, y0 + self.CELL / 2
                rad = self.CELL * 0.38
                cv.create_oval(cx - rad, cy - rad, cx + rad, cy + rad, fill="",
                               outline=DISCS[mine], width=1)
        self._draw_status(s)

    def _score(self, grid):
        return (sum(row.count(0) for row in grid), sum(row.count(1) for row in grid))

    def _draw_status(self, s):
        d, l = self._score(self._grid())
        phase = s.get("phase")
        if phase == "over":
            w = s.get("winner")
            head = "Draw." if not w else ("You win!" if w == self.who else f"{w} wins")
        elif phase == "lobby" or len(s.get("seats") or []) < 2:
            head = "waiting for a second player to join\u2026"
        else:
            turn = s.get("turn"); seats = s.get("seats") or []
            idx = seats.index(turn) if turn in seats else 0
            head = ("Your move \u2014 click a legal cell" if self._my_turn()
                    else f"{NAME.get(idx, '?')} ({turn}) to move")
        mine = self._my_idx()
        me = f"   you are {NAME.get(mine, 'watching')}" if mine is not None else "   watching"
        self.status.config(text=f"{head}    Dark {d} \u2013 {l} Light{me}")


def main():
    ap = argparse.ArgumentParser(description="Reversi / Othello (graphical, click-to-place)")
    ap.add_argument("--who", required=True)
    ap.add_argument("--table", default="table-1")
    ap.add_argument("--dbhost", default="databasehost.frognet")
    ap.add_argument("--bundles-root",
                    default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    lg = LocalGame("reversi", a.table, a.who, a.dbhost, a.bundles_root)
    Reversi(lg, a.who).mainloop()


if __name__ == "__main__":
    main()
