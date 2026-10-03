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
media_codex.py - the A/V codex. The load-bearing first codex: audio is the gate.

This is the Communicator's call. It is the same substrate every other codex uses
(Codex + Channel + per-field Freshness), declared to the SotF-ACP media shape, so an
A/V call converges exactly the way presence and chat do - FULL once, then DIFF, then
SAME when nothing changed.

Bound to the REAL contract read from source (sotf_media_tier.py `SotFMediaHandler`):
  SESSION envelope = (session_id, codec, sr, layout, level_idx)  -> the resident scaffold,
    extract_template once at session init; it rides the FULL and never re-crosses.
  FRAME fields     = (seq, audio, video)                          -> the hot per-frame delta,
    extract_request_dynamic per frame; rebuild_reply reconstitutes on the far side.

Freshness is the doctrine, declared here and enforced by the Channel:
  session_id/codec/sr/layout/level_idx  RESIDENT_ONCE  - envelope, crosses once
  seq                                   LATEST_ONLY    - frame counter
  audio                                 CONTINUOUS     - keep converged every tick; PROTECTED
  video                                 LATEST_ONLY    - droppable; only the newest frame matters
Audio continuous + video latest-only is "video sheds before audio": when the wire
coalesces a tick, the stale video frame is dropped and audio is kept. A repeated audio
payload (Opus DTX silence) encodes SAME - zero wire - exactly as the media tier proves.

FINDING-1 (read in sotf_media_tier.py): the shipped handler base64s the payload as a
TYPE_STR because the old codec UTF-8-mangled TYPE_RAW. This sim mirrors that faithfully
(base64 on the wire). The box rung swaps in the real codec, whose TYPE_RAW now carries
NATIVE BYTES (build 2026-06-10-typeraw-bytes) - so the box drops the base64 and the ~33%
tax with it. Same codex, the backing moves; the doctrine does not.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

from substrate import Codex, Channel, Freshness
from working_memory import WorkingMemory, TransientStore, PermStore

SESSION_FIELDS = ["session_id", "codec", "sr", "layout", "level_idx"]
FRAME_FIELDS = ["seq", "audio", "video"]
_FIELDS = SESSION_FIELDS + FRAME_FIELDS

_FRESHNESS = {
    "session_id": Freshness.RESIDENT_ONCE,
    "codec":      Freshness.RESIDENT_ONCE,
    "sr":         Freshness.RESIDENT_ONCE,
    "layout":     Freshness.RESIDENT_ONCE,
    "level_idx":  Freshness.RESIDENT_ONCE,
    "seq":        Freshness.LATEST_ONLY,
    "audio":      Freshness.CONTINUOUS,     # the gate - protected
    "video":      Freshness.LATEST_ONLY,    # droppable
}


def _b64(b: Optional[bytes]) -> Optional[str]:
    return base64.b64encode(bytes(b)).decode("ascii") if b is not None else None


def _unb64(s: Optional[str]) -> Optional[bytes]:
    return base64.b64decode(s.encode("ascii")) if s is not None else None


class MediaCodex(WorkingMemory):
    """An A/V session. One per call. The session envelope is the resident scaffold;
    each frame offers audio (continuous) and optional video (latest-only)."""

    def __init__(self, transient: TransientStore, perm: PermStore, session_id: str,
                 codec: str = "opus", sr: int = 48000, layout: str = "stereo",
                 level_idx: int = 4):
        super().__init__(transient, perm)
        self.session_id = session_id
        # the resident envelope (extract_template baseline) rides the FULL once
        resident = {"session_id": session_id, "codec": codec, "sr": int(sr),
                    "layout": layout, "level_idx": int(level_idx)}
        self.codex = Codex(None, "POST", f"sotf/{session_id}",
                           _FIELDS, _FRESHNESS, mode="sotf_media", resident=resident)
        self.codex.learn()
        self._channels: Dict[str, Channel] = {}     # peer_id -> sender channel
        # element seeds with envelope + empty frame slots
        st = dict(resident); st.update(seq=0, audio=None, video=None, ts=self.now_ms())
        self._commit(session_id, st)

    def _key(self) -> str:
        return self.session_id

    def frame(self, seq: int, audio: Optional[bytes], video: Optional[bytes] = None) -> None:
        """Offer one media frame. audio is the protected continuous field; video is
        droppable. Mirrors extract_request_dynamic: full field set, diff below it."""
        st = self._live(self._key())
        st["seq"] = int(seq)
        st["audio"] = _b64(audio)
        st["video"] = _b64(video)
        self._commit(self._key(), st)

    def emit_to(self, peer_id: str) -> List[Tuple[bytes, str]]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        st = self._live(self._key())
        ch.offer("seq", st["seq"])
        ch.offer("audio", st["audio"])
        ch.offer("video", st["video"])
        return ch.flush()                            # [(frame, kind), ...]

    # hostReset hooks. The SotF call is NOT stateless and NOT a perm replay: the
    # session ENVELOPE (RESIDENT_ONCE scaffold) re-asserts so the call survives the
    # float and stays discoverable on the new host, but the in-flight FRAME is shed -
    # audio/video are continuous/droppable, never replayed (replaying a frozen frame
    # would be backwards-in-time). The live stream resumes from the next captured
    # frame; channels re-FULL (inherited) so the envelope re-crosses. ffmpeg itself
    # holds no FrogNet state - the reconcile lives here, at the session.
    def _owned_keys(self):
        return [self._key()]

    def _consistency_check(self):
        st = self.p.load(self._key())
        if not st:
            return
        if st.get("seq") or st.get("audio") is not None or st.get("video") is not None:
            st.update(seq=0, audio=None, video=None)   # shed the droppable frame; keep envelope
            self.p.save(self._key(), st)


class MediaReplica:
    """Far side of a call: holds the reference, decodes frames back to (seq, audio
    bytes, video bytes). Mirror of rebuild_reply (base64 -> bytes)."""

    def __init__(self, codex: Codex):
        self.codex = codex
        self.reference: Optional[Dict[str, Any]] = None
        self.frames: List[Dict[str, Any]] = []

    def apply(self, frame: bytes, kind_hint: str = "") -> Dict[str, Any]:
        values, self.reference, kind = self.codex.decode(frame, self.reference)
        out = {"seq": values.get("seq"),
               "audio": _unb64(values.get("audio")),
               "video": _unb64(values.get("video")),
               "session_id": values.get("session_id"),
               "kind": kind}
        self.frames.append(out)
        return out
