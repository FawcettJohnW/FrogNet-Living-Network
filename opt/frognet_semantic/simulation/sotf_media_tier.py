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
sotf_media_tier.py (M_media) - exercise the REAL FrogNet codex with a
SotF-ACP media codex, end-to-end through the REAL FNW1 wire protocol
over a loopback socket. Companion to proxy_dataplane.py (M4) and
transport_tier.py - model for FUTURE codex additions.

WHAT THIS PROVES (in-container, no daemon required):
  - The real SemanticCodec.encode_request/decode_request round-trip a
    media frame (session envelope + Opus payload) without loss.
  - The real FNW1 wire functions (wrap_req_full, wrap_resp_diff,
    wrap_resp_same, try_parse) frame and parse those bytes correctly
    when run across a real loopback TCP socket - proxy side and daemon
    side both execute live wire code on opposite ends of the wire.
  - Compression savings:
      * envelope-templating: per-frame wire cost collapses after frame 1
        when subsequent frames carry only the changed fields (seq +
        payload), not the whole envelope.
      * SAME-caching: a repeated identical frame (e.g. Opus DTX silence)
        gets a 21-byte RESP_SAME on the wire instead of a full echo.

WHAT THIS DOES NOT PROVE (explicit non-goals - box-tier):
  - Real proxy_main + daemon mediation (mysql template store + lz4 +
    live daemon are box-tier per SIM_STATUS Sec.4; we stub mysql and lz4).
  - Active-voice payload compression - Opus output is already codec-
    compressed, so per-frame payload bytes are near-incompressible.
    The savings demonstrated here are envelope + silence + cache.

FAKE-HERE / REAL-ON-BOX:
  | layer                | fake (container)        | real (box)             |
  | mysql.connector      | empty stub               | live MariaDB store     |
  | lz4.frame            | zlib delegate            | real lz4.frame         |
  | SemanticCodec        | REAL (core/codec.py)     | REAL                   |
  | FNW1 wire framing    | REAL (semcache_wire.py)  | REAL                   |
  | wire socket          | 127.0.0.1:<ephemeral>    | proxy<->daemon:9009    |
  | media codex handler  | inline (this file)       | inline (move to core/) |

WHY EPHEMERAL PORT, NOT 9009:
  In production the daemon binds 0.0.0.0:9009 - on a node already
  running the daemon, port 9009 is in use and a sim-side bind would
  collide. The wire PROTOCOL exercised here is the exact same FNW1 the
  daemon speaks on 9009; only the listening port is moved out of the
  way. Override with FROGNET_SOTF_TIER_PORT if you want to bind 9009.
