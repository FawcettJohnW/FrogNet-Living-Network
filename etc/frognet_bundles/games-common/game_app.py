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
"""game_app.py -- one Tk app for every FrogNet tuple game. Runs the codex LOCALLY against the
shared tuple space (no origin server). Launched by the Games hub:

    game_app.py --game connectfour --table den --who alice            # play (take a seat)
    game_app.py --game connectfour --table den --who carol --watch    # watch (read-only)
    game_app.py --game connectfour --table den --who alice --start    # create + seat

Board converges through the shared tuple; the codex's turn rules gate writes."""
import argparse, json, os, sys, time
import tkinter as tk

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# game_app.py lives in <bundles>/games-common/, so the bundles root is ONE level up (the dir
# that holds games-common/ and net.frognet.*/). dirname(_HERE) = <bundles>. (A previous bug
# went up TWO levels, landing above bundles/, so net.frognet.<game>/codex was never found.)
BUNDLES_ROOT = os.environ.get("FROGNET_BUNDLES_ROOT") or os.path.dirname(_HERE)
for cand in (os.path.join(BUNDLES_ROOT, "games-common"),):
    if os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)
from game_tuple_client import LocalGame
try:
    from game_client import render_board, status_line, legal_for
except Exception:
    def render_board(st): return json.dumps(st.get("board"), indent=1)
    def status_line(st, who): return f"phase {st.get('phase')} turn {st.get('turn')}"
    def legal_for(st, who): return st.get("legal") or []

