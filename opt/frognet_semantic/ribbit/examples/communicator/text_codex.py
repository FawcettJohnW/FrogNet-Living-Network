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
text_codex.py - chat (audience: human). Messages are LOSSLESS_EVENTUAL: each one must
land, may be late, and is NEVER dropped - even when, in the same tick, latest-only
fields coalesce. This is the opposite trade from presence/audio: text tolerates
latency, not loss.

Field (chat.<room>):
  msg      LOSSLESS_EVENTUAL  - each queued message crosses as its own frame
  typing   LATEST_ONLY        - only the newest "is typing" matters (coalesces)

The Channel handles the contract: lossless queue depth sets the number of frames per
tick; the latest-only typing flag rides the first frame and converges to SAME after.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from substrate import Codex, Channel, Freshness
from working_memory import WorkingMemory, TransientStore, PermStore

_FIELDS = ["msg", "typing"]
_FRESH = {"msg": Freshness.LOSSLESS_EVENTUAL, "typing": Freshness.LATEST_ONLY}


class TextCodex(WorkingMemory):
    def __init__(self, transient: TransientStore, perm: PermStore, room: str):
        super().__init__(transient, perm)
        self.room = room
        self.codex = Codex(None, "POST", f"chat/{room}",
                           field_order=_FIELDS, freshness=_FRESH)
        self._channels: Dict[str, Channel] = {}
        self._refs: Dict[str, Dict[str, Any]] = {}
        self._log_key = f"chat.{room}.log"
        if self._live(self._log_key) is None:
            self._commit(self._log_key, {"version": 0, "msgs": []})

    # -- local: append to the resumable room log -----------------------------
    def post(self, who: str, text: str) -> Dict[str, Any]:
        log = self._live(self._log_key)
        entry = {"who": who, "text": text, "ts": self.now_ms()}
        log["msgs"].append(entry)
        self._commit(self._log_key, log)
        # stage on every peer channel (each message must reach each peer)
        for ch in self._channels.values():
            ch.offer("msg", entry)
        return entry

    def set_typing(self, who: Optional[str]) -> None:
        for ch in self._channels.values():
            ch.offer("typing", who)

    def channel(self, peer_id: str) -> Channel:
        return self._channels.setdefault(peer_id, Channel(self.codex))

    def emit_to(self, peer_id: str) -> List["tuple"]:
        return self.channel(peer_id).flush()

    # -- receiver: each frame restores one message (or a typing flag) --------
    def ingest(self, source_id: str, frame: bytes) -> Dict[str, Any]:
        ref = self._refs.get(source_id)
        values, new_ref, _kind = self.codex.decode(frame, ref)
        self._refs[source_id] = new_ref
        return values

    def log(self) -> List[Dict[str, Any]]:
        return self._live(self._log_key)["msgs"]
