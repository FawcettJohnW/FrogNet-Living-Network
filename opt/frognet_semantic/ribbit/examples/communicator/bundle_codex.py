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
bundle_codex.py - the family bundles, converging on the one substrate.

The launcher already discovers calendar and backgammon by bundle.json beacon. This is
what runs when you open one: each bundle's element converges through the SAME Codex +
Channel that presence, chat, and the A/V call use. One primitive, surfaced N ways.

Each adapter is bound to the bundle's REAL element shape and to the freshness declared
in its bundle.json - not invented:

  Calendar  (calendar_codex.py element `family-calendar.events` = {version, ts, events}):
      bundle.json freshness: event = lossless-eventual, scaffold = resident-once.
      -> name RESIDENT_ONCE, version LATEST_ONLY, event LOSSLESS_EVENTUAL.
      Every event lands, in order (lossless); the element name rides the FULL once.

  Backgammon (backgammon_codex.py element `game.backgammon.<id>`):
      bundle.json freshness: move = lossless-eventual, position = latest-only,
      scaffold = resident-once.
      -> gid RESIDENT_ONCE, position LATEST_ONLY, move LOSSLESS_EVENTUAL.
      The hot wire unit is a single bounded MOVE-TIP append (per UnREST doctrine: the
      codex conveys a structure, it is NOT a differ - never ship the growing move list).
      Position is latest-only (stale board coalesces); the move log is lossless.

Box rung: the in-memory element here is replaced by the bundle's real codex over the
real SemanticCodec - CalendarCodex against Radicale (CalDAV perm), BackgammonCodex with
its move engine. The freshness declaration and the Channel logic do not change.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from substrate import Codex, Channel, Freshness
from working_memory import WorkingMemory, TransientStore, PermStore


# ===========================================================================
# Calendar
# ===========================================================================
CAL_ELEMENT = "family-calendar.events"
_CAL_FIELDS = ["name", "version", "event"]
_CAL_FRESHNESS = {
    "name":    Freshness.RESIDENT_ONCE,
    "version": Freshness.LATEST_ONLY,
    "event":   Freshness.LOSSLESS_EVENTUAL,     # every event must land, may be late
}


class CalendarCodex(WorkingMemory):
    """The shared family calendar element. add_event commits perm-first then transient,
    and offers the event (lossless) + bumped version (latest)."""

    def __init__(self, transient: TransientStore, perm: PermStore):
        super().__init__(transient, perm)
        self.codex = Codex(None, "POST", "calendar/events",
                           _CAL_FIELDS, _CAL_FRESHNESS, mode="json",
                           resident={"name": CAL_ELEMENT})
        self.codex.learn()
        self._channels: Dict[str, Channel] = {}
        self._commit(CAL_ELEMENT, {"name": CAL_ELEMENT, "version": 0,
                                   "events": [], "ts": self.now_ms()})

    def add_event(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        st = self._live(CAL_ELEMENT)
        st["events"] = [e for e in st["events"] if e.get("uid") != ev.get("uid")] + [ev]
        st["version"] = int(st["version"]) + 1
        self._commit(CAL_ELEMENT, st)
        for ch in self._channels.values():
            ch.offer("event", ev)                    # lossless: queued, never dropped
            ch.offer("version", st["version"])
        return ev

    def emit_to(self, peer_id: str) -> List[Tuple[bytes, str]]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        return ch.flush()

    def events(self) -> List[Dict[str, Any]]:
        return list(self._live(CAL_ELEMENT)["events"])

    # hostReset hooks: I am the perm authority for the calendar element.
    def _owned_keys(self):
        return [CAL_ELEMENT]

    def _consistency_check(self):
        """One event per uid (add_event's invariant). On reconcile after a network
        join, two islands' perms may both hold the uid - keep the latest by ts, so the
        re-asserted memory is coherent before it is published."""
        st = self.p.load(CAL_ELEMENT)
        if not st:
            return
        by_uid = {}
        for e in st.get("events", []):
            u = e.get("uid")
            if u not in by_uid or int(e.get("ts", 0)) >= int(by_uid[u].get("ts", 0)):
                by_uid[u] = e
        deduped = list(by_uid.values())
        if len(deduped) != len(st.get("events", [])):
            st["events"] = deduped
            self.p.save(CAL_ELEMENT, st)


# ===========================================================================
# Backgammon
# ===========================================================================
class BackgammonCodex(WorkingMemory):
    """One table. play_move commits the new position and offers a bounded move-tip
    (lossless) + the position (latest-only). The growing move history stays resident
    memory, never the wire."""

    def __init__(self, transient: TransientStore, perm: PermStore, gid: str,
                 white: str = "white", black: str = "black"):
        super().__init__(transient, perm)
        self.gid = gid
        self.element = f"game.backgammon.{gid}"
        fields = ["gid", "position", "move"]
        freshness = {
            "gid":      Freshness.RESIDENT_ONCE,
            "position": Freshness.LATEST_ONLY,        # stale board coalesces
            "move":     Freshness.LOSSLESS_EVENTUAL,  # every move lands, in order
        }
        self.codex = Codex(None, "POST", f"backgammon/{gid}",
                           fields, freshness, mode="json", resident={"gid": gid})
        self.codex.learn()
        self._channels: Dict[str, Channel] = {}
        self._commit(self.element, {"gid": gid, "white": white, "black": black,
                                    "position": None, "moves": [], "ts": self.now_ms()})

    def play_move(self, move: Dict[str, Any], position: Any) -> None:
        st = self._live(self.element)
        st["moves"].append(move)                     # history is resident memory...
        st["position"] = position
        self._commit(self.element, st)
        for ch in self._channels.values():
            ch.offer("move", move)                   # ...the wire carries the bounded tip
            ch.offer("position", position)

    def emit_to(self, peer_id: str) -> List[Tuple[bytes, str]]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        return ch.flush()

    def moves(self) -> List[Dict[str, Any]]:
        return list(self._live(self.element)["moves"])

    # hostReset hooks: I am the perm authority for this table's element.
    def _owned_keys(self):
        return [self.element]

    def _consistency_check(self):
        """The position must reflect the last move that landed (latest-only position
        can coalesce stale, but the lossless move log is authoritative). If a position
        was published with no move behind it, fall back to no-position so peers
        re-derive from the move log rather than trust an orphan board."""
        st = self.p.load(self.element)
        if not st:
            return
        if st.get("position") is not None and not st.get("moves"):
            st["position"] = None
            self.p.save(self.element, st)


# ===========================================================================
# A generic replica that accumulates lossless fields and converges latest fields,
# so a peer reconstructs the element from FULL + DIFF + SAME.
# ===========================================================================
class ElementReplica:
    def __init__(self, codex: Codex, lossless_fields: Tuple[str, ...]):
        self.codex = codex
        self.lossless = set(lossless_fields)
        self.reference: Optional[Dict[str, Any]] = None
        self.received: Dict[str, List[Any]] = {f: [] for f in lossless_fields}
        self.latest: Dict[str, Any] = {}

    def apply(self, frame: bytes) -> str:
        values, self.reference, kind = self.codex.decode(frame, self.reference)
        for f, v in values.items():
            if f in self.lossless:
                if kind in ("full", "diff") and v is not None:
                    self.received[f].append(v)
            else:
                self.latest[f] = v
        return kind
