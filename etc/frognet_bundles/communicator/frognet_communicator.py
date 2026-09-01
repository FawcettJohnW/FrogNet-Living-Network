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
# FrogNet socket-sets + transport modes - FEATURE STATUS:
# Logic + lifecycle verified in-container: 3 sets -> 3 distinct workers (set ids 0/1/2),
# intentional overlay away from the sensitive set, reap-on-last-release with all per-set
# threads exiting (no leak). Modes 0/1/2 verified on hardware (Pi 5). NOT yet measured:
# socket-set churn under real WireGuard crypto on the box, and the cross-box physical hop.
# Treat throughput/HOL claims as designed-and-unit-proven, not yet load-measured.
#
# =============================================================================
# frognet_communicator.py  -  the Communicator, on the UnREST convergence engine (rung 1: sim)
# =============================================================================
# Built to the doctrine in opt/frognet_semantic/UNREST_DOCTRINE.md:
#   "Exchange MEMORY, not messages."
#
# This is NOT the old bolt-on media encoder. It moves a STRUCTURE between two
# FrogNet endpoints and keeps the far copy CONVERGING on the near copy, using
# the REAL core/codec.py SemanticCodec convergence path:
#
#   - The structure is the payload. Most of it is a STABLE RESIDENT SCAFFOLD
#     (session/user/cell) that is learned once and NEVER re-crosses the wire.
#   - The A/V is ONE HOT FIELD that changes each tick; only changed fields cross.
#   - SAME/DIFF is a property of the data: encode_request_diff() emits only the
#     fields that differ from the running reference, and ZERO bytes when nothing
#     changed (REQ_REPEAT). The resident scaffold's bytes-on-wire amortize to ~0.
#
# Per-protocol codices declare "what memory, how often" via a per-field freshness
# policy; the convergence engine honors it. AVCodex is the first concrete one;
# GameCodex/CalendarCodex are declared here as contrasting freshness shapes.
#
# Backing swaps for higher rungs (code does not change):
#   - lz4: shimmed to stdlib zlib in-sandbox (the box has real lz4).
#   - template store: in-memory here, the real store.py/MariaDB on the box.
#   - wire: in-process byte channel here; sim WireMedium / loopback / real TCP later.
#
# CODEC FINDING (real, surfaced by reading core/codec.py):
#   SemanticCodec.TYPE_RAW decodes as lossy utf-8 ("replace") and caps at 64 KB
#   (2-byte length), so binary is NOT byte-exact through RAW. We carry binary
#   fields as base64 inside a JSON-typed field (4-byte length, exact round-trip).
#   Cost: ~33% base64 tax on the hot A/V field ONLY (scaffold win is unaffected).
#   Fixing the codec's RAW path (return bytes, 4-byte length) removes the tax.
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import json
import base64
import struct
import socket
import shutil
import platform
import threading
import queue
import argparse
import subprocess
import tempfile
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.5"
BUILD = "2026-06-09"

# ---------------------------------------------------------------------------
# lz4 -> zlib shim (sandbox backing; the box has real lz4). MUST precede the
# core.codec import, since codec.py does `import lz4.frame` at module top.
# ---------------------------------------------------------------------------
try:
    import lz4.frame  # noqa: F401
except ModuleNotFoundError:
    import types
    import zlib
    _lz4 = types.ModuleType("lz4")
    _frame = types.ModuleType("lz4.frame")
    _frame.compress = lambda data, *a, **k: zlib.compress(data, 9)
    _frame.decompress = lambda data, *a, **k: zlib.decompress(data)
    _lz4.frame = _frame
    sys.modules["lz4"] = _lz4
    sys.modules["lz4.frame"] = _frame

# ---------------------------------------------------------------------------
# Make `core` importable - SERVER-SIDE ONLY. The codex (BLDC-1/FNWP-1) lives in
# FrogNet, not in the app. The client (sender/viewer) is a thin app that ships
# raw native frames and needs only Python + ffmpeg - it must NOT require core/.
# So core is loaded lazily, by the server (and the --demo self-test), never at
# import time. _load_core() runs the finder and injects the symbols as globals.
# ---------------------------------------------------------------------------
import hashlib  # noqa: E402

SemanticCodec = None
wrap_req_full = wrap_req_diff = wrap_req_repeat = try_parse = None
OP_REQ_FULL = OP_REQ_DIFF = OP_REQ_REPEAT = None
REQ_HASH_LEN = None
_CORE_LOADED = False


def _find_frognet_root() -> Optional[str]:
    cands: List[str] = []
    env = os.environ.get("FROGNET_ROOT")
    if env:
        cands.append(env)
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(7):
        cands.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    cands += ["/opt/frognet_semantic", "/etc/frognet_semantic",
              "/usr/local/frognet_semantic", "/opt/frognet",
              "/opt/frognet/opt/frognet_semantic", os.getcwd()]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "core", "codec.py")):
            return c
    return None


def _load_core() -> None:
    """Locate and import the FrogNet codec. Server/demo only - never the client."""
    global SemanticCodec, wrap_req_full, wrap_req_diff, wrap_req_repeat, try_parse
    global OP_REQ_FULL, OP_REQ_DIFF, OP_REQ_REPEAT, REQ_HASH_LEN, _CORE_LOADED
    if _CORE_LOADED:
        return
    root = _find_frognet_root()
    if root is None:
        sys.stderr.write(
            "frognet_communicator: this is the SERVER (--serve/--demo) and it "
            "needs the FrogNet 'core' package (core/codec.py), not found.\n"
            "  Run it on the FrogNet Host, or set FROGNET_ROOT to the dir that "
            "CONTAINS core/ (e.g. FROGNET_ROOT=/opt/frognet_semantic).\n"
            "  (The client - --connect - does NOT need core; run that on your "
            "camera box with just Python + ffmpeg.)\n")
        sys.exit(2)
    if root not in sys.path:
        sys.path.insert(0, root)
    from core.codec import SemanticCodec as _SC, REQ_HASH_LEN as _RHL
    from core.semcache_wire import (
        wrap_req_full as _wf, wrap_req_diff as _wd, wrap_req_repeat as _wr,
        try_parse as _tp, OP_REQ_FULL as _of, OP_REQ_DIFF as _od,
        OP_REQ_REPEAT as _orp)
    SemanticCodec = _SC; REQ_HASH_LEN = _RHL
    wrap_req_full = _wf; wrap_req_diff = _wd; wrap_req_repeat = _wr; try_parse = _tp
    OP_REQ_FULL = _of; OP_REQ_DIFF = _od; OP_REQ_REPEAT = _orp
    _CORE_LOADED = True


# ===========================================================================
# Resident memory: the template store (real store.py interface, in-memory here)
# ===========================================================================

@dataclass
class _Template:
    """Shape decode_request() reads: .opcode, .url_query_keys, .fragment
    (with 'field_order'), .tokens. This IS the learned resident structure."""
    opcode: int
    url_query_keys: List[str]
    fragment: Dict[str, Any]
    tokens: Dict[str, Any]


