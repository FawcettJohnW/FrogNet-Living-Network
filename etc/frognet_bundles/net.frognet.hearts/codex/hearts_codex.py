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
"""Hearts -- 4 players, one hand. Trick-taking: follow the led suit if you can; hearts
can't be led until 'broken'; no hearts or the Queen of Spades on the first trick. Each
heart scores 1, the Queen of Spades 13. Shoot the moon (take all 26) and instead the
other three take 26 each. Lowest score wins the hand.

Hidden information: each player sees only their own hand (view() redacts the rest) until
the hand is over, after which all is revealed -- so a watcher mid-hand can't peek, but a
replay of a finished hand shows everything. The pass phase is omitted in this cut.
"""
from __future__ import annotations
import random
from typing import Any, Dict, List
from game_base import GameCodex, _copy

SUITS = ["C", "D", "H", "S"]
RANKS = list(range(2, 15))            # 11=J 12=Q 13=K 14=A
QS = [12, "S"]                        # Queen of Spades


def _deck() -> List[List]:
    return [[r, s] for s in SUITS for r in RANKS]


class HeartsCodex(GameCodex):
    NAME = "hearts"
    MIN_PLAYERS = 4
    MAX_PLAYERS = 4

    def initial_setup(self, seats: List[str]) -> Dict[str, Any]:
        deck = _deck()
        random.shuffle(deck)
        hands = {seats[i]: sorted(deck[i * 13:(i + 1) * 13]) for i in range(4)}
        # Lead is whoever holds 2 of clubs.
        leader = next(s for s in seats if [2, "C"] in hands[s])
        return {
            "hands": hands, "trick": [], "led_suit": None, "hearts_broken": False,
            "tricks_won": {s: [] for s in seats}, "trick_no": 0,
            "scores": {}, "first_turn": leader,
        }

    # ---- legality ----------------------------------------------------------
    def _legal_cards(self, state, who) -> List[List]:
        b = state["board"]
        hand = b["hands"].get(who, [])
        first_trick = (b["trick_no"] == 0)
        if not b["trick"]:                              # leading
            if first_trick:
                return [[2, "C"]] if [2, "C"] in hand else []
            if not b["hearts_broken"]:
                non_h = [c for c in hand if c[1] != "H"]
                return non_h or hand                    # only hearts left -> may lead them
            return hand
        led = b["led_suit"]
        follow = [c for c in hand if c[1] == led]
        if follow:
            return follow
        if first_trick:                                 # can't dump hearts/QS on trick 1
            safe = [c for c in hand if c[1] != "H" and c != QS]
            return safe or hand
        return hand

    def legal_actions(self, state, who) -> List[Dict[str, Any]]:
        if state["turn"] != who:
            return []
        return [{"card": c} for c in self._legal_cards(state, who)]

    # ---- play --------------------------------------------------------------
    def apply_action(self, state, who, action) -> bool:
        b = state["board"]
        card = action.get("card")
        if card is None:
            return False
        card = [card[0], card[1]]
        if card not in self._legal_cards(state, who):
            return False
        b["hands"][who].remove(card)
        if not b["trick"]:
            b["led_suit"] = card[1]
        b["trick"].append({"who": who, "card": card})
        if card[1] == "H" or card == QS:
            b["hearts_broken"] = True

        seats = state["seats"]
        if len(b["trick"]) < len(seats):
            state["turn"] = seats[(seats.index(who) + 1) % len(seats)]
            return True

        # trick complete -> resolve winner (highest of led suit)
        led = b["led_suit"]
        win = max((p for p in b["trick"] if p["card"][1] == led),
                  key=lambda p: p["card"][0])
        winner = win["who"]
        b["tricks_won"][winner].extend(p["card"] for p in b["trick"])
        b["trick"] = []
        b["led_suit"] = None
        b["trick_no"] += 1
        if b["trick_no"] >= 13:
            self._score(state)
        else:
            state["turn"] = winner
        return True

    def _score(self, state) -> None:
        b = state["board"]
        seats = state["seats"]
        pts = {}
        for s in seats:
            won = b["tricks_won"][s]
            pts[s] = sum(1 for c in won if c[1] == "H") + (13 if QS in won else 0)
        shooter = next((s for s in seats if pts[s] == 26), None)
        if shooter is not None:                         # shot the moon
            pts = {s: (0 if s == shooter else 26) for s in seats}
        b["scores"] = pts
        low = min(pts.values())
        winners = [s for s in seats if pts[s] == low]
        state["winner"] = winners[0] if len(winners) == 1 else winners
        state["phase"] = "over"

    # ---- hidden info: only your own hand, until the hand is over -----------
    def view(self, state, who) -> Dict[str, Any]:
        if state.get("phase") == "over" or not who:
            return state
        v = _copy(state)
        b = v.get("board") or {}
        hands = b.get("hands") or {}
        b["hands"] = {s: (cards if s == who else len(cards)) for s, cards in hands.items()}
        return v
