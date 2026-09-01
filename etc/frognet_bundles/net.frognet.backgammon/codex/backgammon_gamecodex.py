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
"""backgammon_gamecodex.py -- Backgammon as a games-common GameCodex, so it joins the SAME
lobby model as the card games: seats as a LIST (seat 0 = White, seat 1 = Black), join takes
an open seat, the base handles turn-gate/history/presence/replay. The actual backgammon RULES
are unchanged -- we reuse the proven pure functions (start_position, legal_moves, apply_move,
do_roll) from backgammon_codex. This is the 'manage the same memory, uniform shape' port:
nothing about the game logic changes, only how seating/turns plug into the shared tuple.

Action stream (all in history, so replay folds exactly):
  {"kind":"roll"[, "d1","d2"]}     -> roll dice (random unless given, for deterministic tests)
  {"kind":"move","from":f,"die":d} -> play one checker; turn ends when dice exhausted/blocked
"""
import os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                       # backgammon_codex (rules) is beside us
for _c in (os.path.join(_HERE, "..", "..", "games-common"),
           os.environ.get("FROGNET_GAMES_COMMON", "")):
    if _c and os.path.isdir(_c) and _c not in sys.path:
        sys.path.insert(0, _c)
from game_base import GameCodex
from backgammon_codex import (start_position, legal_moves, apply_move, do_roll,
                              WHITE, BLACK)


class BackgammonGameCodex(GameCodex):
    NAME = "backgammon"
    MIN_PLAYERS = 2
    MAX_PLAYERS = 2

    # seat index -> backgammon color
    def _color(self, state, who):
        seats = state.get("seats") or []
        if who not in seats:
            return None
        return WHITE if seats.index(who) == 0 else BLACK

    def initial_setup(self, seats):
        """The opening board. Stored as state['setup'] and copied to state['board'];
        the base sets turn to seats[0] (White) which is correct for backgammon's opener
        (White rolls first)."""
        return {
            "points": start_position(),
            "bar": {WHITE: 0, BLACK: 0},
            "off": {WHITE: 0, BLACK: 0},
            "dice": [], "dice_remaining": [],
            "phase": "roll",                 # inner roll/move phase (distinct from base lobby/play)
            "legal": [],
            "first_turn": None,              # base picks seats[0]
        }

    # -- map the base's per-viewer 'who' to a color-keyed board the rules operate on --
    def _board_with_turn(self, state):
        """Build the rules' working board from the base state: the rules read board['turn']
        as a color, so set it from whose turn it is (seat -> color)."""
        b = state["board"]
        turn_who = state.get("turn")
        b = dict(b)
        b["turn"] = self._color(state, turn_who)
        return b

    def legal_actions(self, state, who):
        """What 'who' may do right now. Only the player on turn acts. If the inner phase is
        'roll', the sole action is to roll; if 'move', the legal checker moves; bear-off and
        bar entry are already encoded by legal_moves()."""
        if state.get("phase") != "play":
            return []
        if state.get("turn") != who:
            return []
        b = self._board_with_turn(state)
        if b.get("phase") == "roll":
            return [{"kind": "roll"}]
        if b.get("phase") == "move":
            return [{"kind": "move", "from": m["from"], "die": m["die"],
                     "to": m.get("to"), "hit": m.get("hit"), "bear": m.get("bear")}
                    for m in legal_moves(b)]
        return []

    def apply_action(self, state, who, action):
        """Apply a roll or move to the board in place; advance turn/winner/phase. Returns True
        if the action was legal and applied (the base then appends it to history)."""
        if state.get("turn") != who:
            return False
        color = self._color(state, who)
        if color is None:
            return False
        b = state["board"]
        b["turn"] = color                          # rules read color from board['turn']
        kind = action.get("kind")
        ok = False
        if kind == "roll":
            ok = do_roll(b, action.get("d1"), action.get("d2"))
        elif kind == "move":
            ok = apply_move(b, action["from"], action["die"])
        if not ok:
            return False
        # reflect inner result up to the base state
        if b.get("phase") == "gameover":
            state["phase"] = "gameover"
            # winner color -> seat name
            seats = state.get("seats") or []
            widx = 0 if b.get("winner") == WHITE else 1
            state["winner"] = seats[widx] if widx < len(seats) else b.get("winner")
            state["turn"] = None
        else:
            # whose turn now? board['turn'] is a color -> map back to the seat name
            seats = state.get("seats") or []
            cidx = 0 if b.get("turn") == WHITE else 1
            state["turn"] = seats[cidx] if cidx < len(seats) else state.get("turn")
        return True

    def view(self, state, who):
        """Backgammon is perfect-information: everyone sees the whole board. (No redaction,
        unlike liar's dice.) The base adds legal_actions for 'who' on top."""
        return state