class TemplateStore:
    """In-memory stand-in with the real store.py interface. The learned template
    is resident memory; in production this is store.py -> MariaDB, converged on
    both sides. Here both endpoints share one store (the template is resident on
    both by definition)."""

    def __init__(self):
        self._by_key: Dict[Tuple[str, str], _Template] = {}
        self._by_opcode: Dict[int, _Template] = {}

    @staticmethod
    def _opcode(method: str, semantic_path: str) -> int:
        key = f"{method.upper()} {semantic_path}".encode()
        return struct.unpack("<I", hashlib.blake2b(key, digest_size=4).digest())[0]

    def store_templates(self, method: str, semantic_path: str,
                        field_order: List[str], tokens: Optional[dict] = None) -> _Template:
        op = self._opcode(method, semantic_path)
        tpl = _Template(
            opcode=op, url_query_keys=[],
            fragment={"field_order": list(field_order)},
            tokens=tokens or {"ip": {}, "host": {}, "str": {}, "enum": {}})
        self._by_key[(method, semantic_path)] = tpl
        self._by_opcode[op] = tpl
        return tpl

    def lookup_templates(self, method: str, semantic_path: str) -> Optional[_Template]:
        return self._by_key.get((method, semantic_path))

    def lookup_templates_by_opcode(self, opcode: int) -> Optional[_Template]:
        return self._by_opcode.get(opcode)


# ===========================================================================
# "What memory, how often": per-field freshness classes (doctrine Sec.5)
# ===========================================================================

class Freshness(Enum):
    RESIDENT_ONCE = "resident-once"          # learned scaffold; ~never changes
    CONTINUOUS = "continuous"                # keep converged every tick (audio)
    LATEST_DROPPABLE = "latest-only"         # only newest matters (video, position)
    LOSSLESS_EVENTUAL = "lossless-eventual"  # must land, may be late (event, edit)


# ===========================================================================
# Codex interface - per-protocol intelligence (declares structure + freshness)
# ===========================================================================

