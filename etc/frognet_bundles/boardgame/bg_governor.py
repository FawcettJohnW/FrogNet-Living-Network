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
"""bg_governor.py -- the UnRESTful backgammon engine as a network-shared-memory GOVERNOR.

There is NO call/response and NO perm/transient duality. State is objects in the tuple
space under SensorType 'gameserver', addressed <gameserver>/<backgammon.<gid>>/<variable>:

  GAME object   .../state        the board: points,bar,off,turn,phase,dice,legal,cube,
                                 winner, seats, version, ts, and consumed{who:seq}.
  USER object   .../user.<who>   per participant, written by THAT participant only:
                                 {who, seat_claim, present, intent:{...}, intent_seq}.

A participant plays by writing THEIR User object -- claim a seat, or set intent (a Move
object: {kind:roll|move|double|accept|decline, frm?,die?}) and bump intent_seq. They are
changing memory, not sending a message. On any write into the module, Apache invokes this
governor (the .fgs handler). It is STATELESS between invocations and fully idempotent: it
historical-diffs each User's intent_seq against GAME.consumed[who] to see new intent, and
authority lives in GAME so re-running on the same space is a no-op.

Authority the governor keeps for itself (a participant cannot forge by writing memory):
  - DICE: generated here with the governor's RNG on a 'roll' intent. A user writing dice
    into their own object is ignored; only GAME.dice (written here) is real.
  - TURN GATE: only the seated player whose turn it is can move; others' intent is inert.
  - SEATING: a user CLAIMS a seat in their object; the governor validates (free, <=2) and
    records it in GAME.seats. The claim is a request-as-memory; the grant is authority.
"""
from __future__ import annotations
import time, random
from typing import Any, Dict, List, Optional
import bg_rules as R

MODULE = lambda gid: f"backgammon.{gid}"
WHITE, BLACK = R.WHITE, R.BLACK
def _now() -> str: return str(int(time.time() * 1000))

# ---- the tuple space contract (production: MySQL via frognet_tuples; here: any dict-like
#      keyed by the trio). The governor only needs get/put/keys over (TYPE,module,var). ----
class Space:
    typ = "gameserver"  # SensorType = the user-named service; set per instance
    def get(self, typ, module, var) -> Optional[dict]: raise NotImplementedError
    def put(self, typ, module, var, obj) -> None:       raise NotImplementedError
    def vars(self, typ, module) -> List[str]:           raise NotImplementedError

def _new_game(gid) -> dict:
    return {"id": gid, "version": 1, "ts": _now(),
            "points": R.start_position(), "bar": {WHITE:0,BLACK:0}, "off": {WHITE:0,BLACK:0},
            "turn": WHITE, "phase": "roll", "dice": [], "dice_remaining": [], "legal": [],
            "cube": {"value":1,"owner":None}, "winner": None,
            "seats": {}, "consumed": {}}

def _read_game(space, gid) -> dict:
    g = space.get(space.typ, MODULE(gid), "state")
    return g if g is not None else _new_game(gid)

def _seat_users(space, gid) -> List[dict]:
    out = []
    for v in space.vars(space.typ, MODULE(gid)):
        if v.startswith("user."):
            u = space.get(space.typ, MODULE(gid), v)
            if u: out.append(u)
    return sorted(out, key=lambda u: u.get("joined_ts", "0"))   # stable seating order

def governor_step(space: "Space", gid: str, rng: random.Random = random) -> dict:
    """React to current memory. Reconcile seat claims, then at most ONE new turn-action
    from the player on move. Returns the resulting GAME object (also written to memory)."""
    g = _read_game(space, gid)

    # 1) SEATING: grant claimed seats deterministically (claim order), authority in GAME.
    if len(g["seats"]) < 2:
        taken = set(g["seats"].values())
        for u in _seat_users(space, gid):
            who = u["who"]
            if who in g["seats"]: continue
            want = u.get("seat_claim")
            if want in (WHITE, BLACK) and want not in taken:
                g["seats"][who] = want; taken.add(want)
            elif want in (None, "any"):
                free = WHITE if WHITE not in taken else (BLACK if BLACK not in taken else None)
                if free: g["seats"][who] = free; taken.add(free)
            if len(g["seats"]) >= 2: break

    # need both seats to act on moves
    seat_of = {who: s for who, s in g["seats"].items()}
    color_player = {s: who for who, s in g["seats"].items()}

    if g["phase"] not in ("gameover",) and len(g["seats"]) == 2:
        mover_who = color_player.get(g["turn"])
        u = space.get(space.typ, MODULE(gid), f"user.{mover_who}") if mover_who else None
        if u and u.get("intent") and int(u.get("intent_seq", 0)) > int(g["consumed"].get(mover_who, 0)):
            intent = u["intent"]; kind = intent.get("kind")
            applied = False
            if kind == "roll" and g["phase"] == "roll":
                d1 = rng.randint(1,6); d2 = rng.randint(1,6)     # AUTHORITY: governor rolls
                applied = R.do_roll(g, d1, d2)
            elif kind == "move" and g["phase"] == "move":
                applied = R.apply_move(g, int(intent["frm"]), int(intent["die"]))
            elif kind == "double" and g["phase"] == "roll" and g["cube"]["owner"] in (None, g["turn"]):
                g["phase"] = "double-offered"; g["cube"]["offered_by"] = g["turn"]; applied = True
            elif kind == "accept" and g["phase"] == "double-offered":
                g["cube"]["value"] *= 2
                g["cube"]["owner"] = BLACK if g["cube"]["offered_by"] == WHITE else WHITE
                g["cube"].pop("offered_by", None); g["phase"] = "roll"; applied = True
            elif kind == "decline" and g["phase"] == "double-offered":
                g["phase"] = "gameover"; g["winner"] = g["cube"]["offered_by"]; applied = True
            # consume the intent regardless (idempotent): record the seq we acted on
            g["consumed"][mover_who] = int(u.get("intent_seq", 0))

    g["version"] += 1; g["ts"] = _now()
    space.put(space.typ, MODULE(gid), "state", g)
    return g