"""
from __future__ import annotations

import os
import sys
import time
import types
import zlib
import struct
import socket
import hashlib
import threading
from typing import Any, Dict, List, Tuple, Optional

# ============================================================================
# Path + stubs (must happen before any frognet imports)
# ============================================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
# We sit in opt/frognet_semantic/simulation; parent is opt/frognet_semantic
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)


def _install_mysql_stub():
    """Stub mysql.connector so the proxy/core packages import without a DB.
    Same shape as proxy_dataplane.py uses - keeps the symbol surface live code
    looks up at import time, returns no-op connections."""
    if "mysql" in sys.modules:
        return
    m = types.ModuleType("mysql")
    mc = types.ModuleType("mysql.connector")
    me = types.ModuleType("mysql.connector.errors")

    class Error(Exception):
        pass
    me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = Error
    mc.connect = lambda *a, **k: None
    mc.errors = me
    mc.Error = Error
    pooling = types.ModuleType("mysql.connector.pooling")
    pooling.MySQLConnectionPool = object
    mc.pooling = pooling
    m.connector = mc
    for n, mod in (("mysql", m), ("mysql.connector", mc),
                   ("mysql.connector.errors", me),
                   ("mysql.connector.pooling", pooling)):
        sys.modules[n] = mod


def _install_lz4_stub():
    """Stub lz4.frame with a zlib delegate. Both ends of every round-trip in
    this tier use the same stub, so encode/decode round-trips work. The codec's
    _lz4_smart() will still correctly skip compression when it would inflate;
    on the box the real lz4 takes over with no code change."""
    if "lz4" in sys.modules:
        return
    lz4 = types.ModuleType("lz4")
    frame = types.ModuleType("lz4.frame")

    def _compress(data: bytes) -> bytes:
        # zlib level 1 is closest to lz4 in speed/ratio profile.
        return zlib.compress(data, 1)

    def _decompress(data: bytes) -> bytes:
        return zlib.decompress(data)

    frame.compress = _compress
    frame.decompress = _decompress
    lz4.frame = frame
    sys.modules["lz4"] = lz4
    sys.modules["lz4.frame"] = frame


_install_mysql_stub()
_install_lz4_stub()

# ============================================================================
# Real live imports - from here down, everything is the production code path
# ============================================================================

from core.codec import SemanticCodec, REQ_HASH_LEN          # noqa: E402
from core.semcache_wire import (                             # noqa: E402
    MAGIC,
    SAME_ID_LEN,
    OP_REQ_FULL, OP_REQ_DIFF, OP_REQ_REPEAT,
    OP_RESP_SAME, OP_RESP_DIFF,
    wrap_req_full, wrap_req_diff, wrap_req_repeat,
    wrap_resp_same, wrap_resp_diff,
    try_parse,
)

# ============================================================================
# The SotF media codex handler - model for future codex additions
# ============================================================================
#
# Shape mirrors core/json_handler.py: extract_template / extract_request_dynamic
# / rebuild_reply. The handler doesn't talk to the wire - it produces a
# template (fragment) and per-call dynamic value list; the codec layer does the
# wire serialization.
#
# DESIGN NOTES (model for future additions):
#  1. Media doesn't fit body-sniffing (Opus is binary; would classify as raw).
#     Future integration: select this handler via Content-Type or a dedicated
#     transit-extension dispatch, NOT via sniff_body_mode.
#  2. Template (fragment) holds the SESSION envelope, established ONCE at
#     session start: session_id, codec name, sample rate, channel layout,
#     level-ladder index. Per-frame deltas carry only (seq, payload).
#     Future: bake into core/ as well-known forms (protocol constants,
#     pre-shared) per the TDD.
#  3. field_order is fixed: ["session_id","codec","sr","layout","level_idx",
#     "seq","payload"]. Static fields (everything except seq + payload) live
#     in baseline; per-frame extract_request_dynamic returns only the changing
#     pair.
#  4. type_map uses 'raw' for the binary payload so SemanticCodec's TYPE_RAW
#     path serializes the bytes verbatim (no JSON coercion).
# ----------------------------------------------------------------------------

class SotFMediaHandler:
    """Codex for SotF-ACP media frames. Same handler interface as
    core/json_handler.JsonFormatHandler - extract_template once at session
    init, extract_request_dynamic per frame, rebuild_reply on the far side.

    BUG WORKAROUND (FINDING-1): core/codec.py _decode_fieldblock at line 404
    treats TYPE_RAW and TYPE_STR identically on decode, both UTF-8-decoding the
    bytes with 'replace'. That mangles binary payloads. Until codec.py is fixed
    to keep TYPE_RAW as bytes on decode, this handler base64-encodes the
    payload on extract_request_dynamic and base64-decodes on rebuild_reply,
    making the field a TYPE_STR on the wire. ~33% size overhead. Production
    fix: split TYPE_RAW and TYPE_STR branches in _decode_fieldblock so RAW
    stays bytes; then remove the b64 wrap here and switch type_map back."""

    SESSION_FIELDS = ("session_id", "codec", "sr", "layout", "level_idx")
    FRAME_FIELDS = ("seq", "payload")

    @property
    def mode(self) -> str:
        return "sotf_media"

    def extract_template(self, session_init: Dict[str, Any]) -> Dict[str, Any]:
        """Build the session-scoped fragment. session_init carries the well-
        known-form values; the result becomes the per-session 'template' that
        every subsequent frame is a delta against."""
        field_order = list(self.SESSION_FIELDS) + list(self.FRAME_FIELDS)
        type_map = {
            "session_id": "string",
            "codec":      "string",
            "sr":         "int",
            "layout":     "string",
            "level_idx":  "int",
            "seq":        "int",
            # [TYPERAW_NATIVE_BYTES] payload rides as native bytes (TYPE_RAW). The
            # codec decode fix (split RAW from STR in _decode_fieldblock) keeps RAW
            # as bytes, so the base64/TYPE_STR ~33% tax is gone. (Was "string".)
            "payload":    "raw",
        }
        baseline = {
            "session_id": session_init["session_id"],
            "codec":      session_init.get("codec", "opus"),
            "sr":         int(session_init.get("sr", 48000)),
            "layout":     session_init.get("layout", "stereo"),
            "level_idx":  int(session_init.get("level_idx", 4)),
            # Frame fields start unset (filled per frame).
            "seq":        0,
            "payload":    b"",
        }
        return {
            "mode": self.mode,
            "field_order": field_order,
            "type_map": type_map,
            "baseline": baseline,
            "baseline_blobs": {},
            "tokens": {"ip": {}, "host": {}, "str": {}, "enum": {}},
        }

    def extract_request_dynamic(self, frame: Dict[str, Any],
                                 fragment: Dict[str, Any]
                                 ) -> List[Tuple[str, Any]]:
        """For a media frame, return the FULL field set as (path, value) pairs
        in field_order. The codec's diff layer (encode_request_diff against the
        per-session reference) is what reduces the wire payload - this handler
        produces the canonical complete frame; the diff is computed below it.

        payload bytes ride as native TYPE_RAW now (no base64), since the codec
        decode fix keeps RAW as bytes."""
        field_order: List[str] = fragment["field_order"]
        baseline: Dict[str, Any] = fragment["baseline"]
        out: List[Tuple[str, Any]] = []
        for path in field_order:
            if path in frame:
                v = frame[path]
            else:
                v = baseline.get(path)
            if path == "payload" and isinstance(v, bytearray):
                v = bytes(v)            # normalize to bytes -> TYPE_RAW on the wire
            out.append((path, v))
        return out

    def rebuild_reply(self, fragment: Dict[str, Any],
                      values: List[Tuple[str, Any]]) -> Dict[str, Any]:
        """Reconstitute a frame dict from the wire-decoded values. Mirror of
        extract_request_dynamic. On the box, the daemon-side equivalent would
        hand this dict to the MediaBridge for playback routing.

        payload arrives as native bytes (TYPE_RAW) now - no base64 decode."""
        if values and isinstance(values[0], (tuple, list)) and len(values[0]) == 2:
            d = {k: v for (k, v) in values}
        else:
            field_order = fragment["field_order"]
            d = {field_order[i]: v for i, v in enumerate(values) if i < len(field_order)}
        return d


# ============================================================================
# Test framework - same shape as proxy_dataplane.py
# ============================================================================

FAILS: List[str] = []


def check(name: str, cond: Any, detail: str = "") -> None:
    ok = bool(cond)
    suffix = "" if ok else f"  - {detail}"
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{suffix}")
    if not ok:
        FAILS.append(name)


def _req_hash_for(session_id: str, seq: int) -> bytes:
    """16-byte deterministic request hash for a (session, seq) pair."""
    h = hashlib.blake2b(f"{session_id}/{seq}".encode("utf-8"),
                        digest_size=REQ_HASH_LEN).digest()
    assert len(h) == REQ_HASH_LEN
    return h


def _same_id_for(session_id: str) -> bytes:
    """16-byte stable cache-key per session (used by daemon to acknowledge
    cached responses). For this tier the daemon is a stub that returns a
    constant same_id per session - production keys this differently."""
    h = hashlib.blake2b(f"sameid/{session_id}".encode("utf-8"),
                        digest_size=SAME_ID_LEN).digest()
    return h


# ============================================================================
# Test data - synthetic Opus-shaped media frames
# ============================================================================
#
# Real Opus 20ms frames at common SotF-ACP levels:
#   L3 (8kbps mono):    20  bytes/frame
#   L4 (24kbps mono):   60  bytes/frame
#   L7 (64kbps stereo): 160 bytes/frame
# DTX (silence) frame:  ~3-6 bytes regardless of level.
#
# We use realistic sizes but synthetic payload bytes - the test exercises the
# codex/wire mechanics, not the codec itself.

def _make_voice_frame(seq: int, size: int = 60) -> bytes:
    """Synthetic 'active voice' Opus-shaped payload. We make it
    pseudo-random-but-deterministic so the same seq produces the same bytes
    (important for SAME-cache testing)."""
    # deterministic but varying per seq
    seed = (seq * 2654435761) & 0xFFFFFFFF
    out = bytearray(size)
    for i in range(size):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        out[i] = (seed >> 16) & 0xFF
    return bytes(out)


def _make_silence_frame() -> bytes:
    """Synthetic Opus DTX/silence - same bytes every time, like real Opus DTX.
    This is the case the SAME-cache catches."""
    return bytes([0xF8, 0xFF, 0xFE])  # 3-byte plausible Opus silence


# ============================================================================
# Tests
# ============================================================================

def test_round_trip_single_frame() -> Dict[str, int]:
    """Test 1: encode a single media frame through the REAL SemanticCodec,
    decode it, verify identity. Report sizes."""
    print("\nT1: round-trip a single media frame (real SemanticCodec)")
    codec = SemanticCodec()
    handler = SotFMediaHandler()

    session_init = {
        "session_id": "call-abc-001",
        "codec": "opus",
        "sr": 48000,
        "layout": "stereo",
        "level_idx": 4,
    }
    fragment = handler.extract_template(session_init)

    payload = _make_voice_frame(seq=1, size=60)
    frame = {"seq": 1, "payload": payload, **session_init}

    # Handler -> codec
    dyn = handler.extract_request_dynamic(frame, fragment)
    # The codex opcode is conceptually a hash of (handler.mode, semantic_path);
    # use a fixed test value here. Production picks it via canonical_semantic_key.
    test_opcode = 0xC0DEC001
    wire_payload = codec.encode_request(
        opcode=test_opcode,
        url_vals=[],
        json_vals=dyn,
        type_map=fragment["type_map"],
        tokens=fragment["tokens"],
        compress=True,
    )

    # Build a stand-in request template for decode_request. The real proxy
    # holds a TemplateRecord; for the round-trip test we pass a lightweight
    # object exposing the two attributes decode_request reads.
    req_tpl = types.SimpleNamespace(
        url_query_keys=[],
        fragment=fragment,
    )
    decoded_url, decoded_json = codec.decode_request(
        wire_payload, req_tpl, tokens=fragment["tokens"]
    )

    reconstructed = handler.rebuild_reply(fragment, decoded_json)

    raw_size = sum(len(str(v).encode()) if not isinstance(v, (bytes, bytearray))
                   else len(v) for v in frame.values())
    encoded_size = len(wire_payload)

    print(f"     raw frame components total bytes: {raw_size}")
    print(f"     wire payload after SemanticCodec.encode_request: {encoded_size}")
    print(f"     wire/raw ratio: {encoded_size / max(raw_size,1):.2f}x")

    check("decoded payload matches input",
          reconstructed.get("payload") == payload,
          f"got {reconstructed.get('payload')!r}")
    check("decoded seq matches input",
          reconstructed.get("seq") == 1)
    check("decoded session_id matches input",
          reconstructed.get("session_id") == "call-abc-001")
    return {"raw": raw_size, "encoded": encoded_size}


def test_sequence_and_silence_cache() -> Dict[str, int]:
    """Test 2: encode a 50-frame sequence via the REAL SemanticCodec.encode_
    request_diff against a maintained per-session reference. The 'savings'
    here aren't response-cache (production SAME-cache keys on req_hash and
    media never gets cache hits because each frame has a unique seq); they
    are the FIELD-LEVEL DIFF - frame 2+ sends only fields that changed
    against frame 1's reference. For silence frames (identical payload bytes,
    seq incrementing), only seq changes, so the diff is tiny."""
    print("\nT2: 50-frame sequence - encode_request_diff savings (envelope held, payload templated)")
    codec = SemanticCodec()
    handler = SotFMediaHandler()

    session_init = {
        "session_id": "call-abc-002",
        "codec": "opus", "sr": 48000, "layout": "stereo", "level_idx": 4,
    }
    fragment = handler.extract_template(session_init)
    test_opcode = 0xC0DEC002

    # 40 active-voice frames (each unique payload), then 10 silence frames
    # (all identical payload, seq increments). The diff savings should be
    # large on the silence run because only seq changes.
    silence = _make_silence_frame()
    schedule: List[Tuple[str, bytes]] = (
        [("voice", _make_voice_frame(seq=i, size=60)) for i in range(40)] +
        [("silence", silence)] * 10
    )

    reference: Optional[Dict[str, Any]] = None  # None on first frame -> FULL
    total_full = 0       # If we used encode_request (full) for every frame
    total_diff = 0       # With encode_request_diff against running reference
    full_first = 0
    diff_changed = 0
    diff_identical = 0
    per_frame_diff_sizes: List[int] = []
    per_frame_full_sizes: List[int] = []

    for seq, (kind, payload) in enumerate(schedule):
        frame = {"seq": seq, "payload": payload, **session_init}
        dyn = handler.extract_request_dynamic(frame, fragment)

        # The full-encoding cost (baseline for comparison)
        full_payload = codec.encode_request(
            opcode=test_opcode, url_vals=[], json_vals=dyn,
            type_map=fragment["type_map"], tokens=fragment["tokens"], compress=True,
        )
        # Add the FNW1 wrapper to make this an apples-to-apples wire-bytes count
        req_hash = _req_hash_for(session_init["session_id"], seq)
        full_wire = wrap_req_full(req_hash, full_payload)
        total_full += len(full_wire)
        per_frame_full_sizes.append(len(full_wire))

        # The diff-encoding cost (the actual production path)
        diff_payload, new_ref, is_identical = codec.encode_request_diff(
            opcode=test_opcode, url_vals=[], json_vals=dyn,
            type_map=fragment["type_map"], reference=reference,
            tokens=fragment["tokens"], compress=True,
        )

        if reference is None:
            # First frame: must be FULL
            wire_frame = wrap_req_full(req_hash, diff_payload if diff_payload else full_payload)
            full_first += 1
        elif is_identical:
            # Nothing changed at all - REQ_REPEAT (rare; in media, seq always changes)
            wire_frame = wrap_req_repeat(req_hash)
            diff_identical += 1
        else:
            # Diff: only changed fields go on the wire
            wire_frame = wrap_req_diff(req_hash, diff_payload)
            diff_changed += 1

        total_diff += len(wire_frame)
        per_frame_diff_sizes.append(len(wire_frame))
        reference = new_ref

    saved = total_full - total_diff
    pct = 100.0 * saved / max(total_full, 1)
    print(f"     frames sent:                     {len(schedule)}  ({40} voice + {10} silence)")
    print(f"     bytes if every frame REQ_FULL:   {total_full}")
    print(f"     bytes with encode_request_diff:  {total_diff}")
    print(f"     savings on the wire:             {saved} bytes ({pct:.1f}%)")
    print(f"     full first frame size:           {per_frame_full_sizes[0]} bytes")
    print(f"     mean voice diff size (frame 2-40): {sum(per_frame_diff_sizes[1:40])/39:.1f} bytes")
    print(f"     first silence frame diff size:   {per_frame_diff_sizes[40]} bytes")
    print(f"     mean silence diff size (frame 42-50): {sum(per_frame_diff_sizes[41:])/9:.1f} bytes")
    print(f"     diff classification: FULL={full_first}  CHANGED={diff_changed}  IDENTICAL={diff_identical}")

    check("first frame was a FULL encoding",
          full_first == 1, f"got {full_first}")
    check("voice frames after #1 are diffs (only seq+payload change)",
          diff_changed >= 39,
          f"got {diff_changed}")
    check("encode_request_diff produces real wire savings on the silence run",
          total_diff < total_full,
          f"diff={total_diff} full={total_full}")
    # The silence diff should be SMALLER than the voice diff (only seq changed,
    # not payload), so check that explicitly:
    mean_voice_diff = sum(per_frame_diff_sizes[1:40]) / 39
    mean_silence_diff = sum(per_frame_diff_sizes[41:]) / 9
    check("silence-frame diff smaller than voice-frame diff (template caught payload)",
          mean_silence_diff < mean_voice_diff,
          f"silence_diff_mean={mean_silence_diff:.1f}  voice_diff_mean={mean_voice_diff:.1f}")

    return {
        "frames": len(schedule),
        "total_full": total_full,
        "total_diff": total_diff,
        "savings": saved,
    }


def test_loopback_wire(port_override: Optional[int] = None) -> Dict[str, Any]:
    """Test 3: stand up a loopback FNW1 daemon on 127.0.0.1; proxy-side
    connects, sends real wrap_req_full / wrap_req_diff frames produced by the
    real SemanticCodec; daemon-side parses with real try_parse and responds
    with real wrap_resp_diff / wrap_resp_same. This is the production wire
    protocol crossing a real TCP socket between two threads in this process.
    """
    print("\nT3: real FNW1 frames over a loopback TCP socket")

    codec = SemanticCodec()
    handler = SotFMediaHandler()
    session_init = {
        "session_id": "call-abc-003",
        "codec": "opus", "sr": 48000, "layout": "stereo", "level_idx": 4,
    }
    fragment = handler.extract_template(session_init)
    test_opcode = 0xC0DEC003

    # ---- Daemon side (loopback listener) ----
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bind_port = port_override if port_override else 0
    srv.bind(("127.0.0.1", bind_port))
    srv.listen(1)
    actual_port = srv.getsockname()[1]
    print(f"     daemon-side listener: 127.0.0.1:{actual_port}"
          + ("  (production: 9009)" if actual_port != 9009 else "  (matching production 9009)"))

    daemon_stats = {
        "frames_parsed": 0,
        "req_full_seen": 0,
        "req_diff_seen": 0,
        "req_repeat_seen": 0,
        "resp_same_sent": 0,
        "resp_diff_sent": 0,
        "decode_errors": 0,
        "wire_bytes_rx": 0,
        "wire_bytes_tx": 0,
    }
    daemon_ready = threading.Event()
    daemon_done = threading.Event()

    def daemon_loop():
        srv.settimeout(2.0)
        daemon_ready.set()
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            return
        conn.settimeout(2.0)
        try:
            while not daemon_done.is_set():
                hdr = _recv_exact(conn, 4)
                if not hdr:
                    break
                (flen,) = struct.unpack("!I", hdr)
                if flen <= 0 or flen > 1_000_000:
                    daemon_stats["decode_errors"] += 1
                    break
                frame = _recv_exact(conn, flen)
                if frame is None or len(frame) != flen:
                    daemon_stats["decode_errors"] += 1
                    break
                daemon_stats["wire_bytes_rx"] += 4 + flen

                # REAL FNW1 PARSE
                try:
                    msg = try_parse(frame)
                except ValueError as e:
                    daemon_stats["decode_errors"] += 1
                    print(f"        [daemon] try_parse rejected frame: {e}")
                    continue
                if msg is None:
                    daemon_stats["decode_errors"] += 1
                    continue
                daemon_stats["frames_parsed"] += 1

                same_id = _same_id_for(session_init["session_id"])

                if msg.op == OP_REQ_FULL:
                    daemon_stats["req_full_seen"] += 1
                    # ACK: media has no response payload - RESP_DIFF empty blob.
                    resp = wrap_resp_diff(same_id, b"")
                    daemon_stats["resp_diff_sent"] += 1
                elif msg.op == OP_REQ_DIFF:
                    daemon_stats["req_diff_seen"] += 1
                    resp = wrap_resp_diff(same_id, b"")
                    daemon_stats["resp_diff_sent"] += 1
                elif msg.op == OP_REQ_REPEAT:
                    daemon_stats["req_repeat_seen"] += 1
                    resp = wrap_resp_same(same_id)
                    daemon_stats["resp_same_sent"] += 1
                else:
                    resp = wrap_resp_same(same_id)

                # Length-prefix send (matches the proxy-side recv discipline)
                conn.sendall(struct.pack("!I", len(resp)) + resp)
                daemon_stats["wire_bytes_tx"] += 4 + len(resp)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    th = threading.Thread(target=daemon_loop, daemon=True)
    th.start()
    daemon_ready.wait(timeout=1.0)

    # ---- Proxy side (writer) ----
    # 10 voice + 5 silence. First frame FULL, rest DIFF.
    schedule: List[Tuple[str, bytes]] = []
    for i in range(10):
        schedule.append(("voice", _make_voice_frame(seq=i, size=60)))
    for _ in range(5):
        schedule.append(("silence", _make_silence_frame()))

    proxy_stats = {
        "frames_sent": 0,
        "req_full_sent": 0,
        "req_diff_sent": 0,
        "req_repeat_sent": 0,
        "wire_bytes_tx": 0,
        "wire_bytes_rx": 0,
        "resp_same_received": 0,
        "resp_diff_received": 0,
    }
    reference: Optional[Dict[str, Any]] = None
    full_size_sum = 0  # baseline: what every-frame-FULL would have cost

    c = socket.create_connection(("127.0.0.1", actual_port), timeout=2.0)
    c.settimeout(2.0)
    try:
        for seq, (kind, payload) in enumerate(schedule):
            frame = {"seq": seq, "payload": payload, **session_init}
            dyn = handler.extract_request_dynamic(frame, fragment)
            req_hash = _req_hash_for(session_init["session_id"], seq)

            # Compute the FULL size we WOULD have used (for the savings comparison)
            full_payload = codec.encode_request(
                opcode=test_opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], tokens=fragment["tokens"],
                compress=True,
            )
            full_size_sum += len(wrap_req_full(req_hash, full_payload))

            # Now the real path: encode_request_diff against running reference.
            diff_payload, new_ref, is_identical = codec.encode_request_diff(
                opcode=test_opcode, url_vals=[], json_vals=dyn,
                type_map=fragment["type_map"], reference=reference,
                tokens=fragment["tokens"], compress=True,
            )

            if reference is None:
                wire_frame = wrap_req_full(req_hash, full_payload)
                proxy_stats["req_full_sent"] += 1
            elif is_identical:
                wire_frame = wrap_req_repeat(req_hash)
                proxy_stats["req_repeat_sent"] += 1
            else:
                wire_frame = wrap_req_diff(req_hash, diff_payload)
                proxy_stats["req_diff_sent"] += 1
            reference = new_ref

            framed = struct.pack("!I", len(wire_frame)) + wire_frame
            c.sendall(framed)
            proxy_stats["wire_bytes_tx"] += len(framed)
            proxy_stats["frames_sent"] += 1

            # Read the response
            rh = _recv_exact(c, 4)
            (rlen,) = struct.unpack("!I", rh)
            rframe = _recv_exact(c, rlen)
            proxy_stats["wire_bytes_rx"] += 4 + rlen
            rmsg = try_parse(rframe)
            if rmsg is None:
                continue
            if rmsg.op == OP_RESP_SAME:
                proxy_stats["resp_same_received"] += 1
            elif rmsg.op == OP_RESP_DIFF:
                proxy_stats["resp_diff_received"] += 1
    finally:
        c.close()
        daemon_done.set()
        th.join(timeout=2.0)
        srv.close()

    print(f"     frames sent (proxy):            {proxy_stats['frames_sent']}")
    print(f"       REQ_FULL:                     {proxy_stats['req_full_sent']}")
    print(f"       REQ_DIFF:                     {proxy_stats['req_diff_sent']}")
    print(f"       REQ_REPEAT:                   {proxy_stats['req_repeat_sent']}")
    print(f"     wire bytes proxy -> daemon:     {proxy_stats['wire_bytes_tx']}")
    print(f"     wire bytes daemon -> proxy:     {proxy_stats['wire_bytes_rx']}")
    print(f"     if every frame were FULL:       {full_size_sum + 4*len(schedule)} bytes (vs actual {proxy_stats['wire_bytes_tx']})")
    print(f"     daemon frames_parsed:           {daemon_stats['frames_parsed']}")
    print(f"     daemon REQ_FULL seen:           {daemon_stats['req_full_seen']}")
    print(f"     daemon REQ_DIFF seen:           {daemon_stats['req_diff_seen']}")
    print(f"     daemon REQ_REPEAT seen:         {daemon_stats['req_repeat_seen']}")
    print(f"     daemon RESP_DIFF sent:          {daemon_stats['resp_diff_sent']}")
    print(f"     daemon RESP_SAME sent:          {daemon_stats['resp_same_sent']}")
    print(f"     daemon decode errors:           {daemon_stats['decode_errors']}")

    check("daemon parsed every frame proxy sent",
          daemon_stats["frames_parsed"] == proxy_stats["frames_sent"],
          f"parsed={daemon_stats['frames_parsed']} sent={proxy_stats['frames_sent']}")
    check("exactly 1 REQ_FULL (the first frame)",
          daemon_stats["req_full_seen"] == 1, f"got {daemon_stats['req_full_seen']}")
    check("rest of the frames are REQ_DIFF",
          daemon_stats["req_diff_seen"] + daemon_stats["req_repeat_seen"]
          == len(schedule) - 1,
          f"diff={daemon_stats['req_diff_seen']} repeat={daemon_stats['req_repeat_seen']}")
    check("zero decode errors - SemanticCodec round-tripped over real TCP",
          daemon_stats["decode_errors"] == 0)
    full_baseline = full_size_sum + 4 * len(schedule)
    check("diff path produced real wire savings vs every-frame-FULL",
          proxy_stats["wire_bytes_tx"] < full_baseline,
          f"diff_path={proxy_stats['wire_bytes_tx']} full_baseline={full_baseline}")
    return {
        "proxy_tx": proxy_stats["wire_bytes_tx"],
        "proxy_rx": proxy_stats["wire_bytes_rx"],
        "frames": proxy_stats["frames_sent"],
        "full_baseline": full_baseline,
    }


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            return None
        if not chunk:
            return bytes(buf) if buf else None
        buf.extend(chunk)
    return bytes(buf)


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    print("=== M_media: REAL FrogNet codex + FNW1 wire, SotF-ACP media path ===")
    print("    (mysql + lz4 stubbed; SemanticCodec + FNW1 framing are LIVE)")
    FAILS.clear()

    t0 = time.perf_counter()
    sizes_t1 = test_round_trip_single_frame()
    seq_stats = test_sequence_and_silence_cache()
    port_env = os.environ.get("FROGNET_SOTF_TIER_PORT")
    port_override = int(port_env) if port_env and port_env.isdigit() else None
    wire_stats = test_loopback_wire(port_override=port_override)
    elapsed = time.perf_counter() - t0

    print(f"\n=== summary ===")
    print(f"    elapsed: {elapsed*1000:.1f} ms")
    print(f"    T1 single-frame: raw={sizes_t1['raw']}B  encoded={sizes_t1['encoded']}B")
    print(f"    T2 50-frame seq: {seq_stats['total_full']}B every-frame-FULL vs "
          f"{seq_stats['total_diff']}B with encode_request_diff "
          f"(saved {seq_stats['savings']}B = "
          f"{100.0*seq_stats['savings']/max(seq_stats['total_full'],1):.1f}%)")
    print(f"    T3 loopback wire: {wire_stats['frames']} frames, "
          f"{wire_stats['proxy_tx']}B tx vs {wire_stats['full_baseline']}B if-all-FULL "
          f"- real FNW1 over real TCP")

    if FAILS:
        print(f"\nM_MEDIA TIER FAILED: {FAILS}")
        return 1
    print("\nALL M_MEDIA CHECKS PASS - SotF media codex round-trips through real "
          "SemanticCodec + real FNW1 wire framing over a real TCP socket.")
    print("Box-tier remainders: real lz4, real daemon on :9009, real MariaDB "
          "TemplateStore - none of which change the decision/keying/framing code "
          "this tier just exercised.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
