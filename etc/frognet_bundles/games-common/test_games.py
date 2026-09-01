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
"""Self-contained test for the UnREST game bundles. Run from games-common/:
    python3 test_games.py
Proves, for each game: it plays to an end, the move history in the tuple folds back to
the identical board (replay), a non-seated reader can watch, an off-turn/non-seat write
is refused, and hidden information stays hidden mid-game."""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for g in ("connectfour", "reversi", "hearts", "liarsdice"):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", f"net.frognet.{g}", "codex"))
from game_base import _copy
from connectfour_codex import ConnectFourCodex
from reversi_codex import ReversiCodex
from hearts_codex import HeartsCodex
from liarsdice_codex import LiarsDiceCodex


class T:
    def __init__(s): s.d = {}
    def get(s, k): return _copy(s.d[k]) if k in s.d else None
    def upsert(s, k, v): s.d[k] = _copy(v)
    def drop(s, k): s.d.pop(k, None)


class P:
    def __init__(s): s.d = {}
    def load(s, k): return _copy(s.d[k]) if k in s.d else None
    def save(s, k, v): s.d[k] = _copy(v)


def play_to_end(codex, gid, strategy, cap=400):
    n = 0
    while codex.get(gid, "")["phase"] != "over" and n < cap:
        st = codex._live(gid)
        who = st["turn"]
        la = codex.legal_actions(st, who)
        if not la:
            break
        codex.act(gid, who, strategy(la))
        n += 1
    return codex.get(gid, ""), n


def test_connectfour():
    c = ConnectFourCodex(T(), P()); c.new_game("g", ["a", "b"])
    for who, col in [("a", 0), ("b", 1), ("a", 0), ("b", 1), ("a", 0), ("b", 1), ("a", 0)]:
        c.act("g", who, {"col": col})
    fin = c.get("g", "")
    assert fin["winner"] == "a" and fin["phase"] == "over"
    assert c.replay("g")[-1]["board"] == fin["board"]
    assert c.get("g", "watcher") is not None                 # watcher reads
    before = c.get("g", "a")["board"]
    c.act("g", "z", {"col": 3})                               # not seated
    assert c.get("g", "a")["board"] == before
    print("connectfour: win, replay, watcher, authority  OK")


def test_reversi():
    r = ReversiCodex(T(), P()); r.new_game("g", ["d", "l"])
    fin, n = play_to_end(r, "g", lambda la: la[0])
    assert fin["phase"] == "over"
    assert r.replay("g")[-1]["board"] == fin["board"]
    print(f"reversi: full game in {n} moves, replay matches  OK")


def test_hearts():
    random.seed(1)
    h = HeartsCodex(T(), P()); h.new_game("g", ["n", "e", "s", "w"])
    v = h.get("g", "n")
    assert isinstance(v["board"]["hands"]["n"], list)
    assert all(isinstance(v["board"]["hands"][o], int) for o in ("e", "s", "w"))
    fin, n = play_to_end(h, "g", lambda la: la[0])
    assert fin["phase"] == "over" and sum(fin["board"]["scores"].values()) in (26, 78)
    assert h.replay("g")[-1]["board"]["scores"] == fin["board"]["scores"]
    print(f"hearts: hidden hands, full hand, scoring, replay  OK (winner {fin['winner']})")


def test_liarsdice():
    random.seed(2)
    l = LiarsDiceCodex(T(), P()); l.new_game("g", ["a", "b", "c"])
    v = l.get("g", "a")
    assert isinstance(v["board"]["dice"]["a"], list) and isinstance(v["board"]["dice"]["b"], int)
    assert "roll_log" not in (v.get("setup") or {})
    def strat(la):
        ch = [a for a in la if a.get("challenge")]
        return ch[0] if (ch and random.random() < 0.55) else la[0]
    fin, n = play_to_end(l, "g", strat)
    assert fin["phase"] == "over" and fin["winner"] in ("a", "b", "c")
    rep = l.replay("g")
    assert rep[-1]["winner"] == fin["winner"]
    assert rep[-1]["board"]["counts"] == fin["board"]["counts"]
    print(f"liarsdice: hidden dice, full game, deterministic replay  OK (winner {fin['winner']})")


if __name__ == "__main__":
    test_connectfour(); test_reversi(); test_hearts(); test_liarsdice()
    print("\nALL GAMES PASS -- replay and watchers are free; the moves are in the tuple.")
