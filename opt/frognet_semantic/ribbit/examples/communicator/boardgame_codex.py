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
"""boardgame_codex.py - the board-game bundle as a CODEX ON THE SUBSTRATE (phone side).

This is the phone Communicator's integration: a bundle codex on the same Codex+Channel+
Freshness spine as presence/chat/the A/V call - NOT the host-side governor/engine. It
extends the existing BackgammonCodex shape (element game.backgammon.<gid>, position
LATEST_ONLY, move LOSSLESS_EVENTUAL, gid RESIDENT_ONCE, bounded MOVE-TIP on the wire) and
folds in the real rules (bg_rules, lifted verbatim from backgammon_codex.py) so the codex
is AUTHORITATIVE: it rolls dice, gates turns, and rejects illegal moves. The growing move
log stays resident memory; only the bounded move-tip + latest position cross the wire.

Opening this bundle in communicator.py (register in BUNDLE_CODICES) gives a playable table.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from bundle_codex import BackgammonCodex
import bg_rules as R


def _fresh_state(gid, white, black):
    return {"gid": gid, "white": white, "black": black,
            "points": R.start_position(), "bar": {R.WHITE:0, R.BLACK:0},
            "off": {R.WHITE:0, R.BLACK:0}, "turn": R.WHITE, "phase": "roll",
            "dice": [], "dice_remaining": [], "legal": [], "winner": None,
            "position": None, "moves": []}


class BoardGameCodex(BackgammonCodex):
    """Authoritative board-game codex. Same element/freshness as BackgammonCodex; rules
    enforced here so the table is playable directly through the Communicator spine."""

    def __init__(self, transient, perm, gid: str, white: str = "white", black: str = "black"):
        super().__init__(transient, perm, gid, white, black)
        # overwrite the bare initial element with a full rules-bearing position
        st = _fresh_state(gid, white, black)
        st["position"] = self._snapshot(st)
        st["ts"] = self.now_ms()
        self._commit(self.element, st)

    @staticmethod
    def _snapshot(st) -> Dict[str, Any]:
        """The LATEST_ONLY position the wire carries: the board as it stands now."""
        return {"points": list(st["points"]), "bar": dict(st["bar"]), "off": dict(st["off"]),
                "turn": st["turn"], "phase": st["phase"], "dice": list(st["dice"]),
                "dice_remaining": list(st["dice_remaining"]), "legal": list(st["legal"]),
                "winner": st["winner"]}

    def state(self) -> Dict[str, Any]:
        return self._live(self.element)

    def join(self, peer_id: str):
        """A peer begins converging. Ensure its channel exists and stage the CURRENT
        position so its first flush carries a FULL snapshot (mid-game join catches up).
        Subsequent moves ride bounded tips. The codex conveys the structure to the new peer."""
        from substrate import Channel
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        st = self._live(self.element)
        if st.get("position") is not None:
            ch.offer("position", st["position"])     # latest-only: the board as it stands
        return ch

    # --- authority: a participant submits an INTENT; the codex enforces rules ---
    def roll(self, who: str, d1: Optional[int] = None, d2: Optional[int] = None) -> Dict[str, Any]:
        st = self._live(self.element)
        if st["winner"] is not None: return {"ok": False, "why": "game over"}
        if who != st[st["turn"] and "white" or "black"] and who not in (st["white"], st["black"]):
            pass  # name-based seat check below
        seat = R.WHITE if who == st["white"] else R.BLACK if who == st["black"] else None
        if seat != st["turn"]: return {"ok": False, "why": "not your turn"}
        if st["phase"] != "roll": return {"ok": False, "why": "not in roll phase"}
        if not R.do_roll(st, d1, d2): return {"ok": False, "why": "roll rejected"}
        return self._land(st, move={"kind": "roll", "who": who, "dice": st["dice"]})

    def move(self, who: str, frm: int, die: int) -> Dict[str, Any]:
        st = self._live(self.element)
        if st["winner"] is not None: return {"ok": False, "why": "game over"}
        seat = R.WHITE if who == st["white"] else R.BLACK if who == st["black"] else None
        if seat != st["turn"]: return {"ok": False, "why": "not your turn"}
        if st["phase"] != "move": return {"ok": False, "why": "not in move phase"}
        if not R.apply_move(st, frm, die): return {"ok": False, "why": "illegal move"}
        return self._land(st, move={"kind": "move", "who": who, "from": frm, "die": die})

    def _land(self, st, move) -> Dict[str, Any]:
        """Record the move and converge it. CRITICAL: commit the SAME rules-mutated st we
        hold (the store copies on get, so delegating to a parent that re-_live()s would
        discard the rules changes - phase/dice/points). We persist st here, then offer the
        bounded MOVE-TIP (lossless) + latest position on every channel, exactly as the
        parent would, but against the authoritative state."""
        st["legal"] = R.legal_moves(st) if st["phase"] == "move" else []
        pos = self._snapshot(st)
        st["position"] = pos
        st["moves"].append(move)                     # history stays resident memory
        self._commit(self.element, st)               # persist the RULES-MUTATED state
        for ch in self._channels.values():
            ch.offer("move", move)                   # the wire carries the bounded tip
            ch.offer("position", pos)                # ...and the latest position
        return {"ok": True, "state": pos}


# the factory communicator.py registers in BUNDLE_CODICES
def make(c, gid: str = "table", **kw):
    return BoardGameCodex(c.t, c.p, gid, kw.get("white", "white"), kw.get("black", "black"))
