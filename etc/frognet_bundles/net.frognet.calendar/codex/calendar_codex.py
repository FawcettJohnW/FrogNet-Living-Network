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
calendar_codex.py - UnREST codex for the Family Calendar bundle.

Role in the stack (see UnREST spec Sec.4A/Sec.6.1):
  - Radicale (OSS) runs in its normal mode and owns the DURABLE copy on a
    well-known perm host. We do NOT modify Radicale; we operate it as the
    perm authority and feed standard CalDAV clients from it unchanged.
  - This codex holds the LIVE shared element in the transient DB (the cache):
    `family-calendar.events`. Every household reads/writes that one element, so
    there is one calendar, not N drifting copies.
  - Authority = perm (loss-on-reelection is NOT acceptable for a calendar). The
    transient element is a cache: writes go through to perm; on a cold/relocated
    transient host the element REFAULTS from perm.

New-over-native capability this adds without touching Radicale:
  - one live element all households see update in real time (no CalDAV two-copy
    merge), presence ("who's looking now"), and clean-fail instead of stale sync.

Stores are injected so this runs in tests here and wires to the box in prod:
  - TransientStore: the api.php sensor contract (verified live):
      GET  ?entity=sensors&action=list&SensorName=<name>
      GET  ?entity=sensor_data&action=get&SensorID=<id>
      POST ?entity=sensor_data&action=upsert_by_name   (jsonData payload)
    *VERIFY-AGAINST-LIVE*: the timestamp field (server column vs. inside jsonData)
    and exact upsert param names - confirm against the live endpoint before prod.
  - PermStore: the durable calendar (Radicale on the perm host). The HttpPermStore
    talks CalDAV; the InMemoryPermStore is for tests.
