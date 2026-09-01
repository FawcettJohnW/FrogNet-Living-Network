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
game_base.py -- shared base for UnREST turn-based, multiplayer games.

THE WHOLE DESIGN IN ONE SENTENCE: the entire game is ONE tuple in shared memory --
the setup (including any deal), the ordered move history, and a cached current board --
and because the moves live in that tuple, replay and watchers are not features you
build, they are things you already have.

  - A WATCHER is anyone who reads the tuple. There is no subscribe, no broadcast, no
    watcher list. `get` returns the tuple; rendering it is watching. The only thing the
    authority gates is the WRITE: a move is honored only from the player whose turn it
    is. Reads are open. So watching is just reading the memory.

  - REPLAY is folding the move history that is already in the tuple. `fold(setup,
    history[:k])` reconstructs the board at ply k. The reader who did one `get` already
    holds the whole history, so replay can happen with zero further round-trips. There
    is no event log, no capture, no separate store -- the moves were memory the whole
    time.

Memory, not messages.

A concrete game subclasses GameCodex and implements its RULES only:
    NAME, MIN_PLAYERS, MAX_PLAYERS
    initial_setup(seats)            -> the starting board (include any random deal here)
    legal_actions(state, who)       -> list of action dicts the UI can offer
    apply_action(state, who, action)-> bool; mutate state['board'], advance state['turn'],
                                       set state['winner']/state['phase'] when the game ends
    view(state, who)  [optional]    -> per-viewer projection; override to redact hidden
                                       info (a hand, a dice cup) so a live watcher can't
                                       see what a player shouldn't. Default: full state.

Everything else -- seating, the turn gate, history, replay, presence, the working-memory
store pattern that lets the host float -- is here and identical for every game.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def _copy(v: Any) -> Any:
    return json.loads(json.dumps(v))


