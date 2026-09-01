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
working_memory.py - the LOCAL source/sink (working memory), distinct from the wire.

Mirrors the backgammon_codex.py store contract:
  - TransientStore (get/upsert/drop): the floating live cache. Holds NO durable
    state of its own; ALWAYS there (election always yields a holder).
  - PermStore (load/save): the resumable AUTHORITY.
  - _commit(): perm FIRST (authority, resumable across host re-election), THEN
    transient (the live cache).
  - _live(): read transient; if cold/relocated (None), REFAULT from perm and
    upsert - this refault IS the floating-DB behavior in miniature.

This is the SOURCE/SINK a codex reads and writes. It is NOT the convergence medium;
the substrate Channel (substrate.py) is. On the box, InMemory* swaps for the api.php
transient + the perm store; the codex code above does not change.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional


class TransientStore:
    def get(self, name: str) -> Optional[Any]: raise NotImplementedError
    def upsert(self, name: str, value: Any) -> None: raise NotImplementedError
    def drop(self, name: str) -> None: raise NotImplementedError


class PermStore:
    def load(self, key: str) -> Optional[Any]: raise NotImplementedError
    def save(self, key: str, value: Any) -> None: raise NotImplementedError


def _copy(v: Any) -> Any:
    return json.loads(json.dumps(v))


class InMemoryTransient(TransientStore):
    def __init__(self): self._d: Dict[str, Any] = {}
    def get(self, n): return _copy(self._d[n]) if n in self._d else None
    def upsert(self, n, v): self._d[n] = _copy(v)
    def drop(self, n): self._d.pop(n, None)


class InMemoryPerm(PermStore):
    def __init__(self): self._d: Dict[str, Any] = {}
    def load(self, k): return _copy(self._d[k]) if k in self._d else None
    def save(self, k, v): self._d[k] = _copy(v)


class WorkingMemory:
    """Shared _live/_commit base for codices that hold resumable element state."""

    def __init__(self, transient: TransientStore, perm: PermStore):
        self.t = transient
        self.p = perm

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    def _live(self, key: str) -> Optional[Any]:
        st = self.t.get(key)
        if st is None:                       # cold / relocated host -> refault from perm
            st = self.p.load(key)
            if st is not None:
                self.t.upsert(key, st)       # the float in miniature
        return st

    def _commit(self, key: str, st: Dict[str, Any]) -> None:
        st["version"] = int(st.get("version", 0)) + 1
        st["ts"] = self.now_ms()
        self.p.save(key, st)                 # authority first (resumable)
        self.t.upsert(key, st)               # then the live cache

    @staticmethod
    def is_stale_read(read_ts: int, watermark: int) -> bool:
        return int(read_ts) < int(watermark)   # backwards-in-time self-detect

    # ---- hostReset: the one reconcile path (memory, not messages) -----------
    # Trigger-agnostic by doctrine: a network join whose merge completed, a
    # databasehost float, anything - same path ("see 1; case 2 == case 1"). What was
    # may not be; what is may be yet to be discovered. So: reset the world, ensure the
    # consistency of MY world FIRST, THEN write my network memory. This is the eager,
    # explicit form of what _live() does lazily - refault perm -> the (new, cold)
    # transient. Reading the shared tuple vector + the Communicator UI culmination are
    # the dispatcher's job (frognet_host_reset.host_reset_all), once, over everyone's
    # freshly re-asserted memory.
    def hostReset(self) -> Dict[str, Any]:
        rep = {"module": type(self).__name__, "reset": [], "consistency": "ok",
               "written": [], "channels_refulled": 0}
        keys = list(self._owned_keys())
        # 1. reset world - the transient cache belonged to the OLD host; forget it.
        for k in keys:
            self.t.drop(k); rep["reset"].append(k)
        # 2. consistency - perm is the resumable authority; make my world coherent
        #    BEFORE publishing it. A subclass repairs in place; never crash the reconcile.
        try:
            self._consistency_check()
        except Exception as e:                       # pragma: no cover - defensive
            rep["consistency"] = f"repaired:{e}"
        # 3. write memory - re-assert perm -> the (new) transient for every owned key.
        #    upsert, not _commit: same authoritative state, so no version bump.
        for k in keys:
            st = self.p.load(k)
            if st is None:
                continue
            self.t.upsert(k, st)
            rep["written"].append(k)
        # convergence reset: any per-peer reference is stale (the peer set moved), so
        # drop it - the next emit re-establishes the structure from FULL.
        for ch in getattr(self, "_channels", {}).values():
            if hasattr(ch, "hostReset"):
                ch.hostReset(); rep["channels_refulled"] += 1
        return rep

    def _owned_keys(self):
        """Element keys this module is the perm AUTHORITY for. Subclass declares."""
        return []

    def _consistency_check(self) -> None:
        """Assert/repair this module's invariants on perm before re-assertion.
        Default: none. Subclass overrides (e.g. dedup, legal-state)."""
        return
