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
"""bg_play.py -- windowed backgammon participant launched BY the Communicator Games tab.

The Communicator launches us in our OWN window (B.bundle_command builds:
    python3 app/bg_play.py --connect <host> --who <name>
so we accept exactly those args). We are a PARTICIPANT in shared memory: resolve the table
region, read the GAME object to render, write our own user/intent object to act. We render
the board from LOCAL assets pre-staged in the Communicator Assets dir (never the wire) and
composite the live position from the GAME object. No call/response -- we change memory; the
host (or the in-Communicator codex) governs.

Falls back to a text loop if Tk is unavailable.
"""
import argparse, os, sys, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bg_space import TupleSpace, HttpSpace          # the tuple region access
try:
    import bg_assets as A                            # device-local asset dir + sync
except Exception:
    A = None

W, B = "w", "b"
DEFAULT_GID = "table1"

# ---- asset path: render from the device Communicator Assets dir (pre-staged) ----
def asset_dir(domain, game):
    if A:
        return os.path.join(A.communicator_assets_dir(), domain, game)
    return os.path.join(os.path.expanduser("~/.frognet/communicator"), "Assets", domain, game)


def _space(args):
    # launched with --connect <host>: talk to the fabric (Apache/api.php) on that host.
    if args.connect:
        base = args.connect if args.connect.startswith("http") else f"http://{args.connect}"
        # the table region is served by the game host; HttpSpace speaks api.php-style
        return HttpSpace(args.gid, base, typ=args.name)
    if args.http:
        return HttpSpace(args.gid, args.http, typ=args.name)
    return TupleSpace(args.gid, args.dbhost, typ=args.name)


