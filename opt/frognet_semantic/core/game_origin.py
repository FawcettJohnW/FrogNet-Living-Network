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
game_origin.py - the UnREST game ORIGIN for the FrogNet proxy.

This is NOT a codec and NOT a forwarder. It is the authority. A `_game` request
arrives, and this handler reaches into the proxy's working memory, runs the game's
rules in place, writes the new board back, and answers. Nothing is forwarded
upstream; the read IS the only event; the handler controls everything that happens.

It is turn-based and slow by nature, so there is no stream, no delivery ladder, no
convergence machinery - those belong to the hot media path, not here. A move lands,
the board changes, and it sits until the next move. (This is the case the UnREST
docs under-serve: the value is in shared memory, you read it when your turn arrives,
and convergence is irrelevant because you are not reading in a loop.)

Memory model (from working_memory.py / the codex, unchanged):
  - perm  = the resumable AUTHORITY (the board's real home)
  - transient = the floating live cache (where the current host keeps it hot)
  - _commit writes perm first, then transient; _live refaults perm -> transient when
    the host is cold or just floated. So if the node holding a table dies, the next
    node re-reads perm and reconstructs the identical board - the table was never in
    the host. That is location, not authority (UnREST Failure Mode 3, in code).

On the box, the InMemory stores swap for the api.php transient + the perm store; the
codex and this origin do not change.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

# working_memory lives in the communicator bundle; the codices live in their own
# bundles. Resolve both off the bundles root, with the box default as a fallback.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _root in (os.path.join(_HERE, "..", "..", "..", "etc", "frognet_bundles"),
              os.environ.get("FROGNET_BUNDLES", ""),
              "/etc/frognet_bundles"):
    if _root and os.path.isdir(_root):
        sys.path.insert(0, os.path.join(_root, "communicator"))
        _BUNDLES = _root
        break
else:
    _BUNDLES = "/etc/frognet_bundles"

try:
    from working_memory import InMemoryTransient, InMemoryPerm   # communicator bundle, if present
except Exception:                                                # self-contained fallback
    import json as _json

    def _wm_copy(v):
        return _json.loads(_json.dumps(v))

    class InMemoryTransient:
        def __init__(self): self._d = {}
        def get(self, n): return _wm_copy(self._d[n]) if n in self._d else None
        def upsert(self, n, v): self._d[n] = _wm_copy(v)
        def drop(self, n): self._d.pop(n, None)

    class InMemoryPerm:
        def __init__(self): self._d = {}
        def load(self, k): return _wm_copy(self._d[k]) if k in self._d else None
        def save(self, k, v): self._d[k] = _wm_copy(v)


def _load_backgammon():
    sys.path.insert(0, os.path.join(_BUNDLES, "net.frognet.backgammon", "codex"))
    from backgammon_codex import BackgammonCodex
    return BackgammonCodex


def _bundle_loader(bundle: str, module: str, cls: str):
    """Resolve a GameCodex from its bundle, with the shared base on the path."""
    def load():
        sys.path.insert(0, os.path.join(_BUNDLES, "games-common"))
        sys.path.insert(0, os.path.join(_BUNDLES, bundle, "codex"))
        mod = __import__(module)
        return getattr(mod, cls)
    return load


# game name -> a zero-arg loader returning a codex class taking (transient, perm).
# This is the seam for "load the rules from the store": register or resolve by name.
GAME_CODICES: Dict[str, Any] = {
    "backgammon":  _load_backgammon,
    "connectfour": _bundle_loader("net.frognet.connectfour", "connectfour_codex", "ConnectFourCodex"),
    "reversi":     _bundle_loader("net.frognet.reversi",     "reversi_codex",     "ReversiCodex"),
    "hearts":      _bundle_loader("net.frognet.hearts",      "hearts_codex",      "HeartsCodex"),
    "liarsdice":   _bundle_loader("net.frognet.liarsdice",   "liarsdice_codex",   "LiarsDiceCodex"),
}


