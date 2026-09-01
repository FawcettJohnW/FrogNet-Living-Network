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
bg_tuple_store.py -- the UnREST backend for Family Backgammon.

The codex was written to take an injected store: its own docstring says
"Stores injected (api.php transient + perm on the box)." LocalClient injects an
in-memory store and plays hot-seat; HttpClient skips the codex entirely and POSTs
verbs (move/roll/get) to a remote one -- request/response, i.e. REST.

This injects the thing the codex was waiting for: a TransientStore backed by the
FrogNet tuple space (frognet_tuples over the transient DB). The board for a table
lives as ONE shared tuple. Every seated player runs the same codex against that
tuple -- reads it to see the position, writes it after a move. The codex's own turn
rules decide who may write, so the shared tuple is serialized by turns, last write
wins. No server process, no verbs over the wire: the shared remembered space IS the
table. Memory, not messages.
"""
import os
import sys

# frognet_tuples lives in the communicator bundle, a sibling under the bundles root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BR = os.environ.get("FROGNET_BUNDLES_ROOT") or os.path.join(_HERE, "..", "..")
for _cand in (os.path.dirname(os.path.abspath(_BR)),          # flat app root (portable client)
              os.path.join(_HERE, "..", "..", "communicator"),
              os.environ.get("FROGNET_COMMUNICATOR", ""),
              "/etc/frognet_bundles/communicator"):
    if _cand and os.path.isdir(_cand) and os.path.isfile(os.path.join(_cand, "frognet_tuples.py")):
        sys.path.insert(0, _cand)
        break
import frognet_tuples as T                       # the UnREST tuple substrate

# the codex sits one level up under codex/
sys.path.insert(0, os.path.join(_HERE, "..", "codex"))
from backgammon_codex import (BackgammonCodex, InMemoryPerm,   # noqa: E402
                              TransientStore, ELEMENT)

SERVICE = "backgammon"          # SensorType bucket for every board + presence tuple
_PRESENCE = ".presence"


def _safe_gid(gid: str) -> str:
    # The substrate parses a SensorName as var.scope on the FIRST dot, so the scope
    # (session:<gid>) must stay dot-free or the read splits in the wrong place.
    return str(gid).replace(".", "-")


class TupleTransient(TransientStore):
    """Codex TransientStore backed by the FrogNet tuple space.

    The codex addresses state by a dotted name: '<ELEMENT>.<gid>' for the board and
    '<ELEMENT>.<gid>.presence' for seating. We map each to a dot-free (var, scope) so
    (a) the substrate's var.scope parse stays intact and (b) EVERY player resolves the
    identical SensorName for a table -> a single shared tuple, not one per node.
    """
    def __init__(self, dbhost: str):
        self.dbhost = dbhost

    def _addr(self, name):
        rest = name[len(ELEMENT) + 1:]            # "<gid>" or "<gid>.presence"
        if rest.endswith(_PRESENCE):
            return "presence", _safe_gid(rest[:-len(_PRESENCE)])
        return "state", _safe_gid(rest)

    def get(self, name):
        var, gid = self._addr(name)
        scope = T.session_scope(gid)
        # fresh_s=0: the board does not age out between turns; a stale board would be
        # worse than an old one. Presence DOES age, but who_is_here() windows it itself.
        for r in T.get(SERVICE, var, dbhost=self.dbhost):
            if r["scope"] == scope:
                return r["value"]
        return None

    def upsert(self, name, value):
        var, gid = self._addr(name)
        # own=False is load-bearing: the board must OUTLIVE the writer's process.
        # own=True arms atexit cleanup, which would delete the board the instant the
        # player who made the last move closed their app -- wiping the table for everyone.
        T.put(SERVICE, var, T.session_scope(gid), value,
              dbhost=self.dbhost, own=False)

    def drop(self, name):
        # Tuples self-expire by ts; there is nothing to tear down explicitly.
        return None


class TupleClient:
    """Same verbs as LocalClient / HttpClient, but the codex runs locally against the
    SHARED tuple-backed store. Two or more players on the same (dbhost, table) converge
    on one board by reading the space -- nobody messages a server."""
    def __init__(self, dbhost, table="table-1", who="desktop"):
        self.dbhost = dbhost
        self.c = BackgammonCodex(TupleTransient(dbhost), InMemoryPerm())
        self.g = table
        self.who = who
        if self.c.get(self.g) is None:            # first one in opens the table
            self.c.new_game(gid=self.g)

    def get(self):            return self.c.get(self.g)
    def new_game(self):       return self.c.new_game(gid=self.g)
    def roll(self):           return self.c.roll(self.g)
    def move(self, frm, die): return self.c.move(self.g, frm, die)
    def offer_double(self):   return self.c.offer_double(self.g)
    def accept_double(self):  return self.c.accept_double(self.g)
    def decline_double(self): return self.c.decline_double(self.g)

    def presence(self, who):
        self.c.touch_presence(self.g, who)
        return self.c.who_is_here(self.g)
