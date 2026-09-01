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
"""bg_board.py -- the REAL graphical backgammon board (green felt, point triangles, checkers),
reading the game from the lobby/codex tuple via game_tuple_client.LocalGame. This is the proper
board UI for tuple-lobby backgammon -- NOT the text grid in game_app.py.

Drawing is adapted from boardgame/app/bg_play.py; the read layer is the codex LocalGame so it
shows the SAME table the lobby created (SensorType=backgammon, var=state, scope=session:<gid>).

  python3 bg_board.py --table <gid> --who <name> --dbhost databasehost.frognet [--watch]
"""
import argparse, os, sys
import tkinter as tk

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
BUNDLES_ROOT = os.environ.get("FROGNET_BUNDLES_ROOT") or os.path.dirname(_HERE)
from game_tuple_client import LocalGame

FELT="#15543a"; BOARD="#1f6b4a"; FRAME="#5b3a1e"; BAR="#4a2f18"
PT_LIGHT="#d9b382"; PT_DARK="#9c5a2c"; WHITE="#f5f5f5"; BLACK="#1a1a1a"; INK="#f4f7f2"; ACC="#54e08a"
POINT_W, POINT_H = 52, 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--who", required=True)
    ap.add_argument("--dbhost", default="databasehost.frognet")
    ap.add_argument("--bundles-root", default=BUNDLES_ROOT)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--start", action="store_true")
    a = ap.parse_args()

    lg = LocalGame("backgammon", a.table, a.who, a.dbhost, a.bundles_root)
    raw = lg.get()
    present = isinstance(raw, dict) and "phase" in raw
    sys.stderr.write(f"[bg_board] table backgammon/{a.table}: "
                     f"{'present phase='+str(raw.get('phase'))+' seats='+str(raw.get('seats')) if present else 'ABSENT'}\n")
    if not present and not a.watch:
        lg.start(seats=[])
    joined = lg.join("watcher" if a.watch else "player")
    sys.stderr.write(f"[bg_board] join who={a.who} -> seats={joined.get('seats') if isinstance(joined,dict) else joined}\n")
    sys.stderr.flush()

    root = tk.Tk()
    root.title(f"FrogNet Backgammon -- {a.who} . {a.table}")
    root.configure(bg=FELT); root.geometry("820x600")
    cv = tk.Canvas(root, width=800, height=520, bg=FELT, highlightthickness=0)
    cv.pack(padx=8, pady=8)
    status = tk.Label(root, text="connecting...", bg=FELT, fg=INK, font=("Consolas",11)); status.pack(fill="x")
    bar = tk.Frame(root, bg=FELT); bar.pack(fill="x", pady=4)
    cache = {"sig": None, "last": None}

    def my_color(st):
        seats = st.get("seats") or []
        if a.who not in seats: return None
        return "w" if seats.index(a.who)==0 else "b"

    def draw(st):
        cv.delete("all")
        cv.create_rectangle(8,8,792,512, fill=BOARD, outline=FRAME, width=6)
        cv.create_rectangle(396,8,420,512, fill=BAR, outline="")
        xs_l=[20+i*60 for i in range(6)]; xs_r=[430+i*60 for i in range(6)]; xs=xs_l+xs_r
        def tri(i,x,top):
            col = PT_LIGHT if i%2==0 else PT_DARK
            pts = [x,12,x+POINT_W,12,x+POINT_W/2,12+POINT_H] if top else [x,508,x+POINT_W,508,x+POINT_W/2,508-POINT_H]
            cv.create_polygon(pts, fill=col, outline="")
        for i,x in enumerate(xs): tri(i,x,True)
        for i,x in enumerate(xs): tri(i,x,False)
        board = st.get("board") or {}
        pts = board.get("points") or []
        def stack(pi,x,top):
            n = pts[pi] if 0<=pi<len(pts) else 0
            if n==0: return
            color = WHITE if n>0 else BLACK
            for k in range(min(abs(n),5)):
                cy = (24+k*38) if top else (496-k*38)
                cv.create_oval(x+6,cy-16,x+POINT_W-6,cy+16, fill=color, outline="#000")
        for slot,p in enumerate(range(13,25)): stack(p, xs[slot], True)
        for slot,p in enumerate(range(12,0,-1)): stack(p, xs[slot], False)
        off = board.get("off") if isinstance(board.get("off"),dict) else {}
        cv.create_text(700,30, text=f"off w:{off.get('w',0)} b:{off.get('b',0)}",
                       fill=INK, font=("Consolas",11), anchor="w")

    def controls(st):
        for w in bar.winfo_children(): w.destroy()
        seats = st.get("seats") or []
        phase = st.get("phase"); board = st.get("board") or {}; inner = board.get("phase")
        if phase == "lobby":
            tk.Label(bar, text=f"waiting for players... seated: {seats}", bg=FELT, fg="#c6d3cb",
                     font=("Consolas",11)).pack(side="left"); return
        if phase == "gameover":
            tk.Label(bar, text=f"game over -- winner {st.get('winner')}", bg=FELT, fg=ACC,
                     font=("Consolas",12)).pack(side="left"); return
        col = my_color(st)
        if a.watch or col is None:
            tk.Label(bar, text=f"watching . turn {st.get('turn')}", bg=FELT, fg="#c6d3cb",
                     font=("Consolas",11)).pack(side="left"); return
        if st.get("turn") != a.who:
            tk.Label(bar, text="waiting for opponent...", bg=FELT, fg="#c6d3cb",
                     font=("Consolas",11)).pack(side="left"); return
        # my turn: roll or move
        if inner == "roll":
            tk.Button(bar, text="Roll", command=lambda:(lg.act({"kind":"roll"}), pull()),
                      bg=ACC, font=("Consolas",11)).pack(side="left", padx=4)
        elif inner == "move":
            for m in (board.get("legal") or [])[:10]:
                tk.Button(bar, text=f"{m['from']}/{m['die']}",
                          command=lambda mm=m:(lg.act({"kind":"move","from":mm["from"],"die":mm["die"]}), pull()),
                          bg="#90c8a4").pack(side="left", padx=2)

    def _sig(st):
        b=st.get("board") or {}
        return (tuple(b.get("points") or ()), st.get("turn"), st.get("phase"), b.get("phase"),
                tuple(b.get("dice") or ()), tuple((m.get("from"),m.get("die")) for m in (b.get("legal") or ())),
                tuple(st.get("seats") or ()), st.get("winner"))

    def pull():
        try:
            st = lg.get()
            if not isinstance(st, dict):
                if cache["last"] is not None:           # read miss: keep last good board
                    return
                status.config(text="waiting for table..."); return
            cache["last"] = st
            status.config(text=f"turn={st.get('turn')} phase={st.get('phase')} "
                               f"dice={(st.get('board') or {}).get('dice')}"
                               + (f"  WINNER {st.get('winner')}" if st.get('winner') else ""))
            sig=_sig(st)
            if sig != cache["sig"]:
                cache["sig"]=sig
                draw(st); controls(st)
        except Exception as e:
            import traceback; traceback.print_exc()
            status.config(text=f"read error: {e}")

    def loop():
        pull(); root.after(1200, loop)
    root.after(150, loop)
    root.mainloop()


if __name__ == "__main__":
    import traceback as _tb
    try:
        main()
    except Exception:
        _tb.print_exc()
        try:
            with open(os.path.join(os.path.expanduser("~"),"frognet_game_crash.log"),"w") as f:
                _tb.print_exc(file=f)
        except Exception: pass
        sys.exit(3)