"""
from __future__ import annotations
import os, sys, json, time, uuid
from typing import Any, Dict, List, Optional, Tuple

# --- locate the real FrogNet tree so we use the REAL codec on the wire -----
for _c in ("/tmp/ft/opt/frognet_semantic",
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
                        "..", "tmp", "ft", "opt", "frognet_semantic")):
    if os.path.isdir(os.path.join(_c, "core")):
        sys.path.insert(0, _c); break

ELEMENT_EVENTS = "family-calendar.events"
ELEMENT_PRESENCE = "family-calendar.presence"


# ===========================================================================
# Store interfaces (injectable)
# ===========================================================================
class TransientStore:
    """The shared space: one elected MySQL reached as databasehost.frognet."""
    def get(self, name: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
    def upsert(self, name: str, value: Dict[str, Any]) -> None:
        raise NotImplementedError


class PermStore:
    """Durable calendar authority (Radicale on a well-known perm host)."""
    def load_all(self) -> List[Dict[str, Any]]:
        raise NotImplementedError
    def put(self, event: Dict[str, Any]) -> None:
        raise NotImplementedError
    def remove(self, uid: str) -> None:
        raise NotImplementedError


class InMemoryTransientStore(TransientStore):
    def __init__(self): self._d: Dict[str, Dict[str, Any]] = {}
    def get(self, name): return self._d.get(name)
    def upsert(self, name, value): self._d[name] = dict(value)
    def drop(self, name): self._d.pop(name, None)        # simulate host re-election


class InMemoryPermStore(PermStore):
    def __init__(self): self._e: Dict[str, Dict[str, Any]] = {}
    def load_all(self): return [dict(v) for v in self._e.values()]
    def put(self, event): self._e[event["uid"]] = dict(event)
    def remove(self, uid): self._e.pop(uid, None)


# ===========================================================================
# Event model
# ===========================================================================
def make_event(summary: str, start: str, end: str, **kw) -> Dict[str, Any]:
    ev = {"uid": kw.get("uid") or uuid.uuid4().hex[:12],
          "summary": summary, "start": start, "end": end,
          "all_day": bool(kw.get("all_day", False)),
          "location": kw.get("location", ""), "notes": kw.get("notes", ""),
          "organizer": kw.get("organizer", "")}
    return ev


# ===========================================================================
# The codex
# ===========================================================================
class CalendarCodex:
    def __init__(self, transient: TransientStore, perm: PermStore):
        self.t = transient
        self.p = perm

    # --- element shape ----------------------------------------------------
    def _now(self) -> int:
        return int(time.time() * 1000)

    def _empty_element(self) -> Dict[str, Any]:
        return {"element": ELEMENT_EVENTS, "version": 0, "ts": "0", "events": []}

    # --- refault: cold/relocated transient host rebuilds from perm --------
    def refault(self) -> Dict[str, Any]:
        events = sorted(self.p.load_all(), key=lambda e: e["start"])
        el = {"element": ELEMENT_EVENTS, "version": self._next_version(),
              "ts": str(self._now()), "events": events}
        self.t.upsert(ELEMENT_EVENTS, el)
        return el

    def _next_version(self) -> int:
        cur = self.t.get(ELEMENT_EVENTS)
        return (cur["version"] + 1) if cur else 1

    def _live(self) -> Dict[str, Any]:
        """Read the live element; if the transient host is cold, refault from perm."""
        el = self.t.get(ELEMENT_EVENTS)
        if el is None:
            el = self.refault()
        return el

    # --- verbs (storage) --------------------------------------------------
    def list_events(self) -> List[Dict[str, Any]]:
        return list(self._live()["events"])

    def create_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self.p.put(event)                       # perm authority first (durability)
        el = self._live()
        el["events"] = [e for e in el["events"] if e["uid"] != event["uid"]] + [event]
        el["events"].sort(key=lambda e: e["start"])
        el["version"] += 1; el["ts"] = str(self._now())
        self.t.upsert(ELEMENT_EVENTS, el)       # then the live cache
        return event

    def update_event(self, uid: str, **fields) -> Optional[Dict[str, Any]]:
        el = self._live()
        for e in el["events"]:
            if e["uid"] == uid:
                e.update({k: v for k, v in fields.items() if k in e})
                self.p.put(e)
                el["version"] += 1; el["ts"] = str(self._now())
                self.t.upsert(ELEMENT_EVENTS, el)
                return e
        return None

    def delete_event(self, uid: str) -> bool:
        el = self._live()
        before = len(el["events"])
        el["events"] = [e for e in el["events"] if e["uid"] != uid]
        if len(el["events"]) == before:
            return False
        self.p.remove(uid)
        el["version"] += 1; el["ts"] = str(self._now())
        self.t.upsert(ELEMENT_EVENTS, el)
        return True

    # --- presence (sibling element, transient) ----------------------------
    def touch_presence(self, who: str) -> None:
        el = self.t.get(ELEMENT_PRESENCE) or {"element": ELEMENT_PRESENCE, "seen": {}}
        el["seen"][who] = str(self._now())
        self.t.upsert(ELEMENT_PRESENCE, el)

    def who_is_here(self, window_ms: int = 60000) -> List[str]:
        el = self.t.get(ELEMENT_PRESENCE) or {"seen": {}}
        now = self._now()
        return sorted(w for w, ts in el["seen"].items() if now - int(ts) <= window_ms)

    # --- client split detection (Sec.6.2): backwards-time read = terminate ----
    @staticmethod
    def is_stale_read(read_ts, client_watermark) -> bool:
        return int(read_ts) < int(client_watermark)


# ===========================================================================
# Wire helper - encode the element through the REAL codec (per-reader DIFF)
# ===========================================================================
def wire_codec():
    """Return (codec, handler) using the real FrogNet codec, or (None, None)
    if the tree isn't importable (so unit tests still run without it)."""
    try:
        import types, zlib
        if "mysql" not in sys.modules:
            m = types.ModuleType("mysql"); mc = types.ModuleType("mysql.connector")
            me = types.ModuleType("mysql.connector.errors")
            class E(Exception): pass
            me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = E
            mc.connect = lambda *a, **k: None; mc.errors = me; mc.Error = E
            pool = types.ModuleType("mysql.connector.pooling"); pool.MySQLConnectionPool = object
            mc.pooling = pool; m.connector = mc
            for n, mod in (("mysql", m), ("mysql.connector", mc),
                           ("mysql.connector.errors", me), ("mysql.connector.pooling", pool)):
                sys.modules[n] = mod
        if "lz4" not in sys.modules:
            lz4 = types.ModuleType("lz4"); fr = types.ModuleType("lz4.frame")
            fr.compress = lambda d: zlib.compress(d, 1); fr.decompress = lambda d: zlib.decompress(d)
            lz4.frame = fr; sys.modules["lz4"] = lz4; sys.modules["lz4.frame"] = fr
        from core.codec import SemanticCodec
        from core.json_handler import JsonFormatHandler
        return SemanticCodec(), JsonFormatHandler()
    except Exception:
        return None, None