class Codex:
    """A per-protocol codex. Declares its identity, the structure's field order,
    and each field's freshness. Converts app-native <-> shared structure, and
    drives the REAL SemanticCodec convergence path (FULL / DIFF / REPEAT)."""

    METHOD = "POST"
    SEMANTIC_PATH = "/unrest/base"
    FIELD_ORDER: List[str] = []
    FRESHNESS: Dict[str, Freshness] = {}

    # fields whose value is binary -> carried base64-in-JSON for exact round-trip
    BINARY_FIELDS: Tuple[str, ...] = ()

    def __init__(self, store: TemplateStore):
        self.codec = SemanticCodec()
        self.tpl = store.store_templates(self.METHOD, self.SEMANTIC_PATH,
                                         self.FIELD_ORDER)
        self.opcode = self.tpl.opcode
        self.req_hash = hashlib.blake2b(
            f"{self.METHOD} {self.SEMANTIC_PATH}".encode(),
            digest_size=REQ_HASH_LEN).digest()

    # ---- app-native <-> wire-carriable structure ----
    def _wire_value(self, fieldname: str, value: Any) -> Any:
        if fieldname in self.BINARY_FIELDS and isinstance(value, (bytes, bytearray)):
            # base64 inside a dict -> TYPE_JSON (4-byte len, exact). See header.
            return {"b64": base64.b64encode(bytes(value)).decode("ascii")}
        return value

    def _app_value(self, fieldname: str, value: Any) -> Any:
        if fieldname in self.BINARY_FIELDS and isinstance(value, dict) and "b64" in value:
            return base64.b64decode(value["b64"].encode("ascii"))
        return value

    def to_pairs(self, structure: Dict[str, Any]) -> List[Tuple[str, Any]]:
        """Ordered (field, wire_value) pairs in FIELD_ORDER."""
        return [(f, self._wire_value(f, structure.get(f)))
                for f in self.FIELD_ORDER]

    def from_pairs(self, json_vals: List[Tuple[str, Any]]) -> Dict[str, Any]:
        return {f: self._app_value(f, v) for (f, v) in json_vals}

    # ---- convergence: encode against running reference ----
    def encode(self, structure: Dict[str, Any],
               reference: Optional[Dict[str, Any]]) -> Tuple[bytes, Dict[str, Any], str]:
        """Returns (wire_frame, new_reference, kind) kind in {full,diff,repeat}."""
        pairs = self.to_pairs(structure)
        if reference is None:
            encoded = self.codec.encode_request(
                opcode=self.opcode, url_vals=[], json_vals=pairs,
                type_map={}, tokens=self.tpl.tokens, compress=True)
            new_ref = {f: v for (f, v) in pairs}
            return wrap_req_full(self.req_hash, encoded), new_ref, "full"
        encoded, new_ref, identical = self.codec.encode_request_diff(
            opcode=self.opcode, url_vals=[], json_vals=pairs,
            type_map={}, reference=reference, tokens=self.tpl.tokens, compress=True)
        if identical:
            return wrap_req_repeat(self.req_hash), new_ref, "repeat"
        return wrap_req_diff(self.req_hash, encoded), new_ref, "diff"

    # ---- convergence: decode/restore against far reference ----
    def decode(self, semantic_packet: bytes, is_full: bool,
               reference: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Returns (restored_structure, new_far_reference)."""
        _url, json_vals = self.codec.decode_request(
            semantic_packet, self.tpl, self.tpl.tokens,
            reference=None if is_full else reference)
        # decode_request already merges unchanged fields from reference for diffs.
        new_ref = {f: v for (f, v) in json_vals}
        structure = self.from_pairs(json_vals)
        return structure, new_ref


# ===========================================================================
# Concrete codices - three protocols, three freshness shapes (doctrine Sec.5)
# ===========================================================================

class AVCodex(Codex):
    """Video call. Hot: audio (continuous/protected) + video (latest-droppable).
    Resident: session/user/cell scaffold. level: latest. ts: latest."""
    METHOD = "POST"
    SEMANTIC_PATH = "/unrest/av-call"
    FIELD_ORDER = ["session", "user", "cell", "codec", "level", "ts", "audio", "video"]
    FRESHNESS = {
        "session": Freshness.RESIDENT_ONCE, "user": Freshness.RESIDENT_ONCE,
        "cell": Freshness.RESIDENT_ONCE, "codec": Freshness.RESIDENT_ONCE,
        "level": Freshness.LATEST_DROPPABLE, "ts": Freshness.LATEST_DROPPABLE,
        "audio": Freshness.CONTINUOUS,          # protected: a gap is audible
        "video": Freshness.LATEST_DROPPABLE,    # droppable: a stutter is invisible
    }
    BINARY_FIELDS = ("audio", "video")


class GameCodex(Codex):
    """Game. position/input: latest-droppable (LWW). event: lossless-eventual."""
    METHOD = "POST"
    SEMANTIC_PATH = "/unrest/game"
    FIELD_ORDER = ["world", "player", "position", "input", "event", "tick"]
    FRESHNESS = {
        "world": Freshness.RESIDENT_ONCE, "player": Freshness.RESIDENT_ONCE,
        "position": Freshness.LATEST_DROPPABLE, "input": Freshness.LATEST_DROPPABLE,
        "event": Freshness.LOSSLESS_EVENTUAL,   # death/score/pickup - never drop
        "tick": Freshness.LATEST_DROPPABLE,
    }


class CalendarCodex(Codex):
    """Calendar. Almost all resident; edits lossless-eventual; nothing droppable."""
    METHOD = "POST"
    SEMANTIC_PATH = "/unrest/calendar"
    FIELD_ORDER = ["account", "calendar", "records", "edit", "rev"]
    FRESHNESS = {
        "account": Freshness.RESIDENT_ONCE, "calendar": Freshness.RESIDENT_ONCE,
        "records": Freshness.RESIDENT_ONCE,
        "edit": Freshness.LOSSLESS_EVENTUAL, "rev": Freshness.LOSSLESS_EVENTUAL,
    }


# ===========================================================================
# Convergence engine - near publishes, far converges. Wire carries FNWP frames.
# ===========================================================================

@dataclass
class WireStats:
    frames: int = 0
    wire_bytes: int = 0
    full: int = 0
    diff: int = 0
    repeat: int = 0
    # bytes attributable to first-FULL (the resident scaffold + first hot) vs
    # the steady-state DIFF/REPEAT traffic (hot fields only).
    scaffold_bytes: int = 0
    steady_bytes: int = 0
    video_dropped: int = 0       # ticks where droppable video was shed to fit budget


class NearEndpoint:
    """The sending FrogNet: app hands it a structure each tick; it encodes the
    convergence delta and puts an FNWP frame on the wire. It HONORS the codex's
    per-field freshness: under a wire budget it drops LATEST_DROPPABLE binary
    fields (video) to stay live, but never the CONTINUOUS field (audio)."""
    def __init__(self, codex: Codex):
        self.codex = codex
        self.reference: Optional[Dict[str, Any]] = None
        self.stats = WireStats()
        self._last_committed: Dict[str, Any] = {}   # last APP structure actually sent

    def _droppable_binary(self) -> List[str]:
        return [f for f, fr in self.codex.FRESHNESS.items()
                if fr == Freshness.LATEST_DROPPABLE and f in self.codex.BINARY_FIELDS]

    def publish(self, structure: Dict[str, Any],
                budget_bytes: int = 0) -> Tuple[bytes, List[str]]:
        frame, new_ref, kind = self.codex.encode(structure, self.reference)
        dropped: List[str] = []

        # Over budget? Shed droppable video (set it back to last-sent value so the
        # diff omits it; the far side keeps its last good frame). Audio stays.
        if (budget_bytes and len(frame) > budget_bytes
                and self.reference is not None and self._last_committed):
            trial = dict(structure)
            for f in self._droppable_binary():
                if f in self._last_committed:
                    trial[f] = self._last_committed[f]   # unchanged -> omitted
                    dropped.append(f)
            if dropped:
                frame, new_ref, kind = self.codex.encode(trial, self.reference)
                structure = trial

        self.reference = new_ref
        self._last_committed = dict(structure)
        self.stats.frames += 1
        self.stats.wire_bytes += len(frame)
        if dropped:
            self.stats.video_dropped += 1
        if kind == "full":
            self.stats.full += 1; self.stats.scaffold_bytes += len(frame)
        else:
            self.stats.steady_bytes += len(frame)
            if kind == "diff":
                self.stats.diff += 1
            else:
                self.stats.repeat += 1
        return frame, dropped


class FarEndpoint:
    """The receiving FrogNet: pulls FNWP frames off the wire, runs the codex in
    reverse, and converges its copy of the structure."""
    def __init__(self, codex: Codex):
        self.codex = codex
        self.reference: Optional[Dict[str, Any]] = None
        self.structure: Dict[str, Any] = {}

    def receive(self, frame: bytes) -> Dict[str, Any]:
        msg = try_parse(frame)
        if msg is None:
            raise ValueError("unparseable FNWP frame")
        if msg.op == OP_REQ_REPEAT:
            return self.structure  # SAME: far copy already correct
        is_full = (msg.op == OP_REQ_FULL)
        structure, new_ref = self.codex.decode(msg.payload, is_full, self.reference)
        self.reference = new_ref
        self.structure = structure
        return structure


# ===========================================================================
# Media source (canned VP8 via ffmpeg; tree-free IVF parse) - the hot field
# ===========================================================================

def _iter_ivf(path: str):
    with open(path, "rb") as f:
        hdr = f.read(32)
        if hdr[:4] != b"DKIF":
            raise ValueError("not an IVF file")
        seq = 0
        while True:
            fh = f.read(12)
            if len(fh) < 12:
                return
            size = struct.unpack("<I", fh[:4])[0]
            data = f.read(size)
            if len(data) < size:
                return
            is_key = (len(data) > 0 and (data[0] & 0x01) == 0)
            yield seq, is_key, data
            seq += 1


def canned_vp8(ffmpeg: str, fps: int, duration: float, bitrate_kbps: int) -> str:
    path = os.path.join(tempfile.gettempdir(), "frognet_communicator_canned.ivf")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"testsrc=duration={duration}:size=320x240:rate={fps}",
                    "-c:v", "libvpx", "-b:v", f"{bitrate_kbps}k",
                    "-f", "ivf", path], check=True)
    return path


# ===========================================================================
# Demo / verification harness (rung 1: in-process wire)
# ===========================================================================

def run_demo(fps: int, duration: float, bitrate_kbps: int,
             out_ivf: Optional[str], budget_bytes: int) -> int:
    import shutil
    _load_core()                     # demo runs the codex in-process
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found"); return 2

    store = TemplateStore()          # resident memory, shared by both endpoints
    codex_near = AVCodex(store)
    codex_far = AVCodex(store)
    near = NearEndpoint(codex_near)
    far = FarEndpoint(codex_far)

    # The RESIDENT SCAFFOLD: realistic "crap that doesn't need to go" - session,
    # user, and cell records that are stable for the whole call.
    scaffold = {
        "session": {"id": "sess-7f3a", "started": 1733779200, "sr": 0,
                    "layout": "video", "tunnel": "wg-secondary"},
        "user": {"id": "u-julie", "display": "Julie", "device": "pixel-9",
                 "caps": ["vp8", "opus", "h264"], "loc": "Seattle"},
        "cell": {"id": "cell-42", "region": "us-west", "broker": "StreamingFrog",
                 "peers": ["u-donna"], "policy": {"max_level": 7, "min_level": 0}},
        "codec": "vp8",
    }

    print(f"FrogNet UnREST communicator v{VERSION} (build {BUILD})")
    print(f"  codex: {codex_near.SEMANTIC_PATH}  opcode=0x{codex_near.opcode:08x}")
    print(f"  fields: {codex_near.FIELD_ORDER}")
    print(f"  freshness: " + ", ".join(
        f"{k}={v.value}" for k, v in codex_near.FRESHNESS.items()))
    scaffold_raw = len(json.dumps(scaffold).encode())
    print(f"  resident scaffold ~{scaffold_raw} B (sent ONCE, then never)")
    if budget_bytes:
        print(f"  wire budget {budget_bytes} B/tick -> shed droppable VIDEO to fit, "
              f"keep CONTINUOUS audio")

    src = canned_vp8(ffmpeg, fps, duration, bitrate_kbps)
    viewer = open(out_ivf, "wb") if out_ivf else None
    ivf_header_written = False

    converged = av_sync_ok = audio_exact = video_exact_when_sent = 0
    video_sent = total = 0
    for seq, is_key, vp8 in _iter_ivf(src):
        total += 1
        ts = seq * int(1000 / max(fps, 1))
        # A/V UNIT: audio + video share one timestamp. Audio is a real, changing
        # 20ms stream (must stay converged); mic->Opus is the box rung.
        audio = bytes((seq + i) & 0xFF for i in range(160))
        structure = dict(scaffold)
        structure["level"] = 5
        structure["ts"] = ts
        structure["audio"] = audio            # CONTINUOUS / protected
        structure["video"] = vp8              # LATEST_DROPPABLE

        frame, dropped = near.publish(structure, budget_bytes)
        restored = far.receive(frame)

        # Convergence = far copy equals what we actually sent this tick.
        sent_struct = dict(structure)
        if "video" in dropped:
            sent_struct["video"] = near._last_committed["video"]
        if all(restored.get(k) == sent_struct.get(k) for k in codex_near.FIELD_ORDER):
            converged += 1
        # Audio must ALWAYS round-trip byte-exact (protected, never dropped).
        if restored.get("audio") == audio:
            audio_exact += 1
        # A/V SYNC: the timestamp is current every tick (audio never lags), even
        # when video is a frame stale.
        if restored.get("ts") == ts and restored.get("audio") == audio:
            av_sync_ok += 1
        # Video, when actually sent, is byte-exact; when dropped, far keeps last.
        if "video" not in dropped:
            video_sent += 1
            if restored.get("video") == vp8:
                video_exact_when_sent += 1
            if viewer:
                if not ivf_header_written:
                    viewer.write(_ivf_header(320, 240, fps)); ivf_header_written = True
                viewer.write(struct.pack("<IQ", len(vp8), seq)); viewer.write(vp8)

    if viewer:
        viewer.close()

    s = near.stats
    print(f"\n  ticks={total}  converged={converged}/{total}")
    print(f"  AUDIO (protected/continuous): byte-exact {audio_exact}/{total}  "
          f"A/V-sync {av_sync_ok}/{total}")
    print(f"  VIDEO (droppable): sent {video_sent}/{total} "
          f"(dropped {s.video_dropped} to fit budget), "
          f"byte-exact-when-sent {video_exact_when_sent}/{video_sent or 1}")
    print(f"  wire frames: FULL={s.full} DIFF={s.diff} REPEAT={s.repeat}")
    print(f"  wire bytes total={s.wire_bytes}  first-FULL={s.scaffold_bytes}  "
          f"steady={s.steady_bytes}")
    # Pass = perfect convergence, audio never lost, A/V always synced.
    ok = (converged == total and audio_exact == total and av_sync_ok == total
          and video_exact_when_sent == video_sent)
    if budget_bytes:
        ok = ok and s.video_dropped > 0   # the point: video DID shed, audio didn't
    print(f"  result: {'OK - audio held, video shed cleanly' if ok else 'MISMATCH'}")
    return 0 if ok else 1


def _ivf_header(w: int, h: int, fps: int) -> bytes:
    return (b"DKIF" + struct.pack("<HH", 0, 32) + b"VP80"
            + struct.pack("<HHIIII", w, h, fps, 1, 0, 0))


# ===========================================================================
# RUNNABLE: near endpoint (client) and far endpoint (server) over real TCP.
# The cross-machine wire carries the REAL FNWP convergence frames - app speaks
# native (raw VP8) to its local FrogNet (near), which converges it to the far
# FrogNet (server), which restores and plays it. This is the testable program.
# ===========================================================================

# socket message kinds (a length-prefixed envelope around each item on the TCP
# stream; the FRAME payload is the actual FNWP convergence frame)
# The client speaks NATIVE (raw frames) to its FrogNet (the server). The server
# runs the codex (FNWP/BLDC-1 convergence) in-process and fans reconstructed
# frames out to viewers. The CLIENT NEEDS NO core - just Python + ffmpeg.
K_INIT  = 1   # json: {role, stream, fps, w, h}
K_RAW   = 2   # sender->server: [seq:I][ts:Q][key:B][alen:I][vlen:I][audio][video]
K_RET   = 3   # server->viewer: [seq:I][ts:Q][key:B][alen:I][vlen:I][audio][video]
K_STAT  = 4   # json: rolling stats
K_END   = 5   # json: end of stream

# Real audio: 16 kHz, mono, signed 16-bit LE PCM (voice-grade, 32 kB/s).
AUDIO_RATE = 16000
AUDIO_CH   = 1
AUDIO_FMT  = "s16le"
AUDIO_BYTES_PER_SEC = AUDIO_RATE * AUDIO_CH * 2

_RAW_HDR = struct.Struct("!IQBII")
_RET_HDR = struct.Struct("!IQBII")


def _pack_raw(seq, ts, key, audio, video):
    return (_RAW_HDR.pack(seq, ts, 1 if key else 0, len(audio), len(video))
            + audio + video)


def _unpack_raw(payload):
    seq, ts, key, alen, vlen = _RAW_HDR.unpack_from(payload, 0)
    off = _RAW_HDR.size
    audio = payload[off:off + alen]; off += alen
    video = payload[off:off + vlen]
    return seq, ts, bool(key), audio, video


def _pack_ret(seq, ts, key, audio, video):
    return (_RET_HDR.pack(seq, ts, 1 if key else 0, len(audio), len(video))
            + audio + video)


def _unpack_ret(payload):
    seq, ts, key, alen, vlen = _RET_HDR.unpack_from(payload, 0)
    off = _RET_HDR.size
    audio = payload[off:off + alen]; off += alen
    video = payload[off:off + vlen]
    return seq, ts, bool(key), audio, video


def _send_env(sock: socket.socket, kind: int, payload: bytes) -> None:
    sock.sendall(struct.pack("!BI", kind, len(payload)) + payload)


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv_env(sock: socket.socket) -> Optional[Tuple[int, bytes]]:
    head = _recv_exact(sock, 5)
    if head is None:
        return None
    kind, n = struct.unpack("!BI", head)
    payload = _recv_exact(sock, n) if n else b""
    if payload is None:
        return None
    return kind, payload


# ---- OS-aware live capture (structurally complete per OS; runs on a real cam) ----

@dataclass
class Capabilities:
    os_name: str
    ffmpeg: Optional[str]
    cameras: List[str]


def probe_caps() -> Capabilities:
    osn = platform.system()
    ff = shutil.which("ffmpeg")
    cams: List[str] = []
    if osn == "Linux":
        cams = [p for p in ("/dev/video0", "/dev/video1") if os.path.exists(p)]
    elif osn == "Windows":
        cams = ["video=USB Video Device"]   # typical; override with --camera
    elif osn == "Darwin":
        cams = ["0"]
    return Capabilities(osn, ff, cams)


def _live_cmd(cap: Capabilities, fps: int, bitrate_kbps: int, camera: str) -> List[str]:
    br = f"{int(bitrate_kbps)}k"
    # enc = ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "5",
           # "-b:v", br, "-maxrate", br, "-bufsize", f"{int(bitrate_kbps)*2}k",
           # "-r", str(fps), "-g", str(max(1, fps)), "-f", "ivf", "pipe:1"]
    # [LIVE_BITRATE_FIX_V1] The doubled "-b:v br -b:v 4096" made 4096 win =
    # 4096 BITS/sec (~4kbps): encoder starved to q=63, duplicated frames (dup=39),
    # ran below real time (speed<1x) and drifted progressively behind - that was
    # the growing lag, not buffering. Single real bitrate + 1s keyframe interval.
    enc = ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8",
           "-b:v", br,
           "-r", str(fps), "-g", str(max(1, fps)), "-keyint_min", str(max(1, fps)),
           "-f", "ivf", "pipe:1"]
    # [CAPTURE_FRAMERATE_OUTPUT_V1] Do NOT pin -framerate on the INPUT: dshow (and
    # some v4l2/avfoundation devices) abort with "Could not set video options" when
    # the device doesn't enumerate that exact rate - one camera opens, another won't.
    # Let the device pick its native mode; cap the rate on OUTPUT (-r in enc), exactly
    # as the preview builder _cam_ppm_cmd already does.
    if cap.os_name == "Windows":
        return [cap.ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "dshow", "-rtbufsize", "64M", "-i", camera, *enc]
    if cap.os_name == "Darwin":
        return [cap.ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "avfoundation", "-i", camera, *enc]
    return [cap.ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-i", camera, *enc]


def _iter_ivf_stream(pipe):
    """Parse a live IVF byte stream from an ffmpeg stdout pipe."""
    hdr = pipe.read(32)
    if len(hdr) < 32 or hdr[:4] != b"DKIF":
        return
    seq = 0
    while True:
        fh = pipe.read(12)
        if len(fh) < 12:
            return
        size = struct.unpack("<I", fh[:4])[0]
        data = pipe.read(size)
        if len(data) < size:
            return
        is_key = (len(data) > 0 and (data[0] & 0x01) == 0)
        yield seq, is_key, data
        seq += 1


def _mic_cmd(cap: "Capabilities", mic: str) -> Optional[List[str]]:
    """ffmpeg command to capture the microphone as raw PCM on stdout. Per-OS input:
    dshow (Windows), avfoundation (macOS), alsa (Linux/Pi). Returns None if no mic."""
    if not mic:
        return None
    common_out = ["-ac", str(AUDIO_CH), "-ar", str(AUDIO_RATE),
                  "-f", AUDIO_FMT, "pipe:1"]
    base = [cap.ffmpeg, "-hide_banner", "-loglevel", "error"]
    if cap.os_name == "Windows":
        return base + ["-f", "dshow", "-i", mic, *common_out]
    if cap.os_name == "Darwin":
        return base + ["-f", "avfoundation", "-i", mic, *common_out]
    return base + ["-f", "alsa", "-i", mic, *common_out]     # Linux/Pi: hw:X,Y / plughw / default


class _MicBuffer:
    """Captures live PCM from a mic ffmpeg into a bounded buffer, drained per video
    frame. Continuous + non-blocking: a reader thread fills it; the send loop takes
    whatever accumulated since the last frame. Never stalls the video path."""
    def __init__(self, cap, mic):
        self.proc = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._cap_max = AUDIO_BYTES_PER_SEC          # ~1s ceiling; drop oldest beyond
        cmd = _mic_cmd(cap, mic)
        if not cmd:
            return
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
            threading.Thread(target=self._fill, daemon=True).start()
            print(f"  mic: {' '.join(cmd)}")
        except Exception as e:
            print(f"  mic capture failed ({e}); sending silence")
            self.proc = None

    def _fill(self):
        got = 0
        try:
            while True:
                chunk = self.proc.stdout.read(2048)
                if not chunk:
                    break
                got += len(chunk)
                if got and got - len(chunk) == 0:
                    print("  [MIC] first audio bytes received from device", file=sys.stderr)
                with self._lock:
                    self._buf.extend(chunk)
                    if len(self._buf) > self._cap_max:    # drop oldest, keep current
                        del self._buf[:len(self._buf) - self._cap_max]
        except Exception as e:
            print(f"  [MIC] capture stopped: {e}", file=sys.stderr)
        if got == 0:
            err = b""
            try: err = self.proc.stderr.read() if self.proc.stderr else b""
            except Exception: pass
            print(f"  [MIC] NO audio bytes captured - mic produced nothing. {err.decode('utf-8','replace')[:200]}",
                  file=sys.stderr)

    def take(self, nbytes: int) -> bytes:
        """Pull up to one frame's worth of PCM; pad with silence if the mic is
        behind, so audio stays CONTINUOUS (a gap is audible)."""
        with self._lock:
            n = min(nbytes, len(self._buf))
            out = bytes(self._buf[:n]); del self._buf[:n]
        if len(out) < nbytes:
            out += b"\x00" * (nbytes - len(out))     # silence pad - never a hole
        return out

    def close(self):
        if self.proc:
            try: self.proc.terminate()
            except Exception: pass


# ---- viewer: play reconstructed VP8 via ffplay and/or save to .ivf ----

def _overlay_vf(textfile: str) -> str:
    """drawtext filtergraph: up to 5 sensor lines from a RELOADING textfile (so
    values update live without restarting ffplay), plus a LOCAL clock pinned
    top-right. The clock is the machine's own time - always live even if the
    video freezes or the sensors go stale."""
    tf = textfile.replace("\\", "/").replace(":", "\\:")
    sensors = (f"drawtext=textfile='{tf}':reload=1:x=10:y=10:fontsize=18:"
               f"fontcolor=white:box=1:boxcolor=black@0.45:line_spacing=4")
    clock = ("drawtext=text='%{localtime}':x=w-tw-10:y=10:fontsize=18:"
             "fontcolor=white:box=1:boxcolor=black@0.45")
    return sensors + "," + clock


def _overlay_feeder(textfile: str, queries: list, dbhost: str,
                    stop: "threading.Event", interval: float = 5.0) -> None:
    """Resolve each selected sensor query from the transient and write the lines
    to the overlay textfile. Sensor values come from the DB, NOT the video path."""
    import urllib.request as _u
    while not stop.is_set():
        lines = []
        for q in queries[:5]:                       # at most five
            typ, _, nm = q.partition(":")
            url = (f"http://{dbhost}/api.php?entity=sensors&action=values"
                   f"&SensorType={typ}&parse=1")
            if nm:
                url += f"&SensorName={nm}"
            try:
                with _u.urlopen(url, timeout=3.0) as r:
                    rows = json.loads(r.read().decode()).get("rows", [])
                if rows:
                    d = rows[0].get("data") or {}
                    val = d if isinstance(d, (str, int, float)) else \
                        ", ".join(f"{k}={v}" for k, v in list(d.items())[:3])
                    lines.append(f"{nm or typ}: {val}")
                else:
                    lines.append(f"{nm or typ}: --")
            except Exception:
                lines.append(f"{nm or typ}: --")
        try:
            with open(textfile, "w") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
        stop.wait(interval)


class _Viewer:
    def __init__(self, display: bool, save: Optional[str], ffmpeg: Optional[str],
                 fps: int, dims: Tuple[int, int], overlay_file: Optional[str] = None,
                 audio: bool = False):
        self.display = display
        self.audio = audio
        self.save_path = save
        self.fps = fps
        self.w, self.h = dims
        self.proc = None
        self.fh = None
        self._hdr = False
        self._aproc = None                         # lazy audio-playback ffplay
        if display and ffmpeg:
            ffplay = (ffmpeg[:-6] + "ffplay") if ffmpeg.endswith("ffmpeg") else "ffplay"
            cmd = [ffplay, "-hide_banner", "-loglevel", "error", "-autoexit",
                   "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop",
                   "-probesize", "32", "-window_title", "FrogNet UnREST"]
            # Overlay (optional): sensor values from a reloading textfile + a LOCAL
            # clock top-right. Off unless --overlay is given, so the working video
            # path is byte-for-byte unchanged when no overlay is selected.
            vf = _overlay_vf(overlay_file) if overlay_file else None
            if vf:
                cmd += ["-vf", vf]
            cmd += ["-f", "ivf", "-i", "pipe:0"]
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            except Exception:
                self.proc = None
        self._fh_is_stdout = False
        if save == "-":
            # [SAVE_STDOUT_V1] '--save -' streams IVF to stdout (the app pipes it
            # into its decoder). Opening a file literally named '-' was the stale bug.
            self.fh = sys.stdout.buffer
            self._fh_is_stdout = True
        elif save:
            self.fh = open(save, "wb")

    def feed(self, seq: int, is_key: bool, vp8: bytes):
        if not self._hdr:
            if not is_key:
                return                     # wait for a keyframe to start decoding
            head = _ivf_header(self.w, self.h, self.fps)
            for sink in (self.proc.stdin if self.proc else None, self.fh):
                if sink:
                    try: sink.write(head)
                    except (OSError, ValueError): pass
            self._hdr = True
        rec = struct.pack("<IQ", len(vp8), seq) + vp8
        for sink in (self.proc.stdin if self.proc else None, self.fh):
            if sink:
                try: sink.write(rec); sink.flush()
                except (OSError, ValueError): pass

    def play_audio(self, pcm: bytes):
        """Play received PCM via a dedicated ffplay reading raw s16le from stdin.
        Started lazily on first audio so a video-only viewer pays nothing."""
        # [AUDIO_FLAG_V1] play when --audio was given (the peer leg), regardless of
        # display. Was gated on self.display, so the embedded --save peer leg never
        # played. Self leg has no --audio, so it stays muted (no echo).
        if not self.audio:
            return
        if self._aproc is None:
            ffmpeg = shutil.which("ffmpeg") or ""
            ffplay = (ffmpeg[:-6] + "ffplay") if ffmpeg.endswith("ffmpeg") else "ffplay"
            try:
                # Default buffering (NOT nobuffer/low_delay): the constant ALSA
                # underruns came from forcing minimal buffering and then feeding
                # tiny per-frame writes. ffplay's normal buffer absorbs the jitter.
                self._aproc = subprocess.Popen(
                    [ffplay, "-hide_banner", "-loglevel", "error", "-nodisp",
                     "-autoexit",
                     "-f", AUDIO_FMT, "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CH),
                     "-i", "pipe:0"], stdin=subprocess.PIPE)
                print("  [AUDIO-OUT] playback ffplay started", file=sys.stderr)
            except Exception as e:
                print(f"  [AUDIO-OUT] could not start playback: {e}", file=sys.stderr)
                self._aproc = False        # mark unavailable; don't retry
        if self._aproc:
            try: self._aproc.stdin.write(pcm); self._aproc.stdin.flush()
            except (OSError, ValueError): pass

    def close(self):
        sinks = [self.proc.stdin if self.proc else None]
        if self.fh and not self._fh_is_stdout:
            sinks.append(self.fh)
        for sink in sinks:
            if sink:
                try: sink.close()
                except (OSError, ValueError): pass
        if self._aproc:
            try: self._aproc.terminate()
            except Exception: pass


_SCAFFOLD = {
    "session": {"id": "sess-7f3a", "started": 1733779200, "sr": 0,
                "layout": "video", "tunnel": "wg-secondary"},
    "user": {"id": "u-julie", "display": "Julie", "device": "pixel-9",
             "caps": ["vp8", "opus", "h264"], "loc": "Seattle"},
    "cell": {"id": "cell-42", "region": "us-west", "broker": "StreamingFrog",
             "peers": ["u-donna"], "policy": {"max_level": 7, "min_level": 0}},
    "codec": "vp8",
}


def run_server(host: str, port: int, display: bool, save: Optional[str],
               budget_bytes: int) -> int:
    _load_core()                                  # server runs the codex
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port)); srv.listen(16)
    print(f"FrogNet Communicator - FrogNet Host (server) v{VERSION} on {host}:{port}")
    print("  app ships raw frames; FrogNet runs FNWP/BLDC-1 convergence + fan-out")
    if budget_bytes:
        print(f"  wire budget {budget_bytes} B/tick -> sheds droppable video, keeps audio")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_handle_conn,
                         args=(conn, addr, display, save, budget_bytes),
                         daemon=True).start()


# ---- pub/sub: one sender per named stream, N viewers ----
class _Hub:
    def __init__(self, name: str):
        self.name = name
        self.viewers: List[socket.socket] = []
        self.lock = threading.Lock()
        self.sender_active = False

    def add_viewer(self, s): 
        with self.lock: self.viewers.append(s)

    def remove_viewer(self, s):
        with self.lock:
            if s in self.viewers: self.viewers.remove(s)

    def count(self):
        with self.lock: return len(self.viewers)

    def broadcast(self, kind: int, payload: bytes):
        with self.lock:
            dead = []
            for v in self.viewers:
                try: _send_env(v, kind, payload)
                except OSError: dead.append(v)
            for v in dead: self.viewers.remove(v)


_HUBS: Dict[str, _Hub] = {}
_HUBS_LOCK = threading.Lock()


def _get_hub(name: str) -> _Hub:
    with _HUBS_LOCK:
        return _HUBS.setdefault(name, _Hub(name))


def _handle_conn(conn, addr, display, save, budget_bytes):
    who = f"{addr[0]}:{addr[1]}"
    try:
        env = _recv_env(conn)
        if not env or env[0] != K_INIT:
            return
        meta = json.loads(env[1] or b"{}")
        role = meta.get("role", "sender")
        stream = meta.get("stream", "default")
        fps = int(meta.get("fps", 10)); w = int(meta.get("w", 320)); h = int(meta.get("h", 240))
        if role == "viewer":
            print(f"  viewer {who} -> stream '{stream}'")
            _serve_viewer(conn, stream)
        else:
            print(f"  sender {who} -> stream '{stream}' ({w}x{h}@{fps})")
            _serve_sender(conn, stream, fps, w, h, display, save, budget_bytes)
    except Exception as e:
        print(f"  session error ({who}): {e!r}")
    finally:
        try: conn.close()
        except OSError: pass
        print(f"  {who} disconnected")


def _serve_viewer(conn, stream):
    hub = _get_hub(stream)
    hub.add_viewer(conn)
    print(f"  stream '{stream}' now has {hub.count()} viewer(s)")
    try:
        while True:                           # sender thread writes K_RET here
            m = _recv_env(conn)
            if m is None or m[0] == K_END:
                return
    finally:
        hub.remove_viewer(conn)


def _serve_sender(conn, stream, fps, w, h, display, save, budget_bytes):
    hub = _get_hub(stream)
    if hub.sender_active:
        _send_env(conn, K_STAT,
                  json.dumps({"error": "a sender is already active on this stream"}).encode())
        return
    hub.sender_active = True
    store = TemplateStore()
    near = NearEndpoint(AVCodex(store))         # FrogNet<->FrogNet convergence,
    far = FarEndpoint(AVCodex(store))           # in-process (app has no FrogNet)
    local_viewer = (_Viewer(display, save, shutil.which("ffmpeg"), fps, (w, h))
                    if (display or save) else None)
    converged = audio_ok = video_frames = total = 0
    last_video = None
    t0 = time.time()
    try:
        while True:
            m = _recv_env(conn)
            if m is None or m[0] == K_END:
                break
            if m[0] != K_RAW:
                continue
            seq, ts, key, audio, video = _unpack_raw(m[1])
            structure = dict(_SCAFFOLD)
            structure["level"] = 5; structure["ts"] = ts
            structure["audio"] = audio; structure["video"] = video
            frame, dropped = near.publish(structure, budget_bytes)   # encode
            restored = far.receive(frame)                            # restore
            total += 1
            rvid = restored.get("video"); raud = restored.get("audio")
            if isinstance(rvid, (bytes, bytearray)) and isinstance(raud, (bytes, bytearray)):
                converged += 1
            if raud == audio:
                audio_ok += 1
            # Audio is CONTINUOUS: send it EVERY frame. Video is droppable: include
            # it only when it actually changed (else empty -> viewer keeps last frame).
            aud_out = raud if isinstance(raud, (bytes, bytearray)) else b""
            vid_out = b""
            if isinstance(rvid, (bytes, bytearray)) and "video" not in dropped and rvid != last_video:
                video_frames += 1; last_video = rvid; vid_out = rvid
            is_key = (len(vid_out) > 0 and (vid_out[0] & 0x01) == 0)
            hub.broadcast(K_RET, _pack_ret(seq, ts, is_key, aud_out, vid_out))
            if local_viewer and vid_out:
                local_viewer.feed(seq, is_key, vid_out)
            if total % 30 == 0:
                s = near.stats
                stat = json.dumps({
                    "rx": total, "converged": converged, "video": video_frames,
                    "viewers": hub.count(), "wire_bytes": s.wire_bytes,
                    "vdrop": s.video_dropped, "full": s.full,
                    "diff": s.diff, "repeat": s.repeat}).encode()
                _send_env(conn, K_STAT, stat)
                hub.broadcast(K_STAT, stat)
    finally:
        hub.sender_active = False
        if local_viewer:
            local_viewer.close()
        end = json.dumps({"frames": total, "converged": converged}).encode()
        hub.broadcast(K_END, end)
        try: _send_env(conn, K_END, end)
        except OSError: pass
        s = near.stats; dt = time.time() - t0
        print(f"  --- '{stream}' summary ---  ticks={total} converged={converged}/{total} "
              f"audio_exact={audio_ok}/{total} distinct_video={video_frames}")
        print(f"      footprint: FULL={s.full} DIFF={s.diff} REPEAT={s.repeat} "
              f"wire={s.wire_bytes}B (scaffold crossed once) elapsed={dt:.1f}s")


# ===========================================================================
# CLIENT - thin app: ships raw frames (sender) or shows returns (viewer).
# NO core, no codec; just Python + ffmpeg.
# ===========================================================================

def run_client(host, port, role, stream, source, fps, duration,
               bitrate_kbps, camera, display, save,
               overlay=None, overlay_dbhost="databasehost.frognet", mic=None,
               audio=False) -> int:
    cap = probe_caps()
    # [PIPE_CLEAN_V1] all client status to stderr so a '--save -' viewer keeps stdout
    # as a pure IVF data channel (banner/telemetry on stdout corrupted the decoder).
    print(f"FrogNet Communicator - app (client) v{VERSION}  role={role} stream='{stream}'",
          file=sys.stderr)
    print(f"  os {cap.os_name}  ffmpeg {cap.ffmpeg or 'NOT FOUND'}  "
          f"cameras {cap.cameras or 'none'}", file=sys.stderr)
    if role == "viewer":
        return _client_viewer(host, port, stream, fps, display, save, cap,
                              overlay, overlay_dbhost, audio)
    return _client_sender(host, port, stream, source, fps, duration,
                          bitrate_kbps, camera, cap, mic)


def _client_viewer(host, port, stream, fps, display, save, cap,
                   overlay=None, overlay_dbhost="databasehost.frognet",
                   audio=False) -> int:
    if not (display or save):
        print("  viewer needs --display or --save (nothing to show otherwise)",
              file=sys.stderr)
        return 2
    # Optional sensor overlay: feeder thread writes selected sensor values from the
    # transient to a reloading textfile; ffplay drawtext composites them + a local
    # clock onto the picture. No overlay -> video path byte-for-byte unchanged.
    overlay_file = None
    overlay_stop = None
    if overlay and display:
        import tempfile
        queries = [q.strip() for q in overlay.split(",") if q.strip()]
        overlay_file = os.path.join(tempfile.gettempdir(),
                                    f"frognet_overlay_{stream}.txt")
        try:
            open(overlay_file, "w").write("\n")           # exist before ffplay starts
        except Exception:
            overlay_file = None
        if overlay_file:
            overlay_stop = threading.Event()
            threading.Thread(target=_overlay_feeder,
                             args=(overlay_file, queries, overlay_dbhost, overlay_stop),
                             daemon=True).start()
    sock = socket.create_connection((host, port), timeout=10)
    _send_env(sock, K_INIT, json.dumps({"role": "viewer", "stream": stream,
                                        "fps": fps, "w": 320, "h": 240}).encode())
    viewer = _Viewer(display, save, cap.ffmpeg, fps, (320, 240), overlay_file, audio)
    print(f"  viewing stream '{stream}' from {host}:{port}"
          + (f"  overlay={overlay}" if overlay_file else ""), file=sys.stderr)
    try:
        while True:
            m = _recv_env(sock)
            if m is None:
                print("\n  connection closed", file=sys.stderr); break
            k, p = m
            if k == K_RET:
                seq, ts, key, audio, video = _unpack_ret(p)
                if audio:
                    viewer.play_audio(audio)
                if video:
                    viewer.feed(seq, key, video)
            elif k == K_STAT:
                meta = json.loads(p or b"{}")
                if meta.get("error"):
                    print(f"\n  server: {meta['error']}", file=sys.stderr); break
                sys.stderr.write(
                    f"\r  [{stream}] rx {meta.get('rx',0)} video {meta.get('video',0)} "
                    f"viewers {meta.get('viewers','?')}   ")
                sys.stderr.flush()
            elif k == K_END:
                print("\n  --- stream ended ---", file=sys.stderr); break
    except KeyboardInterrupt:
        print("\n  stopped (Ctrl-C)")
    finally:
        viewer.close(); sock.close()
    return 0


def _client_sender(host, port, stream, source, fps, duration,
                   bitrate_kbps, camera, cap, mic=None) -> int:
    if not cap.ffmpeg:
        print("  ffmpeg required (sender captures/encodes)"); return 2
    use_live = (source == "live") or (source == "auto" and bool(cap.cameras))
    cam = camera or (cap.cameras[0] if cap.cameras else "")
    if source == "live" and not cap.cameras:
        print("  --source live but no camera; using canned"); use_live = False

    sock = socket.create_connection((host, port), timeout=10)
    _send_env(sock, K_INIT, json.dumps({"role": "sender", "stream": stream,
                                        "fps": fps, "w": 320, "h": 240}).encode())
    print(f"  sending to FrogNet Host {host}:{port}  "
          f"source={'live:'+cam if use_live else 'canned'}")

    # Real microphone, captured on its OWN ffmpeg into a non-blocking buffer. The
    # send loop pulls one frame's PCM per video frame. No mic -> silence, and the
    # video path is unchanged. Audio is its own capture, never gating video.
    micbuf = _MicBuffer(cap, mic) if mic else None
    aud_per_frame = int(AUDIO_BYTES_PER_SEC / max(fps, 1))
    aud_per_frame -= aud_per_frame % 2               # keep 16-bit sample alignment

    proc = None
    dropped_stale = [0]
    if use_live:
        cmd = _live_cmd(cap, fps, bitrate_kbps, cam)
        print(f"  capture: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        fq: "queue.Queue" = queue.Queue(maxsize=2)

        def _pump():
            for fr in _iter_ivf_stream(proc.stdout):
                if fq.full():
                    try: fq.get_nowait(); dropped_stale[0] += 1
                    except queue.Empty: pass
                fq.put(fr)
            fq.put(None)
        threading.Thread(target=_pump, daemon=True).start()

        def _frames():
            while True:
                fr = fq.get()
                if fr is None: return
                yield fr
        frames = _frames(); period = 0.0
    else:
        src = canned_vp8(cap.ffmpeg, fps, duration, bitrate_kbps)
        frames = _iter_ivf(src); period = 1.0 / max(fps, 1)

    sent = 0
    try:
        for seq, is_key, vp8 in frames:
            ts = seq * int(1000 / max(fps, 1))
            # real mic PCM for this frame (silence-padded if mic is behind); if no
            # mic configured, send a frame of silence so the wire stays continuous.
            audio = micbuf.take(aud_per_frame) if micbuf else b"\x00" * aud_per_frame
            _send_env(sock, K_RAW, _pack_raw(seq, ts, is_key, audio, vp8))
            sent += 1
            sys.stdout.write(f"\r  sent {sent}  stale-dropped {dropped_stale[0]}   ")
            sys.stdout.flush()
            if period:
                time.sleep(period)
    except KeyboardInterrupt:
        print("\n  stopped (Ctrl-C)")
    finally:
        if micbuf:
            micbuf.close()
        try: _send_env(sock, K_END, b"{}")
        except OSError: pass
        if proc: proc.terminate()
        sock.close()
    print(f"\n  done. sent {sent} frames")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="frognet_communicator")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--serve", metavar="[HOST:]PORT", help="run the FrogNet Host (server)")
    g.add_argument("--connect", metavar="HOST:PORT", help="run the app (client)")
    g.add_argument("--demo", action="store_true",
                   help="in-process self-test (convergence + freshness proof)")
    ap.add_argument("--role", choices=["sender", "viewer"], default="sender",
                    help="client role: sender (camera) or viewer (watch only)")
    ap.add_argument("--stream", default="default", help="stream name to publish/subscribe")
    ap.add_argument("--source", choices=["auto", "canned", "live"], default="auto")
    ap.add_argument("--camera", default=None, help="override capture device")
    ap.add_argument("--mic", default=None,
                    help="microphone capture device (dshow 'audio=...' / alsa hw:X,Y / avfoundation ':N')")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--duration", type=float, default=3.0, help="canned length (s)")
    ap.add_argument("--bitrate-kbps", type=int, default=120)
    ap.add_argument("--wire-budget-bytes", type=int, default=0,
                    help="server: max wire bytes/tick; over budget sheds droppable "
                    "video, keeps continuous audio (0 = unlimited)")
    ap.add_argument("--display", action="store_true", help="play returns via ffplay")
    ap.add_argument("--save", default=None, help="write reconstructed .ivf ('-' = stdout)")
    ap.add_argument("--audio", action="store_true",
                    help="viewer: play received audio (peer leg); self stays muted")
    ap.add_argument("--overlay", default=None,
                    help="viewer: comma-separated sensor queries to overlay (max 5), "
                         "each 'SensorType' or 'SensorType:SensorName'")
    ap.add_argument("--overlay-dbhost", default="databasehost.frognet",
                    help="transient host the overlay reads sensor values from")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(f"frognet_communicator v{VERSION} (build {BUILD})"); return 0

    if args.serve:
        host, port = (args.serve.rsplit(":", 1) if ":" in args.serve
                      else ("0.0.0.0", args.serve))
        return run_server(host, int(port), args.display, args.save, args.wire_budget_bytes)
    if args.connect:
        host, port = args.connect.rsplit(":", 1)
        return run_client(host, int(port), args.role, args.stream, args.source,
                          args.fps, args.duration, args.bitrate_kbps, args.camera,
                          args.display, args.save, args.overlay, args.overlay_dbhost,
                          args.mic, args.audio)
    return run_demo(args.fps, args.duration, args.bitrate_kbps, args.save,
                    args.wire_budget_bytes)


if __name__ == "__main__":
    sys.exit(main())
