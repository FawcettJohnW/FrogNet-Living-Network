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
"""
game_client.py -- ONE client for every UnREST game.

Because every game speaks the same shared-tuple contract (state with `seats`, `turn`,
`phase`, `winner`, `board`, `history`, and a `legal` set the player can act on), a single
viewer plays all of them. It does three things, and all three are just reading or writing
the one shared tuple:

  PLAY   -- join a seat, and on your turn write one of the legal actions.
  WATCH  -- read the tuple and render it. No seat, no subscription; watching is reading.
  REPLAY -- fold the move history that is already in the tuple. No server log needed.

Transport is the in-proxy game origin: each op is a `_game` envelope POSTed to HOST/game;
the origin applies it to working memory and returns the (viewer-redacted) state. The game
author wrote none of this -- it is the FrogNet layer underneath.

Launched by the Communicator as:  game_client.py --connect HOST --who NAME  (game/table
come from the per-bundle shim). Also runs standalone with --game/--table/--watch/--cli.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


class OriginClient:
    """Reads and writes the shared game tuple via the proxy game origin. No retry, no
    ack, no seq -- over reliable transit a get just takes as long as the link takes."""
    def __init__(self, host, game, table, who, timeout=30.0):
        if "://" not in host:
            host = "http://" + host
        self.base = host.rstrip("/")
        self.game, self.table, self.who, self.timeout = game, table, who, timeout

    def _op(self, op, **args):
        env = {"_game": 1, "game": self.game, "table": self.table,
               "op": op, "who": self.who, "args": args}
        req = urllib.request.Request(self.base + "/game",
                                     data=json.dumps(env).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def join(self, role="player"): return self._op("join", role=role)
    def get(self):                 return self._op("get")
    def act(self, action):         return self._op("act", action=action)
    def replay(self):              return self._op("replay")
    def presence(self, role):      return self._op("presence", role=role)


# --------------------------------------------------------------------------
# Generic, game-agnostic rendering. Special-cases the common board shapes
# (grid / hands / dice / trick / bid) so all four games read cleanly, with a
# JSON fallback for anything new.
# --------------------------------------------------------------------------
_PIPS = ".XO+*#@"      # seat 0,1,2,... markers for grids (ASCII: a latin-1 console
                       # raises UnicodeEncodeError on a print, which kills the thread)


def render_board(state) -> str:
    b = state.get("board") or {}
    lines = []
    seats = state.get("seats") or []
    if "grid" in b:
        g = b["grid"]
        for row in reversed(g):                          # row 0 is the bottom
            lines.append(" ".join(_PIPS[c + 1] if c >= 0 else "." for c in row))
        if "score" in b:
            lines.append("score: " + ", ".join(f"{s}={b['score'].get(s,0)}" for s in seats))
    if "hands" in b:                                     # hearts
        h = b["hands"]
        for s in seats:
            v = h.get(s)
            shown = _cards(v) if isinstance(v, list) else f"({v} cards)"
            lines.append(f"{s:>8}: {shown}")
        if b.get("trick"):
            lines.append("trick:  " + "  ".join(f"{p['who']}:{_card(p['card'])}" for p in b["trick"]))
        if b.get("scores"):
            lines.append("scores: " + ", ".join(f"{s}={b['scores'][s]}" for s in b["scores"]))
    if isinstance(b.get("dice"), dict):                  # liar's dice (dice keyed by player)
        d = b["dice"]
        for s in (b.get("alive") or seats):
            v = d.get(s)
            shown = " ".join(str(x) for x in v) if isinstance(v, list) else f"[{v} hidden]"
            lines.append(f"{s:>8}: {shown}  ({b.get('counts',{}).get(s,'?')} dice)")
        if b.get("bid"):
            lines.append(f"bid:    {b['bid'][0]}x{b['bid'][1]}  by {b.get('bidder')}")
        if b.get("last_reveal"):
            lines.append(f"reveal: {json.dumps(b['last_reveal'])}")
    if "points" in b:                                    # backgammon (points list + bar/off/dice)
        pts = b.get("points") or []
        bar = b.get("bar") if isinstance(b.get("bar"), dict) else {}
        off = b.get("off") if isinstance(b.get("off"), dict) else {}
        def cell(i):
            v = pts[i] if 0 <= i < len(pts) else 0
            return f"{('w' if v>0 else 'b' if v<0 else '.')}{abs(v) if v else ''}"
        lines.append("pts 13-24: " + " ".join(cell(i) for i in range(13, 25)))
        lines.append("pts 12-1 : " + " ".join(cell(i) for i in range(12, 0, -1)))
        lines.append(f"bar w={bar.get('w',0)} b={bar.get('b',0)}   off w={off.get('w',0)} b={off.get('b',0)}")
        dice = b.get("dice") or []
        if isinstance(dice, list) and dice:
            lines.append(f"dice: {dice}  remaining: {b.get('dice_remaining')}")
    if not lines:                                        # unknown shape -> raw
        lines.append(json.dumps(b, indent=2))
    return "\n".join(lines)


def _card(c): return f"{ {11:'J',12:'Q',13:'K',14:'A'}.get(c[0], c[0]) }{c[1]}"
def _cards(cs): return " ".join(_card(c) for c in cs)


def status_line(state, who) -> str:
    phase, turn, win = state.get("phase"), state.get("turn"), state.get("winner")
    if phase == "over":
        return f"GAME OVER -- winner: {win}" if win else "GAME OVER -- draw"
    if phase == "lobby":
        return f"waiting for players: {state.get('seats')}"
    you = " (your turn)" if turn == who else ""
    return f"turn: {turn}{you}"


def legal_for(state, who):
    """The legal action set for `who`, if the origin included it. Backgammon ships a
    'legal' list on the board; the base games compute it server-side per request, so we
    surface whatever the state carries plus a generic 'pass-through' of board['legal']."""
    b = state.get("board") or {}
    return b.get("legal") or state.get("legal") or []


# --------------------------------------------------------------------------
# CLI mode -- scriptable, headless, used for testing and for no-display hosts.
# --------------------------------------------------------------------------
def run_cli(client: OriginClient, who, watch, replay):
    if replay:
        frames = client.replay().get("state") or []
        for i, f in enumerate(frames):
            print(f"\n--- ply {i} ---")
            print(render_board({"board": f.get("board"), "seats": []}))
        return
    st = (client.join("watcher") if watch else client.join("player")).get("state")
    print(render_board(st)); print(status_line(st, who))
    print("(CLI is a read/inspect view; use the Tk client to take turns.)")


# --------------------------------------------------------------------------
# Tk mode -- what the Communicator hosts.
# --------------------------------------------------------------------------
def run_tk(client: OriginClient, who, game, table, watch):
    import tkinter as tk
    root = tk.Tk()
    root.title(f"{game} . {table} . {who}{' (watching)' if watch else ''}")
    root.configure(bg="#10141a")
    body = tk.Text(root, width=46, height=16, bg="#0c0f14", fg="#cfe", bd=0,
                   font=("Menlo", 13))
    body.pack(padx=10, pady=(10, 4), fill="both", expand=True)
    status = tk.Label(root, text="", bg="#10141a", fg="#9fb", font=("Helvetica", 12))
    status.pack(fill="x", padx=10)
    actions = tk.Frame(root, bg="#10141a"); actions.pack(fill="x", padx=10, pady=8)

    state = {"cur": None}

    def refresh(st=None):
        if st is None:
            st = client.get().get("state")
        state["cur"] = st
        body.config(state="normal"); body.delete("1.0", "end")
        body.insert("end", render_board(st)); body.config(state="disabled")
        status.config(text=status_line(st, who) + "   here: " +
                      ", ".join(p["who"] + ("*" if p["role"] == "player" else "")
                                for p in (st.get("_here") or [])))
        for w in actions.winfo_children():
            w.destroy()
        if not watch and st.get("phase") == "play" and st.get("turn") == who:
            for a in legal_for(st, who)[:14]:
                tk.Button(actions, text=json.dumps(a), command=lambda a=a: do(a),
                          bg="#1d2733", fg="#cfe", bd=0).pack(side="left", padx=3)

    def do(action):
        refresh(client.act(action).get("state"))

    client.join("watcher" if watch else "player")
    refresh()
    root.after(1500, lambda: _poll(root, refresh))       # slow poll: see others' moves
    root.mainloop()


def _poll(root, refresh):
    try:
        refresh()
    except Exception:
        pass
    root.after(1500, lambda: _poll(root, refresh))


def main(default_game="backgammon"):
    ap = argparse.ArgumentParser()
    ap.add_argument("--connect", required=True, help="game origin host (e.g. 10.130.130.1)")
    ap.add_argument("--who", default="player")
    ap.add_argument("--game", default=default_game)
    ap.add_argument("--table", default="table-1")
    ap.add_argument("--watch", action="store_true", help="join as a watcher (read-only)")
    ap.add_argument("--replay", action="store_true", help="show the folded move history")
    ap.add_argument("--cli", action="store_true", help="text mode (no display)")
    a = ap.parse_args()
    client = OriginClient(a.connect, a.game, a.table, a.who)
    if a.cli or a.replay:
        run_cli(client, a.who, a.watch, a.replay)
    else:
        run_tk(client, a.who, a.game, a.table, a.watch)


if __name__ == "__main__":
    main()
