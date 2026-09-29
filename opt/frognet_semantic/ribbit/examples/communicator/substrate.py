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
substrate.py -- Communicator convergence backing, RUNG 0 (sim).

This mirrors the interface of opt/frognet_semantic/.../unrest_substrate.py EXACTLY
(Freshness / Codex / Channel), so the Communicator code that sits on top of it does
NOT change between rungs -- only this backing moves stand-in -> real. On the box,
delete this file and `from unrest_substrate import Freshness, Codex, Channel`; the
Communicator is untouched.

What is real here vs stand-in:
  - REAL (verbatim from unrest_substrate.py): the Freshness classes, and Channel's
    offer()/flush() freshness logic (coalesce latest/continuous; never-drop lossless;
    resident crosses only via FULL). This is pure logic with no core.* dependency.
  - STAND-IN: Codex.encode()/decode() reproduce FULL/DIFF/SAME *convergence semantics*
    with a json-delta instead of the real SemanticCodec + FNW1 byte framing. Same
    (frame, new_reference, kind) contract; the bytes are not BLDC-1. The real codec
    (core.codec / core.semcache_wire) drops in at rung 1.

Convergence (doctrine: exchange MEMORY, not messages):
  - reference is the per-peer held copy. FULL bootstraps it; DIFF carries only the
    changed fields; SAME carries nothing ("your copy is already correct").
  - the far side RESTORES full values from its held reference + the delta.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# -- Freshness: the per-field "how often must this converge" classes ----------
class Freshness(Enum):
    CONTINUOUS = "continuous"                # keep converged every tick (audio)
    LATEST_ONLY = "latest_only"              # only newest matters (video, status)
    LOSSLESS_EVENTUAL = "lossless_eventual"  # must land, may be late (chat, event)
    RESIDENT_ONCE = "resident_once"          # learned scaffold, ~never changes


# -- wire frame (stand-in) ----------------------------------------------------
_MAGIC = b"FC0"
_OP = {"full": b"F", "diff": b"D", "same": b"S"}


def _wrap(kind: str, payload: Optional[dict]) -> bytes:
    body = b"" if payload is None else json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _MAGIC + _OP[kind] + body


class Codex:
    """Per-protocol codex: declares (method, semantic_path) identity, the learned
    field_order, each field's freshness, and (optionally) resident field values that
    ride the FULL once. encode()/decode() are the complete round trip.

    Constructor signature matches unrest_substrate.Codex so construction is identical
    across rungs; `store` is accepted and ignored by this stand-in (the real Codex
    uses it for the byte template). resident={} carries RESIDENT_ONCE values."""

    def __init__(self, store: Any, method: str, semantic_path: str,
                 field_order: List[str], freshness: Dict[str, "Freshness"],
                 mode: str = "json", raw_fields: Optional[set] = None,
                 resident: Optional[Dict[str, Any]] = None):
        self.store = store
        self.method = (method or "GET").upper()
        self.semantic_path = semantic_path
        self.field_order = list(field_order)
        self.freshness = dict(freshness)
        self.mode = mode
        self.raw_fields = set(raw_fields or ())
        self.resident = dict(resident or {})
        # stable opcode (documents identity; real store derives the same deterministically)
        key = f"{self.method} {self.semantic_path}"
        self.opcode = (abs(hash(key)) % (2 ** 31))

    def learn(self):
        """Make the structure resident. No-op for the stand-in (field_order is known
        to both ends by codex declaration); the real codex upserts a template row."""
        return

    # -- bits ONTO the wire ---------------------------------------------------
    def encode(self, values: Dict[str, Any], reference: Optional[Dict[str, Any]]):
        """Returns (frame, new_reference, kind in full/diff/same)."""
        cur = {f: values.get(f) for f in self.field_order}
        if reference is None:
            return _wrap("full", cur), dict(cur), "full"
        changed = {f: cur[f] for f in self.field_order if cur[f] != reference.get(f)}
        if not changed:
            return _wrap("same", None), dict(reference), "same"
        return _wrap("diff", changed), dict(cur), "diff"

    # -- bits OFF the wire (converge the held reference) ----------------------
    def decode(self, frame: bytes, reference: Optional[Dict[str, Any]]):
        """Returns (values, new_reference, kind). SAME leaves the reference as-is."""
        if len(frame) < 4 or frame[:3] != _MAGIC:
            raise ValueError("not a substrate frame")
        op = frame[3:4]
        body = frame[4:]
        if op == _OP["same"]:
            ref = dict(reference or {})
            return dict(ref), ref, "same"
        payload = json.loads(body.decode("utf-8")) if body else {}
        if op == _OP["full"]:
            ref = {f: payload.get(f) for f in self.field_order}
            return dict(ref), ref, "full"
        if op == _OP["diff"]:
            ref = dict(reference or {})
            ref.update(payload)                      # restore full from held ref + delta
            ref = {f: ref.get(f) for f in self.field_order}
            return dict(ref), ref, "diff"
        raise ValueError(f"unexpected op {op!r}")


class Channel:
    """One (peer, codex) convergence channel. offer() stages updates honoring
    freshness; flush() turns a tick's staged state into wire frames. Verbatim
    freshness logic from unrest_substrate.py."""

    def __init__(self, codex: Codex):
        self.codex = codex
        self.reference: Optional[Dict[str, Any]] = None
        self._latest: Dict[str, Any] = {}
        self._lossless: Dict[str, List[Any]] = {}

    def offer(self, field: str, value: Any):
        fr = self.codex.freshness.get(field, Freshness.LATEST_ONLY)
        if fr == Freshness.RESIDENT_ONCE:
            return                                   # crosses only via learn/FULL
        if fr == Freshness.LOSSLESS_EVENTUAL:
            self._lossless.setdefault(field, []).append(value)
        else:                                        # CONTINUOUS / LATEST_ONLY
            self._latest[field] = value              # overwrite => stale dropped

    def hostReset(self):
        """Network/host changed -> the converged reference is stale (the peer set on
        the far end moved). Drop the held reference and staged state so the next flush
        re-establishes the full structure from a FULL frame, not a DIFF against a
        reference the new peer never saw."""
        self.reference = None
        self._latest.clear()
        self._lossless.clear()

    def flush(self) -> List[Tuple[bytes, str]]:
        frames: List[Tuple[bytes, str]] = []
        depth = max([len(q) for q in self._lossless.values()] + [0])
        n_frames = max(depth, 1) if (self._latest or depth) else 0
        carry = dict(self.codex.resident)            # resident values ride the FULL
        carry.update(self.reference or {})
        for f in self.codex.field_order:
            carry.setdefault(f, None)
        for i in range(n_frames):
            values = dict(carry)
            values.update(self._latest)
            for field, q in self._lossless.items():
                if i < len(q):
                    values[field] = q[i]
            frame, new_ref, kind = self.codex.encode(values, self.reference)
            self.reference = new_ref
            frames.append((frame, kind))
            carry = dict(new_ref)
        self._latest.clear()
        self._lossless.clear()
        return frames
