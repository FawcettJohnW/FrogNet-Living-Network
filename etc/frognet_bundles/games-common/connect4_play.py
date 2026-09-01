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
"""connect4_play.py -- graphical, click-a-column Connect Four over the FrogNet tuple board.

Same LocalGame tuple backend as game_app.py, so it shares tables with the text client --
but you DROP A DISC BY CLICKING A COLUMN instead of pressing a "col N" button. Hovering a
legal column shows a ghost disc in your colour; click to drop. Opponent moves arrive by
poll, exactly as everywhere else in FrogNet (the board IS the shared tuple).

    python3 connect4_play.py --who alice --table den --dbhost databasehost.frognet
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

ROWS, COLS = 6, 7
BG = "#0c1118"; FRAME = "#1b3a6b"; HOLE = "#0a1622"; INK = "#e8eef5"; DIM = "#8aa0b8"
COLORS = ["#e5564e", "#f4c542"]          # seat 0 = red, seat 1 = yellow
COLOR_NAME = {0: "Red", 1: "Yellow"}


class C4(tk.Tk):
    CELL = 78; PAD = 16; LIP = 46          # LIP = drop-preview strip above the grid

    def __init__(self, lg, who):
        super().__init__()
        self.lg = lg; self.who = who
        self.state = None; self.hover_col = None
        self.title(f"Connect Four . {who}")
        self.configure(bg=BG)
        self.W = COLS * self.CELL + 2 * self.PAD
        self.H = ROWS * self.CELL + 2 * self.PAD + self.LIP

        self.status = tk.Label(self, text="connecting...", bg=BG, fg=DIM,
                               font=("Helvetica", 13))
        self.status.pack(pady=(10, 2))
        self.canvas = tk.Canvas(self, width=self.W, height=self.H, bg=BG,
                                highlightthickness=0)
        self.canvas.pack(padx=14)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))

        bar = tk.Frame(self, bg=BG); bar.pack(fill="x", pady=8)
        tk.Button(bar, text="New game", command=self._new, bg=FRAME, fg=INK, bd=0,
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
        return g if isinstance(g, list) and len(g) == ROWS else [[-1] * COLS for _ in range(ROWS)]

    def _my_idx(self):
        seats = (self.state or {}).get("seats") or []
        return seats.index(self.who) if self.who in seats else None

    def _my_turn(self):
        s = self.state or {}
        return s.get("phase") == "play" and s.get("turn") == self.who

    def _legal_cols(self):
        try:
            return {a["col"] for a in (legal_for(self.state, self.who) or []) if "col" in a}
        except Exception:
            grid = self._grid()
            return {c for c in range(COLS) if grid[ROWS - 1][c] == -1}

    def _col_at(self, x):
        c = (x - self.PAD) // self.CELL
        return int(c) if 0 <= c < COLS else None

    # ---- interaction
    def _set_hover(self, c):
        if c != self.hover_col:
            self.hover_col = c; self._draw()

    def _hover(self, e):
        self._set_hover(self._col_at(e.x))

    def _click(self, e):
        if not self._my_turn():
            return
        c = self._col_at(e.x)
        if c is not None and c in self._legal_cols():
            try:
                self.state = self.lg.act({"col": c})
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
        c = self.canvas; c.delete("all")
        s = self.state or {}
        grid = self._grid()
        legal = self._legal_cols() if self._my_turn() else set()
        mine = self._my_idx()

        # drop-preview strip: ghost disc over the hovered legal column, in my colour
        if self._my_turn() and self.hover_col in legal and mine is not None:
            x = self.PAD + self.hover_col * self.CELL + self.CELL / 2
            r = self.CELL * 0.40
            yc = self.PAD + self.LIP / 2
            c.create_oval(x - r, yc - r, x + r, yc + r, fill=COLORS[mine % 2],
                          outline="#ffffff66", width=2)

        # board frame
        top = self.PAD + self.LIP
        c.create_rectangle(self.PAD - 8, top - 8, self.W - self.PAD + 8,
                           self.H - self.PAD + 8, fill=FRAME, outline="")

        for col in range(COLS):
            # subtle column highlight when it's a legal click target
            if col in legal:
                x0 = self.PAD + col * self.CELL
                c.create_rectangle(x0 + 2, top - 6, x0 + self.CELL - 2,
                                   self.H - self.PAD + 6, outline="#ffffff22", width=1)
            for row in range(ROWS):                       # row 0 = bottom
                x = self.PAD + col * self.CELL + self.CELL / 2
                y = top + (ROWS - 1 - row) * self.CELL + self.CELL / 2
                v = grid[row][col]
                fill = HOLE if v == -1 else COLORS[v % 2]
                r = self.CELL * 0.40
                c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline="#00000055")

        self._draw_status(s)

    def _draw_status(self, s):
        phase = s.get("phase")
        if phase == "over":
            w = s.get("winner")
            if not w:
                txt = "Draw."
            else:
                seats = s.get("seats") or []
                idx = seats.index(w) if w in seats else 0
                who_txt = "You win!" if w == self.who else f"{w} wins"
                txt = f"{COLOR_NAME.get(idx, '?')} -- {who_txt}"
        elif phase == "lobby" or len(s.get("seats") or []) < 2:
            txt = "waiting for a second player to join..."
        else:
            turn = s.get("turn")
            seats = s.get("seats") or []
            idx = seats.index(turn) if turn in seats else 0
            colr = COLOR_NAME.get(idx, "?")
            txt = "Your move -- click a column" if self._my_turn() else f"{colr} ({turn}) to move"
        mine = self._my_idx()
        me = f"   you are {COLOR_NAME.get(mine, 'watching')}" if mine is not None else "   watching"
        self.status.config(text=txt + me)


def main():
    ap = argparse.ArgumentParser(description="Connect Four (graphical, click-to-drop)")
    ap.add_argument("--who", required=True)
    ap.add_argument("--table", default="table-1")
    ap.add_argument("--dbhost", default="databasehost.frognet")
    ap.add_argument("--bundles-root",
                    default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    lg = LocalGame("connectfour", a.table, a.who, a.dbhost, a.bundles_root)
    C4(lg, a.who).mainloop()


if __name__ == "__main__":
    main()