class GameCodex:
    NAME = "game"
    MIN_PLAYERS = 2
    MAX_PLAYERS = 2

    def __init__(self, transient, perm):
        self.t = transient
        self.p = perm

    def _now(self) -> str:
        return str(int(time.time() * 1000))

    def _key(self, gid: str) -> str:
        return f"game.{self.NAME}.{gid}"

    # ---- rule hooks: a game overrides these and nothing else ----------------
    def initial_setup(self, seats: List[str]) -> Any:
        raise NotImplementedError

    def legal_actions(self, state: Dict[str, Any], who: str) -> List[Dict[str, Any]]:
        return []

    def apply_action(self, state: Dict[str, Any], who: str, action: Dict[str, Any]) -> bool:
        raise NotImplementedError

    def view(self, state: Dict[str, Any], who: str) -> Dict[str, Any]:
        """Per-viewer projection. Default exposes everything. Games with hidden info
        override to redact (see hearts/liarsdice). Once the game is over, show all."""
        return state

    # ---- working-memory store pattern (host floats; perm is authority) ------
    @staticmethod
    def _ok_presence(pv: Any) -> bool:
        # PHP/api.php json_encode renders an empty dict {} as an empty list []. So a dict is
        # fine, and an EMPTY list is just an empty presence. Only a NON-empty list is malformed.
        return isinstance(pv, dict) or (isinstance(pv, list) and len(pv) == 0)

    def _well_formed(self, st: Any) -> bool:
        # a valid game tuple is a dict with the core keys, a seats LIST, and presence that is
        # either a dict or the empty-list the DB returns for an empty dict.
        return (isinstance(st, dict) and "seats" in st and "phase" in st
                and isinstance(st.get("seats"), list)
                and GameCodex._ok_presence(st.get("presence", {})))

    def _live(self, gid: str) -> Optional[Dict[str, Any]]:
        st = self.t.get(self._key(gid))
        if not self._well_formed(st):                   # malformed/foreign -> treat absent
            st = None
        if st is None:                                  # cold / relocated host
            st = self.p.load(gid)
            if not self._well_formed(st):
                st = None
            if st is not None:
                self.t.upsert(self._key(gid), st)
        if isinstance(st, dict) and not isinstance(st.get("presence"), dict):
            st["presence"] = {}                         # normalize DB's []-for-empty-{} back to {}
        return st

    def _commit(self, gid: str, st: Dict[str, Any]) -> None:
        st["version"] = int(st.get("version", 0)) + 1
        st["ts"] = self._now()
        self.p.save(gid, st)                            # authority first (resumable)
        self.t.upsert(self._key(gid), st)               # then the live cache

    # ---- lifecycle ----------------------------------------------------------
    def new_game(self, gid: str, seats: Optional[List[str]] = None) -> Dict[str, Any]:
        seats = list(seats or [])
        st: Dict[str, Any] = {
            "game": self.NAME, "id": gid, "version": 0, "ts": self._now(),
            "seats": seats,
            "min_players": self.MIN_PLAYERS, "max_players": self.MAX_PLAYERS,
            "turn": None, "phase": "lobby", "winner": None,
            "setup": None, "board": None,
            "history": [],                              # the moves -- replay + watch live here
            "presence": {},
        }
        if len(seats) >= self.MIN_PLAYERS:
            self._begin(st)
        self._commit(gid, st)
        return st

    def _begin(self, st: Dict[str, Any]) -> None:
        st["setup"] = self.initial_setup(st["seats"])
        st["board"] = _copy(st["setup"])
        ft = st["board"].get("first_turn") if isinstance(st["board"], dict) else None
        st["turn"] = ft if ft in st["seats"] else st["seats"][0]
        st["phase"] = "play"

    def join(self, gid: str, who: str, role: str = "player") -> Optional[Dict[str, Any]]:
        st = self._live(gid)
        if st is None:
            return None
        seated = who in st["seats"]
        if (role == "player" and not seated and st["phase"] == "lobby"
                and len(st["seats"]) < st["max_players"]):
            st["seats"].append(who)
            seated = True
            if len(st["seats"]) >= st["min_players"]:
                self._begin(st)                         # auto-start when enough are seated
        # presence: a seat is a player, everyone else is a watcher. Just a read marker.
        if not isinstance(st.get("presence"), dict):
            st["presence"] = {}
        st["presence"][who] = {
            "role": "player" if seated else "watcher", "ts": self._now()}
        self._commit(gid, st)
        return st

    def get(self, gid: str, who: str = "") -> Optional[Dict[str, Any]]:
        st = self._live(gid)
        if st is None:
            return None
        return self._project(st, who)                   # reading == watching (redacted)

    def _project(self, st: Dict[str, Any], who: str) -> Dict[str, Any]:
        """The viewer's read: redacted state + the legal actions this viewer may take
        right now (empty for a watcher or off-turn). The client renders buttons from it."""
        v = dict(self.view(st, who))
        v["legal"] = self.legal_actions(st, who) if who else []
        return v

    def act(self, gid: str, who: str, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self._live(gid)
        if st is None:
            return None
        if st["phase"] != "play" or st["winner"] is not None:
            return self._project(st, who)
        if who not in st["seats"]:                       # watchers cannot write
            return self._project(st, who)
        if st["turn"] is not None and who != st["turn"]:  # turn authority
            return self._project(st, who)
        if self.apply_action(st, who, action):
            # current-state-only: the board IS the record. No growing history is written --
            # the tuple stays bounded, so the codec ships a flat per-move delta, not a log
            # that re-ships whole. (Persisted history is deferred; see the db-change callback.)
            self._commit(gid, st)
        return self._project(st, who)

    def touch_presence(self, gid: str, who: str, role: str = "watcher") -> None:
        st = self._live(gid)
        if st is None:
            return
        seated = who in st["seats"]
        st.setdefault("presence", {})[who] = {
            "role": "player" if seated else "watcher", "ts": self._now()}
        self.t.upsert(self._key(gid), st)               # presence is live-cache only

    def who_is_here(self, gid: str, window_ms: int = 60000) -> List[Dict[str, str]]:
        st = self._live(gid)
        if st is None:
            return []
        now = int(self._now())
        out = []
        for who, rec in (st.get("presence") or {}).items():
            if now - int(rec.get("ts", 0)) <= window_ms:
                out.append({"who": who, "role": rec.get("role", "watcher")})
        return sorted(out, key=lambda r: r["who"])

    # ---- replay: fold the history that is already in the tuple --------------
    def fold(self, state: Dict[str, Any], upto: Optional[int] = None) -> Dict[str, Any]:
        """Reconstruct the board at ply `upto` (default: all) from setup + history.
        Pure function of the tuple -- a watcher who read once can call this with no
        further round-trips. This IS replay; there is nothing else to it."""
        seats = state.get("seats") or []
        setup = state.get("setup")
        ft = setup.get("first_turn") if isinstance(setup, dict) else None
        scratch = {
            "game": self.NAME, "id": state.get("id"), "seats": seats,
            "turn": ft if ft in seats else (seats[0] if seats else None),
            "phase": "play", "winner": None,
            "setup": setup, "board": _copy(setup),
            "history": [],
        }
        _std = {"game","id","seats","turn","phase","winner","setup","board",
                "history","version","ts","presence","min_players","max_players"}
        for k, v in state.items():            # carry game-specific top-level state
            if k not in _std and k not in scratch:
                scratch[k] = _copy(v)
        hist = state.get("history") or []
        if upto is None:
            upto = len(hist)
        for h in hist[:upto]:
            self.apply_action(scratch, h["who"], h["action"])
        return {"ply": upto, "board": scratch["board"], "turn": scratch["turn"],
                "winner": scratch["winner"], "phase": scratch["phase"]}

    def replay(self, gid: str) -> List[Dict[str, Any]]:
        """Every board state from the opening to now. Just folds the stored moves."""
        st = self._live(gid)
        if st is None:
            return []
        n = len(st.get("history") or [])
        return [self.fold(st, k) for k in range(n + 1)]
