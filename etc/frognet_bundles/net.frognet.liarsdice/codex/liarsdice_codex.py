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
"""Liar's Dice -- 2 to 6 players. Everyone rolls five hidden dice. Players take turns
raising a bid of the form (quantity, face) -- a higher quantity, or the same quantity at
a higher face. Instead of bidding you may CHALLENGE the previous bid: all dice are
revealed and that face is counted. If there are at least as many as bid, the bidder was
honest and the challenger loses a die; otherwise the bidder loses a die. Lose your last
die and you're out; last player with dice wins. (No wild ones, for clarity.)

Determinism for replay: every roll -- the opening and each post-challenge re-roll -- is
written into a roll_log inside the tuple as it happens. apply_action READS the roll from
the log when replaying, so folding the stored moves reproduces the exact game. Hidden
info: you see only your own dice until a challenge reveal; view() redacts the rest.
"""
from __future__ import annotations
import random
from typing import Any, Dict, List
from game_base import GameCodex, _copy

START_DICE = 5


class LiarsDiceCodex(GameCodex):
    NAME = "liarsdice"
    MIN_PLAYERS = 2
    MAX_PLAYERS = 6

    def _roll(self, n: int) -> List[int]:
        return sorted(random.randint(1, 6) for _ in range(n))

    def initial_setup(self, seats: List[str]) -> Dict[str, Any]:
        rolls = {s: self._roll(START_DICE) for s in seats}
        return {
            "counts": {s: START_DICE for s in seats},
            "dice": {s: rolls[s] for s in seats},
            "alive": list(seats),
            "bid": None, "bidder": None, "round": 0,
            "last_reveal": None,
            "first_turn": seats[0],
        }

    def _next_alive(self, state, who) -> str:
        alive = state["board"]["alive"]
        i = alive.index(who)
        return alive[(i + 1) % len(alive)]

    def legal_actions(self, state, who) -> List[Dict[str, Any]]:
        if state["turn"] != who:
            return []
        b = state["board"]
        acts: List[Dict[str, Any]] = []
        bid = b["bid"]
        total = sum(b["counts"][s] for s in b["alive"])
        # raises: higher quantity, or same quantity higher face
        if bid is None:
            for f in range(1, 7):
                acts.append({"bid": [1, f]})
        else:
            q, f = bid
            for nf in range(f + 1, 7):
                acts.append({"bid": [q, nf]})
            for nq in range(q + 1, total + 1):
                for nf in range(1, 7):
                    acts.append({"bid": [nq, nf]})
            acts.append({"challenge": True})
        return acts

    def apply_action(self, state, who, action) -> bool:
        b = state["board"]
        if "bid" in action:
            q, f = action["bid"]
            q = int(q); f = int(f)
            if not (1 <= f <= 6 and q >= 1):
                return False
            if b["bid"] is not None:
                pq, pf = b["bid"]
                if not (q > pq or (q == pq and f > pf)):
                    return False
            b["bid"] = [q, f]
            b["bidder"] = who
            state["turn"] = self._next_alive(state, who)
            return True

        if action.get("challenge"):
            if b["bid"] is None:
                return False
            q, f = b["bid"]
            count = sum(b["dice"][s].count(f) for s in b["alive"])
            loser = b["bidder"] if count >= q else who   # bid good -> challenger loses
            b["last_reveal"] = {"bid": [q, f], "face_count": count,
                                "challenger": who, "bidder": b["bidder"],
                                "loser": loser, "dice": _copy(b["dice"])}
            b["counts"][loser] -= 1
            if b["counts"][loser] <= 0:
                b["alive"] = [s for s in b["alive"] if s != loser]
            if len(b["alive"]) <= 1:
                state["winner"] = b["alive"][0] if b["alive"] else None
                state["phase"] = "over"
                return True
            # next round: re-roll everyone alive. Capture the roll in the log so replay
            # is exact; during replay the roll is ALREADY in the log and we read it.
            b["round"] += 1
            rk = str(b["round"])
            log = state.setdefault("rolls", {})  # captured re-rolls (state-level, not in board)
            if rk in log:
                rolls = log[rk]                          # replay path: reuse captured roll
            else:
                rolls = {s: self._roll(b["counts"][s]) for s in b["alive"]}
                log[rk] = rolls                          # live path: capture it
            b["dice"] = {s: list(rolls[s]) for s in b["alive"]}
            b["bid"] = None
            b["bidder"] = None
            # loser (if still alive) starts the next round; else next alive after loser.
            start = loser if loser in b["alive"] else None
            if start is None:
                # loser eliminated: the player who would have followed them bids.
                order = list(state["seats"])
                idx = order.index(loser)
                nxt = None
                for k in range(1, len(order) + 1):
                    cand = order[(idx + k) % len(order)]
                    if cand in b["alive"]:
                        nxt = cand; break
                start = nxt
            state["turn"] = start
            return True
        return False

    def view(self, state, who) -> Dict[str, Any]:
        if state.get("phase") == "over" or not who:
            return state
        v = _copy(state)
        b = v.get("board") or {}
        dice = b.get("dice") or {}
        # you see your own dice; others show only a count (their cup is closed)
        b["dice"] = {s: (d if s == who else len(d)) for s, d in dice.items()}
        v.pop("rolls", None)  # captured re-rolls are everyone's dice -- never live
        return v