def run_window(args):
    import tkinter as tk
    space = _space(args); me = args.who; mod = f"backgammon.{args.gid}"
    adir = asset_dir(args.domain, "backgammon")

    root = tk.Tk(); root.title(f"FrogNet Backgammon -- {me}")
    root.configure(bg="#15543a"); root.geometry("820x600")
    cv = tk.Canvas(root, width=800, height=520, bg="#15543a", highlightthickness=0)
    cv.pack(padx=8, pady=8)
    status = tk.Label(root, text="connecting...", bg="#15543a", fg="#f4f7f2",
                      font=("Consolas", 11)); status.pack(fill="x")
    bar = tk.Frame(root, bg="#15543a"); bar.pack(fill="x", pady=4)

    # load board art if present (SVG -> we draw geometry; PNG would need PIL). We draw the
    # board with canvas primitives so it works with zero raster deps, themed by THEME if shipped.
    board_svg = os.path.join(adir, "board.svg")
    have_art = os.path.isfile(board_svg)

    state = {"g": None, "seat": args.seat, "sig": None}

    def read_user():
        return space.get(space.typ, mod, f"user.{me}") or {"who": me, "intent_seq": 0}
    def write_user(u): space.put(space.typ, mod, f"user.{me}", u)
    def set_intent(**kw):
        u = read_user(); u["intent"] = kw; u["intent_seq"] = int(u.get("intent_seq", 0)) + 1; write_user(u)
    def claim(seat):
        u = read_user(); u["seat_claim"] = seat; write_user(u); state["seat"] = seat

    POINT_W, POINT_H = 52, 200
    def draw():
        cv.delete("all")
        cv.create_rectangle(8, 8, 792, 512, fill="#1f6b4a", outline="#5b3a1e", width=6)
        cv.create_rectangle(396, 8, 420, 512, fill="#4a2f18", outline="")     # bar
        g = state["g"]
        # 24 point triangles
        def tri(i, x, top):
            col = "#d9b382" if i % 2 == 0 else "#9c5a2c"
            if top: pts = [x, 12, x + POINT_W, 12, x + POINT_W / 2, 12 + POINT_H]
            else:   pts = [x, 508, x + POINT_W, 508, x + POINT_W / 2, 508 - POINT_H]
            cv.create_polygon(pts, fill=col, outline="")
        xs_l = [20 + i * 60 for i in range(6)]; xs_r = [430 + i * 60 for i in range(6)]
        for i, x in enumerate(xs_l + xs_r): tri(i, x, True)
        for i, x in enumerate(xs_l + xs_r): tri(i, x, False)
        if not g:
            return
        # checkers from the live position
        pts = g["points"]
        def stack(point_idx, x, top):
            n = pts[point_idx]
            if n == 0: return
            color = "#f5f5f5" if n > 0 else "#1a1a1a"
            for k in range(min(abs(n), 5)):
                cy = (24 + k * 38) if top else (496 - k * 38)
                cv.create_oval(x + 6, cy - 16, x + POINT_W - 6, cy + 16, fill=color, outline="#000")
        # map points 13..24 across the top, 12..1 across the bottom (standard)
        order_top = list(range(13, 25)); order_bot = list(range(12, 0, -1))
        xs = xs_l + xs_r
        for slot, p in enumerate(order_top): stack(p, xs[slot], True)
        for slot, p in enumerate(order_bot): stack(p, xs[slot], False)
        cv.create_text(700, 30, text=f"off w:{g['off']['w']} b:{g['off']['b']}",
                       fill="#f4f7f2", font=("Consolas", 11), anchor="w")

    def refresh_controls():
        for w in bar.winfo_children(): w.destroy()
        g = state["g"]; seat = state["seat"]
        if g is None:
            return
        if seat is None:
            tk.Button(bar, text="Sit White", command=lambda: (claim(W), pull()),
                      bg="#54e08a").pack(side="left", padx=4)
            tk.Button(bar, text="Sit Black", command=lambda: (claim(B), pull()),
                      bg="#54e08a").pack(side="left", padx=4)
            return
        my_turn = g.get("seats", {}).get(me) == g["turn"] or seat == g["turn"]
        if g["phase"] == "gameover":
            tk.Label(bar, text=f"game over -- winner {g['winner']}", bg="#15543a",
                     fg="#54e08a", font=("Consolas", 12)).pack(side="left")
            return
        if not my_turn:
            tk.Label(bar, text="waiting for opponent...", bg="#15543a", fg="#c6d3cb",
                     font=("Consolas", 11)).pack(side="left"); return
        if g["phase"] == "roll":
            tk.Button(bar, text="Roll", command=lambda: (set_intent(kind="roll"), pull()),
                      bg="#54e08a", font=("Consolas", 11)).pack(side="left", padx=4)
        elif g["phase"] == "move":
            for m in g.get("legal", [])[:8]:
                tk.Button(bar, text=f"{m['from']}/{m['die']}",
                          command=lambda mm=m: (set_intent(kind="move", frm=mm["from"], die=mm["die"]), pull()),
                          bg="#90c8a4").pack(side="left", padx=2)

    def _draw_sig(g, seat):
        # everything draw()/refresh_controls() actually depend on. If this is unchanged,
        # there is nothing to repaint -- so we DON'T delete("all")/rebuild, killing the
        # once-a-second flash. Redraw happens only on a real state change.
        if not g:
            return ("none", seat)
        return (
            tuple(g.get("points", [])),
            g.get("turn"), g.get("phase"),
            tuple(g.get("dice") or ()),
            g.get("off", {}).get("w"), g.get("off", {}).get("b"),
            g.get("winner"),
            tuple((m.get("from"), m.get("die")) for m in g.get("legal", [])),
            tuple(sorted((g.get("seats") or {}).items())),
            seat,
        )

    def pull():
        try:
            g = space.get(space.typ, mod, "state")
            state["g"] = g
            if g:
                status.config(text=f"turn={g['turn']} phase={g['phase']} dice={g.get('dice')}"
                                   + (f"  WINNER {g['winner']}" if g.get("winner") else ""))
            sig = _draw_sig(g, state.get("seat"))
            if sig != state.get("sig"):          # only repaint on a REAL change -> no flash
                state["sig"] = sig
                draw(); refresh_controls()
        except Exception as e:
            status.config(text=f"read error: {e}")

    if state["seat"]:
        claim(state["seat"])
    def loop():
        pull(); root.after(1200, loop)
    root.after(200, loop)
    status.config(text=f"{me} . table {args.gid} . art {'local' if have_art else 'drawn'}")
    root.mainloop()


def run_text(args):
    space = _space(args); me = args.who; mod = f"backgammon.{args.gid}"
    print(f"[bg_play] text mode -- {me} on table {args.gid}")
    while True:
        g = space.get(space.typ, mod, "state")
        if g: print("turn", g["turn"], "phase", g["phase"], "dice", g.get("dice"), "off", g.get("off"))
        if g and g.get("winner"): print("winner", g["winner"]); break
        time.sleep(1.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", help="FrogNet host (fabric/Apache) -- launcher passes this")
    ap.add_argument("--who", required=True)
    ap.add_argument("--gid", default=DEFAULT_GID)
    ap.add_argument("--name", default="boardgame", help="service name = SensorType region")
    ap.add_argument("--domain", default="home.frognet", help="FrogNet domain for assets")
    ap.add_argument("--seat", choices=[W, B], default=None)
    ap.add_argument("--dbhost"); ap.add_argument("--http")
    a = ap.parse_args()
    try:
        import tkinter  # noqa
        run_window(a)
    except Exception as e:
        print(f"[bg_play] no GUI ({e}); text mode")
        run_text(a)


if __name__ == "__main__":
    main()
