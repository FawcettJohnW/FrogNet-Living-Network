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
"""game_tuple_client.py - run ANY games-common GameCodex LOCALLY against the FrogNet tuple
space. The board for a table is ONE shared tuple (SensorType=<game>, var="state",
scope=session:<gid>); presence is var="presence". Every seated player runs the same codex
against that tuple; the codex's turn rules serialize writes. No origin server, no wire verbs.
Memory, not messages - identical to bg_tuple_store, generalized to the GameCodex base."""
import os, sys, time

# find frognet_tuples (communicator bundle, sibling under bundles root, or env)
_HERE = os.path.dirname(os.path.abspath(__file__))
# frognet_tuples may live: (a) flat at the app root (portable client: parent of bundles/),
# (b) in a sibling communicator bundle (on-box layout), or (c) via env override.
_BR = os.environ.get("FROGNET_BUNDLES_ROOT") or os.path.join(_HERE, "..", "..")
for _cand in (os.path.dirname(os.path.abspath(_BR)),          # app root holding flat modules
              os.path.join(_BR, "..", "communicator"),
              os.path.join(_HERE, "..", "communicator"),
              os.path.join(_HERE, "..", "..", "communicator"),
              os.environ.get("FROGNET_COMMUNICATOR", ""),
              "/etc/frognet_bundles/communicator"):
    if _cand and os.path.isdir(_cand) and os.path.isfile(os.path.join(_cand, "frognet_tuples.py")):
        sys.path.insert(0, _cand); break
import frognet_tuples as T


class _Perm:
    """No durable authority needed for these games; the shared tuple is the board. (Backgammon
    uses a real perm; the card games are fine resuming from the live tuple.)"""
    def load(self, gid): return None
    def save(self, gid, st): return None


class _TupleTransient:
    """TransientStore the GameCodex expects: get(key)->value, upsert(key,value), keyed by the
    codex's 'game.<NAME>.<gid>' name. We map that to (SERVICE, var, session_scope(gid)) so
    every node resolves the SAME tuple for a table -> one shared board."""
    def __init__(self, service, dbhost):
        self.service = service; self.dbhost = dbhost
    def _addr(self, name):
        # name is "game.<NAME>.<gid>"; var is the leaf if it ends in a known suffix
        gid = name.split(".", 2)[2] if name.count(".") >= 2 else name
        return "state", gid.replace(".", "-")
    def get(self, name):
        var, gid = self._addr(name)
        scope = T.session_scope(gid)
        for r in T.get(self.service, var, dbhost=self.dbhost):
            if r["scope"] == scope:
                return r["value"]
        return None
    def upsert(self, name, value):
        var, gid = self._addr(name)
        T.put(self.service, var, T.session_scope(gid), value, dbhost=self.dbhost, own=False)


# registry: game name -> codex class. Imported lazily so a missing game doesn't break the hub.
def _load_codex(game, bundles_root):
    import importlib.util
    cand = os.path.join(bundles_root, f"net.frognet.{game}", "codex")
    common = None
    for c in (os.path.join(bundles_root, "games-common"),):
        if os.path.isdir(c): common = c
    if common and common not in sys.path: sys.path.insert(0, common)
    if os.path.isdir(cand) and cand not in sys.path: sys.path.insert(0, cand)
    fname = {"connectfour":"connectfour_codex","hearts":"hearts_codex",
             "liarsdice":"liarsdice_codex","reversi":"reversi_codex"}.get(game)
    cls = {"connectfour":"ConnectFourCodex","hearts":"HeartsCodex",
           "liarsdice":"LiarsDiceCodex","reversi":"ReversiCodex"}.get(game)
    if not fname: raise ValueError(f"unknown game {game}")
    mod = __import__(fname)
    return getattr(mod, cls)


class LocalGame:
    """The codex bound to the shared tuple for one table. Verbs: start/join/get/act/replay."""
    def __init__(self, game, table, who, dbhost, bundles_root):
        self.game, self.table, self.who = game, table, who
        Codex = _load_codex(game, bundles_root)
        self.codex = Codex(_TupleTransient(game, dbhost), _Perm())
    def exists(self):     return self.codex.get(self.table, self.who) is not None
    def start(self, seats=None): return self.codex.new_game(self.table, seats or [])
    def join(self, role="player"): return self.codex.join(self.table, self.who, role)
    def get(self):        return self.codex.get(self.table, self.who)
    def act(self, action): return self.codex.act(self.table, self.who, action)
    def replay(self):     return self.codex.replay(self.table)
    def here(self):
        self.codex.touch_presence(self.table, self.who,
                                  "player" if self._seated() else "watcher")
        return self.codex.who_is_here(self.table)
    def _seated(self):
        st = self.codex.get(self.table, self.who) or {}
        return self.who in (st.get("seats") or [])
