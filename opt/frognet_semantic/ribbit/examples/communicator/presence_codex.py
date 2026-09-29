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
presence_codex.py - the load-bearing human primitive (audience: human).

Presence is the thinnest human-audience codex and the one the Communicator stands on:
the contact dots, who's-here, and - at the very floor - a ONE-BYTE BEACON whose only
job is to say "alive" or "need help". Sensors are for machines; A/V and text are for
humans; this beacon is the smallest human signal there is, and it converges the same
way everything else does.

Fields (presence.<node>):
  name    RESIDENT_ONCE  - rides the FULL once, never re-ships
  status  LATEST_ONLY    - online / away (only the newest matters)
  beacon  LATEST_ONLY    - 0 = OK/alive, 1 = NEED HELP  (the one byte)
  ts      LATEST_ONLY    - last update

Steady "alive" is SAME (nothing on the wire). A beacon flip is the minimal possible
DIFF: one field, smallest domain. That is the whole point - the floor signal costs
almost nothing and still converges.

Working memory (transient/perm) is the SOURCE/SINK; the substrate Channel is the WIRE.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from substrate import Codex, Channel, Freshness
from working_memory import WorkingMemory, TransientStore, PermStore

OK, NEED_HELP = 0, 1
_FIELDS = ["name", "status", "beacon", "ts"]
_FRESH = {
    "name": Freshness.RESIDENT_ONCE,
    "status": Freshness.LATEST_ONLY,
    "beacon": Freshness.LATEST_ONLY,
    "ts": Freshness.LATEST_ONLY,
}


class PresenceCodex(WorkingMemory):
    def __init__(self, transient: TransientStore, perm: PermStore,
                 node_id: str, name: str):
        super().__init__(transient, perm)
        self.node_id = node_id
        self.name = name
        self.codex = Codex(None, "GET", f"presence/{node_id}",
                           field_order=_FIELDS, freshness=_FRESH,
                           resident={"name": name})
        self._channels: Dict[str, Channel] = {}        # peer_id -> sender channel
        self._refs: Dict[str, Dict[str, Any]] = {}     # source_id -> receiver reference
        # seed local presence
        st = self._live(self._key()) or {
            "version": 0, "name": name, "status": "online",
            "beacon": OK, "ts": self.now_ms(),
        }
        self._commit(self._key(), st)

    def _key(self) -> str:
        return f"presence.{self.node_id}"

    # -- local updates (write working memory) --------------------------------
    def set_status(self, status: str) -> None:
        st = self._live(self._key()); st["status"] = status; self._commit(self._key(), st)

    def set_beacon(self, beacon: int) -> None:
        st = self._live(self._key()); st["beacon"] = int(beacon); self._commit(self._key(), st)

    def need_help(self) -> None: self.set_beacon(NEED_HELP)
    def all_clear(self) -> None: self.set_beacon(OK)

    def local(self) -> Dict[str, Any]:
        return self._live(self._key())

    # -- sender: converge my presence to a peer ------------------------------
    def emit_to(self, peer_id: str) -> List["tuple"]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        st = self._live(self._key())
        ch.offer("status", st["status"])
        ch.offer("beacon", st["beacon"])
        ch.offer("ts", st["ts"])
        return ch.flush()                              # [(frame, kind), ...]

    # -- receiver: reconstruct a source node's presence from a frame ---------
    def ingest(self, source_id: str, frame: bytes) -> Dict[str, Any]:
        ref = self._refs.get(source_id)
        values, new_ref, _kind = self.codex.decode(frame, ref)
        self._refs[source_id] = new_ref
        return values

    def view_of(self, source_id: str) -> Optional[Dict[str, Any]]:
        return self._refs.get(source_id)