BG="#10141a"; INK="#cfe9e0"; ACCENT="#54e08a"; DIM="#8fb0a4"; CARD="#1d2733"


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--table", default="table-1")
    ap.add_argument("--who", required=True)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--dbhost", default=None)
    ap.add_argument("--bundles-root", default=BUNDLES_ROOT)
    a=ap.parse_args()
    dbhost=a.dbhost or "databasehost.frognet"   # game state is DATA -> the data host, NOT _control
    CARD_GAMES = ("connectfour", "hearts", "liarsdice", "reversi", "backgammon")
    if a.game not in CARD_GAMES:
        sys.stderr.write(f"game_app only runs card games {CARD_GAMES}; got '{a.game}'. "
                         f"(backgammon/boardgame have their own apps.)\n")
        sys.exit(2)

    lg=LocalGame(a.game, a.table, a.who, dbhost, a.bundles_root)

    # ensure a well-formed table exists before joining: start it ONLY if truly absent. Never
    # overwrite an existing in-play table (a stale read must not wipe a live game back to lobby).
    # Watchers never create.
    raw_state = lg.get()
    table_present = isinstance(raw_state, dict) and "phase" in raw_state
    sys.stderr.write(f"[game_app] read table {a.game}/{a.table}: "
                     f"{'present phase='+str(raw_state.get('phase'))+' seats='+str(raw_state.get('seats')) if table_present else 'ABSENT/'+type(raw_state).__name__}\n")
    if not table_present and not a.watch:
        st0 = lg.start(seats=[])
        sys.stderr.write(f"[game_app] start: seats={st0.get('seats') if isinstance(st0,dict) else st0} "
                         f"version={st0.get('version') if isinstance(st0,dict) else '?'}\n")
    joined = lg.join("watcher" if a.watch else "player")
    if joined is None:
        sys.stderr.write(f"[game_app] JOIN RETURNED None -- table {a.game}/{a.table} could not be read "
                         f"back from {dbhost}. Seat NOT taken.\n")
    else:
        sys.stderr.write(f"[game_app] join: who={a.who} seats={joined.get('seats')} "
                         f"phase={joined.get('phase')} version={joined.get('version')}\n")
    sys.stderr.flush()

    root=tk.Tk()
    root.title(f"{a.game} . {a.table} . {a.who}{' (watching)' if a.watch else ''}")
    root.configure(bg=BG)
    head=tk.Label(root, text=f"{a.game.upper()} . table {a.table}", bg=BG, fg=ACCENT,
                  font=("Georgia",14,"bold")); head.pack(anchor="w", padx=12, pady=(10,2))
    body=tk.Text(root, width=48, height=16, bg="#0c0f14", fg=INK, bd=0,
                 font=("Consolas",13)); body.pack(padx=12, pady=4, fill="both", expand=True)
    status=tk.Label(root, text="", bg=BG, fg=DIM, font=("Consolas",11), anchor="w",
                    justify="left"); status.pack(fill="x", padx=12)
    actions=tk.Frame(root, bg=BG); actions.pack(fill="x", padx=12, pady=8)
    st_cache={"sig":None}

    def _sig(st):
        if not st: return None
        return (json.dumps(st.get("board"), sort_keys=True), st.get("turn"),
                st.get("phase"), st.get("winner"), len(st.get("history") or []),
                tuple(sorted((p["who"],p["role"]) for p in (st.get("_here") or []))))

    last_good = {"st": None}        # remember the last well-formed state we painted

    def refresh(st=None):
        if st is None:
            st=lg.get()
        if not isinstance(st, dict):
            # A transient read miss must NEVER overwrite a good board or recreate a live table.
            # If we have previously seen a real state, keep showing it and just skip this poll.
            if isinstance(last_good["st"], dict):
                sys.stderr.write("[game_app] read miss -- keeping last good board\n"); sys.stderr.flush()
                return
            # No good state ever seen: only NOW may a player create the table (genuine cold start).
            if not a.watch:
                try:
                    lg.start(seats=[]); lg.join("player"); st=lg.get()
                except Exception:
                    import traceback; traceback.print_exc(); st=None
            if not isinstance(st, dict):
                body.config(state="normal"); body.delete("1.0","end")
                body.insert("end", "waiting for the table...\n(no game here yet)")
                body.config(state="disabled")
                status.config(text=f"{a.game} . {a.table} . not started")
                return
        last_good["st"] = st          # this is a real state; remember it
        try:
            st["_here"]=lg.here()
        except Exception:
            import traceback; traceback.print_exc()
            st["_here"]=[]
        sig=_sig(st)
        if sig==st_cache["sig"]:           # flash-gate: only repaint on real change
            return
        st_cache["sig"]=sig
        sys.stderr.write(f"[game_app] PAINT phase={st.get('phase')} turn={st.get('turn')} "
                         f"board_keys={list((st.get('board') or {}).keys()) if isinstance(st.get('board'),dict) else type(st.get('board')).__name__}\n")
        sys.stderr.flush()
        body.config(state="normal"); body.delete("1.0","end")
        try:
            _rendered = render_board(st)
        except Exception:
            import traceback; traceback.print_exc()
            _rendered = "(render error -- see console)"
        sys.stderr.write(f"[game_app] rendered {len(_rendered)} chars: {_rendered[:60]!r}\n"); sys.stderr.flush()
        body.insert("end", _rendered); body.config(state="disabled")
        try: root.update_idletasks()
        except Exception: pass
        here=", ".join(p["who"]+("*" if p["role"]=="player" else "")
                       for p in (st.get("_here") or []))
        status.config(text=status_line(st, a.who)+(f"\nhere: {here}" if here else ""))
        for w in actions.winfo_children(): w.destroy()
        if not a.watch and st.get("phase")=="play" and st.get("turn")==a.who:
            for act in (legal_for(st, a.who) or [])[:14]:
                tk.Button(actions, text=_label(act), command=lambda ac=act: do(ac),
                          bg=CARD, fg=INK, bd=0, padx=8, pady=4,
                          activebackground=ACCENT).pack(side="left", padx=3)
        elif not a.watch and st.get("phase")=="lobby":
            tk.Label(actions, text="waiting for players to join...", bg=BG, fg=DIM,
                     font=("Consolas",11)).pack(side="left")

    def _label(a):
        if isinstance(a, dict):
            for k in ("col","cell","card","bid","move","from"):
                if k in a: return f"{k} {a[k]}"
        return json.dumps(a)

    def do(action):
        refresh(lg.act(action))

    refresh()
    def poll():
        try: refresh()
        except Exception:
            import traceback; traceback.print_exc()
        root.after(1200, poll)
    root.after(600, poll)
    root.mainloop()


if __name__ == "__main__":
    import traceback as _tb
    try:
        main()
    except Exception:
        _tb.print_exc()
        try:
            import os as _os
            logp=_os.path.join(_os.path.expanduser("~"), "frognet_game_crash.log")
            with open(logp,"w") as f: _tb.print_exc(file=f)
            sys.stderr.write(f"\n[game_app] CRASH written to {logp}\n")
        except Exception: pass
        sys.exit(3)
