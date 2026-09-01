#!/opt/frognet_semantic/venv/bin/python3
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
core/codec.py - FrogNet Semantic Codec (v4.1) with DIFF encoding

v4.1 fixes:
- BUG 1 (REQ_REPEAT never fires): reference=None sentinel distinguishes
  "never sent" from "sent with zero fields".  encode_*_diff now correctly
  returns is_identical=True for repeated zero-field requests (echo, getHosts).
- BUG 3 (LZ4 inflates small payloads): lz4 skipped when the compressed
  output is larger than the raw payload.  LZ4 frame overhead is ~15 bytes;
  compressing 0-50 byte payloads makes them bigger.
- REQ_HASH_LEN constant (16 bytes / 128 bits) defined as single source of
  truth for wire-protocol request hashes.  Birthday collision at 10K items
  ~ 10?2? - astronomically safe.
"""

from __future__ import annotations

import struct
import json
from typing import Any, Dict, List, Tuple, Optional

import lz4.frame

# Fixed, authoritative wire version
WIRE_VERSION = 5   # v5: TYPE_INT widened int32(<i) -> int64(<q). v4 peers rejected by _assert_version.
FLAG_COMPRESSED = 0x01
FLAG_DIFF = 0x02  # Indicates diff (not all fields present)

# -----------------------------------------------------------------------
# Request hash size on the wire.  16 bytes saves 16 per request vs SHA256.
# Every REQ_FULL, REQ_REPEAT, REQ_RAW, REQ_MISS carries this hash.
# -----------------------------------------------------------------------
REQ_HASH_LEN = 16


def _lz4_smart(payload: bytes) -> Tuple[bytes, bool]:
    """
    Compress payload with LZ4 only when it actually shrinks the data.
    Returns (output_bytes, was_compressed).
    """
    if not payload:
        return payload, False
    compressed = lz4.frame.compress(payload)
    if len(compressed) < len(payload):
        return compressed, True
    return payload, False


class SemanticCodec:
    TYPE_NULL  = 0
    TYPE_RAW   = 1
    TYPE_STR   = 2
    TYPE_INT   = 3
    TYPE_FLOAT = 4
    TYPE_BOOL  = 5
    TYPE_JSON  = 7   # dict / list ONLY

    # ========================================================================
    # DIFF Encoding
    # ========================================================================

    def encode_request_diff(
        self,
        opcode: int,
        url_vals: List[Tuple[str, Any]],
        json_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        reference: Optional[Dict[str, Any]],  # None = never sent
        tokens,
        compress: bool = True,
    ) -> Tuple[bytes, Dict[str, Any], bool]:
        """
        Encode request as diff against reference values.

        Returns:
            (encoded_bytes, updated_reference, is_identical)

        reference semantics:
            None  -> first time for this (target_ip, opcode).  Send FULL.
            {}    -> previously sent with zero dynamic fields.  If still zero -> IDENTICAL.
            {k:v} -> previously sent with these values.  Diff against them.
        """
        merged = (url_vals or []) + (json_vals or [])

        # Build new reference and find changed fields
        new_reference = {}
        changed = []

        for idx, (field, value) in enumerate(merged):
            new_reference[field] = value
            if reference is not None:
                ref_value = reference.get(field)
                if not self._values_equal(value, ref_value):
                    changed.append((idx, field, value))
            else:
                # First time: every field is "changed"
                changed.append((idx, field, value))

        # BUG 1 FIX: reference is not None means we HAVE sent before.
        # If nothing changed (including zero-field -> zero-field), signal IDENTICAL.
        if not changed and reference is not None:
            return b"", new_reference, True

        fb = bytearray()
        for idx, field, value in changed:
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = FLAG_DIFF
        payload = bytes(fb)

        # BUG 3 FIX: only compress if it actually saves bytes
        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(changed))
        return header + payload, new_reference, False

    def encode_reply_diff(
        self,
        opcode: int,
        dynamic_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        reference: Optional[Dict[str, Any]],  # None = never sent
        tokens,
        compress: bool = True,
    ) -> Tuple[bytes, Dict[str, Any], bool]:
        """
        Encode reply as diff against reference values.

        Returns:
            (encoded_bytes, updated_reference, is_identical)
        """
        vals = dynamic_vals or []

        new_reference = {}
        changed = []

        for idx, (field, value) in enumerate(vals):
            new_reference[field] = value
            if reference is not None:
                ref_value = reference.get(field)
                if not self._values_equal(value, ref_value):
                    changed.append((idx, field, value))
            else:
                changed.append((idx, field, value))

        if not changed and reference is not None:
            return b"", new_reference, True

        fb = bytearray()
        for idx, field, value in changed:
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = FLAG_DIFF
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(changed))
        return header + payload, new_reference, False

    def _values_equal(self, a: Any, b: Any) -> bool:
        """Compare values with tolerance for floats."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if type(a) != type(b):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return abs(float(a) - float(b)) < 1e-9
            return False
        if isinstance(a, float):
            return abs(a - b) < 1e-9
        if isinstance(a, dict):
            if set(a.keys()) != set(b.keys()):
                return False
            return all(self._values_equal(a[k], b[k]) for k in a)
        if isinstance(a, list):
            if len(a) != len(b):
                return False
            return all(self._values_equal(x, y) for x, y in zip(a, b))
        return a == b

    # ========================================================================
    # FULL Encoding (original - for bootstrap/RAW)
    # ========================================================================

    def encode_request(
        self,
        opcode: int,
        url_vals: List[Tuple[str, Any]],
        json_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        tokens,
        compress: bool = True,
    ) -> bytes:
        merged = (url_vals or []) + (json_vals or [])

        fb = bytearray()
        for idx, (field, value) in enumerate(merged):
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = 0
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(merged))
        return header + payload

    def encode_reply(
        self,
        opcode: int,
        dynamic_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        tokens,
        compress: bool = True,
    ) -> bytes:

        fb = bytearray()
        for idx, (field, value) in enumerate(dynamic_vals or []):
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = 0
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(dynamic_vals or []))
        return header + payload

    def encode_error_reply(self, status: int, msg: str) -> bytes:
        # Sentinel opcode for engine-level errors. With 32-bit opcodes
        # we can pick a value far from anything CRC32 of a real path
        # would map to (and from store.py's 0-avoidance fallback).
        opcode = 0xFFFFFFFF
        flags = 0
        payload = f"{int(status)}:{msg}".encode("utf-8", "replace")

        fb = struct.pack("<HB", 0, self.TYPE_STR)
        fb += struct.pack("<H", len(payload)) + payload

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, 1)
        return header + fb

    def decode_error_reply(self, blob: bytes):
        """Inverse of encode_error_reply: recover (status, msg) from an engine error
        frame. The session layer must preserve the ACTUAL reason (e.g. "no templates
        for opcode=...") when it wraps the OP_ERROR -- flattening it to a generic string
        stripped the signal the proxy uses to trigger a RAW re-bootstrap, so a
        template-less opcode 503'd forever. Returns (status, msg) or None if not an
        error frame. Layout: header(BBIH,8) + <HB idx,type>(3) + <H len>(2) + payload."""
        try:
            if not blob or len(blob) < 13:
                return None
            if struct.unpack("<I", blob[2:6])[0] != 0xFFFFFFFF:
                return None
            n = struct.unpack("<H", blob[11:13])[0]
            payload = blob[13:13 + n].decode("utf-8", "replace")
            status_str, _, msg = payload.partition(":")
            return int(status_str), msg
        except (struct.error, ValueError, IndexError):
            return None

    # ========================================================================
    # Decoding (with DIFF support)
    # ========================================================================

    def decode_request(
        self,
        packet: bytes,
        req_tpl,
        tokens,
        reference: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Tuple[str, Any]], List[Tuple[str, Any]]]:
        version, flags, opcode, n = struct.unpack("<BBIH", packet[:8])
        self._assert_version(version)

        fb = packet[8:]
        if flags & FLAG_COMPRESSED:
            fb = lz4.frame.decompress(fb)

        vals = self._decode_fieldblock(fb, n)
        is_diff = bool(flags & FLAG_DIFF)

        url_keys = getattr(req_tpl, "url_query_keys", []) or []
        json_fields = (getattr(req_tpl, "fragment", {}) or {}).get("field_order", []) or []

        if is_diff and reference:
            all_fields = url_keys + json_fields
            for idx, field in enumerate(all_fields):
                if idx not in vals and field in reference:
                    vals[idx] = reference[field]

        url_vals = [(k, vals.get(i)) for i, k in enumerate(url_keys)]
        base = len(url_keys)
        json_vals = [(f, vals.get(base + j)) for j, f in enumerate(json_fields)]

        return url_vals, json_vals

    def decode_reply(
        self,
        packet: bytes,
        resp_tpl,
        tokens,
        reference: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, Any]]:
        version, flags, opcode, n = struct.unpack("<BBIH", packet[:8])
        self._assert_version(version)

        fb = packet[8:]
        if flags & FLAG_COMPRESSED:
            fb = lz4.frame.decompress(fb)

        vals = self._decode_fieldblock(fb, n)
        is_diff = bool(flags & FLAG_DIFF)

        frag = getattr(resp_tpl, "fragment", {}) or {}
        field_order = frag.get("field_order", []) or []

        if is_diff and reference:
            for idx, field in enumerate(field_order):
                if idx not in vals and field in reference:
                    vals[idx] = reference[field]

        if not field_order:
            return [(f"field{idx}", vals.get(idx)) for idx in range(n)]

        return [(field_order[i], vals.get(i)) for i in range(len(field_order))]

    # ========================================================================
    # Internal helpers
    # ========================================================================

    def _assert_version(self, version: int) -> None:
        if version != WIRE_VERSION:
            # [CODEC-DECODE] localization print - first16 of the buffer
            # we're trying to decode tells us whether it's an FNW1 frame
            # (464e5731 = 'FNW1') being wrong-layered, or some other 'F'
            # leading payload (daemon misencoded).
            try:
                import inspect, sys
                frame = inspect.currentframe().f_back
                packet = frame.f_locals.get('packet') or frame.f_locals.get('blob')
                first16 = packet[:16].hex() if packet else '<no-packet>'
                print(f"[CODEC-DECODE] first16={first16} caller={frame.f_code.co_name}", file=sys.stderr)
            except Exception:
                pass
            raise ValueError(f"Unsupported semantic wire version {version}")

    def _type_to_id(self, value: Any) -> int:
        if value is None:
            return self.TYPE_NULL
        if isinstance(value, (dict, list)):
            return self.TYPE_JSON
        if isinstance(value, bool):
            return self.TYPE_BOOL
        if isinstance(value, int):
            return self.TYPE_INT
        if isinstance(value, float):
            return self.TYPE_FLOAT
        if isinstance(value, (bytes, bytearray)):
            return self.TYPE_RAW
        return self.TYPE_STR

    def _encode_value(self, type_id: int, value: Any) -> bytes:
        if type_id == self.TYPE_NULL:
            return b""
        if type_id == self.TYPE_JSON:
            data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return struct.pack("<I", len(data)) + data  # 4-byte length, supports up to 4GB
        if type_id == self.TYPE_RAW:
            if not isinstance(value, (bytes, bytearray)):
                raise TypeError(f"TYPE_RAW requires bytes, got {type(value).__name__}: {value!r}")
            b = bytes(value)
            if len(b) > 0xFFFF:
                raise ValueError(f"TYPE_RAW over 65535-byte wire field: {len(b)} bytes")
            return struct.pack("<H", len(b)) + b
        if type_id == self.TYPE_STR:
            if not isinstance(value, str):
                raise TypeError(f"TYPE_STR requires str, got {type(value).__name__}: {value!r}")
            b = value.encode("utf-8")  # strict: un-encodable raises, no silent 'replace'
            if len(b) > 0xFFFF:
                raise ValueError(f"TYPE_STR over 65535-byte wire field: {len(b)} bytes")
            return struct.pack("<H", len(b)) + b
        if type_id == self.TYPE_INT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"TYPE_INT requires int, got {type(value).__name__}: {value!r}")
            if not (-9223372036854775808 <= value <= 9223372036854775807):
                raise ValueError(f"TYPE_INT does not fit signed int64 wire field: {value!r}")
            return struct.pack("<q", value)
        if type_id == self.TYPE_FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"TYPE_FLOAT requires number, got {type(value).__name__}: {value!r}")
            return struct.pack("<d", float(value))
        if type_id == self.TYPE_BOOL:
            if not isinstance(value, (bool, int)):
                raise TypeError(f"TYPE_BOOL requires bool/int, got {type(value).__name__}: {value!r}")
            return struct.pack("<B", 1 if value else 0)
        raise ValueError(f"Unknown type_id {type_id}")

    def _decode_fieldblock(self, fb: bytes, n: int) -> Dict[int, Any]:
        vals: Dict[int, Any] = {}
        p = 0
        for _ in range(n):
            idx, type_id = struct.unpack("<HB", fb[p:p+3])
            p += 3
            if type_id == self.TYPE_NULL:
                vals[idx] = None
            elif type_id == self.TYPE_JSON:
                L = struct.unpack("<I", fb[p:p+4])[0]; p += 4
                vals[idx] = json.loads(fb[p:p+L].decode("utf-8", "replace")); p += L
            elif type_id == self.TYPE_RAW:
                # [TYPERAW_NATIVE_BYTES] TYPE_RAW carries opaque binary (VP8/Opus
                # frames, etc.). Return the raw bytes UNCHANGED. The old code folded
                # RAW in with STR and ran .decode("utf-8","replace"), which mangled
                # every non-UTF-8 byte to U+FFFD and returned a str (FINDING-1) -
                # which is why the media path had to base64 as TYPE_STR.
                L = struct.unpack("<H", fb[p:p+2])[0]; p += 2
                vals[idx] = fb[p:p+L]; p += L
            elif type_id == self.TYPE_STR:
                L = struct.unpack("<H", fb[p:p+2])[0]; p += 2
                vals[idx] = fb[p:p+L].decode("utf-8", "replace"); p += L
            elif type_id == self.TYPE_INT:
                vals[idx] = struct.unpack("<q", fb[p:p+8])[0]; p += 8
            elif type_id == self.TYPE_FLOAT:
                vals[idx] = struct.unpack("<d", fb[p:p+8])[0]; p += 8
            elif type_id == self.TYPE_BOOL:
                vals[idx] = bool(fb[p]); p += 1
            else:
                raise ValueError(f"Unknown type_id {type_id}")
        return vals

# ---- DEPLOYMENT MARKER ----
print("[codec] BUILD=2026-03-24-v1", flush=True)