class GameOrigin:
    """Holds every table for every game in ONE working memory and serves `_game`
    requests against it. One instance per proxy node; the board lives here, hot, and
    in perm, resumable."""

    def __init__(self, transient=None, perm=None):
        # Shared across all games/tables on this node: one floating cache, one perm.
        self.t = transient if transient is not None else InMemoryTransient()
        self.p = perm if perm is not None else InMemoryPerm()
        self._codices: Dict[str, Any] = {}

    def _codex(self, game: str):
        if game not in self._codices:
            loader = GAME_CODICES.get(game)
            if loader is None:
                return None
            self._codices[game] = loader()(self.t, self.p)   # codex over shared memory
        return self._codices[game]

    # -- the one entry point: a request lands, memory changes, we answer -------
    def serve(self, body: Any) -> Tuple[int, str]:
        env = self._parse(body)
        game = env.get("game", "backgammon")
        table = env.get("table") or "table-1"
        op = env.get("op", "get")
        who = env.get("who", "")
        args = env.get("args") or {}

        codex = self._codex(game)
        if codex is None:
            return 404, self._err(f"no such game '{game}'", game, table, op)

        # Auto-open a table on first touch so joining is just reading it.
        if op != "new_game" and codex.get(table) is None:
            codex.new_game(gid=table)

        try:
            state = self._dispatch(codex, op, table, who, args)
        except Exception as e:                       # never crash the proxy on bad input
            return 400, self._err(f"{op} failed: {e}", game, table, op)

        out = {"_game": 1, "game": game, "table": table, "op": op, "state": state}
        if hasattr(codex, "who_is_here"):
            try:
                out["here"] = codex.who_is_here(table)
            except Exception:
                pass
        return 200, json.dumps(out, separators=(",", ":"))

    def _dispatch(self, codex, op: str, table: str, who: str, args: Dict[str, Any]):
        # Generic multiplayer GameCodex ops (connectfour/reversi/hearts/liarsdice/...).
        # A GameCodex exposes act(); reads are viewer-aware (hidden info redacted).
        if hasattr(codex, "act"):
            if op == "get":
                return self._get(codex, table, who)
            if op == "join":
                return codex.join(table, who, args.get("role", "player"))
            if op == "act":
                action = args.get("action") or {k: v for k, v in args.items() if k != "role"}
                return codex.act(table, who, action)
            if op == "replay":
                return codex.replay(table)
            if op == "presence":
                codex.touch_presence(table, who, args.get("role", "watcher"))
                return self._get(codex, table, who)
            if op == "new_game":
                return codex.new_game(table, args.get("seats"))
            return self._get(codex, table, who)

        # Backgammon-specific ops.
        if op == "get":
            return codex.get(table)
        if op == "new_game":
            return codex.new_game(gid=table,
                                  white=args.get("white", "white"),
                                  black=args.get("black", "black"))
        if op == "roll":
            return codex.roll(table, args.get("d1"), args.get("d2"))
        if op == "move":
            return codex.move(table, args["from"], args["die"])
        if op == "offer_double":
            return codex.offer_double(table)
        if op == "accept_double":
            return codex.accept_double(table)
        if op == "decline_double":
            return codex.decline_double(table)
        if op == "presence":
            if who and hasattr(codex, "touch_presence"):
                codex.touch_presence(table, who)
            return codex.get(table)
        return codex.get(table)

    @staticmethod
    def _get(codex, table, who):
        try:
            return codex.get(table, who)
        except TypeError:
            return codex.get(table)

    @staticmethod
    def _parse(body: Any) -> Dict[str, Any]:
        if isinstance(body, dict):
            return body
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body).decode("utf-8", "replace")
        if not body:
            return {}
        try:
            d = json.loads(body)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _err(msg: str, game: str, table: str, op: str) -> str:
        return json.dumps({"_game": 1, "game": game, "table": table, "op": op,
                           "error": msg}, separators=(",", ":"))


def looks_like_game(text: str) -> bool:
    """The proxy routes a body here when it is JSON carrying `"_game": 1`."""
    if not text:
        return False
    t = text.lstrip()
    if not t.startswith("{") or '"_game"' not in t[:200]:
        return False
    try:
        d = json.loads(t)
    except Exception:
        return False
    return isinstance(d, dict) and d.get("_game") == 1
